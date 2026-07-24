#!/usr/bin/env python3
"""Unit tests for the kernel budget checker."""

import importlib.util
import io
import json
import copy
import statistics
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-kernel-budgets.py")
STACK_SCRIPT = Path(__file__).with_name("check-kernel-stack-usage.py")
SPEC = importlib.util.spec_from_file_location("kernel_budgets", SCRIPT)
kernel_budgets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kernel_budgets)


class KernelBudgetTests(unittest.TestCase):
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
        for required_glob in ("os/**/*.inc", "*_policy.inc"):
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
