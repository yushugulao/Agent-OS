#!/usr/bin/env python3
"""Regression tests for the formal evaluation execution-domain preflight."""

from __future__ import annotations

import json
import importlib.util
import ast
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import evaluation_platform as platform_probe
import duration_profile_attestation as duration_attestation


REPOSITORY = Path(__file__).resolve().parents[1]
TRUSTED_PYTHON_ENTRY = REPOSITORY / "scripts" / "trusted-python-entry.py"


HARDWARE = {
    "cpu_model": "Example Stable CPU",
    "logical_cpu_count": 4,
    "memory_total_bytes": 8 * 1024 * 1024 * 1024,
    "source": platform_probe.HARDWARE_SOURCE,
}


def completed(argv: list[str], returncode: int, output: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, returncode, output, b"")


class EvaluationPlatformTests(unittest.TestCase):
    def test_duration_attestation_rejects_previous_schema(self) -> None:
        value = duration_attestation.build_duration_attestation(
            contract_root=REPOSITORY, profile="none", toolprefix="n/a",
            qemu="n/a", python_bin="n/a", host_cc="n/a", shell_bin="n/a",
        )
        value["schema_version"] = duration_attestation.SCHEMA_VERSION - 1
        with self.assertRaisesRegex(
            duration_attestation.DurationAttestationError, "schema differs"
        ):
            duration_attestation.validate_duration_attestation(
                value, contract_root=REPOSITORY
            )

    def test_formal_duration_attestation_rejects_provisional_local_profile(self) -> None:
        profile = {
            "calibration_status": "provisional_requires_full_suite",
            "name": "local-e3", "profile_id": "fixture", "status": "matched",
        }
        platform = {
            "domain": "native-msys2", "duration_profile": profile,
            "entry_domain": "native-msys2", "hardware": {}, "runtime": {},
            "tools": {}, "uname": {},
        }
        value = {
            "applicability": "calibrated-local-e3",
            "configuration": duration_attestation._configuration(REPOSITORY),
            "platform": platform, "profile": profile,
            "platform_identity_sha256": duration_attestation.duration_platform_identity_sha256(platform),
            "schema_version": duration_attestation.SCHEMA_VERSION,
        }
        contract = (None, lambda *_args, **_kwargs: None,
                    lambda *_args, **_kwargs: "local-e3")
        with mock.patch.object(duration_attestation, "_platform_contract", return_value=contract):
            with self.assertRaisesRegex(
                duration_attestation.DurationAttestationError,
                "requires calibrated_full_suite",
            ):
                duration_attestation.validate_duration_attestation(
                    value, contract_root=REPOSITORY
                )

    def test_duration_attestation_binds_execution_tools_and_campaign_platform(self) -> None:
        platform_tools = {
            label: {"path": f"/tools/{label}", "sha256": "a" * 64, "version": "v1"}
            for label in duration_attestation.EXECUTION_TOOL_LABELS
        }
        platform = {
            "domain": "native-msys2", "entry_domain": "native-msys2",
            "duration_profile": {"name": "local-e3"}, "hardware": HARDWARE,
            "runtime": {"version": "fixture"}, "tools": platform_tools,
            "uname": {"system": "fixture"},
        }
        value = {
            "platform": platform, "profile": {"name": "local-e3"},
            "platform_identity_sha256": duration_attestation.duration_platform_identity_sha256(platform),
        }
        execution = {
            label: {"path": record["path"], "executable_sha256": record["sha256"],
                    "first_line": record["version"]}
            for label, record in platform_tools.items()
        }
        duration_attestation.validate_duration_execution_binding(
            value, execution, expected_platform=platform
        )
        execution["qemu"]["executable_sha256"] = "b" * 64
        with self.assertRaisesRegex(
            duration_attestation.DurationAttestationError, "qemu"
        ):
            duration_attestation.validate_duration_execution_binding(value, execution)
        execution["qemu"]["executable_sha256"] = "a" * 64
        changed_platform = {**platform, "hardware": {**HARDWARE, "logical_cpu_count": 8}}
        with self.assertRaisesRegex(
            duration_attestation.DurationAttestationError, "differs from campaign"
        ):
            duration_attestation.validate_duration_execution_binding(
                value, execution, expected_platform=changed_platform
            )

    def test_full_verify_is_routed_through_formal_execution_domain(self) -> None:
        self.assertIn("full-verify", platform_probe.FORMAL_MODES)

    def test_formal_tool_set_covers_build_runtime_and_provenance_helpers(self) -> None:
        self.assertEqual(
            set(platform_probe.TOOL_LABELS),
            {
                "bash", "env", "git", "make", "python", "assembler", "compiler", "host_cc",
                "linker", "objcopy", "objdump", "size", "qemu", "timeout",
                "readlink", "sha256sum",
            },
        )
        self.assertEqual(
            set(platform_probe.MSYS_EXTRA_TOOL_LABELS),
            {"cygpath", "host_objdump", "uname"},
        )

    def test_platform_cli_requires_explicit_host_cc_and_duration_profile(self) -> None:
        parser = platform_probe._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["doctor", "--repo", str(REPOSITORY)])
        parsed = parser.parse_args([
            "doctor", "--repo", str(REPOSITORY),
            "--host-cc", "/bound/clang",
            "--duration-profile", "none",
        ])
        self.assertEqual(parsed.host_cc, "/bound/clang")
        self.assertEqual(parsed.duration_profile, "none")

    def test_local_e3_profile_rejects_host_cc_mutation_and_none_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = (
                "qemu", "assembler", "compiler", "linker", "objcopy", "objdump", "size",
                "host_cc", "python", "bash", "make", "git",
            )
            tools = {name: {"path": f"/bound/{name}"} for name in names}
            tools.update(compiler={"path": "/bound/riscv-none-elf-gcc"},
                         host_cc={"path": "/bound/clang"})
            proof = {"domain": "native-msys2", "tools": tools}

            def identity(command: str, name: str) -> dict[str, object]:
                if name == "host_cc" and command != "/bound/clang":
                    raise ValueError("host_cc version differs")
                return {
                    "requested_path": command, "executable": {"path": command},
                    "version_argv": [command, "--version"],
                    "version_first_line": "fixture",
                }

            calibration = mock.Mock()
            calibration.executable_identity.side_effect = identity
            calibration.validate_live_calibration_profile.return_value = "fixture-local-e3"
            calibration.capture_host_identity.return_value = {}
            budget = mock.Mock()
            budget.load_config.return_value = {"agent_test_suite": {
                "calibration_status": "provisional_requires_full_suite",
                "local_calibration_profile": {},
            }}
            budget.resolve_executable_once.side_effect = lambda path, _label: Path(path)
            budget.resolve_gcc_subprogram.side_effect = lambda _gcc, name: Path(f"/bound/{name}")
            budget.select_kernel_budget_toolchain.return_value = (
                "local", {"profile_id": "fixture-local-e3"}
            )

            with mock.patch.object(
                platform_probe,
                "_load_profile_component",
                side_effect=(calibration, budget),
            ):
                matched = platform_probe._duration_profile_binding(root, proof, "local-e3")
            self.assertEqual(matched["status"], "matched")
            tools["host_cc"]["path"] = "/usr/bin/cc"
            with (
                mock.patch.object(
                    platform_probe,
                    "_load_profile_component",
                    side_effect=(calibration, budget),
                ),
                self.assertRaisesRegex(
                    platform_probe.PlatformPreflightError, "host_cc version differs"
                ),
            ):
                platform_probe._duration_profile_binding(root, proof, "local-e3")
            disabled = platform_probe._duration_profile_binding(root, proof, "none")
            self.assertEqual(disabled["status"], "disabled-different-runner")

    def test_native_windows_is_not_a_formal_collection_domain(self) -> None:
        current_directory = Path.cwd()
        with (
            mock.patch.object(platform_probe.os, "name", "nt"),
            mock.patch.object(platform_probe.sys, "platform", "win32"),
            self.assertRaisesRegex(
                platform_probe.PlatformPreflightError, "fully re-executed"
            ),
        ):
            platform_probe.probe_native_linux_domain(
                repo=current_directory,
                toolprefix="riscv64-linux-gnu-",
                qemu="qemu-system-riscv64",
                python_bin="python3",
                host_cc="cc",
                duration_profile="none",
            )

    def test_python_39_is_rejected_before_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "python3"
            executable.write_bytes(b"python")
            with (
                mock.patch.object(
                    platform_probe, "_regular_executable", return_value=executable
                ),
                mock.patch.object(
                    platform_probe,
                    "_run_bytes",
                    return_value=completed([], 0, b"3.9.18\nCPython\n1111\n"),
                ),
                self.assertRaisesRegex(
                    platform_probe.PlatformPreflightError, "Python >= 3.10"
                ),
            ):
                platform_probe._python_identity("python3", cwd=Path(temporary))

    def test_old_wsl_without_version_is_accepted_only_after_full_distro_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "wsl.exe"
            launcher.write_bytes(b"bound-wsl-launcher")
            tools = {
                label: {
                    "argv0": label,
                    "path": f"/usr/bin/{label}",
                    "sha256": "a" * 64,
                    "version": f"{label} 1",
                }
                for label in platform_probe.TOOL_LABELS
            }
            tools["host_cc"]["argv0"] = "/opt/host/bin/clang"
            tools["host_cc"]["path"] = "/opt/host/bin/clang"
            observed = {
                "distribution": "Ubuntu-24.04",
                "hardware": dict(HARDWARE),
                "kernel": "6.6-wsl2",
                "repository": "/mnt/e/project",
                "toolprefix": "/usr/bin/riscv64-linux-gnu-",
                "tools": tools,
            }
            calls: list[list[str]] = []

            def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
                calls.append(list(argv))
                if argv[1:] == ["--version"]:
                    return completed(argv, 1, b"unknown option\n")
                if any("wslpath -a" in argument for argument in argv):
                    return completed(
                        argv,
                        0,
                        b"wsl launcher notice\n__AGENTOS_WSLPATH__/mnt/e/project\n",
                    )
                return completed(
                    argv,
                    0,
                    b"wsl launcher notice\n__AGENTOS_PLATFORM_JSON__"
                    + json.dumps(observed, sort_keys=True).encode("utf-8")
                    + b"\n",
                )

            with (
                mock.patch.object(platform_probe.os, "name", "nt"),
                mock.patch.object(
                    platform_probe, "_regular_executable", return_value=launcher
                ),
                mock.patch.object(platform_probe, "_run_bytes", side_effect=fake_run),
            ):
                result = platform_probe.probe_windows_wsl_domain(
                    repo=root,
                    distro="Ubuntu-24.04",
                    toolprefix="riscv64-linux-gnu-",
                    qemu="qemu-system-riscv64",
                    host_cc="/opt/host/bin/clang",
                    duration_profile="none",
                )
            self.assertEqual(result["domain"], "windows-wsl")
            self.assertEqual(result["hardware"], HARDWARE)
            self.assertEqual(result["schema_version"], platform_probe.SCHEMA_VERSION)
            self.assertEqual(result["requested_host_cc"], "/opt/host/bin/clang")
            self.assertEqual(result["duration_profile"]["name"], "none")
            self.assertIn("bound by SHA256", result["launcher"]["version"])
            self.assertTrue(
                all("-d" in argv and "Ubuntu-24.04" in argv for argv in calls[1:])
            )
            self.assertTrue(
                any(any("wslpath -a" in argument for argument in argv) for argv in calls)
            )

    def test_wsl_reexec_uses_one_clean_domain_and_converts_host_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "wsl.exe"
            launcher.write_bytes(b"launcher")
            tools = {
                label: {
                    "path": f"/usr/bin/{label}",
                    "sha256": "a" * 64,
                }
                for label in platform_probe.TOOL_LABELS
            }
            preflight = {
                "domain": "windows-wsl",
                "distribution": "Ubuntu",
                "repository": {"execution_path": "/mnt/e/repo"},
                "launcher": {
                    "path": str(launcher),
                    "sha256": platform_probe._sha256(launcher),
                },
                "toolprefix": "/usr/bin/riscv64-linux-gnu-",
                "tools": tools,
                "duration_profile": {
                    "calibration_status": "not-applicable",
                    "name": "none",
                    "profile_id": "none",
                    "status": "disabled-different-runner",
                },
            }
            observed: list[str] = []

            def fake_process(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
                observed.extend(argv)
                return completed(argv, 0, b"")

            with mock.patch.object(platform_probe.subprocess, "run", side_effect=fake_process):
                rc = platform_probe.execute_in_wsl(
                    preflight=preflight,
                    repo=root,
                    distro="Ubuntu",
                    script_relative="scripts/run-evaluation-suite.sh",
                    mode="full-verify",
                    host_environment={
                        "EVALUATION_BOOTS": "7",
                        "EVALUATION_FULL_VERIFY_TIMEOUT": "4321",
                    },
                )
            self.assertEqual(rc, 0)
            self.assertEqual(observed[:4], [str(launcher), "-d", "Ubuntu", "--"])
            self.assertIn("-i", observed)
            self.assertIn("AGENTOS_EVALUATION_EXECUTION_DOMAIN=windows-wsl:Ubuntu", observed)
            for name in ("CC", "HOSTCC", "HOST_CC"):
                self.assertIn(f"{name}={tools['host_cc']['path']}", observed)
            self.assertIn("EVALUATION_BOOTS=7", observed)
            self.assertIn("EVALUATION_FULL_VERIFY_TIMEOUT=4321", observed)
            self.assertIn("AGENT_TEST_DURATION_PROFILE=none", observed)
            self.assertIn("/mnt/e/repo/scripts/run-evaluation-suite.sh", observed)
            self.assertNotIn(str(root), observed[4:])

    def test_msys2_identity_rejects_real_cygwin_and_mingw(self) -> None:
        def uname(system: str) -> object:
            return types.SimpleNamespace(
                sysname=system,
                nodename="host",
                release="3.6.10",
                version="build",
                machine="x86_64",
            )

        for system in ("CYGWIN_NT-10.0-22631", "MINGW64_NT-10.0-22631"):
            with (
                self.subTest(system=system),
                mock.patch.object(platform_probe.os, "name", "posix"),
                mock.patch.object(platform_probe.sys, "platform", "cygwin"),
                mock.patch.object(platform_probe.os, "uname", return_value=uname(system)),
                self.assertRaisesRegex(
                    platform_probe.PlatformPreflightError, "Cygwin and Windows Python"
                ),
            ):
                platform_probe._current_msys2_identity({"MSYSTEM": "MSYS"})

        accepted = uname("MSYS_NT-10.0-22631")
        with (
            mock.patch.object(platform_probe.os, "name", "posix"),
            mock.patch.object(platform_probe.sys, "platform", "cygwin"),
            mock.patch.object(platform_probe.os, "uname", return_value=accepted),
        ):
            identity = platform_probe._current_msys2_identity({"MSYSTEM": "MSYS"})
        self.assertEqual(identity["windows_version"], "10.0-22631")
        self.assertNotIn("node", identity)

    def test_proc_hardware_identity_ignores_dynamic_frequency(self) -> None:
        cpuinfo = """\
processor : 0
model name : Example Stable CPU
cpu MHz : 1200.000

processor : 1
model name : Example Stable CPU
cpu MHz : 4900.000
"""
        identity = platform_probe._parse_proc_hardware_identity(
            cpuinfo, "MemTotal:       8388608 kB\nMemFree: 1 kB\n"
        )
        self.assertEqual(
            identity,
            {
                "cpu_model": "Example Stable CPU",
                "logical_cpu_count": 2,
                "memory_total_bytes": 8 * 1024 * 1024 * 1024,
                "source": platform_probe.HARDWARE_SOURCE,
            },
        )
        self.assertNotIn("MHz", json.dumps(identity, sort_keys=True))

    def test_proc_reader_uses_bound_posix_descriptor_not_host_path_policy(self) -> None:
        payload = b"processor\t: 0\nmodel name\t: Bound CPU\n"
        with (
            mock.patch.object(platform_probe.os, "open", return_value=17) as opener,
            mock.patch.object(
                platform_probe.os,
                "fstat",
                return_value=types.SimpleNamespace(st_mode=0o100444),
            ),
            mock.patch.object(platform_probe.os, "read", side_effect=[payload, b""]),
            mock.patch.object(platform_probe.os, "close") as closer,
            mock.patch.object(
                platform_probe,
                "_safe_regular_file",
                side_effect=AssertionError("procfs must not use Win32 reparse checks"),
            ),
        ):
            observed = platform_probe._read_bounded_proc_file(
                Path("/proc/cpuinfo"), "/proc/cpuinfo"
            )
        self.assertEqual(observed, payload.decode("utf-8"))
        opener.assert_called_once()
        closer.assert_called_once_with(17)

        with self.assertRaisesRegex(
            platform_probe.PlatformPreflightError, "bound procfs input"
        ):
            platform_probe._read_bounded_proc_file(
                Path("/proc/meminfo"), "/proc/cpuinfo"
            )

    def test_proc_hardware_identity_rejects_missing_or_malformed_fields(self) -> None:
        valid_cpu = "processor : 0\nmodel name : Example CPU\n"
        cases = (
            ("missing CPU", "model name : Example CPU\n", "MemTotal: 1024 kB\n"),
            (
                "duplicate CPU",
                valid_cpu + "processor : 0\nmodel name : Example CPU\n",
                "MemTotal: 1024 kB\n",
            ),
            (
                "mixed malformed CPU",
                valid_cpu + "processor : garbage\nmodel name : Example CPU\n",
                "MemTotal: 1024 kB\n",
            ),
            ("missing model", "processor : 0\n", "MemTotal: 1024 kB\n"),
            ("wrong unit", valid_cpu, "MemTotal: 1024 MB\n"),
            ("duplicate memory", valid_cpu, "MemTotal: 1024 kB\nMemTotal: 1024 kB\n"),
            ("zero memory", valid_cpu, "MemTotal: 0 kB\n"),
        )
        for name, cpuinfo, meminfo in cases:
            with self.subTest(name=name), self.assertRaises(
                platform_probe.PlatformPreflightError
            ):
                platform_probe._parse_proc_hardware_identity(cpuinfo, meminfo)

        arm = platform_probe._parse_proc_hardware_identity(
            "processor : 0\nProcessor : ARMv7 Processor rev 1\n",
            "MemTotal: 1024 kB\n",
        )
        self.assertEqual(arm["cpu_model"], "ARMv7 Processor rev 1")
        self.assertEqual(arm["logical_cpu_count"], 1)

    def test_wsl_hardware_probe_rejects_each_lowercase_processor_record(self) -> None:
        ast.parse(platform_probe._WSL_PROBE_PROGRAM)
        self.assertIn(
            'if raw_key.strip()=="processor":',
            platform_probe._WSL_PROBE_PROGRAM,
        )
        self.assertIn(
            'raise SystemExit("malformed procfs processor identifier")',
            platform_probe._WSL_PROBE_PROGRAM,
        )

    def test_hardware_schema_rejects_dynamic_or_noncanonical_claims(self) -> None:
        for mutation in (
            {**HARDWARE, "cpu_mhz": 4200},
            {**HARDWARE, "cpu_model": " Example  CPU "},
            {**HARDWARE, "logical_cpu_count": True},
            {**HARDWARE, "memory_total_bytes": 1001},
            {**HARDWARE, "source": "host-command"},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(
                platform_probe.PlatformPreflightError
            ):
                platform_probe._validate_hardware_identity(mutation)

    def test_msys2_python_cannot_be_replaced_by_windows_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary) / "python3"
            expected.write_bytes(b"python")
            observation = {
                "implementation": r"C:\\Python312\\python.exe",
                "runtime": "CPython",
                "msystem": "MSYS",
                "os_name": "nt",
                "platform": "win32",
                "python": "3.12.4",
                "system": "Windows",
                "isolated": 1,
                "no_site": 1,
                "safe_path": 1,
                "ignore_environment": 1,
            }
            raw = (
                "__AGENTOS_MSYS_PYTHON__"
                + json.dumps(observation, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            with self.assertRaisesRegex(
                platform_probe.PlatformPreflightError, "POSIX namespace"
            ):
                platform_probe._parse_msys_python_observation(
                    raw, expected_path=expected
                )

    def test_forged_msys2_marker_with_inherited_build_setting_is_rejected(self) -> None:
        tools = {
            label: {"path": f"/bound/{label}"}
            for label in platform_probe.MSYS_TOOL_LABELS
        }
        controlled_path = platform_probe._msys_controlled_path(tools)
        environment = {
            "AGENT_TEST_DURATION_PROFILE": "none",
            "AGENTOS_EVALUATION_EXECUTION_DOMAIN": "native-msys2",
            "BASH_BIN": tools["bash"]["path"],
            "BASH_ENV": "/tmp/injected",
            "CC": tools["host_cc"]["path"],
            "HOME": "/tmp",
            "HOSTCC": tools["host_cc"]["path"],
            "HOST_CC": tools["host_cc"]["path"],
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "MAKE_TOOL": tools["make"]["path"],
            "MSYSTEM": "MSYS",
            "PATH": controlled_path,
            "PYTHONHASHSEED": "0",
            "PYTHON_BIN": tools["python"]["path"],
            "QEMU": tools["qemu"]["path"],
            "SIZE_TOOL": tools["size"]["path"],
            "SYSTEMDRIVE": "C:",
            "TOOLPREFIX": "riscv-none-elf-",
            "TMPDIR": "/tmp",
            "TEMP": r"C:\tmp",
            "TMP": r"C:\tmp",
            "TZ": "UTC",
            "SYSTEMROOT": r"C:\Windows",
            "WINDIR": r"C:\Windows",
        }
        with (
            mock.patch.dict(platform_probe.os.environ, environment, clear=True),
            self.assertRaisesRegex(
                platform_probe.PlatformPreflightError, "unexpected=.*BASH_ENV"
            ),
        ):
            platform_probe._validate_msys_clean_entry(
                tools=tools,
                toolprefix="riscv-none-elf-",
                duration_profile_name="none",
                temporary_directory=Path("/tmp"),
                windows_temporary_directory=r"C:\tmp",
                windows_system_drive="C:",
            )

    def test_msys2_reexec_uses_bound_env_i_and_drops_host_injections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "scripts" / "run-evaluation-suite.sh"
            script.parent.mkdir()
            script.write_text("#!/usr/bin/env bash\n", encoding="ascii")
            tools = {
                label: {
                    "path": f"/bound/{label}",
                    "sha256": "a" * 64,
                }
                for label in platform_probe.MSYS_TOOL_LABELS
            }
            preflight = {
                "domain": "native-msys2",
                "duration_profile": {
                    "calibration_status": "not-applicable",
                    "name": "none",
                    "profile_id": "none",
                    "status": "disabled-different-runner",
                },
                "hardware": dict(HARDWARE),
                "repository": {"execution_path": str(root.resolve())},
                "runtime": {"path": "/usr/bin/msys-2.0.dll", "sha256": "b" * 64},
                "temporary_directory": "/tmp",
                "windows_system_drive": "C:",
                "windows_temporary_directory": r"C:\tmp",
                "toolprefix": "riscv-none-elf-",
                "tools": tools,
            }
            observed: list[str] = []

            def fake_process(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
                observed.extend(argv)
                return completed(argv, 0, b"")

            with (
                mock.patch.object(platform_probe, "_verify_bound_msys2_preflight"),
                mock.patch.object(platform_probe.subprocess, "run", side_effect=fake_process),
            ):
                rc = platform_probe.execute_in_msys2(
                    preflight=preflight,
                    repo=root,
                    script_relative="scripts/run-evaluation-suite.sh",
                    mode="full-verify",
                    host_environment={
                        "BASH_ENV": "/tmp/injected",
                        "MAKEFLAGS": "-j99",
                        "PYTHONPATH": "/tmp/injected",
                        "EVALUATION_BOOTS": "7",
                        "EVALUATION_FULL_VERIFY_TIMEOUT": "4321",
                    },
                )
            self.assertEqual(rc, 0)
            self.assertEqual(observed[:2], [tools["env"]["path"], "-i"])
            self.assertIn("AGENTOS_EVALUATION_EXECUTION_DOMAIN=native-msys2", observed)
            self.assertIn("MSYSTEM=MSYS", observed)
            self.assertIn("SYSTEMDRIVE=C:", observed)
            for name in ("CC", "HOSTCC", "HOST_CC"):
                self.assertIn(f"{name}={tools['host_cc']['path']}", observed)
            self.assertIn("EVALUATION_BOOTS=7", observed)
            self.assertIn("EVALUATION_FULL_VERIFY_TIMEOUT=4321", observed)
            self.assertIn("AGENT_TEST_DURATION_PROFILE=none", observed)
            self.assertIn("--noprofile", observed)
            self.assertIn("--norc", observed)
            self.assertFalse(any("BASH_ENV" in item for item in observed))
            self.assertFalse(any("MAKEFLAGS" in item for item in observed))
            self.assertFalse(any("PYTHONPATH" in item for item in observed))

    def test_msys2_runtime_change_between_probe_and_exec_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "msys-2.0.dll"
            runtime.write_bytes(b"runtime-v1")
            tool_directory = root / "tools"
            tool_directory.mkdir()
            tools: dict[str, dict[str, str]] = {}
            for label in platform_probe.MSYS_TOOL_LABELS:
                path = tool_directory / label
                path.write_bytes(label.encode("ascii"))
                tools[label] = {
                    "path": str(path),
                    "sha256": platform_probe._sha256(path),
                }
            preflight = {
                "domain": "native-msys2",
                "duration_profile": {
                    "calibration_status": "not-applicable",
                    "name": "none",
                    "profile_id": "none",
                    "status": "disabled-different-runner",
                },
                "hardware": dict(HARDWARE),
                "repository": {"execution_path": str(root.resolve())},
                "runtime": {
                    "path": str(runtime),
                    "sha256": platform_probe._sha256(runtime),
                },
                "temporary_directory": str(root),
                "windows_system_drive": "C:",
                "windows_temporary_directory": r"C:\tmp",
                "tools": tools,
            }
            with (
                mock.patch.object(platform_probe, "_current_msys2_identity"),
                mock.patch.object(
                    platform_probe, "_canonical_windows_system_drive",
                    return_value="C:",
                ),
                mock.patch.object(
                    platform_probe,
                    "_probe_proc_hardware_identity",
                    return_value=dict(HARDWARE),
                ),
            ):
                platform_probe._verify_bound_msys2_preflight(preflight, repo=root)
                runtime.write_bytes(b"runtime-v2")
                with self.assertRaisesRegex(
                    platform_probe.PlatformPreflightError, "runtime changed"
                ):
                    platform_probe._verify_bound_msys2_preflight(
                        preflight, repo=root
                    )


class TrustedPythonEntryTests(unittest.TestCase):
    @staticmethod
    def _load_launcher(path: Path, name: str):
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot load launcher fixture: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_python_310_flags_without_safe_path_are_supported(self) -> None:
        launchers = (
            TRUSTED_PYTHON_ENTRY,
            REPOSITORY / "scripts" / "trusted-python-child.py",
        )
        for index, launcher in enumerate(launchers):
            module = self._load_launcher(launcher, f"agentos_launcher_{index}")
            flags = types.SimpleNamespace(isolated=1, no_site=1)
            for marker, search_path, expected in (
                (True, ["/usr/lib/python3.10"], True),
                (False, ["/usr/lib/python3.10"], True),
                (False, [""], False),
            ):
                with self.subTest(launcher=launcher.name, marker=marker, path=search_path):
                    fake_sys = types.SimpleNamespace(
                        flags=flags,
                        path=search_path,
                        _agentos_safe_path=marker,
                    )
                    with mock.patch.object(module, "sys", fake_sys):
                        self.assertEqual(module._safe_startup_path(), expected)

    @staticmethod
    def _inside_formal_runtime() -> bool:
        return (
            getattr(sys, "_agentos_safe_path", False) is True
            and sys.executable == getattr(sys, "_base_executable", "")
            and sys.flags.isolated == 1 and sys.flags.no_site == 1
        )

    def _fixture(self, root: Path, body: str) -> Path:
        (root / "scripts").mkdir()
        (root / "host_tools").mkdir()
        shutil.copyfile(TRUSTED_PYTHON_ENTRY, root / "scripts" / TRUSTED_PYTHON_ENTRY.name)
        target = root / "host_tools" / "evaluation_campaign.py"
        target.write_text(body, encoding="ascii")
        return root / "scripts" / TRUSTED_PYTHON_ENTRY.name

    def test_capture_collector_rejects_nonisolated_direct_main(self) -> None:
        if self._inside_formal_runtime():
            self.skipTest("authenticated shim intentionally cannot expose a raw interpreter")
        result = subprocess.run(
            [sys.executable, str(REPOSITORY / "scripts" / "capture-final-evidence.py"), "--help"],
            cwd=REPOSITORY,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("formal entry requires", result.stderr)

    def test_isolated_launcher_blocks_source_shadow_from_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = self._fixture(root, "import json\nprint('safe entry')\n")
            sentinel = root / "source-shadow-executed"
            (root / "json.py").write_text(
                f"open({str(sentinel)!r}, 'w').write('executed')\n",
                encoding="utf-8",
            )
            environment = {**os.environ, "PYTHONPATH": str(root)}
            if not self._inside_formal_runtime():
                negative = subprocess.run(
                    [sys.executable, "-c", "import json"], cwd=root,
                    env=environment, check=False,
                )
                self.assertEqual(negative.returncode, 0)
                self.assertTrue(sentinel.is_file(), "attack fixture did not execute")
                sentinel.unlink()
            protected = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(launcher),
                    "host_tools/evaluation_campaign.py",
                ],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(protected.returncode, 0, protected.stderr)
            self.assertEqual(protected.stdout.strip(), "safe entry")
            self.assertFalse(sentinel.exists())

    def test_isolated_launcher_blocks_sitecustomize_before_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = self._fixture(root, "print('safe entry')\n")
            sentinel = root / "sitecustomize-executed"
            (root / "sitecustomize.py").write_text(
                f"open({str(sentinel)!r}, 'w').write('executed')\n",
                encoding="utf-8",
            )
            environment = {**os.environ, "PYTHONPATH": str(root)}
            if not self._inside_formal_runtime():
                negative = subprocess.run(
                    [sys.executable, "-c", "pass"], cwd=root / "scripts",
                    env=environment, check=False,
                )
                self.assertEqual(negative.returncode, 0)
                self.assertTrue(sentinel.is_file(), "sitecustomize fixture did not execute")
                sentinel.unlink()
            protected = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(launcher),
                    "host_tools/evaluation_campaign.py",
                ],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(protected.returncode, 0, protected.stderr)
            self.assertEqual(protected.stdout.strip(), "safe entry")
            self.assertFalse(sentinel.exists())

    def test_no_site_launcher_blocks_venv_startup_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = self._fixture(root, "print('safe entry')\n")
            environment_root = root / "venv"
            backing_interpreter = getattr(
                sys, "_agentos_backing_executable", sys.executable
            )
            creation = subprocess.run(
                [
                    str(backing_interpreter),
                    "-I",
                    "-S",
                    "-m",
                    "venv",
                    "--without-pip",
                    str(environment_root),
                ],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(creation.returncode, 0, creation.stderr)
            interpreter = next(
                (
                    candidate
                    for candidate in (
                        environment_root / "bin" / "python3",
                        environment_root / "bin" / "python",
                        environment_root / "Scripts" / "python.exe",
                    )
                    if candidate.is_file()
                ),
                None,
            )
            self.assertIsNotNone(interpreter, "venv interpreter is unavailable")
            assert interpreter is not None
            capability = subprocess.run(
                [str(interpreter), "-I", "-c", "pass"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                capability.returncode, 0,
                capability.stderr.decode("utf-8", "replace"),
            )
            site_packages = next(
                (
                    candidate
                    for candidate in (
                        environment_root / "Lib" / "site-packages",
                        *sorted((environment_root / "lib").glob("python*/site-packages")),
                        *sorted((environment_root / "lib64").glob("python*/site-packages")),
                    )
                    if candidate.is_dir()
                ),
                None,
            )
            self.assertIsNotNone(site_packages, "venv site-packages is unavailable")
            assert site_packages is not None
            site_sentinel = root / "sitecustomize-executed"
            pth_sentinel = root / "pth-executed"
            (site_packages / "sitecustomize.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(site_sentinel)!r}).write_text('executed', encoding='ascii')\n",
                encoding="utf-8",
            )
            (site_packages / "injected.pth").write_text(
                "import pathlib; "
                f"pathlib.Path({str(pth_sentinel)!r}).write_text('executed', encoding='ascii')\n",
                encoding="utf-8",
            )
            vulnerable = subprocess.run(
                [str(interpreter), "-I", "-c", "pass"], cwd=root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(vulnerable.returncode, 0, vulnerable.stderr)
            triggered = tuple(
                sentinel
                for sentinel in (site_sentinel, pth_sentinel)
                if sentinel.is_file()
            )
            self.assertTrue(triggered, "venv startup hook fixture did not execute")
            for sentinel in triggered:
                sentinel.unlink()
            protected = subprocess.run(
                [
                    str(interpreter),
                    "-I",
                    "-S",
                    str(launcher),
                    "host_tools/evaluation_campaign.py",
                ],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(protected.returncode, 0, protected.stderr)
            self.assertEqual(protected.stdout.strip(), "safe entry")
            self.assertFalse(site_sentinel.exists())
            self.assertFalse(pth_sentinel.exists())

    def test_isolated_launcher_blocks_unchecked_hash_local_pyc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = self._fixture(
                root,
                "import evidence_toolchain_attestation\nprint('safe entry')\n",
            )
            module = root / "host_tools" / "evidence_toolchain_attestation.py"
            module.write_text("print('trusted source')\n", encoding="ascii")
            sentinel = root / "cached-shadow-executed"
            poison = root / "poison.py"
            poison.write_text(
                f"open({str(sentinel)!r}, 'w').write('executed')\n",
                encoding="utf-8",
            )
            cache = root / "host_tools" / "__pycache__" / (
                f"evidence_toolchain_attestation.{sys.implementation.cache_tag}.pyc"
            )
            cache.parent.mkdir()
            py_compile.compile(
                str(poison),
                cfile=str(cache),
                dfile=str(module),
                doraise=True,
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
            )
            environment = {**os.environ, "PYTHONPATH": str(module.parent)}
            if not self._inside_formal_runtime():
                negative = subprocess.run(
                    [sys.executable, "-c", "import evidence_toolchain_attestation"],
                    cwd=root / "scripts", env=environment, check=False,
                )
                self.assertEqual(negative.returncode, 0)
                self.assertTrue(sentinel.is_file(), "cached attack fixture did not execute")
                sentinel.unlink()
            protected = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(launcher),
                    "host_tools/evaluation_campaign.py",
                ],
                cwd=root,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(protected.returncode, 0, protected.stderr)
            self.assertEqual(protected.stdout.splitlines(), ["trusted source", "safe entry"])
            self.assertFalse(sentinel.exists())

    @unittest.skipUnless(os.name == "posix", "shell routing fixture requires POSIX")
    def test_package_shell_routes_bundle_through_isolated_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = root / "calls"
            fake_python = root / "python"
            fake_python.write_text(
                "#!/usr/bin/bash\nprintf '%s\\n' \"$@\" >\"${CALLS}\"\n",
                encoding="ascii",
            )
            fake_python.chmod(0o755)
            bash_candidates = (
                Path(sys.executable).resolve().with_name("bash.exe"),
                Path(sys.executable).resolve().with_name("bash"),
            )
            bash = next((path for path in bash_candidates if path.is_file()), None)
            if bash is None:
                self.skipTest("Bash is unavailable")
            result = subprocess.run(
                [
                    str(bash),
                    "scripts/package-evaluation-evidence.sh",
                    "verify",
                    "evidence/releases/fixture",
                ],
                cwd=REPOSITORY,
                env={
                    **os.environ,
                    "PYTHON_BIN": str(fake_python),
                    "CALLS": str(calls),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:" + os.environ.get("PATH", ""),
                },
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = calls.read_text(encoding="utf-8").splitlines()
            self.assertEqual(arguments[:2], ["-I", "-S"])
            self.assertEqual(
                Path(arguments[2]).resolve(),
                (REPOSITORY / "scripts" / "trusted-python-entry.py").resolve(),
            )
            self.assertEqual(arguments[3:6], [
                "host_tools/evaluation_bundle.py", "verify", "--bundle",
            ])
            self.assertIn("--contract-root", arguments)
            contract_index = arguments.index("--contract-root")
            self.assertLess(contract_index + 1, len(arguments))
            self.assertTrue(arguments[contract_index + 1])

    def test_msys2_hardware_change_between_probe_and_exec_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "msys-2.0.dll"
            runtime.write_bytes(b"runtime")
            tools: dict[str, dict[str, str]] = {}
            for label in platform_probe.MSYS_TOOL_LABELS:
                path = root / label
                path.write_bytes(label.encode("ascii"))
                tools[label] = {
                    "path": str(path),
                    "sha256": platform_probe._sha256(path),
                }
            preflight = {
                "domain": "native-msys2",
                "duration_profile": {
                    "calibration_status": "not-applicable",
                    "name": "none",
                    "profile_id": "none",
                    "status": "disabled-different-runner",
                },
                "hardware": dict(HARDWARE),
                "repository": {"execution_path": str(root.resolve())},
                "runtime": {
                    "path": str(runtime),
                    "sha256": platform_probe._sha256(runtime),
                },
                "temporary_directory": str(root),
                "windows_system_drive": "C:",
                "windows_temporary_directory": r"C:\tmp",
                "tools": tools,
            }
            changed = {**HARDWARE, "logical_cpu_count": 8}
            with (
                mock.patch.object(platform_probe, "_current_msys2_identity"),
                mock.patch.object(
                    platform_probe, "_canonical_windows_system_drive",
                    return_value="C:",
                ),
                mock.patch.object(
                    platform_probe,
                    "_probe_proc_hardware_identity",
                    return_value=changed,
                ),
                self.assertRaisesRegex(
                    platform_probe.PlatformPreflightError,
                    "hardware identity changed",
                ),
            ):
                platform_probe._verify_bound_msys2_preflight(preflight, repo=root)


if __name__ == "__main__":
    unittest.main()
