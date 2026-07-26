#!/usr/bin/env python3
"""Unit tests for the kernel budget checker."""

import importlib.util
import io
import json
import copy
import re
import statistics
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-kernel-budgets.py")
STACK_SCRIPT = Path(__file__).with_name("check-kernel-stack-usage.py")
SPEC = importlib.util.spec_from_file_location("kernel_budgets", SCRIPT)
kernel_budgets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kernel_budgets)


class KernelBudgetTests(unittest.TestCase):
    def assert_store_wrapper_projection_protocol(self, body, call):
        call_at = body.index(call)
        complete_at = body.index("agent_file_store_complete(&commit, result)")
        self.assertLess(call_at, complete_at)
        before_complete = body[call_at:complete_at]
        before_complete = re.sub(r"\breturn\s*$", "", before_complete)
        self.assertIsNone(
            re.search(r"\b(?:goto|return)\b", before_complete)
        )

    def assert_projection_checkpoint_guard(self, body):
        self.assertIn("txn_projection_pending", body)
        guard = body.index("txn_projection_pending")
        self.assertLess(guard, body.index("agent_metadata_txn_unlock()"))

    def assert_projection_idle_guard(self, body):
        compact = re.sub(r"\s+", "", body)
        self.assertIn(
            "if(!agent_metadata_txn_owned(0)||txn_projection_pending)panic(",
            compact,
        )

    def assert_persist_projection_guard(self, body, barriers):
        self.assertEqual(
            body.count("agent_metadata_txn_projection_require_idle()"), 1
        )
        idle_at = body.index("agent_metadata_txn_projection_require_idle()")
        for barrier in barriers:
            self.assertLess(idle_at, body.index(barrier))

    def test_source_measurement_is_physical_and_excludes_generated_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "os").mkdir()
            (root / "os" / "a.c").write_bytes(b"one\n\nthree\n")
            (root / "os" / "a.h").write_bytes(b"one")
            (root / "os" / "generated.inc").write_bytes(b"two\nlines\n")
            (root / "os" / "initproc.S").write_bytes(b"ignored\n")
            (root / "mode_policy.inc").write_bytes(b"policy\n")
            lines, files = kernel_budgets.measure_source_lines(
                root,
                [
                    "os/**/*.c",
                    "os/**/*.h",
                    "os/**/*.S",
                    "os/**/*.inc",
                    "*_policy.inc",
                ],
                ["os/initproc.S"],
            )
            self.assertEqual((lines, files), (7, 4))

    def test_aggregate_source_bytes_ignore_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lf.c").write_bytes(b"one\ntwo\n")
            (root / "crlf.c").write_bytes(b"one\r\ntwo\r\n")
            self.assertEqual(
                kernel_budgets.measure_file_source(root, "lf.c"),
                kernel_budgets.measure_file_source(root, "crlf.c"),
            )

    def test_metadata_private_apis_stay_out_of_shared_agent_contract(self):
        shared = (SCRIPT.parent.parent / "os" / "agent_internal.h").read_text(
            encoding="utf-8"
        )
        private_api = re.compile(
            r"agent_(?:metadata_(?:catalog|store|query|scan|directory)|"
            r"file_(?:state|query|scan|directory)|query|scan|directory)_"
        )
        private_include = re.compile(
            r'#include\s+"agent_(?:metadata_(?:catalog|internal|query|scan|directory)|'
            r'file_(?:state_internal|name_policy)|query|scan|directory).*\.h"'
        )
        self.assertIsNone(private_api.search(shared))
        self.assertIsNone(private_include.search(shared))

    def test_catalog_projection_commit_is_explicit_and_non_indirect(self):
        root = SCRIPT.parent.parent
        catalog = (root / "os" / "agent_metadata_catalog.c").read_text(
            encoding="utf-8"
        )
        metadata = (root / "os" / "agent_metadata.c").read_text(
            encoding="utf-8"
        )
        objects = (root / "os" / "agent_metadata_objects.c").read_text(
            encoding="utf-8"
        )
        query = (root / "os" / "agent_metadata_query.c").read_text(
            encoding="utf-8"
        )
        scan = (root / "os" / "agent_metadata_scan.c").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("projection_commit", catalog)
        self.assertNotIn("agent_file_catalog_sync", catalog)
        self.assertIsNone(
            re.search(r"\(\*\s*[A-Za-z_]\w*\s*\)\s*\(", catalog)
        )
        wrappers = (
            (
                "agent_metadata_storage_init(void)",
                "agent_metadata_store_load(&commit)",
            ),
            (
                "agent_file_store_load(void)",
                "agent_metadata_store_load(&commit)",
            ),
            (
                "agent_file_store_reload(uint scope_id)",
                "agent_metadata_store_reload(scope_id, &commit)",
            ),
            (
                "agent_file_install_empty_store(void)",
                "agent_metadata_store_install_empty(&commit)",
            ),
        )
        for signature, call in wrappers:
            with self.subTest(signature=signature):
                start = objects.index(signature)
                end = objects.index("\n}\n", start)
                body = objects[start:end]
                self.assert_store_wrapper_projection_protocol(body, call)
        complete_at = objects.index(
            "static int\nagent_file_store_complete("
        )
        complete_end = objects.index("\n}\n", complete_at)
        complete = objects[complete_at:complete_end]
        sync_at = complete.index("agent_file_catalog_sync(&commit->delta)")
        finish_at = complete.index("agent_metadata_store_finish(commit")
        unlock_at = complete.index("agent_metadata_txn_unlock()")
        self.assertLess(sync_at, finish_at)
        self.assertLess(finish_at, unlock_at)
        install_at = objects.index("agent_file_install_empty_store(void)")
        install_end = objects.index("\n}\n", install_at)
        install = objects[install_at:install_end]
        install_sync_at = install.index(
            "agent_file_store_complete(&commit, result)"
        )
        install_failure_at = install.index("if (result < 0)")
        self.assertLess(install_sync_at, install_failure_at)

        clear_at = catalog.index("agent_metadata_catalog_clear(")
        clear_end = catalog.index("\n}\n", clear_at)
        clear = catalog[clear_at:clear_end]
        self.assertLess(
            clear.index("agent_metadata_txn_projection_begin()"),
            clear.index("memset(agent_catalog_files"),
        )
        apply_at = catalog.index("agent_metadata_catalog_apply_snapshot(")
        apply_end = catalog.index("\n}\n", apply_at)
        apply = catalog[apply_at:apply_end]
        self.assertLess(
            apply.index("agent_metadata_txn_projection_begin()"),
            apply.index("if (reload_one_scope)"),
        )

        sync_at = objects.index(
            "agent_file_catalog_sync(const struct agent_catalog_delta *delta)"
        )
        sync_end = objects.index("\n}\n", sync_at)
        sync = objects[sync_at:sync_end]
        self.assertEqual(sync.count("agent_metadata_txn_projection_ack()"), 1)
        self.assertLess(
            sync.index("agent_metadata_query_invalidate_locked("),
            sync.index("agent_metadata_scan_catalog_sync(delta)"),
        )
        self.assertLess(
            sync.index("agent_metadata_scan_catalog_sync(delta)"),
            sync.index("agent_metadata_txn_projection_ack()"),
        )
        scan_sync = kernel_budgets.source_function_body(
            scan,
            "agent_metadata_scan_catalog_sync(const struct agent_catalog_delta *delta)",
        )
        self.assertIn("delta->applied_slots", scan_sync)
        self.assertLess(
            scan_sync.index("delta->applied_slots"),
            scan_sync.index("scan.seen[i] = 1"),
        )
        self.assertNotIn("agent_metadata_txn_projection_ack", scan_sync)

        invalidate_at = query.index(
            "agent_metadata_query_invalidate_locked(uint scope_id, int full)"
        )
        invalidate_end = query.index("\n}\n", invalidate_at)
        invalidate = query[invalidate_at:invalidate_end]
        self.assertLess(
            invalidate.index("agent_metadata_txn_work_charge(0)"),
            invalidate.index("if (full)"),
        )
        execute_at = query.index("agent_metadata_query_execute_locked(")
        execute_end = query.index("\n}\n", execute_at)
        execute = query[execute_at:execute_end]
        self.assertLess(
            execute.index("agent_metadata_txn_projection_require_idle()"),
            execute.index("limit = q->max_hits"),
        )

        unlock_at = metadata.index("agent_metadata_txn_unlock(void)")
        unlock_end = metadata.index("\n}\n", unlock_at)
        unlock = metadata[unlock_at:unlock_end]
        self.assertLess(
            unlock.index("txn_depth == 1 && txn_projection_pending"),
            unlock.index("txn_depth--"),
        )
        checkpoint_at = metadata.index(
            "agent_metadata_txn_checkpoint_unlocked(void)"
        )
        checkpoint_end = metadata.index("\n}\n", checkpoint_at)
        checkpoint = metadata[checkpoint_at:checkpoint_end]
        self.assert_projection_checkpoint_guard(checkpoint)
        transition_at = metadata.index(
            "agent_metadata_txn_projection_transition(int pending)"
        )
        transition_end = metadata.index("\n}\n", transition_at)
        transition = metadata[transition_at:transition_end]
        self.assertIn("agent_metadata_txn_owned(0)", transition)
        self.assertIn("pending == txn_projection_pending", transition)
        require_idle_at = metadata.index(
            "agent_metadata_txn_projection_require_idle(void)"
        )
        require_idle_end = metadata.index("\n}\n", require_idle_at)
        require_idle = metadata[require_idle_at:require_idle_end]
        self.assert_projection_idle_guard(require_idle)

        store = (root / "os" / "agent_metadata_store.c").read_text(
            encoding="utf-8"
        )
        load_at = store.index("agent_file_load_snapshot(int force")
        load_end = store.index("\n}\n", load_at)
        self.assertNotIn("agent_file_persist_system()", store[load_at:load_end])
        install_at = store.index("agent_metadata_store_install_empty(")
        install_end = store.index("\n}\n", install_at)
        self.assertNotIn(
            "agent_file_persist_system()", store[install_at:install_end]
        )
        for signature, barriers in (
            (
                "agent_meta_persist_start_locked(uint owner)",
                (
                    "if (agent_meta_persist.phase",
                    "agent_meta_store_prepare_banks_locked()",
                ),
            ),
            (
                "agent_meta_persist_step_locked(void)",
                (
                    "if (agent_meta_persist.phase",
                    "if (agent_meta_persist.restart_target)",
                ),
            ),
        ):
            with self.subTest(signature=signature):
                start = store.index(signature)
                end = store.index("\n}\n", start)
                body = store[start:end]
                self.assert_persist_projection_guard(body, barriers)
        finish_at = store.index("agent_metadata_store_finish(")
        finish_end = store.index("\n}\n", finish_at)
        finish = store[finish_at:finish_end]
        self.assert_persist_projection_guard(
            finish,
            (
                "if (commit->repair_required",
                "agent_file_persist_system()",
                "agent_metadata_reload_release()",
            ),
        )
        self.assertIn(
            "!!commit->reload_owned != agent_metadata_reload_is_current()",
            finish,
        )
        self.assertLess(
            finish.index("agent_file_persist_system()"),
            finish.index("agent_metadata_reload_release()"),
        )

    def test_projection_protocol_rejects_early_wrapper_return(self):
        body = """
agent_metadata_store_load(&commit);
return -1;
agent_file_store_complete(&commit, result);
"""
        with self.assertRaises(AssertionError):
            self.assert_store_wrapper_projection_protocol(
                body, "agent_metadata_store_load(&commit)"
            )

    def test_metadata_scan_boundary_rejects_static_regressions(self):
        root = SCRIPT.parent.parent
        objects = (root / "os" / "agent_metadata_objects.c").read_text(
            encoding="utf-8"
        )
        scan = (root / "os" / "agent_metadata_scan.c").read_text(
            encoding="utf-8"
        )
        kernel_budgets.validate_metadata_scan_boundary_text(objects, scan)
        early_ack = objects.replace(
            "\tagent_metadata_scan_catalog_sync(delta);\n"
            "\tagent_metadata_txn_projection_ack();",
            "\tagent_metadata_txn_projection_ack();\n"
            "\tagent_metadata_scan_catalog_sync(delta);",
            1,
        )
        reverse_dependency = scan + (
            "\nvoid bad(void) { agent_metadata_note_catalog_changes(0); }\n"
        )
        unbounded_step = scan.replace(
            "steps < SCAN_STEP",
            "steps < AGENT_FILE_META_MAX",
            1,
        )
        inflated_step = scan.replace("#define SCAN_STEP 16", "#define SCAN_STEP 160", 1)
        inflated_interval = scan.replace(
            "#define SCAN_INTERVAL 20", "#define SCAN_INTERVAL 200", 1
        )
        inflated_rest = scan.replace(
            "#define SCAN_REST_MULTIPLIER 4",
            "#define SCAN_REST_MULTIPLIER 40",
            1,
        )
        no_tick_budget = scan.replace("scan.last_step_tick != now", "1", 1)
        no_request_cooldown = scan.replace(
            "scan_rest_deadline(now, now)", "now", 1
        )
        cases = (
            ("projection ACK before scan sync", early_ack, scan),
            ("scan reverse dependency", objects, reverse_dependency),
            ("unbounded directory step", objects, unbounded_step),
            ("inflated directory budget", objects, inflated_step),
            ("inflated scan interval", objects, inflated_interval),
            ("inflated cooldown multiplier", objects, inflated_rest),
            ("missing per-tick budget", objects, no_tick_budget),
            ("missing request cooldown", objects, no_request_cooldown),
        )
        for name, bad_objects, bad_scan in cases:
            with self.subTest(name=name):
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_metadata_scan_boundary_text(
                        bad_objects, bad_scan
                    )

    def test_metadata_directory_boundary_rejects_static_regressions(self):
        root = SCRIPT.parent.parent
        objects = (root / "os" / "agent_metadata_objects.c").read_text(
            encoding="utf-8"
        )
        directory = (root / "os" / "agent_metadata_directory.c").read_text(
            encoding="utf-8"
        )
        kernel_budgets.validate_metadata_directory_boundary_text(objects, directory)
        retained_hook = objects + (
            "\nvoid agent_fs_note_create(struct inode *ip, char *path) {}\n"
        )
        reverse_dependency = objects + '#include "agent_metadata_directory.h"\n'
        blocking_gate = directory.replace(
            "agent_metadata_txn_try_external()", "agent_metadata_txn_lock(1)", 1
        )
        synchronous_persist = directory.replace(
            "agent_metadata_store_mark_dirty(scope_id)",
            "agent_metadata_store_persist()",
            1,
        )
        direct_generation = directory.replace(
            "agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL)",
            "agent_dependency_generation++",
            1,
        )
        writable_state = "static int directory_state;\n" + directory
        indirect_callback = directory + (
            "\nvoid bad(void) { void (*callback)(void); callback(); }\n"
        )
        missing_scan_note = directory.replace(
            "\tagent_metadata_scan_note_slot(slot);\n", "", 1
        )
        early_unlock = directory.replace(
            "\tagent_metadata_scan_note_slot(slot);",
            "\tagent_metadata_txn_unlock();\n"
            "\tagent_metadata_scan_note_slot(slot);",
            1,
        )
        cases = (
            ("objects retained hook", retained_hook, directory),
            ("objects reverse dependency", reverse_dependency, directory),
            ("blocking VFS gate", objects, blocking_gate),
            ("synchronous persistence", objects, synchronous_persist),
            ("direct generation state", objects, direct_generation),
            ("writable directory state", objects, writable_state),
            ("indirect callback", objects, indirect_callback),
            ("missing scan note", objects, missing_scan_note),
            ("early transaction unlock", objects, early_unlock),
        )
        for name, bad_objects, bad_directory in cases:
            with self.subTest(name=name):
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_metadata_directory_boundary_text(
                        bad_objects, bad_directory
                    )

        identity_fields = (
            "view.scope_id != scope_id",
            "view.meta->dev != ip->dev",
            "view.meta->inum != ip->inum",
            "view.meta->incarnation != ip->vfs_incarnation",
        )
        for field in identity_fields:
            self.assertEqual(directory.count(field), 2)
            missing_update = directory.replace(field, "0", 1)
            missing_delete = "0".join(directory.rsplit(field, 1))
            for owner, weakened in (
                ("update", missing_update),
                ("delete", missing_delete),
            ):
                with self.subTest(owner=owner, missing=field):
                    with self.assertRaises(kernel_budgets.BudgetError):
                        kernel_budgets.validate_metadata_directory_boundary_text(
                            objects, weakened
                        )

    def test_metadata_directory_store_symbol_whitelist(self):
        allowed = set(
            kernel_budgets.REQUIRED_METADATA_DIRECTORY_STORE_SYMBOLS
        )
        kernel_budgets.validate_metadata_directory_store_symbols(allowed)
        forbidden = (
            "agent_metadata_store_persist_system",
            "agent_metadata_store_submit_wait_locked",
            "agent_metadata_store_reload_wait_locked",
        )
        for symbol in forbidden:
            with self.subTest(symbol=symbol):
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_metadata_directory_store_symbols(
                        allowed | {symbol}
                    )

    def test_metadata_directory_callgraph_rejects_indirect_forms(self):
        direct = (
            'edge: { sourcename: "hook" '
            'targetname: "agent_metadata_store_mark_dirty" }'
        )
        kernel_budgets.validate_metadata_directory_callgraph(direct)
        forms = (
            ("typedef callback", "agent_callback_t callback; callback();"),
            ("ops member callback", "ops->callback();"),
        )
        for name, source in forms:
            callgraph = (
                'node: { title: "__indirect_call" '
                'label: "Indirect Call Placeholder" }\n'
                'edge: { sourcename: "hook" '
                f'targetname: "__indirect_call" label: "{source}" }}'
            )
            with self.subTest(name=name):
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_metadata_directory_callgraph(callgraph)

    def test_projection_protocol_guards_indirect_checkpoint_helpers(self):
        body = """
if (txn_projection_pending)
    panic("pending");
evil_repair_helper();
agent_metadata_txn_unlock();
"""
        self.assert_projection_checkpoint_guard(body)
        with self.assertRaises(AssertionError):
            self.assert_projection_checkpoint_guard(
                body.replace("txn_projection_pending", "0")
            )

    def test_projection_protocol_rejects_weakened_idle_guard(self):
        body = """
if (!agent_metadata_txn_owned(0) || txn_projection_pending)
    panic("pending");
"""
        self.assert_projection_idle_guard(body)
        with self.assertRaises(AssertionError):
            self.assert_projection_idle_guard(
                body.replace(" || txn_projection_pending", "")
            )

    def test_projection_protocol_rejects_late_persist_guard(self):
        body = """
if (agent_meta_persist.phase == AGENT_META_PERSIST_IDLE)
    agent_meta_store_prepare_banks_locked();
agent_metadata_txn_projection_require_idle();
"""
        with self.assertRaises(AssertionError):
            self.assert_persist_projection_guard(
                body,
                (
                    "if (agent_meta_persist.phase",
                    "agent_meta_store_prepare_banks_locked()",
                ),
            )
        with self.assertRaises(AssertionError):
            self.assert_persist_projection_guard(
                body.replace(
                    "agent_metadata_txn_projection_require_idle();", ""
                ),
                (
                    "if (agent_meta_persist.phase",
                    "agent_meta_store_prepare_banks_locked()",
                ),
            )

    def test_limit_accepts_boundary_and_rejects_growth(self):
        output = io.StringIO()
        with redirect_stdout(output):
            kernel_budgets.check_limit("metric", 110, 100, 110, " bytes")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_limit("metric", 111, 100, 110, " bytes")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_limit(
                    "metric", float("nan"), 100, 110, " bytes"
                )
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_limit(
                    "metric", 97, 100, 105, " bytes", ratchet=True
                )

    def test_nm_symbol_size_is_read_as_hex(self):
        output = (
            "0000000000000000 0000000000000340 B kernel_budget_struct_proc\n"
        )
        self.assertEqual(
            kernel_budgets.parse_nm_symbol_size(
                output, "kernel_budget_struct_proc"
            ),
            0x340,
        )

    def test_stack_virtual_capacity_symbol_size_is_read_as_hex(self):
        output = (
            "0000000000000340 0000000002000000 B "
            "kernel_budget_kernel_stack_virtual_capacity\n"
        )
        self.assertEqual(
            kernel_budgets.parse_nm_symbol_size(
                output, "kernel_budget_kernel_stack_virtual_capacity"
            ),
            32 * 1024 * 1024,
        )

    def test_stack_reserved_physical_pool_size_is_read_as_hex(self):
        output = (
            "0000000002000340 0000000000800000 B "
            "kernel_budget_kernel_stack_reserved_physical_pool\n"
        )
        self.assertEqual(
            kernel_budgets.parse_nm_symbol_size(
                output,
                "kernel_budget_kernel_stack_reserved_physical_pool",
            ),
            8 * 1024 * 1024,
        )

    def test_boot_stack_symbol_span_addresses_are_read_as_hex(self):
        output = (
            "0000000080240000 B boot_stack\n"
            "0000000080250000 B boot_stack_top\n"
        )
        start = kernel_budgets.parse_nm_symbol_address(output, "boot_stack")
        end = kernel_budgets.parse_nm_symbol_address(
            output, "boot_stack_top"
        )
        self.assertEqual(end - start, 64 * 1024)

    def test_boot_entry_target_is_bound_to_the_measured_root(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = Path(temp) / "entry.S"
            entry.write_text(
                "_entry:\n    la sp, boot_stack_top\n    call boot_main\n",
                encoding="utf-8",
            )
            self.assertEqual(
                kernel_budgets.measure_boot_entry_target(entry),
                "boot_main",
            )
            entry.write_text(
                "_entry:\n    call first\n    call second\n",
                encoding="utf-8",
            )
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.measure_boot_entry_target(entry)

    def test_forbidden_symbol_prefixes_allow_only_explicit_facades(self):
        output = "\n".join(
            (
                "00000000 00000008 b agent_meta_store",
                "00000008 00000010 t agent_event_emit.constprop.0",
                "00000018 00000010 T sys_agent_event_read",
                "00000028 00000010 T agent_event_facade",
            )
        )
        symbols = kernel_budgets.parse_nm_defined_symbols(output)
        self.assertEqual(
            kernel_budgets.forbidden_symbols(
                symbols,
                ["agent_meta_", "agent_event_"],
                ["agent_event_facade"],
            ),
            ["agent_event_emit.constprop.0", "agent_meta_store"],
        )

    def test_object_symbol_ownership_and_undefined_parser(self):
        defined = (
            "00000000 00000010 T agent_context_init\n"
            "00000010 00000010 T agent_map_context\n"
        )
        undefined = "                 U agent_observe_record_context\n"
        symbols = kernel_budgets.parse_nm_defined_symbols(defined)
        self.assertTrue(
            all(
                kernel_budgets.symbol_allowed(
                    symbol,
                    ["agent_context_"],
                    ["agent_map_context"],
                )
                for symbol in symbols
            )
        )
        self.assertEqual(
            kernel_budgets.parse_nm_undefined_symbols(undefined),
            {"agent_observe_record_context"},
        )
        writable = (
            "00000020 00000008 B agent_context_shared_state\n"
        )
        records = kernel_budgets.parse_nm_defined_records(defined + writable)
        self.assertEqual(
            kernel_budgets.invalid_global_object_exports(records, []),
            ["agent_context_shared_state (B)"],
        )

    def test_module_export_types_reject_authority_state(self):
        for kind in ("B", "C", "D", "G", "S", "V"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    kernel_budgets.invalid_global_object_exports(
                        {"authority_state": kind}, []
                    ),
                    [f"authority_state ({kind})"],
                )
        self.assertEqual(
            kernel_budgets.invalid_global_object_exports(
                {"operation": "T", "weak_operation": "W"}, []
            ),
            [],
        )
        self.assertEqual(
            kernel_budgets.invalid_global_object_exports(
                {"policy_table": "R"}, ["policy_table"]
            ),
            [],
        )
        self.assertEqual(
            kernel_budgets.invalid_global_object_exports(
                {"policy_table": "R"}, []
            ),
            ["policy_table (R)"],
        )

    def test_module_graph_requires_exact_edges_and_reviewed_sccs(self):
        expected = {
            "core": {"context"},
            "context": {"observe"},
            "observe": {"context"},
            "leaf": set(),
        }
        allowed = [frozenset(("context", "observe"))]
        kernel_budgets.validate_module_dependency_graph(
            expected, expected, allowed, 2, "test graph"
        )

        missing_edge = copy.deepcopy(expected)
        missing_edge["core"] = set()
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                missing_edge, expected, allowed, 2, "test graph"
            )

        stale_policy = copy.deepcopy(expected)
        stale_policy["leaf"] = {"core"}
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                expected, stale_policy, allowed, 2, "test graph"
            )

        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                expected, expected, [], 2, "test graph"
            )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                expected, expected, allowed, 1, "test graph"
            )

    def test_controlled_agent_namespace_is_explicit(self):
        for symbol in (
            "agentinit",
            "agent_context_append",
            "sys_agent_call",
            "sys_context_push",
            "resource_reserve_many",
            "workflow_lifecycle_create",
        ):
            with self.subTest(symbol=symbol):
                self.assertTrue(
                    kernel_budgets.is_controlled_agent_symbol(symbol)
                )
        for symbol in ("sys_open", "ordinary_agent_helper"):
            with self.subTest(symbol=symbol):
                self.assertFalse(
                    kernel_budgets.is_controlled_agent_symbol(symbol)
                )

    def test_integration_bridge_exports_are_exact_and_code_only(self):
        allowed = ["agent_create_proc", "agent_worker_create_proc"]
        records = {
            "agent_create_proc": "T",
            "agent_worker_create_proc": "T",
            "ordinary_proc_helper": "D",
        }
        self.assertEqual(
            kernel_budgets.validate_integration_bridge_exports(
                "proc", records, allowed
            ),
            set(allowed),
        )
        missing = dict(records)
        missing.pop("agent_worker_create_proc")
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_integration_bridge_exports(
                "proc", missing, allowed
            )
        unexpected = dict(records)
        unexpected["agent_unreviewed_bridge"] = "T"
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_integration_bridge_exports(
                "proc", unexpected, allowed
            )
        writable = dict(records)
        writable["agent_create_proc"] = "D"
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_integration_bridge_exports(
                "proc", writable, allowed
            )

    def test_integration_bridge_inventory_is_fail_closed(self):
        expected = {"build/os/proc.o", "build/os/syscall.o"}
        kernel_budgets.validate_integration_bridge_inventory(
            expected, expected
        )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_integration_bridge_inventory(
                expected | {"build/os/unregistered.o"}, expected
            )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_integration_bridge_inventory(
                {"build/os/proc.o"}, expected
            )

    def test_controlled_integration_graph_counts_proc_bridge(self):
        defined = {
            "core": {"agent_core_run"},
            "proc": {"agent_create_proc"},
            "resource_controller": {"resource_reserve_many"},
        }
        undefined = {
            "core": {"agent_create_proc"},
            "proc": {"agent_core_run", "resource_reserve_many"},
            "resource_controller": set(),
        }
        self.assertEqual(
            kernel_budgets.build_controlled_dependency_graph(
                defined, undefined
            ),
            {
                "core": {"proc"},
                "proc": {"core", "resource_controller"},
                "resource_controller": set(),
            },
        )
        owner_missing = copy.deepcopy(undefined)
        owner_missing["proc"].add("agent_missing_owner")
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.build_controlled_dependency_graph(
                defined, owner_missing
            )
        duplicate = copy.deepcopy(defined)
        duplicate["resource_controller"].add("agent_core_run")
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.build_controlled_dependency_graph(
                duplicate, undefined
            )

    def test_integration_graph_rejects_edge_and_scc_drift(self):
        expected = {
            "core": {"proc"},
            "facade": {"core"},
            "proc": {"facade"},
            "leaf": set(),
        }
        allowed = [frozenset(("core", "facade", "proc"))]
        kernel_budgets.validate_module_dependency_graph(
            expected, expected, allowed, 3, "integration graph"
        )
        missing = copy.deepcopy(expected)
        missing["core"] = set()
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                missing, expected, allowed, 3, "integration graph"
            )
        extra = copy.deepcopy(expected)
        extra["leaf"] = {"core"}
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                extra, expected, allowed, 3, "integration graph"
            )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                expected, expected, [], 3, "integration graph"
            )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_module_dependency_graph(
                expected, expected, allowed, 2, "integration graph"
            )

    def test_file_line_measurement_rejects_repository_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core.c").write_bytes(b"one\n\ntwo")
            self.assertEqual(
                kernel_budgets.measure_file_lines(root, "core.c"), 3
            )
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.measure_file_lines(root, "../outside.c")

    def test_runtime_size_includes_bss(self):
        output = (
            "   text    data     bss     dec     hex filename\n"
            " 219616    5220 18715408 18940244 1210154 build/kernel\n"
        )
        self.assertEqual(
            kernel_budgets.parse_size_output(output),
            (219616, 5220, 18715408, 18940244),
        )

    def test_stack_command_carries_machine_budget(self):
        config = {
            "kernel_stack": {
                "stack_size_bytes": 16384,
                "guard_size_bytes": 4096,
                "safety_margin_bytes": 4096,
                "interrupt_entry_bytes": 256,
                "baseline_required_bytes": 14000,
                "max_required_bytes": 15188,
                "boot_root": "main",
                "boot_stack_size_bytes": 65536,
                "baseline_boot_required_bytes": 10000,
                "max_boot_required_bytes": 10500,
                "stack_boundaries": ["swtch"],
                "allowed_indirect_callers": ["usertrapret"],
                "recursion_bounds": ["printf=2"],
            }
        }
        command = kernel_budgets.stack_check_command(
            Path("/repo"), config, Path("build/os"), Path("scripts/stack.py")
        )
        limit_index = command.index("--required-limit")
        self.assertEqual(command[limit_index + 1], "15188")
        baseline_index = command.index("--required-baseline")
        self.assertEqual(command[baseline_index + 1], "14000")
        boot_limit_index = command.index("--boot-required-limit")
        self.assertEqual(command[boot_limit_index + 1], "10500")
        boot_baseline_index = command.index("--boot-required-baseline")
        self.assertEqual(command[boot_baseline_index + 1], "10000")
        self.assertIn("main", command)
        self.assertIn("printf=2", command)

    def test_stack_build_config_must_match_machine_budget(self):
        stack = {
            "stack_size_bytes": 16384,
            "boot_stack_size_bytes": 65536,
            "boot_root": "main",
            "guard_size_bytes": 4096,
            "safety_margin_bytes": 4096,
            "interrupt_entry_bytes": 256,
            "stack_boundaries": ["swtch"],
            "allowed_indirect_callers": ["usertrapret"],
            "recursion_bounds": ["printf=2", "freewalk=3"],
        }
        text = "\n".join(
            (
                "KSTACK_SIZE_BYTES=16384",
                "KSTACK_BOOT_SIZE_BYTES=65536",
                "KSTACK_BOOT_ROOT=main",
                "KSTACK_GUARD_SIZE_BYTES=4096",
                "KSTACK_SAFETY_MARGIN=4096",
                "KERNELVEC_FRAME_SIZE_BYTES=256",
                "KSTACK_STACK_BOUNDARIES=swtch",
                "KSTACK_INDIRECT_CALLERS=usertrapret",
                "KSTACK_RECURSION_BOUNDS=printf=2 freewalk=3",
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config"
            path.write_text(text, encoding="utf-8")
            kernel_budgets.validate_stack_build_config(path, stack)
            path.write_text(
                text.replace("KSTACK_SIZE_BYTES=16384", "KSTACK_SIZE_BYTES=32768"),
                encoding="utf-8",
            )
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.validate_stack_build_config(path, stack)

    def test_boot_stack_path_has_an_independent_capacity_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            callgraph = root / "callgraph"
            source.mkdir()
            callgraph.mkdir()
            (source / "root.c").write_text("", encoding="utf-8")
            (callgraph / "root.ci").write_text(
                "\n".join(
                    (
                        'graph: { title: "root.c"',
                        'node: { title: "main" label: "main\\nroot.c:1:1'
                        '\\n32 bytes (static)" }',
                        'node: { title: "usertrap" label: "usertrap'
                        '\\nroot.c:2:1\\n32 bytes (static)" }',
                        'node: { title: "kerneltrap" label: "kerneltrap'
                        '\\nroot.c:3:1\\n32 bytes (static)" }',
                        'node: { title: "leaf" label: "leaf\\nroot.c:4:1'
                        '\\n16 bytes (static)" }',
                        'edge: { sourcename: "main" targetname: "leaf" }',
                        'edge: { sourcename: "usertrap" '
                        'targetname: "leaf" }',
                        'edge: { sourcename: "kerneltrap" '
                        'targetname: "leaf" }',
                    )
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                (
                    sys.executable,
                    str(STACK_SCRIPT),
                    "--callgraph-dir",
                    str(callgraph),
                    "--source-dir",
                    str(source),
                    "--stack-size",
                    "1024",
                    "--guard-size",
                    "512",
                    "--safety-margin",
                    "0",
                    "--interrupt-entry",
                    "256",
                    "--boot-root",
                    "main",
                    "--boot-stack-size",
                    "300",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("boot stack check failed", result.stderr)

    def test_canonical_profile_rejects_resource_test_macros(self):
        config = {
            "kernel_stack": {
                "stack_size_bytes": 16384,
                "guard_size_bytes": 4096,
                "interrupt_entry_bytes": 256,
            }
        }
        base = (
            "-D LOG_LEVEL_WARN -DKSTACK_SIZE=16384 "
            "-DKSTACK_GUARD_SIZE=4096 -DKERNELVEC_FRAME_SIZE=256"
        )
        kernel_budgets.validate_canonical_defines(
            base, config, "LOG_LEVEL_WARN"
        )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_canonical_defines(
                base + " -DFS_STORAGE_TINY_TEST_PROFILE=1",
                config,
                "LOG_LEVEL_WARN",
            )

    def test_repository_config_is_valid_json_and_schema(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        kernel_budgets.validate_config(config)
        tests = config["agent_test_suite"]
        self.assertEqual(len(tests["calibration_samples"]), 3)
        self.assertEqual(tests["runner_tag"], "agentos-qemu-calibrated")
        self.assertEqual(
            tuple(tests["expected_cases"]),
            kernel_budgets.REQUIRED_AGENT_TEST_CASES,
        )
        modules = config["agent_modules"]
        self.assertEqual(
            config["agent_state_pages"]["baseline_per_process_bytes"],
            21 * 4096,
        )
        missing_agent_state = copy.deepcopy(config)
        del missing_agent_state["agent_state_pages"]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(missing_agent_state)
        self.assertEqual(
            {bridge["name"] for bridge in modules["integration_bridges"]},
            {
                "bio",
                "file",
                "fs",
                "loader",
                "main",
                "proc",
                "syscall",
                "trap",
                "vfs_security",
            },
        )
        core = next(
            entry
            for entry in modules["modules"]
            if entry["name"] == "core"
        )
        self.assertEqual(core["allowed_bridge_dependencies"], ["proc"])
        query = next(
            entry for entry in modules["modules"]
            if entry["name"] == "metadata_query"
        )
        self.assertEqual(
            query["allowed_dependencies"],
            ["file_state", "metadata", "metadata_catalog"],
        )
        scan = next(
            entry for entry in modules["modules"]
            if entry["name"] == "metadata_scan"
        )
        self.assertEqual(
            scan["allowed_dependencies"],
            ["file_state", "metadata", "metadata_catalog", "metadata_store"],
        )
        self.assertEqual(scan["allowed_global_symbols"], ["agent_file_request_scan"])
        directory = next(
            entry for entry in modules["modules"]
            if entry["name"] == "metadata_directory"
        )
        self.assertEqual(directory["max_bss_bytes"], 0)
        self.assertEqual(
            directory["allowed_dependencies"],
            [
                "file_state",
                "metadata",
                "metadata_catalog",
                "metadata_objects",
                "metadata_scan",
                "metadata_store",
            ],
        )
        self.assertEqual(
            directory["allowed_global_symbols"],
            [
                "agent_fs_note_create",
                "agent_fs_note_delete",
                "agent_fs_note_truncate",
                "agent_fs_note_write",
                "agent_fs_sync_write",
            ],
        )
        objects = next(
            entry for entry in modules["modules"]
            if entry["name"] == "metadata_objects"
        )
        self.assertIn("metadata_query", objects["allowed_dependencies"])
        self.assertIn("metadata_scan", objects["allowed_dependencies"])
        self.assertNotIn("metadata_directory", objects["allowed_dependencies"])
        file_bridge = next(
            bridge for bridge in modules["integration_bridges"]
            if bridge["name"] == "file"
        )
        self.assertIn("metadata_directory", file_bridge["allowed_dependencies"])
        self.assertNotIn("metadata_objects", file_bridge["allowed_dependencies"])
        writable_directory = copy.deepcopy(config)
        next(
            entry for entry in writable_directory["agent_modules"]["modules"]
            if entry["name"] == "metadata_directory"
        )["max_bss_bytes"] = 1
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "zero-BSS ownership boundary"
        ):
            kernel_budgets.validate_config(writable_directory)
        duplicate_bridge_path = copy.deepcopy(config)
        duplicate_bridge_path["agent_modules"]["integration_bridges"][1][
            "object_path"
        ] = duplicate_bridge_path["agent_modules"]["integration_bridges"][0][
            "object_path"
        ]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(duplicate_bridge_path)
        broken_integration_cycle = copy.deepcopy(config)
        broken_core = next(
            entry
            for entry in broken_integration_cycle["agent_modules"]["modules"]
            if entry["name"] == "core"
        )
        broken_core["allowed_bridge_dependencies"] = []
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(broken_integration_cycle)
        bad_bridge_export = copy.deepcopy(config)
        bad_bridge_export["agent_modules"]["integration_bridges"][0][
            "allowed_global_symbols"
        ] = ["ordinary_kernel_symbol"]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(bad_bridge_export)
        relaxed_scc = copy.deepcopy(config)
        relaxed_scc["agent_modules"]["max_scc_size"] = 4
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(relaxed_scc)
        escaped_namespace = copy.deepcopy(config)
        escaped_core = next(
            entry
            for entry in escaped_namespace["agent_modules"]["modules"]
            if entry["name"] == "core"
        )
        escaped_core["allowed_global_prefixes"].append("sys_hidden_")
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(escaped_namespace)
        neutral_exact_escape = copy.deepcopy(config)
        escaped_core = next(
            entry
            for entry in neutral_exact_escape["agent_modules"]["modules"]
            if entry["name"] == "core"
        )
        escaped_core["allowed_global_symbols"].append(
            "kernel_agent_bridge"
        )
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(neutral_exact_escape)
        duplicate_exact_owner = copy.deepcopy(config)
        duplicate_identity = next(
            entry
            for entry in duplicate_exact_owner["agent_modules"]["modules"]
            if entry["name"] == "identity"
        )
        duplicate_identity["allowed_global_symbols"].append("agent_make")
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "duplicate Agent module exact export owner",
        ):
            kernel_budgets.validate_config(duplicate_exact_owner)
        exact_under_other_prefix = copy.deepcopy(config)
        ambiguous_facade = next(
            entry
            for entry in exact_under_other_prefix["agent_modules"]["modules"]
            if entry["name"] == "facade"
        )
        ambiguous_facade["allowed_global_symbols"].append(
            "agent_identity_unreviewed"
        )
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "ambiguous Agent module export owner",
        ):
            kernel_budgets.validate_config(exact_under_other_prefix)
        overlapping_prefix = copy.deepcopy(config)
        overlapping_identity = next(
            entry
            for entry in overlapping_prefix["agent_modules"]["modules"]
            if entry["name"] == "identity"
        )
        overlapping_identity["allowed_global_prefixes"].append(
            "agent_core_private_"
        )
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "overlapping Agent module export prefixes",
        ):
            kernel_budgets.validate_config(overlapping_prefix)
        missing_aggregate_member = copy.deepcopy(config)
        missing_aggregate_member["agent_modules"]["aggregate_budgets"][0][
            "members"
        ].remove("metadata_catalog")
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "member inventory drift"
        ):
            kernel_budgets.validate_config(missing_aggregate_member)
        duplicate_aggregate_member = copy.deepcopy(config)
        duplicate_aggregate_member["agent_modules"]["aggregate_budgets"][0][
            "members"
        ].append("metadata_catalog")
        with self.assertRaisesRegex(kernel_budgets.BudgetError, "contains duplicates"):
            kernel_budgets.validate_config(duplicate_aggregate_member)
        missing_contract_header = copy.deepcopy(config)
        missing_contract_header["agent_modules"]["aggregate_budgets"][0][
            "contract_headers"
        ].pop()
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "contract header inventory drift"
        ):
            kernel_budgets.validate_config(missing_contract_header)
        duplicate_contract_header = copy.deepcopy(config)
        duplicate_contract_header["agent_modules"]["aggregate_budgets"][0][
            "contract_headers"
        ].append("os/agent_metadata_internal.h")
        with self.assertRaisesRegex(kernel_budgets.BudgetError, "contains duplicates"):
            kernel_budgets.validate_config(duplicate_contract_header)
        missing_header_glob = copy.deepcopy(config)
        missing_header_glob["agent_modules"]["aggregate_budgets"][0][
            "contract_header_globs"
        ].pop()
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "contract_header_globs inventory drift"
        ):
            kernel_budgets.validate_config(missing_header_glob)
        missing_discard = copy.deepcopy(config)
        missing_discard["agent_modules"]["aggregate_budgets"][0][
            "discarded_sections"
        ].clear()
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(missing_discard)
        unexplained_source_allowance = copy.deepcopy(config)
        unexplained_source_allowance["agent_modules"]["aggregate_budgets"][0][
            "source_budget_policy"
        ] = "unreviewed"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "must explain the 5% source allowance"
        ):
            kernel_budgets.validate_config(unexplained_source_allowance)
        excess_source_bytes = copy.deepcopy(config)
        source_group = excess_source_bytes["agent_modules"]["aggregate_budgets"][0]
        source_group["max_source_bytes"] = int(
            source_group["baseline_source_bytes"] * 1.05
        ) + 1
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "max_source_bytes exceeds"
        ):
            kernel_budgets.validate_config(excess_source_bytes)
        for case_change in ("remove", "append"):
            with self.subTest(case_change=case_change):
                incomplete = copy.deepcopy(config)
                if case_change == "remove":
                    incomplete["agent_test_suite"]["expected_cases"].pop()
                else:
                    incomplete["agent_test_suite"]["expected_cases"].append(
                        "unreviewed_ucore"
                    )
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_config(incomplete)
        for required_glob in ("os/**/*.inc", "*_abi.h", "*_policy.inc"):
            with self.subTest(required_glob=required_glob):
                incomplete = copy.deepcopy(config)
                incomplete["kernel_source"]["include_globs"].remove(
                    required_glob
                )
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_config(incomplete)

    def test_calibrated_duration_summary_is_fail_closed(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)

        too_few = copy.deepcopy(config)
        too_few["agent_test_suite"]["calibration_samples"].pop()
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "requires at least three samples",
        ):
            kernel_budgets.validate_config(too_few)

        non_finite = copy.deepcopy(config)
        non_finite["agent_test_suite"]["calibration_samples"][0][
            "total_seconds"
        ] = float("nan")
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(non_finite)

        duplicate_id = copy.deepcopy(config)
        duplicate_id["agent_test_suite"]["calibration_samples"][1][
            "sample_id"
        ] = duplicate_id["agent_test_suite"]["calibration_samples"][0][
            "sample_id"
        ]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(duplicate_id)

        wrong_median = copy.deepcopy(config)
        totals = [
            sample["total_seconds"]
            for sample in wrong_median["agent_test_suite"][
                "calibration_samples"
            ]
        ]
        median = statistics.median(totals)
        wrong_median["agent_test_suite"]["baseline_seconds"] = median * 1.005
        wrong_median["agent_test_suite"]["max_seconds"] = median * 1.08
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "baseline must match the calibration median",
        ):
            kernel_budgets.validate_config(wrong_median)

        uncovered_sample = copy.deepcopy(config)
        uncovered_sample["agent_test_suite"]["calibration_samples"][0][
            "total_seconds"
        ] = uncovered_sample["agent_test_suite"]["max_seconds"] + 1.0
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "limit does not cover every calibration sample",
        ):
            kernel_budgets.validate_config(uncovered_sample)

        excessive_headroom = copy.deepcopy(config)
        excessive_headroom["agent_test_suite"]["max_seconds"] = (
            excessive_headroom["agent_test_suite"]["baseline_seconds"]
            * 1.1000005
        )
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "limit exceeds 110% of the calibration median",
        ):
            kernel_budgets.validate_config(excessive_headroom)

    def test_agent_module_inventory_and_object_paths_are_fail_closed(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        missing = copy.deepcopy(config)
        missing["agent_modules"]["modules"] = [
            entry
            for entry in missing["agent_modules"]["modules"]
            if entry["name"] != "context"
        ]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(missing)

        wrong_object = copy.deepcopy(config)
        context = next(
            entry
            for entry in wrong_object["agent_modules"]["modules"]
            if entry["name"] == "context"
        )
        context["object_path"] = "build/os/agent_core.o"
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(wrong_object)

    def test_lifecycle_abi_modules_have_ratchet_baselines(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        lifecycle = next(
            entry
            for entry in config["agent_modules"]["modules"]
            if entry["name"] == "lifecycle"
        )
        source = SCRIPT.parent.parent / lifecycle["source_path"]
        self.assertEqual(lifecycle["baseline_lines"], 279)
        self.assertEqual(lifecycle["max_lines"], 293)
        self.assertEqual(
            lifecycle["allowed_dependencies"],
            ["metadata", "workflow_lifecycle"],
        )
        self.assertEqual(len(source.read_text(encoding="utf-8").splitlines()), 279)
        metadata = next(
            entry
            for entry in config["agent_modules"]["modules"]
            if entry["name"] == "metadata"
        )
        source = SCRIPT.parent.parent / metadata["source_path"]
        self.assertEqual(metadata["baseline_lines"], 322)
        self.assertEqual(metadata["max_lines"], 339)
        self.assertEqual(len(source.read_text(encoding="utf-8").splitlines()), 322)
        syscall = next(
            bridge
            for bridge in config["agent_modules"]["integration_bridges"]
            if bridge["name"] == "syscall"
        )
        self.assertIn("lifecycle", syscall["allowed_dependencies"])

    def test_agent_aggregate_rejects_unregistered_private_header(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "os").mkdir()
            (root / "os" / "agent_metadata.c").write_text(
                '#include "agent_metadata_query_internal.h"\n', encoding="utf-8"
            )
            (root / "os" / "agent_metadata_query_internal.h").write_text(
                "int private_query(void);\n", encoding="utf-8"
            )
            entries = [
                {"name": "metadata", "source_path": "os/agent_metadata.c"}
            ]
            group = {
                "name": "metadata_control_plane",
                "members": ["metadata"],
                "contract_headers": [],
                "contract_header_globs": ["os/agent_metadata*.h"],
            }
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "private header inventory drift"
            ):
                kernel_budgets.validate_agent_aggregate_header_inventory(
                    root, entries, group
                )

    def test_agent_aggregate_include_closure_rejects_unplanned_header_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "os").mkdir()
            (root / "os" / "agent_metadata.c").write_text(
                '#include "agent_catalog_extra.h"\n', encoding="utf-8"
            )
            (root / "os" / "agent_catalog_extra.h").write_text(
                "int catalog_extra(void);\n", encoding="utf-8"
            )
            entries = [
                {"name": "metadata", "source_path": "os/agent_metadata.c"}
            ]
            group = {
                "name": "metadata_control_plane",
                "members": ["metadata"],
                "contract_headers": [],
                "contract_header_globs": ["os/agent_metadata*.h"],
            }
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "include closure has unregistered"
            ):
                kernel_budgets.validate_agent_aggregate_header_inventory(
                    root, entries, group
                )

    def test_agent_aggregate_residency_counts_unknown_sections(self):
        berkeley = (
            "text data bss dec hex filename\n"
            "117 0 13 130 82 object.o\n"
        )
        sections = (
            ".text 10 0\n.gnu.linkonce.r.future 7 0\n"
            ".eh_frame 100 0\n.bss 7 0\n.future_nobits 6 0\n"
        )

        def fake_size(command, description):
            return berkeley if "-B" in command else sections

        with mock.patch.object(kernel_budgets, "run_tool", side_effect=fake_size):
            self.assertEqual(
                kernel_budgets.measure_agent_object_residency(
                    "fake-size", Path("object.o"), [".eh_frame"]
                ),
                (17, 13),
            )

    def test_agent_aggregate_residency_rejects_initialized_data(self):
        output = "text data bss dec hex filename\n10 1 0 11 b object.o\n"
        with mock.patch.object(kernel_budgets, "run_tool", return_value=output):
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "initialized writable data"
            ):
                kernel_budgets.measure_agent_object_residency(
                    "fake-size", Path("object.o"), [".eh_frame"]
                )

    def test_agent_aggregate_runtime_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entries = []
            for name in ("one", "two", "three"):
                source = root / f"{name}.c"
                obj = root / f"{name}.o"
                source.write_text("line\n", encoding="utf-8")
                obj.write_bytes(b"object")
                entries.append(
                    {
                        "name": name,
                        "source_path": source.name,
                        "object_path": obj.name,
                    }
                )
            group = {
                "name": "test_group",
                "members": ["one", "two", "three"],
                "contract_headers": ["contract.h"],
                "contract_header_globs": ["contract.h"],
                # Source gets 5% for contracts; binary residency gets no growth.
                "baseline_source_lines": 4,
                "max_source_lines": 4,
                "baseline_source_bytes": 24,
                "max_source_bytes": 24,
                "baseline_loaded_text_bytes": 30,
                "max_loaded_text_bytes": 30,
                "baseline_bss_bytes": 30,
                "max_bss_bytes": 30,
                "discarded_sections": [".eh_frame"],
            }
            (root / "contract.h").write_text("contract\n", encoding="utf-8")
            (root / "os").mkdir()
            (root / "os" / "kernel.ld").write_text(
                "SECTIONS { /DISCARD/ : { *(.eh_frame) } }\n", encoding="utf-8"
            )
            cases = (
                ("source_lines", "line\nextra\n", 10, 10),
                ("source_bytes", "longer\n", 10, 10),
                ("loaded_text_bytes", "line\n", 11, 10),
                ("bss_bytes", "line\n", 10, 11),
            )
            for metric, source_text, text_size, bss_size in cases:
                with self.subTest(metric=metric):
                    for entry in entries:
                        (root / entry["source_path"]).write_text(
                            "line\n", encoding="utf-8"
                        )
                    (root / entries[0]["source_path"]).write_text(
                        source_text, encoding="utf-8"
                    )
                    def fake_size(command, description):
                        if "-B" in command:
                            total = text_size + bss_size
                            return (
                                "text data bss dec hex filename\n"
                                f"{text_size} 0 {bss_size} {total} 0 object.o\n"
                            )
                        return ".text 1 0\n"

                    with mock.patch.object(
                        kernel_budgets, "run_tool", side_effect=fake_size
                    ), redirect_stdout(io.StringIO()):
                        with self.assertRaisesRegex(
                            kernel_budgets.BudgetError, f"{metric} exceeded"
                        ):
                            kernel_budgets.check_agent_aggregate_budgets(
                                root, entries, [group], "fake-size"
                            )

    def test_json_loader_rejects_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "budgets.json"
            path.write_text('{"baseline": NaN}', encoding="utf-8")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.load_config(path)

    def test_agent_suite_sums_only_recorded_qemu_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            timings = Path(temp) / "timings"
            timings.write_text("one 100.25\ntwo 200.5\n", encoding="utf-8")
            args = SimpleNamespace(
                agent_test_seconds=None,
                agent_test_start_ns=None,
                agent_test_timing_file=str(timings),
            )
            config = {
                "agent_test_suite": {
                    "expected_cases": ["one", "two"],
                    "baseline_seconds": 300.0,
                    "max_seconds": 330.0,
                    "calibration_status": "calibrated_full_suite",
                }
            }
            output = io.StringIO()
            with redirect_stdout(output):
                kernel_budgets.check_agent_tests(args, config)
            self.assertIn("actual=300.75 seconds", output.getvalue())

    def test_agent_suite_rejects_summary_only_duration_inputs(self):
        config = {
            "agent_test_suite": {
                "expected_cases": ["one"],
                "baseline_seconds": 1.0,
                "max_seconds": 1.1,
                "calibration_status": "calibrated_full_suite",
            }
        }
        for seconds, start_ns in ((1.0, None), (None, 1)):
            with self.subTest(seconds=seconds, start_ns=start_ns):
                args = SimpleNamespace(
                    agent_test_seconds=seconds,
                    agent_test_start_ns=start_ns,
                    agent_test_timing_file=None,
                )
                with self.assertRaisesRegex(
                    kernel_budgets.BudgetError,
                    "complete per-case timing file",
                ):
                    kernel_budgets.check_agent_tests(args, config)

    def test_agent_suite_rejects_zero_case_duration(self):
        config = {
            "agent_test_suite": {
                "expected_cases": ["one"],
                "baseline_seconds": 1.0,
                "max_seconds": 1.1,
                "calibration_status": "calibrated_full_suite",
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            timings = Path(temp) / "timings"
            timings.write_text("one 0\n", encoding="utf-8")
            args = SimpleNamespace(
                agent_test_seconds=None,
                agent_test_start_ns=None,
                agent_test_timing_file=str(timings),
            )
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_agent_tests(args, config)

    def test_agent_suite_rejects_provisional_duration(self):
        config = {
            "agent_test_suite": {
                "expected_cases": ["one"],
                "baseline_seconds": 1.0,
                "max_seconds": 1.1,
                "calibration_status": "provisional_requires_full_suite",
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            timings = Path(temp) / "timings"
            timings.write_text("one 1.0\n", encoding="utf-8")
            args = SimpleNamespace(
                agent_test_seconds=None,
                agent_test_start_ns=None,
                agent_test_timing_file=str(timings),
            )
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    kernel_budgets.BudgetError,
                    "duration is provisional",
                ):
                    kernel_budgets.check_agent_tests(args, config)

    def test_agent_suite_calibration_persists_provisional_measurement(self):
        with tempfile.TemporaryDirectory() as temp:
            timings = Path(temp) / "timings"
            timings.write_text("one 12.5\ntwo 13.5\n", encoding="utf-8")
            args = SimpleNamespace(
                agent_test_seconds=None,
                agent_test_start_ns=None,
                agent_test_timing_file=str(timings),
                agent_test_calibration=True,
            )
            config = {
                "agent_test_suite": {
                    "expected_cases": ["one", "two"],
                    "baseline_seconds": 10.0,
                    "max_seconds": 11.0,
                    "calibration_status": "provisional_requires_full_suite",
                }
            }
            output = io.StringIO()
            with redirect_stdout(output):
                kernel_budgets.check_agent_tests(args, config)
            self.assertIn("actual=26.000 seconds", output.getvalue())
            self.assertEqual(
                timings.read_text(encoding="utf-8"),
                "one 12.5\ntwo 13.5\n",
            )


if __name__ == "__main__":
    unittest.main()
