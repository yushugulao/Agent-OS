#!/usr/bin/env python3
"""内核预算检查器的精简行为测试。"""

import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
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
    def test_repository_policy_is_valid_and_cli_uses_explicit_root(self):
        root = SCRIPT.parent.parent
        config = kernel_budgets.load_config(root / "ci/kernel-budgets.json")
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(config["canonical_toolchain"]["init_proc"], "agentfinal_ucore")
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

    def test_config_rejects_unknown_schema_and_non_finite_number(self):
        root = SCRIPT.parent.parent
        source = json.loads((root / "ci/kernel-budgets.json").read_text(encoding="utf-8"))
        source["schema_version"] = 2
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.validate_config(source)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "budget.json"
            path.write_text('{"baseline": NaN}', encoding="utf-8")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.load_config(path)

    def test_security_configuration_mutations_fail_closed(self):
        root = SCRIPT.parent.parent
        source = json.loads(
            (root / "ci/kernel-budgets.json").read_text(encoding="utf-8")
        )
        cases = (
            ("tool hash", ("local_kernel_budget_toolchains", 0,
                           "executable_sha256"),
             lambda value: {key: item for key, item in value.items()
                            if key != "ld"}, "inventory mismatch"),
            ("profile binding", ("local_kernel_budget_toolchains", 0,
                                 "profile_id"),
             lambda _: "detached-local-profile", "must have one"),
            ("case inventory", ("agent_test_suite", "expected_cases"),
             lambda value: value[:-1], "required 21-case"),
            ("bridge path", ("agent_modules", "integration_bridges", 0,
                             "object_path"),
             lambda _: "../bio.o", "build/os object path"),
            ("SCC policy", ("agent_modules", "allowed_sccs"),
             lambda value: value[:-1], "reviewed architectural cycles"),
            ("aggregate headers", ("agent_modules", "aggregate_budgets", 0,
                                   "contract_headers"),
             lambda value: value[:-1], "contract header inventory drift"),
            ("profile isolation", ("agent_modules", "test_only_sources", 0,
                                   "production_object_excluded"),
             lambda _: False, "production_object_excluded drift"),
        )
        for name, path, mutate, diagnostic in cases:
            with self.subTest(name=name):
                config = copy.deepcopy(source)
                owner = config
                for key in path[:-1]:
                    owner = owner[key]
                owner[path[-1]] = mutate(owner[path[-1]])
                with self.assertRaisesRegex(
                    kernel_budgets.BudgetError, diagnostic
                ):
                    kernel_budgets.validate_config(config)

    def test_agent_execution_plane_policy_inventory_is_exact(self):
        cases = kernel_budgets.REQUIRED_AGENT_TEST_CASES
        bench_index = cases.index("agentbench_ucore")
        self.assertEqual(
            cases[bench_index + 1 : bench_index + 4],
            (
                "agentcontract_ucore",
                "agent_eevdf_ucore",
                "agenttask_ucore",
            ),
        )
        self.assertEqual(len(cases), 21)
        self.assertEqual(kernel_budgets.REQUIRED_AGENT_MAX_SCC_SIZE, 4)
        module_sccs = frozenset(
            (
                frozenset(
                    ("context", "observe", "observe_timeline", "provenance")
                ),
                frozenset(("evidence_ring", "observe_ledger")),
                frozenset(("ipc", "live_query_events")),
            )
        )
        self.assertEqual(
            kernel_budgets.REQUIRED_AGENT_ALLOWED_SCCS,
            module_sccs,
        )
        self.assertEqual(
            kernel_budgets.REQUIRED_AGENT_INTEGRATION_ALLOWED_SCCS,
            module_sccs
            | {
                frozenset(("core", "facade", "proc", "task_bridge")),
            },
        )
        self.assertEqual(
            kernel_budgets.REQUIRED_AGENT_AGGREGATES["agent_execution_plane"],
            frozenset(
                (
                    "execution_contract",
                    "provenance",
                    "task_channel",
                    "task_bridge",
                )
            ),
        )
        self.assertEqual(
            kernel_budgets.REQUIRED_AGENT_AGGREGATE_HEADERS[
                "agent_execution_plane"
            ],
            frozenset(
                (
                    "os/agent_context_path.h",
                    "os/agent_evidence_ring.h",
                    "os/agent_execution_contract.h",
                    "os/agent_observe_internal.h",
                    "os/agent_provenance.h",
                    "os/agent_sha256.h",
                    "os/agent_task_bridge.h",
                    "os/agent_task_channel.h",
                    "os/agent_tool_protocol.h",
                )
            ),
        )

    def test_production_cli_dispatches_fail_closed(self):
        root = SCRIPT.parent.parent
        common = [
            sys.executable, str(SCRIPT), "--config",
            str(root / "ci/kernel-budgets.json"), "--root", str(root),
        ]
        cases = (
            ("kernel", [], "--cc, --ld, --objcopy"),
            ("agent-modules", [
                "--object-dir", "../escape", "--cc", "unused", "--ld", "unused",
                "--objcopy", "unused", "--objdump", "unused", "--nm", "unused",
                "--size", "unused",
            ], "object directory escapes repository"),
        )
        for check, extra, diagnostic in cases:
            with self.subTest(check=check):
                result = subprocess.run(
                    common + ["--check", check] + extra,
                    cwd=root.parent, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(diagnostic, result.stderr)

    def test_source_measurement_is_physical_normalized_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "os").mkdir()
            (root / "os/a.c").write_bytes(b"one\n\nthree\n")
            (root / "os/a.h").write_bytes(b"one")
            (root / "os/generated.inc").write_bytes(b"two\nlines\n")
            lines, files = kernel_budgets.measure_source_lines(
                root, ["os/**/*.c", "os/**/*.h", "os/**/*.inc"],
                ["os/generated.inc"],
            )
            self.assertEqual((lines, files), (4, 2))
            (root / "lf.c").write_bytes(b"one\ntwo\n")
            (root / "crlf.c").write_bytes(b"one\r\ntwo\r\n")
            self.assertEqual(
                kernel_budgets.measure_file_source(root, "lf.c"),
                kernel_budgets.measure_file_source(root, "crlf.c"),
            )
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.measure_file_source(root, "../outside.c")

    def test_limit_boundary_growth_and_ratchet(self):
        with redirect_stdout(io.StringIO()):
            kernel_budgets.check_limit("metric", 110, 100, 110, " bytes")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_limit("metric", 111, 100, 110, " bytes")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_limit("metric", float("nan"), 100, 110, " bytes")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.check_limit("metric", 97, 100, 110, " bytes", ratchet=True)

    def test_nm_parsers_use_hex_and_reject_ambiguous_symbols(self):
        output = (
            "0000000080240000 0000000000000340 B measured\n"
            "0000000080250000 B measured_end\n"
            "0000000080260000 T callable\n"
            "                 U dependency\n"
        )
        self.assertEqual(kernel_budgets.parse_nm_symbol_size(output, "measured"), 0x340)
        self.assertEqual(
            kernel_budgets.parse_nm_symbol_address(output, "measured_end"),
            0x80250000,
        )
        self.assertIn("callable", kernel_budgets.parse_nm_defined_symbols(output))
        self.assertIn("dependency", kernel_budgets.parse_nm_undefined_symbols(output))
        with self.assertRaises(kernel_budgets.BudgetError):
            kernel_budgets.parse_nm_symbol_size(output + output, "measured")

    def test_symbol_authority_filters_are_fail_closed(self):
        records = {"agent_ok": "T", "agent_secret": "B", "agent_table": "R"}
        self.assertTrue(
            kernel_budgets.is_controlled_agent_symbol(
                "workflow_scheduler_select"
            )
        )
        self.assertFalse(
            kernel_budgets.is_controlled_agent_symbol("workflow_select")
        )
        self.assertEqual(
            kernel_budgets.forbidden_symbols(records, ["agent_"], ["agent_ok"]),
            ["agent_secret", "agent_table"],
        )
        self.assertEqual(
            kernel_budgets.invalid_global_object_exports(records, ["agent_table"]),
            ["agent_secret (B)"],
        )

    def test_boot_entry_has_one_direct_target(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = Path(temp) / "entry.S"
            entry.write_text("_entry:\n  la sp, boot_stack_top\n  call boot_main\n", encoding="utf-8")
            self.assertEqual(kernel_budgets.measure_boot_entry_target(entry), "boot_main")
            entry.write_text("_entry:\n  call first\n  call second\n", encoding="utf-8")
            with self.assertRaises(kernel_budgets.BudgetError):
                kernel_budgets.measure_boot_entry_target(entry)

    def test_stack_analyzer_rejects_missing_indirect_and_boot_overflow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            callgraph = root / "callgraph"
            source.mkdir()
            callgraph.mkdir()
            (source / "root.c").write_text("void root(void) {}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing: root"):
                kernel_stack.read_callgraphs(callgraph, source, ("root",))

            with self.assertRaisesRegex(ValueError, "no compiled call"):
                kernel_stack.resolve_indirect_call_edges(
                    {"caller-node": set()}, {kernel_stack.INDIRECT_NODE: set()},
                    {"caller": ["caller-node"], "target": ["target-node"]},
                    {}, {"caller": {"target"}},
                )

            nodes = (
                ("main", 32), ("usertrap", 32),
                ("kerneltrap", 32), ("leaf", 16),
            )
            graph = ['graph: { title: "root.c"']
            graph.extend(
                f'node: {{ title: "{name}" label: "{name}\\nroot.c:1:1'
                f'\\n{size} bytes (static)" }}'
                for name, size in nodes
            )
            graph.extend(
                f'edge: {{ sourcename: "{name}" targetname: "leaf" }}'
                for name in ("main", "usertrap", "kerneltrap")
            )
            (callgraph / "root.ci").write_text("\n".join(graph), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(STACK_SCRIPT),
                    "--callgraph-dir", str(callgraph), "--source-dir", str(source),
                    "--translation-unit", "root", "--stack-size", "1024",
                    "--guard-size", "512", "--safety-margin", "0",
                    "--interrupt-entry", "256", "--boot-root", "main",
                    "--boot-stack-size", "300",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("boot stack check failed", result.stderr)

    def test_agent_timing_requires_exact_positive_case_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "timings"
            path.write_text("one 1.25\ntwo 2.5\n", encoding="utf-8")
            records, total = kernel_budgets.read_agent_timing_file(path, ["one", "two"])
            self.assertEqual(records, [("one", 1.25), ("two", 2.5)])
            self.assertEqual(total, 3.75)
            for text in ("two 2.5\none 1.25\n", "one 0\ntwo 2.5\n", "one nan\ntwo 2.5\n"):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(kernel_budgets.BudgetError):
                    kernel_budgets.read_agent_timing_file(path, ["one", "two"])

    def test_toolchain_invocation_resolution_and_failure_diagnostics(self):
        completed = subprocess.CompletedProcess(
            args=["fixture"], returncode=1, stdout=b"", stderr=b"bad:\xff"
        )
        from unittest import mock

        with mock.patch.object(kernel_budgets.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(kernel_budgets.BudgetError, "bad:"):
                kernel_budgets.run_tool(["fixture"], "fixture diagnostic")

        root = SCRIPT.parent.parent
        config = json.loads(
            (root / "ci/kernel-budgets.json").read_text(encoding="utf-8")
        )
        prefix = config["canonical_toolchain"]["prefix"]
        suffixes = {
            "gcc": "gcc",
            "ld": "ld",
            "objcopy": "objcopy",
            "objdump": "objdump",
            "nm": "nm",
            "size": "size",
        }
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            targets = temp / "targets"
            invocations = temp / "bin"
            targets.mkdir()
            invocations.mkdir()
            gcc_target = targets / f"{prefix}gcc-15"
            ld_target = targets / f"{prefix}ld.bfd"
            gcc_target.write_bytes(b"gcc fixture")
            ld_target.write_bytes(b"ld fixture")
            gcc_link = invocations / f"{prefix}gcc"
            ld_link = invocations / f"{prefix}ld"
            try:
                gcc_link.symlink_to(gcc_target)
                ld_link.symlink_to(ld_target)
            except OSError as error:
                self.skipTest(f"symbolic links unavailable: {error}")
            if (
                not gcc_link.is_symlink()
                or not ld_link.is_symlink()
                or gcc_link.resolve() != gcc_target.resolve()
                or ld_link.resolve() != ld_target.resolve()
            ):
                self.skipTest("host does not provide real resolvable symlinks")

            requested = {
                name: invocations / f"{prefix}{suffix}"
                for name, suffix in suffixes.items()
            }
            kind, profile = kernel_budgets.select_kernel_budget_toolchain(
                config, requested
            )
            self.assertEqual(kind, "canonical")
            self.assertEqual(profile, config["canonical_toolchain"])
            self.assertEqual(
                kernel_budgets.resolve_executable_once(
                    gcc_link, "Ubuntu GCC fixture"
                ),
                gcc_target.resolve(),
            )
            self.assertEqual(
                kernel_budgets.resolve_executable_once(
                    ld_link, "Ubuntu ld fixture"
                ),
                ld_target.resolve(),
            )

            wrong_link = invocations / "malicious-gcc"
            wrong_link.symlink_to(gcc_target)
            malicious = dict(requested)
            malicious["gcc"] = wrong_link
            with mock.patch.object(
                kernel_budgets,
                "resolve_executable_once",
                side_effect=AssertionError("wrong invocation was resolved"),
            ) as resolver:
                with self.assertRaisesRegex(
                    kernel_budgets.BudgetError, "approved prefix"
                ):
                    kernel_budgets.validate_kernel_budget_toolchain(
                        config,
                        malicious["gcc"],
                        malicious["ld"],
                        malicious["objcopy"],
                        malicious["objdump"],
                        malicious["nm"],
                        malicious["size"],
                        temp / "build-config",
                        temp / "initproc.S",
                    )
                resolver.assert_not_called()

            loop_link = invocations / f"{prefix}size"
            loop_target = invocations / "size-loop-target"
            loop_link.symlink_to(loop_target)
            loop_target.symlink_to(loop_link)
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "cannot resolve"
            ):
                kernel_budgets.resolve_executable_once(
                    loop_link, "symlink loop fixture"
                )

    def test_canonical_cc1_uses_the_pinned_cpp_split_package(self):
        from unittest import mock

        root = SCRIPT.parent.parent
        config = json.loads(
            (root / "ci/kernel-budgets.json").read_text(encoding="utf-8")
        )
        profile = config["canonical_toolchain"]
        self.assertEqual(profile["cc1_package"], "cpp-15-riscv64-linux-gnu")
        self.assertEqual(
            profile["cc1_package_version"], profile["gcc_package_version"]
        )
        tools = {
            name: Path(f"/approved/{name}")
            for name in (
                "gcc", "cc1", "as", "ld", "objcopy", "objdump", "nm", "size"
            )
        }

        def resolve_approved(_path, description):
            return tools[description.removeprefix("canonical ")]

        def exact_owner(_path, description):
            name = description.removeprefix("canonical ")
            if name == "gcc":
                return {profile["gcc_package"]}
            if name == "cc1":
                return {profile["cc1_package"]}
            return {profile["binutils_package"]}

        with mock.patch.object(
            kernel_budgets, "resolve_executable_once", side_effect=resolve_approved
        ), mock.patch.object(
            kernel_budgets, "dpkg_tool_owner", side_effect=exact_owner
        ), mock.patch.object(kernel_budgets, "run_tool", return_value=""):
            kernel_budgets.validate_canonical_kernel_budget_tools(profile, tools)

        def wrong_cc1_owner(path, description):
            if description == "canonical cc1":
                return {profile["gcc_package"]}
            return exact_owner(path, description)

        with mock.patch.object(
            kernel_budgets, "resolve_executable_once", side_effect=resolve_approved
        ), mock.patch.object(
            kernel_budgets, "dpkg_tool_owner", side_effect=wrong_cc1_owner
        ), mock.patch.object(kernel_budgets, "run_tool", return_value=""):
            with self.assertRaisesRegex(
                kernel_budgets.BudgetError, "canonical cc1 is owned"
            ):
                kernel_budgets.validate_canonical_kernel_budget_tools(
                    profile, tools
                )


if __name__ == "__main__":
    unittest.main()
