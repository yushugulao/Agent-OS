#!/usr/bin/env python3
"""Unit tests for the kernel budget checker."""

import importlib.util
import gzip
import hashlib
import io
import json
import copy
import os
import re
import shutil
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
STACK_SPEC = importlib.util.spec_from_file_location("kernel_stack", STACK_SCRIPT)
kernel_stack = importlib.util.module_from_spec(STACK_SPEC)
STACK_SPEC.loader.exec_module(kernel_stack)


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

    def test_local_kernel_budget_profile_is_closed_and_calibration_bound(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        kernel_budgets.validate_config(config)
        self.assertEqual(
            config["canonical_toolchain"]["ldflags"],
            ["-m", "elf64lriscv", "-z", "max-page-size=4096"],
        )
        self.assertEqual(config["kernel_source"]["baseline_lines"], 63037)
        self.assertEqual(config["kernel_source"]["max_lines"], 63037)
        self.assertEqual(config["struct_proc"]["baseline_bytes"], 23464)
        self.assertEqual(config["struct_proc"]["max_bytes"], 24638)

        missing_hash = copy.deepcopy(config)
        del missing_hash["local_kernel_budget_toolchains"][0][
            "executable_sha256"
        ]["ld"]
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "inventory mismatch"
        ):
            kernel_budgets.validate_config(missing_hash)

        duplicate_prefix = copy.deepcopy(config)
        duplicate = copy.deepcopy(
            duplicate_prefix["local_kernel_budget_toolchains"][0]
        )
        duplicate["profile_id"] = "duplicate-local-profile"
        duplicate_prefix["local_kernel_budget_toolchains"].append(duplicate)
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "prefix is not unique"
        ):
            kernel_budgets.validate_config(duplicate_prefix)

        detached_profile = copy.deepcopy(config)
        detached_profile["local_kernel_budget_toolchains"][0][
            "profile_id"
        ] = "detached-local-profile"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "must have one kernel budget"
        ):
            kernel_budgets.validate_config(detached_profile)

        for tool_name in (
            "toolchain_cc",
            "toolchain_ld",
            "toolchain_objcopy",
            "toolchain_objdump",
            "toolchain_as",
        ):
            with self.subTest(calibration_version=tool_name):
                drifted = copy.deepcopy(config)
                drifted["agent_test_suite"]["local_calibration_profile"][
                    "tool_versions"
                ][tool_name] = "0.0"
                with self.assertRaisesRegex(
                    kernel_budgets.BudgetError,
                    "versions differ from the calibration profile",
                ):
                    kernel_budgets.validate_config(drifted)

    def test_cli_resolves_repository_paths_from_explicit_root(self):
        root = SCRIPT.parent.parent
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--check",
                "config",
                "--config",
                "ci/kernel-budgets.json",
                "--root",
                str(root),
            ],
            cwd=root.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configuration is valid", result.stdout)

    def test_tool_diagnostics_tolerate_non_utf8_bytes(self):
        result = subprocess.CompletedProcess(
            args=["fixture"], returncode=1, stdout=b"", stderr=b"bad:\xff"
        )
        with mock.patch.object(
            kernel_budgets.subprocess, "run", return_value=result
        ), self.assertRaisesRegex(kernel_budgets.BudgetError, "bad:"):
            kernel_budgets.run_tool(["fixture"], "fixture diagnostic")

    def test_local_kernel_budget_toolchain_is_hashed_and_build_bound(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool_paths = {}
            for name in (
                "gcc",
                "cc1",
                "as",
                "ld",
                "objcopy",
                "objdump",
                "nm",
                "size",
            ):
                path = root / f"fixture-{name}.exe"
                path.write_bytes(f"fixture {name}\n".encode("ascii"))
                tool_paths[name] = path
            profile = {
                "profile_id": "fixture-local-profile",
                "prefix": "fixture-",
                "gcc_version": "15.2.0",
                "binutils_version": "2.45",
                "executable_sha256": {
                    name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in tool_paths.items()
                },
            }
            config["local_kernel_budget_toolchains"] = [profile]
            build_config = root / "build-config"
            canonical = config["canonical_toolchain"]
            build_config.write_text(
                "\n".join(
                    (
                        f"CC={tool_paths['gcc']}",
                        f"CC1={tool_paths['cc1']}",
                        f"AS_SUBPROGRAM={tool_paths['as']}",
                        f"LD={tool_paths['ld']}",
                        f"OBJDUMP={tool_paths['objdump']}",
                        "CFLAGS=" + " ".join(canonical["cflags"]),
                        "LDFLAGS=" + " ".join(canonical["ldflags"]),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            initproc = root / "initproc.S"
            initproc.write_text(
                '.string "agentfinal_ucore"\n', encoding="utf-8"
            )

            def fake_run(command, _description):
                if "-print-prog-name=cc1" in command:
                    return f"{tool_paths['cc1']}\n"
                if "-print-prog-name=as" in command:
                    return f"{tool_paths['as']}\n"
                if "-dumpfullversion" in command:
                    return "15.2.0\n"
                return "GNU fixture tool 2.45\n"

            argv = tuple(
                str(tool_paths[name])
                for name in ("gcc", "ld", "objcopy", "objdump", "nm", "size")
            )
            output = io.StringIO()
            with mock.patch.object(
                kernel_budgets, "run_tool", side_effect=fake_run
            ), redirect_stdout(output):
                kind, selected, resolved = (
                    kernel_budgets.validate_kernel_budget_toolchain(
                        config, *argv, build_config, initproc
                    )
                )
            self.assertEqual(kind, "local")
            self.assertEqual(selected["profile_id"], "fixture-local-profile")
            self.assertEqual(set(resolved), set(tool_paths))
            self.assertIn("fixture-local-profile", output.getvalue())

            original_build = build_config.read_text(encoding="utf-8")
            for field, tool_name in (("CC", "gcc"), ("CC1", "cc1"),
                                     ("AS_SUBPROGRAM", "as"),
                                     ("LD", "ld"), ("OBJDUMP", "objdump")):
                with self.subTest(build_receipt=field):
                    other_tool = root / f"other-{tool_name}.exe"
                    other_tool.write_bytes(b"other tool\n")
                    build_config.write_text(
                        original_build.replace(
                            f"{field}={tool_paths[tool_name]}",
                            f"{field}={other_tool}",
                            1,
                        ),
                        encoding="utf-8",
                    )
                    with mock.patch.object(
                        kernel_budgets, "run_tool", side_effect=fake_run
                    ), self.assertRaisesRegex(
                        kernel_budgets.BudgetError,
                        f"kernel build {field} differs",
                    ):
                        kernel_budgets.validate_kernel_budget_toolchain(
                            config, *argv, build_config, initproc
                        )
                    build_config.write_text(original_build, encoding="utf-8")

            tool_paths["size"].write_bytes(b"tampered\n")
            with mock.patch.object(
                kernel_budgets, "run_tool", side_effect=fake_run
            ), self.assertRaisesRegex(
                kernel_budgets.BudgetError, "size SHA-256 differs"
            ):
                kernel_budgets.validate_kernel_budget_toolchain(
                    config, *argv, build_config, initproc
                )

    def test_canonical_toolchain_requires_paths_ownership_and_integrity(self):
        with (SCRIPT.parent.parent / "ci" / "kernel-budgets.json").open(
            encoding="utf-8"
        ) as stream:
            profile = json.load(stream)["canonical_toolchain"]
        direct = ("gcc", "ld", "objcopy", "objdump", "nm", "size")
        tools = {
            name: Path(f"/approved/{name}")
            for name in (*direct, "cc1", "as")
        }

        def approved_path(_path, description):
            return tools[description.rsplit(" ", 1)[-1]]

        def approved_owner(_path, description):
            name = description.rsplit(" ", 1)[-1]
            package = (
                profile["gcc_package"]
                if name in ("gcc", "cc1")
                else profile["binutils_package"]
            )
            return {package}

        with mock.patch.object(
            kernel_budgets, "resolve_executable_once", side_effect=approved_path
        ), mock.patch.object(
            kernel_budgets, "dpkg_tool_owner", side_effect=approved_owner
        ), mock.patch.object(
            kernel_budgets, "run_tool", return_value=""
        ):
            kernel_budgets.validate_canonical_kernel_budget_tools(
                profile, tools
            )

            wrapped = dict(tools)
            wrapped["gcc"] = Path("/wrapper/gcc")
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "approved /usr/bin executable"
            ):
                kernel_budgets.validate_canonical_kernel_budget_tools(
                    profile, wrapped
                )

        def wrong_cc1_owner(path, description):
            if description.endswith("cc1"):
                return {"unapproved-package"}
            return approved_owner(path, description)

        with mock.patch.object(
            kernel_budgets, "resolve_executable_once", side_effect=approved_path
        ), mock.patch.object(
            kernel_budgets, "dpkg_tool_owner", side_effect=wrong_cc1_owner
        ), mock.patch.object(
            kernel_budgets, "run_tool", return_value=""
        ), self.assertRaisesRegex(kernel_budgets.BudgetError, "cc1 is owned"):
            kernel_budgets.validate_canonical_kernel_budget_tools(profile, tools)

        with mock.patch.object(
            kernel_budgets, "resolve_executable_once", side_effect=approved_path
        ), mock.patch.object(
            kernel_budgets, "dpkg_tool_owner", side_effect=approved_owner
        ), mock.patch.object(
            kernel_budgets, "run_tool", return_value="package drift\n"
        ), self.assertRaisesRegex(
            kernel_budgets.BudgetError, "integrity verification reported drift"
        ):
            kernel_budgets.validate_canonical_kernel_budget_tools(profile, tools)

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
            scan_sync.index("i < AGENT_META_STALE_BYTES"),
            scan_sync.index("scan.seen[i] |= delta->applied_slots[i]"),
        )
        self.assertNotIn("agent_metadata_txn_projection_ack", scan_sync)

        invalidate_at = query.index(
            "agent_metadata_query_invalidate_locked(uint scope_id, int full)"
        )
        invalidate_end = query.index("\n}\n", invalidate_at)
        invalidate = query[invalidate_at:invalidate_end]
        self.assertEqual(invalidate.count("agent_metadata_txn_work_charge(0)"), 1)
        self.assertNotIn("query_cache", invalidate)
        execute_at = query.index("agent_metadata_query_execute_locked(")
        execute_end = query.index("\n}\n", execute_at)
        execute = query[execute_at:execute_end]
        self.assertLess(
            execute.index("agent_metadata_txn_projection_require_idle()"),
            execute.index("agent_query_plan_build(q, r, &plan)"),
        )

        unlock_at = metadata.index("agent_metadata_txn_unlock(void)")
        unlock_end = metadata.index("\n}\n", unlock_at)
        unlock = metadata[unlock_at:unlock_end]
        self.assertLess(
            unlock.index("txn_depth == 1 && txn_projection_pending"),
            unlock.index("txn_depth--"),
        )
        checkpoint_at = metadata.index(
            "agent_metadata_txn_checkpoint_mode(int cleanup)"
        )
        checkpoint_end = metadata.index("\n}\n", checkpoint_at)
        checkpoint = metadata[checkpoint_at:checkpoint_end]
        self.assert_projection_checkpoint_guard(checkpoint)
        self.assertIn(
            "return agent_metadata_txn_checkpoint_mode(0);", metadata
        )
        self.assertIn(
            "return agent_metadata_txn_checkpoint_mode(1);", metadata
        )
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
        internal = (root / "os" / "agent_metadata_internal.h").read_text(
            encoding="utf-8"
        )
        for removed in (
            "agent_meta_store_empty_proven",
            "agent_metadata_store_install_empty",
            "agent_file_install_empty_store",
            "agent_metadata_store_has_durable_bank",
        ):
            self.assertNotIn(removed, store + objects + internal)
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
        lossy_root_lookup = scan.replace(
            "root_dir_status(&root_status)", "root_dir()", 1
        )
        ignored_root_status = scan.replace(
            "root == 0 || root_status != FS_LOOKUP_FOUND", "root == 0", 1
        )
        ignored_bind_failure = scan.replace(
            "\t\tif (bind_failed) {\n\t\t\tscan.failures++;\n"
            "\t\t\tscan.retry = 1;",
            "\t\tif (bind_failed) {\n\t\t\t(void)bind_failed;",
            1,
        )
        unseen_on_failure = scan.replace(
            "\tSCAN_NOTE(slot);\n\tif (agent_metadata_catalog_edit_begin_scan",
            "\t(void)slot;\n\tif (agent_metadata_catalog_edit_begin_scan",
            1,
        )
        lost_sidecar_failure = scan.replace(
            "agent_file_state_set_index(ip, slot + 1, persist, 0) < 0)\n"
            "\t\t\tgoto retry;",
            "agent_file_state_set_index(ip, slot + 1, persist, 0) < 0)\n"
            "\t\t\treturn changes;",
            1,
        )
        optional_failure_output = scan.replace(
            "\t*failed = 0;", "\tif (failed)\n\t\t*failed = 0;", 1
        )
        lost_resume = scan.replace(
            "scan.offset = off;\n\t\t\tscan_pause(1, 1);",
            "scan.offset = 0;\n\t\t\tscan_pause(1, 0);",
            1,
        )
        lost_scope_isolation = scan.replace(
            "scan_scope_failed(view.scope_id, 0)", "0", 1
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
            ("lossy root lookup", objects, lossy_root_lookup),
            ("ignored root status", objects, ignored_root_status),
            ("ignored bind failure", objects, ignored_bind_failure),
            ("unseen mutation failure", objects, unseen_on_failure),
            ("lost sidecar failure", objects, lost_sidecar_failure),
            ("optional bind failure output", objects, optional_failure_output),
            ("lost resumable offset", objects, lost_resume),
            ("lost scope isolation", objects, lost_scope_isolation),
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
        raw_create_path = directory.replace(
            "agent_metadata_scan_index_inode(ip, key, &failed)",
            "agent_metadata_scan_index_inode(ip, path, &failed)",
            1,
        )
        missing_scan_note = directory.replace(
            "\tchanges = agent_metadata_scan_index_inode(ip, key, &failed);\n", "", 1
        )
        early_unlock = directory.replace(
            "\tchanges = agent_metadata_scan_index_inode(ip, key, &failed);",
            "\tagent_metadata_txn_unlock();\n"
            "\tchanges = agent_metadata_scan_index_inode(ip, key, &failed);",
            1,
        )
        direct_inode_unbind = directory.replace(
            "\tif (agent_metadata_catalog_clear_slot(slot) < 0)",
            "\tip->agent_meta_slot = 0;\n"
            "\tif (agent_metadata_catalog_clear_slot(slot) < 0)",
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
            ("raw create metadata path", objects, raw_create_path),
            ("missing scan note", objects, missing_scan_note),
            ("early transaction unlock", objects, early_unlock),
            ("directory bypasses catalog unbind", objects, direct_inode_unbind),
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
            self.assertEqual(directory.count(field), 1)
            with self.subTest(missing=field):
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.validate_metadata_directory_boundary_text(
                        objects, directory.replace(field, "0", 1)
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

    def test_budget_headroom_is_tight_by_default(self):
        self.assertEqual(
            kernel_budgets.validate_pair(
                {"baseline": 100, "maximum": 100},
                "baseline",
                "maximum",
                integer=True,
            ),
            (100, 100),
        )
        self.assertEqual(
            kernel_budgets.validate_pair(
                {"baseline": 101, "maximum": 107},
                "baseline",
                "maximum",
                integer=True,
            ),
            (101, 107),
        )
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "more than 5%"
        ):
            kernel_budgets.validate_pair(
                {"baseline": 100, "maximum": 106},
                "baseline",
                "maximum",
                integer=True,
            )
        self.assertEqual(
            kernel_budgets.validate_pair(
                {"baseline": 100.0, "maximum": 110.0},
                "baseline",
                "maximum",
                max_headroom=0.10,
            ),
            (100.0, 110.0),
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
                "indirect_call_edges": ["dispatch=target"],
                "recursion_bounds": ["printf=2"],
            }
        }
        command = kernel_budgets.stack_check_command(
            Path("/repo"),
            config,
            Path("build/os"),
            Path("scripts/stack.py"),
            ("proc", "syscall"),
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
        self.assertIn("dispatch=target", command)
        self.assertEqual(command.count("--translation-unit"), 2)
        self.assertLess(command.index("proc"), command.index("syscall"))

    def test_stack_callgraph_inventory_tracks_production_units(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "os"
            callgraph = root / "build"
            source.mkdir()
            callgraph.mkdir()
            (source / "production.c").write_text("void live(void) {}\n")
            (source / "profile_test.c").write_text("void test(void) {}\n")
            (callgraph / "production.ci").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing: profile_test"):
                kernel_stack.read_callgraphs(callgraph, source)
            kernel_stack.read_callgraphs(
                callgraph, source, ("production",)
            )
            (callgraph / "profile_test.ci").write_text("", encoding="utf-8")
            kernel_stack.read_callgraphs(
                callgraph, source, ("production",)
            )
            (callgraph / "unknown.ci").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale: unknown"):
                kernel_stack.read_callgraphs(
                    callgraph, source, ("production",)
                )
            (callgraph / "unknown.ci").unlink()
            for inventory, message in (
                (("production", "production"), "duplicate"),
                (("missing",), "source is missing"),
                (("../escape",), "invalid"),
            ):
                with self.subTest(inventory=inventory), self.assertRaisesRegex(
                    ValueError, message
                ):
                    kernel_stack.read_callgraphs(
                        callgraph, source, inventory
                    )

    def test_repository_production_stack_inventory_excludes_profiles(self):
        root = SCRIPT.parent.parent
        config = json.loads(
            (root / "ci" / "kernel-budgets.json").read_text(encoding="utf-8")
        )
        units = kernel_budgets.production_translation_units(root, config)
        self.assertIn("proc", units)
        self.assertNotIn("agent_metadata_test", units)
        self.assertNotIn("agent_observe_test", units)
        self.assertNotIn("wait_atomic_test", units)
        self.assertNotIn("fs_allocator_test", units)

    def test_stack_indirect_edges_replace_only_declared_callsite(self):
        graph = {
            "caller-node": {kernel_stack.INDIRECT_NODE},
            "other-node": {kernel_stack.INDIRECT_NODE},
        }
        incoming = {
            kernel_stack.INDIRECT_NODE: {"caller-node", "other-node"}
        }
        definitions = {
            "caller": ["caller-node"],
            "target": ["target-node"],
        }
        kernel_stack.resolve_indirect_call_edges(
            graph,
            incoming,
            definitions,
            {},
            {"caller": {"target"}},
        )
        self.assertEqual(graph["caller-node"], {"target-node"})
        self.assertEqual(
            graph["other-node"], {kernel_stack.INDIRECT_NODE}
        )
        self.assertEqual(
            incoming[kernel_stack.INDIRECT_NODE], {"other-node"}
        )

    def test_stack_indirect_edge_must_match_compiled_call(self):
        with self.assertRaisesRegex(ValueError, "no compiled call"):
            kernel_stack.resolve_indirect_call_edges(
                {"caller-node": set()},
                {kernel_stack.INDIRECT_NODE: set()},
                {
                    "caller": ["caller-node"],
                    "target": ["target-node"],
                },
                {},
                {"caller": {"target"}},
            )

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
            "indirect_call_edges": ["dispatch=target"],
            "recursion_bounds": [
                "printf=2",
                "freewalk=3",
                "uvm_prune_empty_walk=3",
            ],
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
                "KSTACK_INDIRECT_CALL_EDGES=dispatch=target",
                "KSTACK_RECURSION_BOUNDS=printf=2 freewalk=3 "
                "uvm_prune_empty_walk=3",
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
        expected_statuses = frozenset(
            ("calibrated_full_suite", "provisional_requires_full_suite")
        )
        self.assertEqual(
            kernel_budgets.AGENT_TEST_CALIBRATION_STATUSES,
            expected_statuses,
        )
        self.assertIn(
            tests["calibration_status"],
            expected_statuses,
        )
        calibrated_fields = frozenset(
            (
                "baseline_seconds",
                "max_seconds",
                "calibration_samples",
                "source_fingerprint_sha256",
                "calibration_source_commit",
                "calibration_source_tree",
                "calibration_manifest_file",
                "calibration_manifest_sha256",
                "calibration_profile_id",
            )
        )
        self.assertEqual(
            kernel_budgets.AGENT_TEST_CALIBRATED_FIELDS,
            calibrated_fields,
        )
        if (
            tests["calibration_status"]
            == kernel_budgets.AGENT_TEST_CALIBRATION_READY
        ):
            self.assertTrue(calibrated_fields.issubset(tests))
        else:
            self.assertEqual(calibrated_fields.intersection(tests), set())
        self.assertNotIn("runner_tag", tests)
        self.assertNotIn("runner_profile", tests)
        legacy_remote = copy.deepcopy(config)
        legacy_remote["agent_test_suite"]["runner_tag"] = "obsolete"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "remote runner fields are obsolete"
        ):
            kernel_budgets.validate_config(legacy_remote)
        self.assertEqual(
            tuple(tests["expected_cases"]),
            kernel_budgets.REQUIRED_AGENT_TEST_CASES,
        )
        modules = config["agent_modules"]
        self.assertEqual(
            config["agent_state_pages"]["baseline_per_process_bytes"],
            21 * 4096,
        )
        trapframes = config["trapframe_pages"]
        self.assertEqual(trapframes["baseline_per_thread_bytes"], 4096)
        self.assertEqual(
            trapframes["baseline_admitted_pool_bytes"], 128 * 16 * 4096
        )
        self.assertEqual(
            trapframes["baseline_reserved_pool_bytes"], 32 * 16 * 4096
        )
        legacy_mail = config["legacy_mail_sidecar"]
        self.assertEqual(
            legacy_mail["baseline_per_process_bytes"], 2 * 4096
        )
        self.assertEqual(
            legacy_mail["baseline_pool_bytes"], 128 * 2 * 4096
        )
        self.assertEqual(
            legacy_mail["baseline_ordinary_pool_bytes"], 96 * 2 * 4096
        )
        self.assertEqual(
            legacy_mail["baseline_reserved_pool_bytes"], 32 * 2 * 4096
        )
        missing_legacy_mail = copy.deepcopy(config)
        del missing_legacy_mail["legacy_mail_sidecar"]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(missing_legacy_mail)
        missing_trapframes = copy.deepcopy(config)
        del missing_trapframes["trapframe_pages"]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(missing_trapframes)
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
                "kalloc",
                "loader",
                "main",
                "open_file_io_lease",
                "pipe",
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
            [
                "background",
                "file_state",
                "metadata",
                "metadata_catalog",
                "metadata_store",
            ],
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
            ],
        )
        objects = next(
            entry for entry in modules["modules"]
            if entry["name"] == "metadata_objects"
        )
        self.assertIn("metadata_query", objects["allowed_dependencies"])
        self.assertIn("metadata_scan", objects["allowed_dependencies"])
        self.assertNotIn("metadata_directory", objects["allowed_dependencies"])
        self.assertEqual(
            {
                entry["name"]: tuple(entry["required_cflags"])
                for entry in modules["modules"]
                if "required_cflags" in entry
            },
            kernel_budgets.REQUIRED_AGENT_MODULE_CFLAGS,
        )
        missing_size_policy = copy.deepcopy(config)
        del next(
            entry
            for entry in missing_size_policy["agent_modules"]["modules"]
            if entry["name"] == "metadata_actions"
        )["required_cflags"]
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "optimization policy drift"
        ):
            kernel_budgets.validate_config(missing_size_policy)
        expanded_size_policy = copy.deepcopy(config)
        next(
            entry
            for entry in expanded_size_policy["agent_modules"]["modules"]
            if entry["name"] == "core"
        )["required_cflags"] = ["-Os"]
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "optimization policy drift"
        ):
            kernel_budgets.validate_config(expanded_size_policy)
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
        duplicate_identity["allowed_global_symbols"].append(
            "agent_scope_controller_departing"
        )
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
            "agent_core_unreviewed"
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

    def test_repository_background_maintain_stays_in_core_owner(self):
        source = (SCRIPT.parent.parent / "os" / "agent_core.c").read_text(
            encoding="utf-8"
        )
        body = kernel_budgets.source_function_body(
            source, "agent_background_maintain(void)"
        )
        self.assertEqual(
            body.count("agent_metadata_background_maintain();"), 1
        )
        facade = (SCRIPT.parent.parent / "os" / "agent.c").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("agent_background_maintain(void)", facade)

    def test_profile_support_is_explicitly_test_only(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        supports = config["agent_modules"]["test_only_sources"]
        self.assertEqual(
            [support["name"] for support in supports],
            [
                "metadata_crash_profile",
                "metadata_boot_recovery_profile",
                "observe_recovery_profile",
                "wait_atomic_profile",
                "fs_allocator_fault_profile",
                "physical_page_profile",
            ],
        )
        support = supports[0]
        self.assertTrue(support["production_object_excluded"])
        self.assertEqual(
            support["required_macro"], "AGENT_METADATA_CRASH_PHASE"
        )
        self.assertEqual(
            set(config["kernel_source"]["exclude_paths"]),
            {
                "os/initproc.S",
                *(owner["source_path"] for owner in supports),
            },
        )
        kernel_budgets.validate_config(config)
        for field, value in (
            ("production_object_excluded", False),
            ("required_macro", "AGENT_METADATA_EIO_PHASE"),
        ):
            broken = copy.deepcopy(config)
            broken["agent_modules"]["test_only_sources"][0][field] = value
            with self.subTest(field=field), self.assertRaises(
                kernel_budgets.BudgetError
            ):
                kernel_budgets.validate_config(broken)

        for name, mutate in (
            (
                "missing filesystem allocator owner",
                lambda broken: broken["agent_modules"][
                    "test_only_sources"
                ].pop(),
            ),
            (
                "test owner counted as production",
                lambda broken: broken["kernel_source"]["exclude_paths"].remove(
                    "os/wait_atomic_test.c"
                ),
            ),
            (
                "production source hidden",
                lambda broken: broken["kernel_source"]["exclude_paths"].append(
                    "os/proc.c"
                ),
            ),
            (
                "duplicate exclusion",
                lambda broken: broken["kernel_source"]["exclude_paths"].append(
                    "os/initproc.S"
                ),
            ),
        ):
            broken = copy.deepcopy(config)
            mutate(broken)
            with self.subTest(name=name), self.assertRaises(
                kernel_budgets.BudgetError
            ):
                kernel_budgets.validate_config(broken)

    def test_profile_symbols_are_forbidden_in_production(self):
        supports = [
            {"allowed_profile_symbols": ["profile_one", "profile_two"]},
            {"allowed_profile_symbols": ["profile_three"]},
        ]
        self.assertEqual(
            kernel_budgets.test_only_symbol_leaks(
                {"production", "profile_two"}, supports
            ),
            ["profile_two"],
        )
        self.assertEqual(
            kernel_budgets.test_only_symbol_leaks({"production"}, supports),
            [],
        )

    def test_canonical_budget_submake_clears_test_profiles(self):
        makefile = (SCRIPT.parent.parent / "Makefile").read_text(
            encoding="utf-8"
        )
        start = makefile.index("override KERNEL_BUDGET_MAKE_ARGS =")
        end = makefile.index("\n\n$(STRUCT_PROC_BUDGET_PROBE):", start)
        budget_args = makefile[start:end]
        for variable in (
            "PHYSICAL_PAGE_TEST_HOOKS",
            "AGENT_CONTEXT_SYNC_TEST_PROFILE",
            "AGENT_OBSERVE_TEST_PROFILE",
            "WAIT_ATOMIC_TEST_PROFILE",
            "FS_ALLOCATOR_FAULT_TEST_PROFILE",
            "FS_ALLOCATOR_DELETE_BARRIER_MUTANT",
            "DURABILITY_POWERCUT_TEST_PROFILE",
            "AGENT_METADATA_CRASH_PHASE",
            "AGENT_METADATA_EIO_PHASE",
            "AGENT_METADATA_SELECT_FAULT_BANK",
            "AGENT_METADATA_BOOT_READ_FAULT",
            "AGENT_METADATA_BOOT_READ_FAULT_COUNT",
            "AGENT_METADATA_BOOT_READ_FAULT_BANK",
            "VIRTIO_DISK_TEST",
            "FS_STORAGE_TINY_TEST_PROFILE",
        ):
            with self.subTest(variable=variable):
                self.assertEqual(
                    sum(
                        line in (f"\t{variable}= \\", f"\t{variable}=")
                        for line in budget_args.splitlines()
                    ),
                    1,
                )
        self.assertIn("KSTACK_INDIRECT_CALL_EDGES='", budget_args)
        self.assertIn("override KERNEL_BUDGET_TOOLPREFIX = $(TOOLPREFIX)", makefile)
        self.assertIn("override KERNEL_BUDGET_PYTHON = $(PYTHON_BIN)", makefile)
        self.assertIn("override PY = $(PYTHON_BIN)", makefile)

    def test_inactive_profile_objects_are_removed_before_build(self):
        makefile = (SCRIPT.parent.parent / "Makefile").read_text(
            encoding="utf-8"
        )
        inventory_start = makefile.index("INACTIVE_PROFILE_C_SRCS :=")
        inventory_end = makefile.index("\nAS_SRCS =", inventory_start)
        inventory = makefile[inventory_start:inventory_end]
        self.assertEqual(
            set(re.findall(r"INACTIVE_PROFILE_C_SRCS \+= \$K/(\S+\.c)", inventory)),
            {
                "agent_metadata_test.c",
                "agent_metadata_recovery_test.c",
                "agent_observe_test.c",
                "wait_atomic_test.c",
                "fs_allocator_test.c",
                "physical_page_test.c",
            },
        )
        self.assertEqual(
            inventory.count("C_SRCS := $(filter-out $K/"), 6
        )
        self.assertIn(
            "INACTIVE_PROFILE_OBJS = $(addprefix $(BUILDDIR)/,"
            "$(INACTIVE_PROFILE_C_SRCS:.c=.o))",
            inventory,
        )

        config_start = makefile.index("$(KSTACK_BUILD_CONFIG): .FORCE")
        config_end = makefile.index("\n\n$(AS_OBJS):", config_start)
        config_rule = makefile[config_start:config_end]
        cleanup = "@rm -f $(INACTIVE_PROFILE_OBJS)"
        self.assertEqual(config_rule.count(cleanup), 1)
        self.assertLess(config_rule.index(cleanup), config_rule.index("@printf"))

    @unittest.skipUnless(
        sys.platform != "win32" and shutil.which("make"),
        "GNU make profile transition test requires a POSIX host",
    )
    def test_profile_transition_removes_only_inactive_objects(self):
        root = SCRIPT.parent.parent
        profile_objects = {
            "agent_metadata_test.o",
            "agent_metadata_recovery_test.o",
            "agent_observe_test.o",
            "wait_atomic_test.o",
            "fs_allocator_test.o",
            "physical_page_test.o",
        }
        enabled = (
            "AGENT_METADATA_CRASH_PHASE=1",
            "AGENT_METADATA_BOOT_READ_FAULT=busy",
            "AGENT_OBSERVE_TEST_PROFILE=1",
            "WAIT_ATOMIC_TEST_PROFILE=1",
            "FS_ALLOCATOR_FAULT_TEST_PROFILE=1",
            "PHYSICAL_PAGE_TEST_HOOKS=1",
        )
        env = os.environ.copy()
        for variable in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
            env.pop(variable, None)

        with tempfile.TemporaryDirectory() as temp:
            build = Path(temp) / "build"
            object_dir = build / "os"
            object_dir.mkdir(parents=True)
            for name in profile_objects | {"proc.o"}:
                (object_dir / name).touch()
            target = build / ".kernel-stack-config"
            command = [
                "make",
                "--no-print-directory",
                str(target),
                f"BUILDDIR={build}",
            ]

            subprocess.run(
                command + list(enabled),
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(
                all((object_dir / name).exists() for name in profile_objects)
            )

            subprocess.run(
                command,
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue((object_dir / "proc.o").exists())
            self.assertTrue(
                all(not (object_dir / name).exists() for name in profile_objects)
            )

    def test_kernel_host_selftest_inventory_is_exact_and_non_overridable(self):
        root = SCRIPT.parent.parent
        makefile = (root / "Makefile").read_text(encoding="utf-8")
        match = re.search(
            r"override KERNEL_BUDGET_PYTHON_SELFTESTS := \\\n(?P<body>.*?)\n\n"
            r"kernel-budget-selftest:",
            makefile,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        actual = set(
            re.findall(
                r"(?:scripts|host_tools)/[A-Za-z0-9_-]+\.py",
                match.group("body"),
            )
        )
        expected = {
            path.relative_to(root).as_posix()
            for path in (root / "scripts").glob("test-*.py")
        } | {
            "scripts/check-wait-queue-contract.py",
            "scripts/check-bio-fs-must-check.py",
            "scripts/check-fs-allocator-state.py",
            "scripts/check-agent-metadata-content-fastpath.py",
            "scripts/check-agent-metadata-read-view.py",
            "scripts/check-background-dispatch-fastpath.py",
            "scripts/check-close-lazy-finalizer.py",
            "scripts/check-copyoutv-window.py",
            "scripts/check-filepool-freelist.py",
            "scripts/check-fs-epoch-index.py",
            "scripts/check-inode-mapping-guard.py",
            "scripts/check-kernel-work-receipt.py",
            "scripts/check-read-epoch-lazy-finalizer.py",
            "scripts/check-sequential-read-batch.py",
            "scripts/check-syscall-file-transaction.py",
            "scripts/check-traditional-io-fastpath.py",
            "scripts/check-vm-page-table-fastpath.py",
        }
        self.assertEqual(actual, expected)
        self.assertRegex(makefile, r"(?m)^kernel-budget-selftest:.*printf-format-static-check$")
        self.assertNotRegex(makefile, r"(?m)^kernel-budget-selftest:.*\bprintf-format-check\b")
        self.assertEqual(makefile.count("$(PYTHON_CMD) scripts/test-printf-format-contract.py"), 1)
        self.assertIn("CC=$(call shell_quote,$(HOST_CC))", makefile)
        self.assertIn("HOSTCC=$(call shell_quote,$(HOST_CC))", makefile)

    def test_parallel_build_and_test_limits_are_closed(self):
        makefile = (SCRIPT.parent.parent / "Makefile").read_text(
            encoding="utf-8"
        )
        adaptive = {
            "AGENTOS_BUILD_JOBS": "build",
            "AGENTOS_TEST_JOBS": "host",
        }
        for name, kind in adaptive.items():
            declaration = (
                f"{name} ?= $(or $(shell $(PYTHON_BIN) -I -S -B "
                f"scripts/resource-jobs.py --kind {kind} 2>/dev/null),1)"
            )
            self.assertEqual(makefile.count(declaration), 1)
            self.assertIn(f"ifneq ($(words $({name})),1)", makefile)
            self.assertIn(
                f"ifeq ($(filter $({name}),$(AGENTOS_JOB_VALUES)),)",
                makefile,
            )
        self.assertNotIn(".NOTPARALLEL", makefile)
        local_check = (
            "local-check:\n"
            "\t+@$(MAKE) --no-print-directory local-host-selftests\n"
            "\t+@$(MAKE) --no-print-directory kernel-budget-check\n"
            "\t+@$(MAKE) --no-print-directory user-stack-check\n"
        )
        self.assertEqual(makefile.count(local_check), 1)
        self.assertIn(
            "override LOCAL_HOST_SELFTESTS := \\\n"
            "\t$(HOST_CONTRACT_TESTS) \\\n"
            "\t$(filter-out $(HOST_CONTRACT_TESTS),"
            "$(EVIDENCE_CAPTURE_TESTS))",
            makefile,
        )
        self.assertRegex(
            makefile,
            r"(?m)^local-host-selftests: \$\(LOCAL_HOST_SELFTESTS\).*"
            r"agent-observe-disk-format-check printf-format-static-check$",
        )
        self.assertIn(
            "AGENTOS_SUBMAKE_JOBS = $(if $(filter -j% --jobs=% "
            "--jobserver-auth=% --jobserver-fds=%,$(MAKEFLAGS)),"
            ",-j$(AGENTOS_BUILD_JOBS))",
            makefile,
        )
        self.assertIn(
            "ifneq ($(filter -j% --jobs=% --jobserver-auth=% "
            "--jobserver-fds=%,$(MAKEFLAGS)),)\n"
            "build: $(BUILDDIR)/kernel\n"
            "else\n"
            "build:\n"
            "\t+@$(MAKE) $(AGENTOS_SUBMAKE_JOBS) --no-print-directory "
            "$(BUILDDIR)/kernel\n"
            "endif\n",
            makefile,
        )
        for fragment in (
            "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore build",
            "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) -C baseline_ucore run",
            "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) build "
            "TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) LOG=warn "
            "INIT_PROC=agentfinal_ucore",
            "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) build "
            "TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX)) LOG=warn "
            "INIT_PROC=rp_agentos_orch",
            "$(MAKE) $(AGENTOS_SUBMAKE_JOBS) run "
            "TOOLPREFIX=$(call shell_quote,$(TOOLPREFIX))",
        ):
            self.assertIn(fragment, makefile)
        self.assertIn(
            "override KERNEL_BUDGET_BUILDDIR = build/ci-kernel-budget",
            makefile,
        )
        self.assertIn(
            "BUILDDIR=$(call shell_quote,$(KERNEL_BUDGET_BUILDDIR))",
            makefile,
        )
        self.assertIn(
            "-u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES "
            "-u GNUMAKEFLAGS -u MAKEFILES",
            makefile,
        )
        self.assertEqual(
            makefile.count(
                "+@$(KERNEL_BUDGET_SUBMAKE) "
                "$(KERNEL_BUDGET_BUILDDIR)/kernel "
                "$(KERNEL_BUDGET_MAKE_ARGS)"
            ),
            2,
        )

    @unittest.skipUnless(
        sys.platform != "win32" and shutil.which("make"),
        "GNU make jobserver contract requires a POSIX host",
    )
    def test_parallel_worker_limits_reject_invalid_make_values(self):
        root = SCRIPT.parent.parent
        environment = os.environ.copy()
        for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
            environment.pop(name, None)
        for variable in ("AGENTOS_BUILD_JOBS", "AGENTOS_TEST_JOBS"):
            accepted = subprocess.run(
                ["make", "--no-print-directory", "-n", ".FORCE", f"{variable}=8"],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            for value in ("0", "25", "1 2", "eight"):
                rejected = subprocess.run(
                    [
                        "make", "--no-print-directory", "-n", ".FORCE",
                        f"{variable}={value}",
                    ],
                    cwd=root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("must be an integer between 1 and 24", rejected.stderr)

    @unittest.skipUnless(
        sys.platform != "win32" and shutil.which("make"),
        "GNU make jobserver contract requires a POSIX host",
    )
    def test_recursive_build_reuses_outer_parallel_policy(self):
        makefile = (SCRIPT.parent.parent / "Makefile").read_text(
            encoding="utf-8"
        )
        assignment = next(
            line for line in makefile.splitlines()
            if line.startswith("AGENTOS_SUBMAKE_JOBS = ")
        )
        fixture = (
            "AGENTOS_BUILD_JOBS := 8\n"
            f"{assignment}\n"
            ".PHONY: print\n"
            "print:\n"
            "\t@printf '%s\\n' '$(AGENTOS_SUBMAKE_JOBS)'\n"
        )
        environment = os.environ.copy()
        for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "parallel.mk"
            path.write_text(fixture, encoding="ascii")
            serial = subprocess.run(
                ["make", "-s", "-f", str(path), "print"],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(serial.stdout.strip(), "-j8")
            for option in ("-j1", "-j2"):
                inherited = subprocess.run(
                    ["make", option, "-s", "-f", str(path), "print"],
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(inherited.stdout.strip(), "")

    @unittest.skipUnless(
        sys.platform != "win32" and shutil.which("make"),
        "GNU make jobserver contract requires a POSIX host",
    )
    def test_kernel_budget_build_reuses_only_outer_jobserver_flags(self):
        makefile = (SCRIPT.parent.parent / "Makefile").read_text(
            encoding="utf-8"
        )
        lines = makefile.splitlines()
        start = next(
            index for index, line in enumerate(lines)
            if line.startswith("override KERNEL_BUDGET_SUBMAKE_JOBS = ")
        )
        assignment_lines = [lines[start].removeprefix("override ")]
        while assignment_lines[-1].endswith("\\"):
            start += 1
            assignment_lines.append(lines[start])
        submake_start = next(
            index for index, line in enumerate(lines)
            if line.startswith("override KERNEL_BUDGET_SUBMAKE = ")
        )
        submake_lines = [lines[submake_start].removeprefix("override ")]
        while submake_lines[-1].endswith("\\"):
            submake_start += 1
            submake_lines.append(lines[submake_start])
        fixture = (
            "AGENTOS_BUILD_JOBS := 8\n"
            + "\n".join(assignment_lines)
            + "\n.PHONY: print\n"
            + "print:\n\t@printf '%s\\n' "
            + "'$(KERNEL_BUDGET_SUBMAKE_JOBS)'\n"
        )
        environment = os.environ.copy()
        for name in (
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES"
        ):
            environment.pop(name, None)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "budget-parallel.mk"
            path.write_text(fixture, encoding="ascii")
            serial = subprocess.run(
                ["make", "-s", "-f", str(path), "print"],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(serial.stdout.strip(), "-j8")
            explicit_serial = subprocess.run(
                ["make", "-j1", "-s", "-f", str(path), "print"],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(explicit_serial.stdout.strip(), "-j1")
            inherited = subprocess.run(
                ["make", "-j2", "-s", "-f", str(path), "print"],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            inherited_flags = inherited.stdout.strip().split()
            self.assertIn("-j2", inherited_flags)
            self.assertTrue(
                any(
                    flag.startswith(("--jobserver-auth=", "--jobserver-fds="))
                    for flag in inherited_flags
                ),
                inherited.stdout,
            )
            child = Path(temp) / "child.mk"
            child.write_text(
                "check:\n\t@printf 'child=%s\\n' '$(MAKEFLAGS)'\n",
                encoding="ascii",
            )
            nested = Path(temp) / "nested.mk"
            nested.write_text(
                "AGENTOS_BUILD_JOBS := 8\n"
                + "\n".join(assignment_lines)
                + "\n"
                + "\n".join(submake_lines)
                + "\n.PHONY: all\n"
                + "all:\n\t+@$(KERNEL_BUDGET_SUBMAKE) "
                + "--no-print-directory -f child.mk check\n",
                encoding="ascii",
            )
            nested_result = subprocess.run(
                ["make", "-j2", "-s", "-f", str(nested)],
                cwd=temp,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertNotIn("jobserver unavailable", nested_result.stderr)
            child_flags = nested_result.stdout.removeprefix("child=").split()
            self.assertIn("-j2", child_flags)
            self.assertTrue(
                any(
                    flag.startswith(("--jobserver-auth=", "--jobserver-fds="))
                    for flag in child_flags
                ),
                nested_result.stdout,
            )

    def test_stack_indirect_edge_inventory_matches_compiled_call_owners(self):
        expected = [
            "agent_durable_arena_validate=agent_observe_store_validate",
            "agent_durable_arena_update_scope=agent_observe_store_update_scope",
            "agent_durable_arena_recover=agent_observe_store_recover",
            "agent_durable_arena_has_scope=agent_observe_store_has_scope",
            "agent_durable_notify_locked=agent_meta_durable_dirty",
            "agent_durable_section_replicated=agent_meta_durable_replicated",
            "agent_durable_section_active_replicated=agent_meta_durable_active_replicated",
            "agent_durable_section_persist_scope=agent_meta_durable_persist_scope",
            "agent_durable_section_mirror_scope=agent_observe_store_replicated_scope",
            "agent_identity_lease_progress=agent_observe_lease_persist_bridge",
        ]
        root = SCRIPT.parent.parent
        makefile = (root / "Makefile").read_text(encoding="utf-8")
        default_start = makefile.index("KSTACK_INDIRECT_CALL_EDGES ?=")
        default_end = makefile.index("\n# Sv39", default_start)
        default_edges = re.findall(
            r"[A-Za-z0-9_]+=[A-Za-z0-9_]+",
            makefile[default_start:default_end],
        )
        canonical_match = re.search(
            r"KSTACK_INDIRECT_CALL_EDGES='([^']+)'",
            makefile,
        )
        self.assertIsNotNone(canonical_match)
        canonical_edges = canonical_match.group(1).split()
        with (root / "ci" / "kernel-budgets.json").open(
            encoding="utf-8"
        ) as stream:
            configured_edges = json.load(stream)["kernel_stack"][
                "indirect_call_edges"
            ]

        self.assertEqual(default_edges, expected)
        self.assertEqual(canonical_edges, expected)
        self.assertEqual(sorted(configured_edges), sorted(expected))
        self.assertEqual(len(configured_edges), len(set(configured_edges)))

    def test_stack_recursion_inventory_matches_bounded_walkers(self):
        expected = ["freewalk=3", "uvm_prune_empty_walk=3"]
        root = SCRIPT.parent.parent
        makefile = (root / "Makefile").read_text(encoding="utf-8")
        default_match = re.search(
            r"^KSTACK_RECURSION_BOUNDS \?= (.+)$",
            makefile,
            flags=re.MULTILINE,
        )
        canonical_match = re.search(
            r"^\s*KSTACK_RECURSION_BOUNDS='([^']+)'",
            makefile,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(default_match)
        self.assertIsNotNone(canonical_match)
        with (root / "ci" / "kernel-budgets.json").open(
            encoding="utf-8"
        ) as stream:
            configured_bounds = json.load(stream)["kernel_stack"][
                "recursion_bounds"
            ]

        self.assertEqual(default_match.group(1).split(), expected)
        self.assertEqual(canonical_match.group(1).split(), expected)
        self.assertEqual(configured_bounds, expected)

    def test_legacy_mail_sidecar_schema_is_fail_closed(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)

        legacy_mail = config["legacy_mail_sidecar"]
        self.assertEqual(legacy_mail["baseline_per_process_bytes"], 8192)
        self.assertEqual(legacy_mail["baseline_pool_bytes"], 128 * 8192)
        kernel_budgets.validate_config(config)

        missing = copy.deepcopy(config)
        del missing["legacy_mail_sidecar"]
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(missing)

        bad_symbol = copy.deepcopy(config)
        bad_symbol["legacy_mail_sidecar"]["per_process_symbol"] = ""
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(bad_symbol)

    def test_duration_calibration_state_schema_is_fail_closed(self):
        config_path = SCRIPT.parent.parent / "ci" / "kernel-budgets.json"
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)

        self.assertEqual(
            kernel_budgets.calibrated_agent_test_limit((18.0, 18.0, 18.0)),
            18.9,
        )

        calibrated_fields = frozenset(
            (
                "baseline_seconds",
                "max_seconds",
                "calibration_samples",
                "source_fingerprint_sha256",
                "calibration_source_commit",
                "calibration_source_tree",
                "calibration_manifest_file",
                "calibration_manifest_sha256",
                "calibration_profile_id",
            )
        )
        self.assertEqual(
            kernel_budgets.AGENT_TEST_CALIBRATED_FIELDS,
            calibrated_fields,
        )
        suite = config["agent_test_suite"]
        suite["calibration_status"] = (
            kernel_budgets.AGENT_TEST_CALIBRATION_PROVISIONAL
        )
        for field in calibrated_fields:
            suite.pop(field, None)
        kernel_budgets.validate_config(config)

        suite.update(
            {
                "baseline_seconds": 255.370930671,
                "max_seconds": 268.14,
                "calibration_status": (
                    kernel_budgets.AGENT_TEST_CALIBRATION_READY
                ),
                "source_fingerprint_sha256": "0" * 64,
                "calibration_source_commit": (
                    "0123456789abcdef0123456789abcdef01234567"
                ),
                "calibration_source_tree": (
                    "89abcdef0123456789abcdef0123456789abcdef"
                ),
                "calibration_manifest_file": (
                    "evidence/calibrations/0123456789ab/manifest.json"
                ),
                "calibration_manifest_sha256": "f" * 64,
                "calibration_profile_id": suite[
                    "local_calibration_profile"
                ]["profile_id"],
                "calibration_samples": [
                    {
                        "sample_id": "agent18-0123456789ab-01",
                        "total_seconds": 261.343281873,
                        "timing_file": (
                            "evidence/calibrations/0123456789ab/01.timing"
                        ),
                        "timing_file_sha256": "1" * 64,
                        "attestation_digest_sha256": "4" * 64,
                    },
                    {
                        "sample_id": "agent18-0123456789ab-02",
                        "total_seconds": 237.948978492,
                        "timing_file": (
                            "evidence/calibrations/0123456789ab/02.timing"
                        ),
                        "timing_file_sha256": "2" * 64,
                        "attestation_digest_sha256": "5" * 64,
                    },
                    {
                        "sample_id": "agent18-0123456789ab-03",
                        "total_seconds": 255.370930671,
                        "timing_file": (
                            "evidence/calibrations/0123456789ab/03.timing"
                        ),
                        "timing_file_sha256": "3" * 64,
                        "attestation_digest_sha256": "6" * 64,
                    },
                ],
            }
        )
        kernel_budgets.validate_config(config)

        unknown = copy.deepcopy(config)
        unknown["agent_test_suite"]["calibration_status"] = "unknown"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "calibration_status is not recognized"
        ):
            kernel_budgets.validate_config(unknown)

        for field in calibrated_fields:
            missing = copy.deepcopy(config)
            del missing["agent_test_suite"][field]
            with self.subTest(missing_calibrated_field=field), self.assertRaises(
                kernel_budgets.BudgetError
            ):
                kernel_budgets.validate_config(missing)

        too_few = copy.deepcopy(config)
        too_few["agent_test_suite"]["calibration_samples"].pop()
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "requires exactly three samples",
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
            "more than 10% growth headroom",
        ):
            kernel_budgets.validate_config(excessive_headroom)

        noncanonical_limit = copy.deepcopy(config)
        noncanonical_limit["agent_test_suite"]["max_seconds"] += 0.002
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "calibrated 5% headroom policy"
        ):
            kernel_budgets.validate_config(noncanonical_limit)

        malformed_fingerprint = copy.deepcopy(config)
        malformed_fingerprint["agent_test_suite"][
            "source_fingerprint_sha256"
        ] = "A" * 64
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "requires a lowercase SHA-256",
        ):
            kernel_budgets.validate_config(malformed_fingerprint)

        malformed_timing_hash = copy.deepcopy(config)
        malformed_timing_hash["agent_test_suite"]["calibration_samples"][0][
            "timing_file_sha256"
        ] = "A" * 64
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError,
            "timing_file_sha256 requires a lowercase SHA-256",
        ):
            kernel_budgets.validate_config(malformed_timing_hash)

        mismatched_timing_path = copy.deepcopy(config)
        mismatched_timing_path["agent_test_suite"]["calibration_samples"][0][
            "timing_file"
        ] = "evidence/calibrations/0123456789ab/not-the-sample.timing"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "timing_file must be"
        ):
            kernel_budgets.validate_config(mismatched_timing_path)

        mismatched_manifest = copy.deepcopy(config)
        mismatched_manifest["agent_test_suite"][
            "calibration_manifest_file"
        ] = "evidence/calibrations/ffffffffffff/manifest.json"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "calibration_manifest_file must be"
        ):
            kernel_budgets.validate_config(mismatched_manifest)

        malformed_commit = copy.deepcopy(config)
        malformed_commit["agent_test_suite"][
            "calibration_source_commit"
        ] = "0123456789ab"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "full lowercase Git"
        ):
            kernel_budgets.validate_config(malformed_commit)

        malformed_tree = copy.deepcopy(config)
        malformed_tree["agent_test_suite"]["calibration_source_tree"] = "A" * 40
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "calibration_source_tree"
        ):
            kernel_budgets.validate_config(malformed_tree)

        malformed_attestation_digest = copy.deepcopy(config)
        malformed_attestation_digest["agent_test_suite"][
            "calibration_samples"
        ][0]["attestation_digest_sha256"] = "A" * 64
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "attestation_digest_sha256"
        ):
            kernel_budgets.validate_config(malformed_attestation_digest)

        formula_side_channel = copy.deepcopy(config)
        formula_side_channel["agent_test_suite"]["calibration_samples"][0][
            "manual_formula"
        ] = "load * constant"
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "fields must be"
        ):
            kernel_budgets.validate_config(formula_side_channel)

        for stale_field in calibrated_fields:
            provisional = copy.deepcopy(config)
            provisional_suite = provisional["agent_test_suite"]
            provisional_suite["calibration_status"] = (
                kernel_budgets.AGENT_TEST_CALIBRATION_PROVISIONAL
            )
            for field in calibrated_fields - {stale_field}:
                del provisional_suite[field]
            with self.subTest(stale_field=stale_field), self.assertRaisesRegex(
                kernel_budgets.BudgetError,
                "must not carry stale calibrated fields",
            ):
                kernel_budgets.validate_config(provisional)

    def test_legacy_schema_two_calibration_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            commit = "0123456789abcdef0123456789abcdef01234567"
            package = root / "evidence" / "calibrations" / commit[:12]
            package.mkdir(parents=True)
            manifest = {
                "schema": 2,
                "purpose": "agent_test_suite_duration_calibration",
                "review_boundary": "legacy synthetic fixture",
            }
            manifest_data = json.dumps(manifest, sort_keys=True).encode()
            (package / "manifest.json").write_bytes(manifest_data)
            config = {
                "agent_test_suite": {
                    "calibration_status": "calibrated_full_suite",
                    "calibration_manifest_file": (
                        f"evidence/calibrations/{commit[:12]}/manifest.json"
                    ),
                    "calibration_manifest_sha256": hashlib.sha256(
                        manifest_data
                    ).hexdigest(),
                }
            }
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "manifest fields mismatch"
            ):
                kernel_budgets.check_agent_test_calibration_evidence(
                    root, config, 1
                )

    def test_agent_duration_fingerprint_covers_sources_and_contract(self):
        self.assertIn(
            "scripts/guest_failure_classifier.py",
            kernel_budgets.AGENT_TEST_SOURCE_REQUIRED_PATHS,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in kernel_budgets.AGENT_TEST_SOURCE_REQUIRED_PATHS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"required:{relative}\n", encoding="utf-8")

            representatives = (
                "os/core.c",
                "os/core.h",
                "os/entry.S",
                "os/kernel.ld",
                "os/format.inc",
                "os/kernelld.py",
                "user/src/case.c",
                "user/src/case.h",
                "user/src/entry.S",
                "user/include/api.h",
                "user/lib/library.c",
                "user/lib/library.h",
                "user/lib/arch/start.S",
                "user/lib/arch/user.ld",
                "user/lib/format.inc",
                "nfs/fs.c",
                "nfs/fs.h",
                "nfs/entry.S",
                "nfs/fs.ld",
                "nfs/format.inc",
                "agent_contract_abi.h",
                "agent_contract_policy.h",
                "agent_contract_policy.inc",
            )
            for relative in representatives:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"source:{relative}\n", encoding="utf-8")

            generated = root / "os" / "initproc.S"
            generated.write_text("generated one\n", encoding="utf-8")
            tests = {
                "expected_cases": ["one", "two"],
                "local_calibration_profile": {
                    "schema_version": 1,
                    "profile_id": "fixture-local-e3-v1",
                    "cpu": "test cpu",
                    "runtime": "test runtime",
                    "toolchain_prefix": "test-toolchain-",
                    "tool_versions": {
                        "qemu": "1",
                        "toolchain_cc": "1",
                        "toolchain_ld": "1",
                        "toolchain_objcopy": "1",
                        "toolchain_objdump": "1",
                        "toolchain_as": "1",
                        "host_cc": "1",
                        "python": "1",
                        "bash": "1",
                        "make": "1",
                        "git": "1",
                    },
                },
                "calibration_status": (
                    kernel_budgets.AGENT_TEST_CALIBRATION_READY
                ),
            }
            config = {
                "canonical_toolchain": {
                    "prefix": "test-toolchain-",
                    "gcc_version": "test gcc",
                },
                "local_kernel_budget_toolchains": [
                    {
                        "profile_id": "fixture-local-e3-v1",
                        "prefix": "fixture-toolchain-",
                        "gcc_version": "1",
                        "binutils_version": "1",
                        "executable_sha256": {
                            name: "1" * 64
                            for name in (
                                "gcc",
                                "ld",
                                "objcopy",
                                "objdump",
                                "nm",
                                "size",
                            )
                        },
                    }
                ],
                "agent_test_suite": tests,
            }
            fingerprint, paths = kernel_budgets.agent_test_source_fingerprint(
                root, config
            )
            self.assertTrue(set(representatives).issubset(paths))
            self.assertTrue(
                set(kernel_budgets.AGENT_TEST_SOURCE_REQUIRED_PATHS).issubset(
                    paths
                )
            )
            self.assertNotIn("os/initproc.S", paths)
            self.assertNotIn(".gitlab-ci.yml", paths)
            tests["source_fingerprint_sha256"] = fingerprint
            with redirect_stdout(io.StringIO()):
                kernel_budgets.check_agent_test_source_fingerprint(root, config)

            for relative in paths:
                path = root / relative
                original = path.read_bytes()
                path.write_bytes(original + b"mutation\n")
                with self.subTest(source=relative), self.assertRaisesRegex(
                    kernel_budgets.BudgetError, "fingerprint mismatch"
                ):
                    kernel_budgets.check_agent_test_source_fingerprint(
                        root, config
                    )
                path.write_bytes(original)

            (root / "docs").mkdir()
            (root / "docs" / "unrelated.md").write_text(
                "not a suite input\n", encoding="utf-8"
            )
            generated.write_text("generated two\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                kernel_budgets.check_agent_test_source_fingerprint(root, config)

            changed_contract = copy.deepcopy(config)
            changed_contract["agent_test_suite"]["local_calibration_profile"][
                "tool_versions"
            ]["qemu"] = "different qemu"
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "fingerprint mismatch"
            ):
                kernel_budgets.check_agent_test_source_fingerprint(
                    root, changed_contract
                )

            changed_toolchain = copy.deepcopy(config)
            changed_toolchain["canonical_toolchain"]["gcc_version"] = (
                "different gcc"
            )
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "fingerprint mismatch"
            ):
                kernel_budgets.check_agent_test_source_fingerprint(
                    root, changed_toolchain
                )

            changed_local_toolchain = copy.deepcopy(config)
            changed_local_toolchain["local_kernel_budget_toolchains"][0][
                "executable_sha256"
            ]["gcc"] = "2" * 64
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "fingerprint mismatch"
            ):
                kernel_budgets.check_agent_test_source_fingerprint(
                    root, changed_local_toolchain
                )

            (root / kernel_budgets.AGENT_TEST_SOURCE_REQUIRED_PATHS[0]).unlink()
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "input is missing"
            ):
                kernel_budgets.agent_test_source_fingerprint(root, config)

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

        missing_bss = copy.deepcopy(config)
        background = next(
            entry
            for entry in missing_bss["agent_modules"]["modules"]
            if entry["name"] == "background"
        )
        del background["max_bss_bytes"]
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "max_bss_bytes"
        ):
            kernel_budgets.validate_config(missing_bss)

        modules = {
            entry["name"]: entry
            for entry in config["agent_modules"]["modules"]
        }
        self.assertIn(
            "agent_context_append_system_causal",
            modules["context"]["allowed_global_symbols"],
        )
        self.assertEqual(
            modules["background"]["allowed_global_symbols"],
            [
                "agent_background_request",
                "agent_background_work_pending",
                "agent_background_take",
            ],
        )
        self.assertEqual(modules["background"]["allowed_dependencies"], [])
        self.assertEqual(modules["identity_lease"]["allowed_dependencies"], [])
        self.assertEqual(
            modules["identity_lease"]["allowed_global_prefixes"], []
        )
        self.assertIn(
            "agent_identity_lease_allocator_admit",
            modules["identity_lease"]["allowed_global_symbols"],
        )
        recovery = modules["observe_recovery"]
        self.assertEqual(recovery["baseline_lines"], 374)
        self.assertEqual(recovery["max_lines"], 393)
        self.assertEqual(recovery["max_bss_bytes"], 240)
        self.assertEqual(
            recovery["allowed_dependencies"],
            ["metadata", "observe_store"],
        )
        self.assertEqual(
            recovery["allowed_global_symbols"],
            [
                "agent_observe_recovery_bind",
                "agent_observe_recovery_init",
                "agent_observe_recovery_unbind_proc",
                "sys_agent_observe_recovery",
            ],
        )
        self.assertNotIn(
            "sys_agent_observe_recovery",
            modules["observe_store"]["allowed_global_symbols"],
        )
        capacity = modules["observe_capacity"]
        self.assertEqual(capacity["baseline_lines"], 707)
        self.assertEqual(capacity["max_lines"], 742)
        self.assertEqual(capacity["max_bss_bytes"], 320)
        self.assertEqual(
            capacity["allowed_dependencies"],
            ["background", "durable_section", "workflow_lifecycle"],
        )
        ledger = modules["observe_ledger"]
        self.assertEqual(ledger["baseline_lines"], 2374)
        self.assertEqual(ledger["max_lines"], 2374)
        self.assertEqual(ledger["max_bss_bytes"], 223232)
        self.assertIn(
            "agent_observe_checkpoint_entry_validate",
            ledger["allowed_global_symbols"],
        )
        self.assertEqual(modules["observe_store"]["baseline_lines"], 896)
        self.assertEqual(modules["observe_store"]["max_lines"], 923)
        self.assertEqual(modules["observe_store"]["max_bss_bytes"], 0)

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
        self.assertEqual(lifecycle["baseline_lines"], 340)
        self.assertEqual(lifecycle["max_lines"], 351)
        self.assertEqual(
            lifecycle["allowed_dependencies"],
            [
                "identity",
                "identity_lease",
                "metadata",
                "resource_controller",
                "workflow_lifecycle",
            ],
        )
        self.assertEqual(len(source.read_text(encoding="utf-8").splitlines()), 340)
        metadata = next(
            entry
            for entry in config["agent_modules"]["modules"]
            if entry["name"] == "metadata"
        )
        source = SCRIPT.parent.parent / metadata["source_path"]
        self.assertEqual(metadata["baseline_lines"], 294)
        self.assertEqual(metadata["max_lines"], 300)
        self.assertEqual(len(source.read_text(encoding="utf-8").splitlines()), 294)
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
            object_dir = root / "build" / "os"
            object_dir.mkdir(parents=True)
            entries = []
            for name in ("one", "two", "three"):
                source = root / f"{name}.c"
                obj = object_dir / f"{name}.o"
                source.write_text("line\n", encoding="utf-8")
                obj.write_bytes(b"object")
                entries.append(
                    {
                        "name": name,
                        "source_path": source.name,
                        "object_path": f"build/os/{obj.name}",
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

    def test_agent_object_directory_ignores_stale_default_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stale_dir = root / "build" / "os"
            isolated_dir = root / "build" / "ci-kernel-budget" / "os"
            stale_dir.mkdir(parents=True)
            isolated_dir.mkdir(parents=True)
            (stale_dir / "metadata.o").write_bytes(b"stale")
            (isolated_dir / "metadata.o").write_bytes(b"isolated")
            (root / "metadata.c").write_text("line\n", encoding="utf-8")
            (root / "contract.h").write_text("contract\n", encoding="utf-8")
            (root / "os").mkdir()
            (root / "os" / "kernel.ld").write_text(
                "SECTIONS { /DISCARD/ : { *(.eh_frame) } }\n",
                encoding="utf-8",
            )
            entries = [
                {
                    "name": "metadata",
                    "source_path": "metadata.c",
                    "object_path": "build/os/metadata.o",
                }
            ]
            group = {
                "name": "fixture",
                "members": ["metadata"],
                "contract_headers": ["contract.h"],
                "contract_header_globs": ["contract.h"],
                "baseline_source_lines": 2,
                "max_source_lines": 2,
                "baseline_source_bytes": 14,
                "max_source_bytes": 14,
                "baseline_loaded_text_bytes": 10,
                "max_loaded_text_bytes": 10,
                "baseline_bss_bytes": 1,
                "max_bss_bytes": 1,
                "discarded_sections": [".eh_frame"],
            }
            inspected = []

            def measure(_size, path, _discarded):
                inspected.append(path.resolve())
                return 10, 1

            with mock.patch.object(
                kernel_budgets,
                "measure_agent_object_residency",
                side_effect=measure,
            ), redirect_stdout(io.StringIO()):
                kernel_budgets.check_agent_aggregate_budgets(
                    root, entries, [group], "fake-size", isolated_dir
                )
            self.assertEqual(inspected, [(isolated_dir / "metadata.o").resolve()])
            self.assertNotIn((stale_dir / "metadata.o").resolve(), inspected)
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "escapes repository"
            ):
                kernel_budgets.resolve_agent_object_path(
                    root, root.parent, "build/os/metadata.o"
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
                    "local_calibration_profile": {
                        "profile_id": "test-local-profile"
                    },
                }
            }
            output = io.StringIO()
            with redirect_stdout(output):
                kernel_budgets.check_agent_tests(args, config)
            self.assertIn("actual=300.75 seconds", output.getvalue())

    def test_agent_timing_inventory_validates_without_duration_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            timings = Path(temp) / "timings"
            timings.write_text("one 1.25\ntwo 2.5\n", encoding="utf-8")
            args = SimpleNamespace(agent_test_timing_file=str(timings))
            config = {
                "agent_test_suite": {"expected_cases": ["one", "two"]}
            }
            output = io.StringIO()
            with redirect_stdout(output):
                kernel_budgets.check_agent_test_timing_inventory(args, config)
            self.assertIn(
                "cases=2 total=3.750 seconds threshold=not-applied",
                output.getvalue(),
            )

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
                root=str(SCRIPT.parent.parent),
            )
            config = {
                "canonical_toolchain": {
                    "prefix": "test-toolchain-",
                    "gcc_version": "test gcc",
                },
                "agent_test_suite": {
                    "expected_cases": ["one", "two"],
                    "calibration_status": "provisional_requires_full_suite",
                    "local_calibration_profile": {
                        "schema_version": 1,
                        "profile_id": "fixture-local-e3-v1",
                        "cpu": "test cpu",
                        "runtime": "test runtime",
                        "toolchain_prefix": "test-toolchain-",
                        "tool_versions": {
                            "qemu": "1",
                            "toolchain_cc": "1",
                            "toolchain_ld": "1",
                            "toolchain_objcopy": "1",
                            "toolchain_objdump": "1",
                            "toolchain_as": "1",
                            "host_cc": "1",
                            "python": "1",
                            "bash": "1",
                            "make": "1",
                            "git": "1",
                        },
                    },
                }
            }
            output = io.StringIO()
            with redirect_stdout(output):
                kernel_budgets.check_agent_tests(args, config)
            self.assertIn("actual=26.000 seconds", output.getvalue())
            self.assertRegex(
                output.getvalue(), r"source/contract: sha256=[0-9a-f]{64}"
            )
            self.assertEqual(
                timings.read_text(encoding="utf-8"),
                "one 12.5\ntwo 13.5\n",
            )

    def test_agent_suite_policy_rejects_provisional_before_execution(self):
        config = {
            "agent_test_suite": {
                "expected_cases": ["one"],
                "calibration_status": "provisional_requires_full_suite",
            }
        }
        with self.assertRaisesRegex(
            kernel_budgets.BudgetError, "duration is provisional"
        ):
            kernel_budgets.check_agent_test_policy(config)


if __name__ == "__main__":
    unittest.main()
