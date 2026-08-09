#!/usr/bin/env python3
"""闭锁式 Host sanitizer 选择的回归检查。"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import host_probe_toolchain as toolchain


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent


class HostProbeToolchainTests(unittest.TestCase):
    def test_unavailable_sanitizer_fails_closed_by_default(self) -> None:
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="unsupported")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(toolchain.subprocess, "run", return_value=failed),
            self.assertRaisesRegex(RuntimeError, "refusing an unsanitized"),
        ):
            toolchain.required_sanitizer_flags(["cc"], Path(temporary))

    def test_local_opt_in_is_explicit_and_functional_only(self) -> None:
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="unsupported")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(
                os.environ,
                {toolchain.UNSANITIZED_OPT_IN: "1"},
                clear=True,
            ),
            mock.patch.object(toolchain.subprocess, "run", return_value=failed),
        ):
            flags = toolchain.required_sanitizer_flags(["cc"], Path(temporary))
        self.assertEqual(flags, [])
        self.assertIn("functional-only", toolchain.probe_mode(flags))

    def test_localized_compiler_bytes_do_not_require_text_decoding(self) -> None:
        failed = subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"\x81\x8dlocalized compiler error"
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(
                os.environ,
                {toolchain.UNSANITIZED_OPT_IN: "1"},
                clear=True,
            ),
            mock.patch.object(toolchain.subprocess, "run", return_value=failed),
        ):
            flags = toolchain.required_sanitizer_flags(["cc"], Path(temporary))
        self.assertEqual(flags, [])

    def test_invalid_opt_in_and_broken_runtime_are_rejected(self) -> None:
        compiled = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        failed_run = subprocess.CompletedProcess([], 1, stdout="", stderr="runtime")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(
                os.environ,
                {toolchain.UNSANITIZED_OPT_IN: "unexpected"},
                clear=True,
            ),
            mock.patch.object(
                toolchain.subprocess,
                "run",
                side_effect=[compiled, failed_run],
            ),
            self.assertRaisesRegex(RuntimeError, "must be 0 or 1"),
        ):
            toolchain.required_sanitizer_flags(["cc"], Path(temporary))

    def test_supported_sanitizer_is_mandatory_mode(self) -> None:
        passed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                toolchain.subprocess,
                "run",
                side_effect=[passed, passed],
            ),
        ):
            flags = toolchain.required_sanitizer_flags(["cc"], Path(temporary))
        self.assertEqual(flags, list(toolchain.SANITIZER_FLAGS))
        self.assertEqual(toolchain.probe_mode(flags), "ASan/UBSan")

    def test_host_compiler_uses_explicit_fallback_order(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CC": "cc-from-cc --flag", "HOSTCC": "cc-from-hostcc"},
            clear=True,
        ):
            self.assertEqual(toolchain.host_compiler(), ["cc-from-hostcc"])
        with mock.patch.dict(
            os.environ,
            {"CC": "cc-from-cc --flag", "HOST_CC": "cc-from-host-cc"},
            clear=True,
        ):
            self.assertEqual(toolchain.host_compiler(), ["cc-from-host-cc"])
        with mock.patch.dict(os.environ, {"CC": "cc-from-cc --flag"}, clear=True):
            self.assertEqual(toolchain.host_compiler(), ["cc-from-cc", "--flag"])

    def test_probe_runtime_enforces_abort_options(self) -> None:
        environment = toolchain.probe_environment(list(toolchain.SANITIZER_FLAGS))
        self.assertIn("halt_on_error=1", environment["ASAN_OPTIONS"])
        self.assertEqual(environment["UBSAN_OPTIONS"], "halt_on_error=1")

    def test_shell_records_are_data_only_and_complete(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(toolchain, "host_compiler", return_value=["cc", "-m64"]),
            mock.patch.object(
                toolchain,
                "required_sanitizer_flags",
                return_value=list(toolchain.SANITIZER_FLAGS),
            ),
        ):
            records = toolchain.shell_records(Path(temporary))
        self.assertEqual(records[:2], ["compiler\tcc", "compiler\t-m64"])
        self.assertIn("flag\t-fsanitize=address,undefined", records)
        self.assertIn(
            "environment\tUBSAN_OPTIONS=halt_on_error=1", records
        )
        self.assertEqual(records[-1], "mode\tASan/UBSan")

    def test_mandatory_production_harnesses_share_sanitizer_policy(self) -> None:
        for name in (
            "test-exec-image-policy.py",
            "test-agent-metadata-disk-format.py",
            "test-mkfs-host-snapshot.py",
            "test-printf-format-contract.py",
            "test-rp-evidence-file-field.py",
            "test-rp-state-append.py",
        ):
            text = (SCRIPT_DIR / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("required_sanitizer_flags", text)
                self.assertIn("probe_environment(sanitizer_flags)", text)
                self.assertIn("*sanitizer_flags", text)
                self.assertIn("probe_mode(sanitizer_flags)", text)

    def test_formal_shell_harness_inventory_uses_shared_policy(self) -> None:
        parallel_runner = ast.parse(
            (SCRIPT_DIR / "run-parallel-qemu-regressions.py").read_text(
                encoding="utf-8"
            )
        )
        resource_cases = next(
            node.value
            for node in parallel_runner.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "RESOURCE_CASES"
                for target in node.targets
            )
        )
        resource_runners = {
            Path(node.args[1].value).name
            for node in ast.walk(resource_cases)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RegressionCase"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        }
        expected_runners = {
            "run-file-resource-tests.sh",
            "run-fs-allocator-fault-tests.sh",
            "run-fs-enospc-tests.sh",
            "run-fs-epoch-tests.sh",
            "run-physical-resource-tests.sh",
            "run-proc-reap-tests.sh",
            "run-syscall-fairness-tests.sh",
            "run-thread-resource-tests.sh",
            "run-virtio-disk-tests.sh",
            "run-workflow-teardown-race-tests.sh",
        }
        self.assertEqual(resource_runners, expected_runners)
        shell_harnesses = expected_runners | {
            "test-identity-lease-deferred.sh",
            "test-observe-reap-state.sh",
        }
        direct_compiler = re.compile(
            r'(?m)^[ \t]*(?:"?\$\{(?:HOST_CC|HOSTCC|CC(?::-cc)?)[^}]*\}"?'
            r'|"?\$(?:HOST_CC|HOSTCC|CC)"?|cc|gcc)[ \t]'
        )
        for name in sorted(shell_harnesses):
            text = (SCRIPT_DIR / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("host-probe-toolchain.sh", text)
                self.assertIn("host_probe_setup", text)
                self.assertIn("host_probe_compile", text)
                self.assertIn("host_probe_run", text)
                self.assertIn("host_probe_report", text)
                self.assertIsNone(direct_compiler.search(text))

    def test_no_test_runner_invokes_a_bare_host_compiler(self) -> None:
        direct_compiler = re.compile(
            r'(?m)^[ \t]*(?:"?\$\{(?:HOST_CC|HOSTCC|CC(?::-cc)?)[^}]*\}"?'
            r'|"?\$(?:HOST_CC|HOSTCC|CC)"?|cc|gcc)[ \t]'
        )
        for pattern in ("run-*-tests.sh", "test-*.sh"):
            for path in SCRIPT_DIR.glob(pattern):
                with self.subTest(path=path.name):
                    self.assertIsNone(
                        direct_compiler.search(path.read_text(encoding="utf-8"))
                    )

    def test_formal_entrypoints_forbid_local_unsanitized_opt_in(self) -> None:
        full_verify = (SCRIPT_DIR / "run-full-verification.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("export AGENTOS_ALLOW_UNSANITIZED_HOST_PROBES=0", full_verify)


if __name__ == "__main__":
    unittest.main(verbosity=2)
