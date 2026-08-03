#!/usr/bin/env python3
"""Regression tests for the evaluation collection campaign."""

from __future__ import annotations

import json
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import evaluation_campaign as campaign
import evaluation_contract as contract
import evaluation_platform as platform_probe
import agenteval_measurement_source_contract as measurement_source
import plain_ucore_action_runner as action_runner
import scenario_timing_source_contract as scenario_timing_source


COMMIT = "a" * 40
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _measurement_receipt(root: Path) -> dict[str, object]:
    records = []
    for index, relative in enumerate(measurement_source._receipt_source_paths(), 1):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == measurement_source.EVALUATION_SUITE_SOURCE_PATH:
            raw = path.read_bytes()
        elif relative in {
            "host_tools/evaluation_scenario.py",
            "host_tools/check_seeded_action_state.py",
        }:
            raw = f"# {path.name}{os.linesep}".encode("utf-8")
        else:
            raw = f"timing-source-{index}:{relative}\n".encode("utf-8")
        path.write_bytes(raw)
        records.append({
            "bytes": len(raw),
            "path": relative,
            "sha256": campaign._sha256(path),
        })
    return {
        "contract_versions": {
            "functional": measurement_source.FUNCTIONAL_CONTRACT_VERSION,
            "functional_compile": (
                measurement_source.FUNCTIONAL_COMPILE_CONTRACT_VERSION
            ),
            "micro": measurement_source.CONTRACT_VERSION,
            "policy": measurement_source.POLICY_INVENTORY_SCHEMA,
            "scenario": scenario_timing_source.CONTRACT_VERSION,
        },
        "formal_boot_count": measurement_source.FORMAL_BOOT_COUNT,
        "policy_inventory": measurement_source.measurement_source_policy_inventory(),
        "schema": measurement_source.RECEIPT_SCHEMA,
        "source_commit": COMMIT,
        "sources": records,
        "stop_rule": measurement_source.STOP_RULE,
    }


def _test_hardware() -> dict[str, object]:
    try:
        return platform_probe._probe_proc_hardware_identity()
    except platform_probe.PlatformPreflightError:
        # Native Windows is not a formal execution domain, but these unit
        # fixtures still need a canonical proof before their execution call is
        # replaced by mocks.
        return {
            "cpu_model": "Unit Test CPU",
            "logical_cpu_count": 4,
            "memory_total_bytes": 8 * 1024 * 1024 * 1024,
            "source": platform_probe.HARDWARE_SOURCE,
        }


def _platform_proof(root: Path) -> dict[str, object]:
    tools: dict[str, dict[str, str]] = {}
    directory = root / "platform-tools"
    directory.mkdir(parents=True, exist_ok=True)
    for label in platform_probe.TOOL_LABELS:
        argv0 = "cc" if label == "host_cc" else label
        path = directory / argv0
        path.write_bytes(f"platform:{label}\n".encode("ascii"))
        path.chmod(0o755)
        tools[label] = {
            "argv0": argv0,
            "path": str(path.resolve()),
            "sha256": campaign._sha256(path),
            "version": f"{label} 1",
        }
    return {
        "distribution": None,
        "domain": "native-linux",
        "duration_profile": {
            "calibration_status": "not-applicable",
            "name": "none",
            "profile_id": "none",
            "status": "disabled-different-runner",
        },
        "entry_domain": "native-linux",
        "hardware": _test_hardware(),
        "kind": platform_probe.KIND,
        "launcher": dict(tools["bash"]),
        "repository": {
            "execution_path": str(root.resolve()),
            "host_path": str(root.resolve()),
        },
        "requested_host_cc": "cc",
        "schema_version": platform_probe.SCHEMA_VERSION,
        "status": "ready",
        "toolprefix": "/opt/riscv/bin/riscv64-linux-gnu-",
        "tools": tools,
    }


def _msys_platform_proof(root: Path) -> dict[str, object]:
    proof = _platform_proof(root)
    tools = proof["tools"]
    assert isinstance(tools, dict)
    directory = root / "platform-tools"
    for label in platform_probe.MSYS_EXTRA_TOOL_LABELS:
        path = directory / label
        path.write_bytes(f"platform:{label}\n".encode("ascii"))
        path.chmod(0o755)
        tools[label] = {
            "argv0": label,
            "path": str(path.resolve()),
            "sha256": campaign._sha256(path),
            "version": f"{label} 1",
        }
    runtime = directory / "msys-2.0.dll"
    runtime.write_bytes(b"fixture msys runtime\n")
    proof.update({
        "domain": "native-msys2",
        "entry_domain": "native-msys2",
        "runtime": {
            "path": str(runtime.resolve()),
            "sha256": campaign._sha256(runtime),
            "version": "fixture-msys-runtime-1",
        },
        "temporary_directory": "/r/tmp",
        "uname": {
            "command": "MSYS_NT-10.0-26200 fixture",
            "machine": "x86_64",
            "release": "fixture",
            "system": "MSYS_NT-10.0-26200",
            "version": "fixture",
            "windows_version": "10.0-26200",
        },
        "windows_temporary_directory": r"R:\tmp",
        "windows_system_drive": "C:",
    })
    return proof


def _scenario_environment(host_cc: str | None = None) -> dict[str, object]:
    def tool(argv0: str, number: str) -> dict[str, str]:
        return {
            "argv0": argv0,
            "path": f"/usr/bin/{Path(argv0).name}",
            "sha256": number * 64,
            "version": f"{argv0} 1",
        }

    tools = {
        "assembler": tool("riscv64-linux-gnu-as", "2"),
        "bash": tool("bash", "7"),
        "compiler": tool("riscv64-linux-gnu-gcc", "8"),
        "env": tool("env", "b"),
        "host_cc": tool("cc", "1"),
        "linker": tool("riscv64-linux-gnu-ld", "c"),
        "make": tool("make", "9"),
        "objcopy": tool("riscv64-linux-gnu-objcopy", "d"),
        "objdump": tool("riscv64-linux-gnu-objdump", "e"),
        "qemu": tool("qemu-system-riscv64", "a"),
        "timeout": tool("timeout", "f"),
    }
    if host_cc is not None:
        host_path = Path(host_cc)
        tools["host_cc"] = {
            "argv0": host_cc,
            "path": host_cc,
            "sha256": campaign._sha256(host_path),
            "version": "host_cc 1",
        }
    if os.name == "nt":
        launcher = {
            "argv0": "wsl.exe",
            "path": r"C:\Windows\System32\wsl.exe",
            "sha256": "6" * 64,
            "version": "wsl.exe 1",
        }
        domain = "wsl-clean-shell"
        launcher_argv = [
            launcher["path"],
            "-d",
            "Ubuntu",
            "--",
            tools["env"]["path"],
            "-i",
        ]
    else:
        launcher = dict(tools["env"])
        domain = "native-clean-shell"
        launcher_argv = [tools["env"]["path"], "-i"]
    return {
        "clean_environment": campaign._scenario_clean_environment(tools),
        "domain": domain,
        "launcher": launcher,
        "launcher_argv": launcher_argv,
        "tools": tools,
    }


def _bound_scenario_environment(*_args: object, **kwargs: object) -> dict[str, object]:
    return _scenario_environment(str(kwargs["host_cc"]))


def _create(
    root: Path,
    *,
    boots: int = 7,
    clean: bool = True,
    artifact_root: str = "results/evaluation/runs/run-1",
    run_id: str = "run-1",
    timeout_seconds: int = campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
) -> Path:
    manifest = root / artifact_root / "campaign.json"
    suite = root / "ci" / "evaluation-suite.json"
    suite.parent.mkdir(parents=True, exist_ok=True)
    suite.write_bytes((PROJECT_ROOT / "ci" / "evaluation-suite.json").read_bytes())

    def fake_probe(name: str, _args: list[str], _repo: Path) -> dict[str, str]:
        path = root / "tools" / Path(name).name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"tool:{name}\n".encode("utf-8"))
        return {
            "argv0": name,
            "path": str(path),
            "sha256": campaign._sha256(path),
            "version": f"{name} 1",
        }

    platform_proof = _platform_proof(root)
    measurement_receipt = _measurement_receipt(root)
    with (
        mock.patch.object(campaign, "repository_identity", return_value=(COMMIT, clean)),
        mock.patch.object(
            campaign,
            "build_measurement_source_receipt",
            return_value=measurement_receipt,
        ),
        mock.patch.object(
            campaign,
            "require_formal_execution_domain",
            return_value=platform_proof,
        ),
        mock.patch.object(
            campaign,
            "probe_executable",
            side_effect=fake_probe,
        ),
    ):
        value = campaign.create_campaign(
            repo=root,
            output=manifest,
            run_id=run_id,
            requested_boots=boots,
            toolprefix="riscv64-linux-gnu-",
            qemu="qemu-system-riscv64",
            python_bin="python3",
            shell_bin="bash",
            host_cc="cc",
            duration_profile="none",
            timeout_seconds=timeout_seconds,
        )
    manifest.parent.joinpath("preflight.log").write_text(
        campaign.format_preflight_receipt(value),
        encoding="ascii",
        newline="\n",
    )
    return manifest


def _guest_log(challenge: str) -> str:
    lines = [f"agenteval_ucore: challenge={challenge}"]
    experiments = (
        ("file_query_path_index", ("path_walk", "index"), (8, 24, 48, 96)),
        ("file_query_table_ablation", ("scan", "index"), (24, 64, 96)),
        ("tool_batch", ("scalar", "batch"), (24, 64, 96)),
        ("context_access", ("syscall", "direct"), (24, 64, 96)),
    )
    for experiment, variants, loads in experiments:
        for load in loads:
            for pair in range(1, 8):
                order = (
                    "AB"
                    if (pair & 1) == (int(challenge, 16) & 1)
                    else "BA"
                )
                ordered = variants if order == "AB" else tuple(reversed(variants))
                for variant in ordered:
                    cache = {
                        "path_walk": "warm-paths",
                        "scan": "forced-scan",
                        "index": "ready-index",
                    }.get(variant, "warm")
                    work = load * load if experiment.startswith("file_query") else load
                    lines.append(
                        "agenteval_ucore: sample schema=2 "
                        f"experiment={experiment} load={load} pair={pair} "
                        f"variant={variant} order={order} cache={cache} "
                        f"operations={load} work_units={work} duration_us=10 "
                        "workload_fingerprint=1111111111111111 "
                        "result_fingerprint=2222222222222222 status=measured"
                    )
    lines.append("agenteval_ucore: worker passed")
    return "\n".join(lines) + "\n"


def _write_scenario_preflight(manifest: Path, value: dict[str, object]) -> Path:
    receipt = manifest.parent.parent / "scenario-preflight.log"
    receipt.write_text(
        campaign.format_preflight_receipt(value),
        encoding="ascii",
        newline="\n",
    )
    return receipt


class CampaignSourceGateIntegrationTests(unittest.TestCase):
    def _repo(self, base: Path) -> tuple[Path, str]:
        root = base / "repo"
        (root / "os").mkdir(parents=True)
        (root / "user" / "src").mkdir(parents=True)
        (root / "Makefile").write_text("all:\n\t@true\n", encoding="ascii")
        (root / "os" / "kernel.c").write_text("int kernel;\n", encoding="ascii")
        (root / "user" / "src" / "app.c").write_text("int app;\n", encoding="ascii")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "campaign@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Campaign Test"], cwd=root, check=True
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=root, check=True)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        return root, commit

    def test_campaign_gate_rejects_info_exclude_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, commit = self._repo(Path(temporary))
            (root / ".git" / "info" / "exclude").write_text(
                "os/hidden.c\nuser/src/hidden.c\n", encoding="ascii"
            )
            for relative in ("os/hidden.c", "user/src/hidden.c"):
                hidden = root / relative
                hidden.write_text("int hidden;\n", encoding="ascii")
                self.assertEqual(
                    subprocess.check_output(
                        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                        cwd=root,
                    ),
                    b"",
                )
                with self.assertRaisesRegex(campaign.CampaignError, "source gate"):
                    campaign._evaluation_source_gate(
                        root,
                        commit,
                        "results/evaluation/runs/formal-fixture",
                        "before boot",
                    )
                hidden.unlink()

    def test_campaign_prepare_removes_preseeded_dependency_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, commit = self._repo(Path(temporary))
            poison = root / "build" / "os" / "kernel.d"
            poison.parent.mkdir(parents=True)
            poison.write_text("$(error poisoned)\n", encoding="ascii")
            campaign._prepare_evaluation_build_tree(
                root,
                commit,
                "results/evaluation/runs/formal-fixture",
                "micro boot-01",
            )
            self.assertFalse((root / "build").exists())

    def test_artifact_root_cannot_authorize_a_source_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, commit = self._repo(Path(temporary))
            (root / ".git" / "info" / "exclude").write_text(
                "os/hidden.c\n", encoding="ascii"
            )
            (root / "os" / "hidden.c").write_text("int hidden;\n", encoding="ascii")
            with self.assertRaisesRegex(
                campaign.CampaignError, "source gate"
            ):
                campaign._evaluation_source_gate(
                    root,
                    commit,
                    "os/runs/formal-fixture",
                    "before boot",
                )


class CampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        source_gate = mock.patch.object(campaign, "_evaluation_source_gate")
        prepare = mock.patch.object(campaign, "_prepare_evaluation_build_tree")
        source_gate.start()
        prepare.start()
        self.addCleanup(prepare.stop)
        self.addCleanup(source_gate.stop)
        if os.name == "nt" or sys.platform == "cygwin":
            patcher = mock.patch.object(
                platform_probe,
                "_probe_proc_hardware_identity",
                return_value=_test_hardware(),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_formal_campaigns_reject_optional_stopping_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(campaign.CampaignError, "fixed 7-boot"):
                _create(root, boots=8)

            micro = _create(root)
            output = root / "results" / "evaluation" / "scenario-8.json"
            with self.assertRaisesRegex(campaign.CampaignError, "fixed 7-boot"):
                campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=output,
                    requested_boots=8,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )

    def test_repository_identity_rejects_hidden_index_flags(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)

                def run_git(*arguments: str) -> None:
                    subprocess.run(
                        ["git", *arguments],
                        cwd=root,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )

                run_git("init", "-q")
                run_git("config", "user.email", "campaign@example.invalid")
                run_git("config", "user.name", "Campaign Test")
                source = root / "source.c"
                source.write_text("int value = 1;\n", encoding="ascii")
                run_git("add", "source.c")
                run_git("commit", "-q", "-m", "source")
                run_git("update-index", flag, "source.c")
                source.write_text("int value = 2;\n", encoding="ascii")

                with self.assertRaisesRegex(
                    campaign.CampaignError, "hidden or nonstandard"
                ):
                    campaign.repository_identity(root)

    def test_measurement_source_receipt_is_strict_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            receipt = value["measurement_source_receipt"]
            self.assertEqual(receipt["source_commit"], COMMIT)
            self.assertEqual(
                receipt["stop_rule"], measurement_source.STOP_RULE
            )

            forged = json.loads(json.dumps(value))
            forged["measurement_source_receipt"]["unexpected"] = True
            with self.assertRaisesRegex(
                campaign.CampaignError, "measurement source receipt"
            ):
                campaign.validate_campaign(forged)

            source = root / receipt["sources"][0]["path"]
            source.write_bytes(source.read_bytes() + b"changed")
            with self.assertRaisesRegex(
                campaign.CampaignError, "measurement timing sources changed"
            ):
                campaign._require_measurement_receipt(
                    root, COMMIT, receipt, "before boot"
                )

    def test_platform_hardware_proof_is_strict_and_campaign_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            original = json.loads(manifest.read_text(encoding="utf-8"))
            campaign.validate_campaign(original)
            hardware = original["platform"]["hardware"]
            self.assertEqual(
                set(hardware),
                {
                    "cpu_model", "logical_cpu_count", "memory_total_bytes",
                    "source",
                },
            )
            self.assertEqual(hardware["source"], platform_probe.HARDWARE_SOURCE)

            original_hash = campaign._sha256(manifest)
            changed = json.loads(json.dumps(original))
            changed["platform"]["hardware"]["cpu_model"] += " changed"
            changed_path = root / "changed-campaign.json"
            changed_path.write_text(
                json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            self.assertNotEqual(original_hash, campaign._sha256(changed_path))

            mutations = []
            missing = json.loads(json.dumps(original))
            del missing["platform"]["hardware"]
            mutations.append(missing)
            dynamic = json.loads(json.dumps(original))
            dynamic["platform"]["hardware"]["cpu_mhz"] = 4200
            mutations.append(dynamic)
            malformed = json.loads(json.dumps(original))
            malformed["platform"]["hardware"]["memory_total_bytes"] = "8 GiB"
            mutations.append(malformed)
            legacy = json.loads(json.dumps(original))
            legacy["platform"]["schema_version"] = 1
            mutations.append(legacy)
            for value in mutations:
                with self.subTest(value=value["platform"]), self.assertRaises(
                    campaign.CampaignError
                ):
                    campaign.validate_campaign(value)

    def test_hardware_change_before_boot_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            expected = value["platform"]["hardware"]
            changed_count = (
                expected["logical_cpu_count"] + 1
                if expected["logical_cpu_count"] < platform_probe.MAX_LOGICAL_CPU_COUNT
                else expected["logical_cpu_count"] - 1
            )
            changed = {**expected, "logical_cpu_count": changed_count}
            with (
                mock.patch.object(
                    platform_probe,
                    "_probe_proc_hardware_identity",
                    return_value=changed,
                ),
                self.assertRaisesRegex(
                    campaign.CampaignError, "hardware binding changed"
                ),
            ):
                campaign._verify_native_execution_binding(
                    root, value, value["boots"][0]
                )

    def test_scenario_binds_and_revalidates_micro_platform_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro_path = _create(root)
            micro = json.loads(micro_path.read_text(encoding="utf-8"))
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            output = micro_path.parent / "scenario" / "scenario-plan.json"
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                value = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro_path,
                    output=output,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            self.assertEqual(value["schema_version"], campaign.SCENARIO_SCHEMA_VERSION)
            self.assertEqual(value["platform"], micro["platform"])
            self.assertEqual(
                value["measurement_source_receipt"],
                micro["measurement_source_receipt"],
            )
            self.assertEqual(
                value["run"]["platform_sha256"],
                campaign._canonical_sha256(micro["platform"]),
            )

            expected = value["platform"]["hardware"]
            changed_count = (
                expected["logical_cpu_count"] + 1
                if expected["logical_cpu_count"] < platform_probe.MAX_LOGICAL_CPU_COUNT
                else expected["logical_cpu_count"] - 1
            )
            changed = {**expected, "logical_cpu_count": changed_count}
            with (
                mock.patch.object(
                    platform_probe,
                    "_probe_proc_hardware_identity",
                    return_value=changed,
                ),
                self.assertRaisesRegex(
                    campaign.CampaignError, "hardware binding changed"
                ),
            ):
                campaign._verify_scenario_execution_binding(
                    root, value, value["boots"][0]
                )

            tampered = json.loads(json.dumps(value))
            tampered["platform"]["hardware"]["logical_cpu_count"] = changed_count
            with self.assertRaisesRegex(
                campaign.CampaignError, "platform proof differs from its hash"
            ):
                campaign.validate_scenario_campaign(tampered)

    def test_portable_absolute_paths_and_process_path_are_host_independent(self) -> None:
        for valid in (
            "/usr/bin/python3",
            "C:\\Tools\\python.exe",
            "D:/Tools/python.exe",
            "\\\\server\\share\\python.exe",
        ):
            self.assertTrue(campaign._is_portable_absolute_path(valid), valid)
        for invalid in (
            "python3",
            "../bin/python3",
            "/usr/../bin/python3",
            "C:\\Tools/../python.exe",
            "C:python.exe",
        ):
            self.assertFalse(campaign._is_portable_absolute_path(invalid), invalid)
        self.assertEqual(
            campaign._trusted_process_path(
                [{"path": "/usr/bin/python3"}, {"path": "/opt/qemu/bin/qemu"}]
            ),
            "/usr/bin:/opt/qemu/bin",
        )
        self.assertEqual(
            campaign._trusted_process_path(
                [{"path": "C:\\Tools\\python.exe"}, {"path": "D:\\Qemu\\qemu.exe"}]
            ),
            "C:\\Tools;D:\\Qemu",
        )
        with self.assertRaisesRegex(campaign.CampaignError, "mix"):
            campaign._trusted_process_path(
                [{"path": "/usr/bin/python3"}, {"path": "C:\\Qemu\\qemu.exe"}]
            )

    def test_host_cc_resolution_rejects_a_path_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted = root / "trusted"
            attacker = root / "attacker"
            trusted.mkdir()
            attacker.mkdir()
            trusted_cc = trusted / "cc"
            attacker_cc = attacker / "cc"
            for path, content in (
                (trusted_cc, b"trusted\n"),
                (attacker_cc, b"attacker\n"),
            ):
                path.write_bytes(content)
                path.chmod(0o755)
            identity = {"argv0": "cc", "path": str(trusted_cc)}
            campaign._verify_bound_host_cc_resolution(
                identity, str(trusted)
            )
            with self.assertRaisesRegex(
                campaign.CampaignError, "differs from the attested"
            ):
                campaign._verify_bound_host_cc_resolution(
                    identity, os.pathsep.join((str(attacker), str(trusted)))
                )

    def test_campaign_schema_accepts_foreign_posix_execution_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = _create(Path(temporary))
            value = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(value["run"]["execution_domain"], "native-linux")
            paths = {
                "assembler": "/opt/riscv/bin/riscv64-linux-gnu-as",
                "bash": "/opt/tools/bash",
                "compiler": "/opt/riscv/bin/riscv64-linux-gnu-gcc",
                "git": "/opt/tools/git",
                "linker": "/opt/riscv/bin/riscv64-linux-gnu-ld",
                "make": "/opt/tools/make",
                "objcopy": "/opt/riscv/bin/riscv64-linux-gnu-objcopy",
                "objdump": "/opt/riscv/bin/riscv64-linux-gnu-objdump",
                "python": "/opt/tools/python3",
                "qemu": "/opt/qemu/qemu-system-riscv64",
            }
            for label, path in paths.items():
                value["environment"][label]["path"] = path
            for boot in value["boots"]:
                boot["command_argv"][:2] = [
                    paths["bash"],
                    "/src/agentos/scripts/run-agent-tests.sh",
                ]
                boot["command_environment"] = campaign._micro_boot_environment(
                    value["environment"],
                    value["platform"],
                    boot["challenge"],
                    boot["guest_log"],
                )
            campaign.validate_campaign(value)

    def test_scenario_schema_accepts_foreign_posix_host_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            output = micro.parent / "scenario" / "scenario-plan.json"
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                value = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=output,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            protocol = value["protocol"]
            protocol["python_bin"] = "/opt/host/python3"
            protocol["git_bin"] = "/opt/host/git"
            execution_environment = protocol["execution_environment"]
            execution_environment["domain"] = "native-clean-shell"
            execution_environment["launcher"] = dict(
                execution_environment["tools"]["env"]
            )
            execution_environment["launcher_argv"] = [
                execution_environment["tools"]["env"]["path"],
                "-i",
            ]
            value["run"]["scenario_environment_sha256"] = campaign._canonical_sha256(
                protocol["execution_environment"]
            )
            micro_environment = {
                "python": {"path": protocol["python_bin"], "sha256": protocol["python_sha256"]},
                "git": {"path": protocol["git_bin"], "sha256": protocol["git_sha256"]},
            }
            for boot in value["boots"]:
                boot["command_argv"][:2] = [
                    protocol["python_bin"],
                    "/src/agentos/host_tools/check_seeded_action_state.py",
                ]
                boot["command_environment"] = campaign._scenario_boot_environment(
                    micro_environment, protocol["execution_environment"]
                )
            campaign.validate_scenario_campaign(value)

    def test_scenario_probe_uses_an_empty_non_login_shell(self) -> None:
        paths = [
            "/opt/riscv/bin/riscv64-linux-gnu-as",
            "/usr/bin/bash",
            "/opt/riscv/bin/riscv64-linux-gnu-gcc",
            "/usr/bin/env",
            "/usr/bin/cc",
            "/opt/riscv/bin/riscv64-linux-gnu-ld",
            "/usr/bin/make",
            "/opt/riscv/bin/riscv64-linux-gnu-objcopy",
            "/opt/riscv/bin/riscv64-linux-gnu-objdump",
            "/usr/bin/qemu-system-riscv64",
            "/usr/bin/timeout",
        ]
        outputs = [
            "\n".join(
                (
                    f"__AGENTEVAL_PATH__{path}",
                    "__AGENTEVAL_SHA256__" + f"{number:x}" * 64,
                    f"__AGENTEVAL_VERSION__tool-{number}",
                )
            )
            for number, path in enumerate(paths, 1)
        ]
        current_directory = Path.cwd()
        with (
            mock.patch.object(campaign.os, "name", "posix"),
            mock.patch.object(campaign, "_run", side_effect=outputs) as run,
        ):
            environment = campaign._probe_scenario_environment(
                current_directory,
                wsl_distro="Ubuntu",
                toolprefix="/opt/riscv/bin/riscv64-linux-gnu-",
                qemu="qemu-system-riscv64",
                host_cc="/usr/bin/cc",
            )
        self.assertEqual(environment["domain"], "native-clean-shell")
        self.assertEqual(environment["launcher_argv"], ["/usr/bin/env", "-i"])
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[0:2], ["/usr/bin/env", "-i"])
            self.assertEqual(
                command[2:7],
                [
                    "HOME=/tmp",
                    f"PATH={campaign.SCENARIO_CLEAN_PATH}",
                    "LANG=C",
                    "LC_ALL=C",
                    "TZ=UTC",
                ],
            )
            self.assertEqual(
                command[7:11],
                ["/bin/bash", "--noprofile", "--norc", "-c"],
            )
            self.assertNotIn("-l", command)
            self.assertNotIn("-lc", command)

        msys_bootstrap_path = "/usr/bin:/opt/riscv/bin"
        with (
            mock.patch.object(campaign.os, "name", "posix"),
            mock.patch.dict(
                campaign.os.environ,
                {
                    "AGENTOS_EVALUATION_EXECUTION_DOMAIN": "native-msys2",
                    "BASH_BIN": "/usr/bin/bash",
                    "PATH": msys_bootstrap_path,
                },
                clear=True,
            ),
            mock.patch.object(campaign, "_run", side_effect=outputs) as msys_run,
        ):
            msys_environment = campaign._probe_scenario_environment(
                current_directory,
                wsl_distro="Ubuntu",
                toolprefix="/opt/riscv/bin/riscv64-linux-gnu-",
                qemu="qemu-system-riscv64",
                host_cc="/usr/bin/cc",
                posix_temporary="/r/tmp",
                native_temporary=r"R:\tmp",
                system_drive="C:",
            )
        self.assertEqual(msys_environment["domain"], "native-msys2-clean-shell")
        self.assertEqual(
            msys_environment["clean_environment"]["SYSTEMDRIVE"], "C:"
        )
        for call in msys_run.call_args_list:
            command = call.args[0]
            self.assertIn("SYSTEMDRIVE=C:", command)
            self.assertLess(command.index("SYSTEMDRIVE=C:"), command.index("/usr/bin/bash"))

    def test_scenario_environment_injection_mutations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                original = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=micro.parent / "scenario" / "scenario-plan.json",
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )

            injected_flags = json.loads(json.dumps(original))
            injected_flags["protocol"]["execution_environment"][
                "clean_environment"
            ]["MAKEFLAGS"] = "-j99 --eval=malicious"
            injected_flags["run"]["scenario_environment_sha256"] = (
                campaign._canonical_sha256(
                    injected_flags["protocol"]["execution_environment"]
                )
            )
            with self.assertRaisesRegex(
                campaign.CampaignError, "clean environment"
            ):
                campaign.validate_scenario_campaign(injected_flags)

            login_shell = json.loads(json.dumps(original))
            login_shell["protocol"]["execution_environment"]["launcher_argv"] += [
                "/usr/bin/bash",
                "-lc",
            ]
            login_shell["run"]["scenario_environment_sha256"] = (
                campaign._canonical_sha256(
                    login_shell["protocol"]["execution_environment"]
                )
            )
            with self.assertRaisesRegex(campaign.CampaignError, "launcher"):
                campaign.validate_scenario_campaign(login_shell)

            inherited_process_flag = json.loads(json.dumps(original))
            inherited_process_flag["boots"][0]["command_environment"][
                "MAKEFLAGS"
            ] = "-j99"
            with self.assertRaisesRegex(
                campaign.CampaignError, "process environment differs"
            ):
                campaign.validate_scenario_campaign(inherited_process_flag)

            changed_host_cc = json.loads(json.dumps(original))
            changed_host_cc["protocol"]["execution_environment"][
                "clean_environment"
            ]["CC"] = "/usr/bin/clang"
            changed_host_cc["run"]["scenario_environment_sha256"] = (
                campaign._canonical_sha256(
                    changed_host_cc["protocol"]["execution_environment"]
                )
            )
            with self.assertRaisesRegex(
                campaign.CampaignError, "clean environment differs"
            ):
                campaign.validate_scenario_campaign(changed_host_cc)

            missing_assembler = json.loads(json.dumps(original))
            del missing_assembler["protocol"]["execution_environment"]["tools"][
                "assembler"
            ]
            missing_assembler["run"]["scenario_environment_sha256"] = (
                campaign._canonical_sha256(
                    missing_assembler["protocol"]["execution_environment"]
                )
            )
            with self.assertRaisesRegex(
                campaign.CampaignError, "invalid scenario execution-domain tools"
            ):
                campaign.validate_scenario_campaign(missing_assembler)

    def test_command_timeout_fails_closed_with_diagnostics(self) -> None:
        error = subprocess.TimeoutExpired(
            ["probe-tool", "--version"], 3, output=b"partial output\n"
        )
        with mock.patch.object(campaign.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(
                campaign.CampaignError, "timed out.*partial output"
            ):
                campaign._run(["probe-tool", "--version"], Path.cwd(), timeout_seconds=3)

    def test_scenario_pair_deadline_covers_both_targets_and_all_phases(self) -> None:
        deadline = campaign.scenario_pair_deadline_contract(600)
        self.assertEqual(deadline["contract"], "paired-scenario-deadline-v1")
        self.assertEqual(deadline["target_count"], 2)
        self.assertEqual(deadline["target_deadline"]["phases"], ["clean", "build", "guest"])
        self.assertEqual(deadline["target_deadline"]["phase_timeout_seconds"], 630)
        self.assertEqual(deadline["target_deadline"]["server_deadline_seconds"], 1900)
        self.assertEqual(deadline["coordination_allowance_seconds"], 60)
        self.assertEqual(deadline["pair_deadline_seconds"], 3860)
        for invalid in (True, 59, 3601):
            with self.assertRaisesRegex(campaign.CampaignError, "between 60 and 3600"):
                campaign.scenario_pair_deadline_contract(invalid)

    def test_create_requires_seven_clean_boots_and_unique_challenges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(campaign.CampaignError, "fixed 7-boot"):
                _create(root, boots=6)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(campaign.CampaignError, "clean committed"):
                _create(root, clean=False)

        for invalid_timeout in (180, 900.0, True):
            with self.subTest(timeout=invalid_timeout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with self.assertRaisesRegex(campaign.CampaignError, "fixed micro"):
                    _create(root, timeout_seconds=invalid_timeout)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            challenges = [boot["challenge"] for boot in value["boots"]]
            repeated_manifest = _create(
                root,
                artifact_root="results/evaluation/runs/run-2",
                run_id="run-2",
            )
            repeated = json.loads(repeated_manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                challenges,
                [boot["challenge"] for boot in repeated["boots"]],
            )
            self.assertNotEqual(
                campaign._derive_micro_challenge(COMMIT, 1),
                campaign._derive_micro_challenge("b" * 40, 1),
            )
            self.assertEqual(
                set(value["environment"]),
                {"assembler", "bash", "compiler", "git", "host_cc", "linker", "make", "objcopy", "objdump", "python", "qemu"},
            )
            self.assertEqual(
                value["protocol"]["micro_timeout_seconds"],
                campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
            )
            self.assertEqual(value["protocol"]["expected_samples_per_boot"], 182)
            self.assertEqual(len(challenges), len(set(challenges)))
            self.assertNotIn("0" * 16, challenges)
            for index, boot in enumerate(value["boots"], 1):
                self.assertEqual(int(boot["challenge"], 16) & 1, index & 1)
                self.assertEqual(
                    boot["command_environment"]["AGENT_EVAL_CHALLENGE_HEX"],
                    boot["challenge"],
                )
                self.assertEqual(
                    boot["command_environment"]["AGENT_TEST_GUEST_LOG_FILE"],
                    boot["guest_log"],
                )
                self.assertEqual(
                    boot["command_environment"]["AGENT_TEST_DURATION_PROFILE"],
                    "none",
                )
                self.assertEqual(
                    boot["command_environment"]["AGENTOS_EVALUATION_EXECUTION_DOMAIN"],
                    "native-linux",
                )
                self.assertEqual(
                    boot["command_environment"]["CASE_TIMEOUT"],
                    f"{campaign.FORMAL_MICRO_TIMEOUT_SECONDS}s",
                )
                self.assertTrue(Path(boot["command_argv"][0]).is_absolute())
                self.assertEqual(
                    boot["command_environment"]["MAKE_TOOL"],
                    value["environment"]["make"]["path"],
                )
                for name in ("TEMP", "TMP", "TMPDIR"):
                    self.assertEqual(boot["command_environment"][name], "/tmp")
                for name in ("CC", "HOSTCC", "HOST_CC"):
                    self.assertEqual(
                        boot["command_environment"][name],
                        value["environment"]["host_cc"]["path"],
                    )
                self.assertIn(
                    Path(value["environment"]["git"]["path"]).parent.resolve(),
                    [
                        Path(item).resolve()
                        for item in boot["command_environment"]["PATH"].split(os.pathsep)
                    ],
                )

            msys_platform = {
                **value["platform"],
                "domain": "native-msys2",
                "entry_domain": "native-msys2",
                "temporary_directory": "/r/tmp",
                "windows_temporary_directory": r"R:\tmp",
                "windows_system_drive": "C:",
            }
            msys_environment = campaign._micro_boot_environment(
                value["environment"],
                msys_platform,
                value["boots"][0]["challenge"],
                value["boots"][0]["guest_log"],
            )
            self.assertEqual(msys_environment["TMPDIR"], "/r/tmp")
            self.assertEqual(msys_environment["TEMP"], r"R:\tmp")
            self.assertEqual(msys_environment["TMP"], r"R:\tmp")
            self.assertEqual(msys_environment["SYSTEMDRIVE"], "C:")
            self.assertEqual(
                campaign.get_campaign_metadata(manifest, "duration_profile"), "none"
            )

            tampered_profile = json.loads(json.dumps(value))
            tampered_profile["boots"][0]["command_environment"][
                "AGENT_TEST_DURATION_PROFILE"
            ] = "local-e3"
            with self.assertRaisesRegex(campaign.CampaignError, "environment differs"):
                campaign.validate_campaign(tampered_profile)

            coordinated = json.loads(json.dumps(value))
            coordinated["platform"]["duration_profile"] = {
                "calibration_status": "calibrated_full_suite",
                "name": "local-e3",
                "profile_id": "forged-local-e3",
                "status": "matched",
            }
            for boot in coordinated["boots"]:
                boot["command_environment"][
                    "AGENT_TEST_DURATION_PROFILE"
                ] = "local-e3"
            with self.assertRaisesRegex(
                campaign.CampaignError, "requires native-msys2"
            ):
                campaign.validate_campaign(coordinated, contract_root=PROJECT_ROOT)

            tampered_protocol = json.loads(json.dumps(value))
            tampered_protocol["protocol"]["micro_timeout_seconds"] = 180
            with self.assertRaisesRegex(campaign.CampaignError, "protocol"):
                campaign.validate_campaign(tampered_protocol)

            tampered_protocol = json.loads(json.dumps(value))
            tampered_protocol["protocol"]["micro_timeout_seconds"] = 900.0
            tampered_protocol["boots"][0]["command_environment"][
                "CASE_TIMEOUT"
            ] = "900.0s"
            with self.assertRaisesRegex(campaign.CampaignError, "protocol"):
                campaign.validate_campaign(tampered_protocol)

    def test_sample_identifier_grammar_accepts_registered_compound_names(self) -> None:
        self.assertEqual(
            f"^{campaign.SAMPLE_IDENTIFIER_PATTERN}$",
            contract.TOKEN.pattern,
        )
        for identifier in ("path_walk", "variant-2", "a" + "0" * 63):
            with self.subTest(identifier=identifier):
                self.assertIsNotNone(contract.TOKEN.fullmatch(identifier))
        for identifier in ("Path_walk", "a" * 65):
            with self.subTest(identifier=identifier):
                self.assertIsNone(contract.TOKEN.fullmatch(identifier))

        challenge = "0000000000000001"
        with tempfile.TemporaryDirectory() as temporary:
            guest = Path(temporary) / "guest.log"
            guest.write_text(_guest_log(challenge), encoding="utf-8")
            count, orders = campaign._read_sample_orders(guest, challenge, 182)
            self.assertEqual(count, 182)
            self.assertEqual(orders, ["AB", "BA"])

            guest.write_text(
                _guest_log(challenge).replace(
                    "variant=path_walk", "variant=Path_walk", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                campaign.CampaignError, "malformed evaluation sample marker"
            ):
                campaign._read_sample_orders(guest, challenge, 182)

    def test_preflight_receipt_binds_immutable_plan_and_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            planned = json.loads(manifest.read_text(encoding="utf-8"))
            for invalid_schema in (6.0, True, "6"):
                forged_schema = json.loads(json.dumps(planned))
                forged_schema["schema_version"] = invalid_schema
                with self.subTest(schema_version=invalid_schema):
                    with self.assertRaisesRegex(
                        campaign.CampaignError, "unsupported campaign"
                    ):
                        campaign.validate_campaign(forged_schema)
            receipt = campaign.format_preflight_receipt(planned)
            receipt_path = manifest.parent / "preflight.log"
            receipt_path.write_text(receipt, encoding="ascii", newline="\n")
            campaign.check_preflight_receipt(manifest, receipt_path)
            receipt_path.unlink()
            with mock.patch.object(campaign, "_run_micro_process") as run:
                with self.assertRaisesRegex(
                    campaign.CampaignError, "preflight receipt"
                ):
                    campaign.execute_and_record_boot(
                        repo=root,
                        manifest_path=manifest,
                        boot_id="boot-01",
                        timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                    )
                run.assert_not_called()
            receipt_path.write_text(receipt, encoding="ascii", newline="\n")

            collected = json.loads(json.dumps(planned))
            collected["phase"] = "collected"
            timestamp = planned["run"]["started_at_utc"]
            collected["run"]["completed_at_utc"] = timestamp
            for boot in collected["boots"]:
                boot["exit_code"] = 0
                boot["finished_at_utc"] = timestamp
                for key in (
                    "guest_log_sha256",
                    "image_final_sha256",
                    "image_input_sha256",
                    "kernel_sha256",
                    "runner_log_sha256",
                ):
                    boot[key] = "a" * 64
                boot["observed_sample_orders"] = ["AB", "BA"]
                boot["sample_count"] = planned["protocol"][
                    "expected_samples_per_boot"
                ]
                boot["status"] = "passed"
            campaign.validate_campaign(collected)
            self.assertEqual(receipt, campaign.format_preflight_receipt(collected))

            manifest.write_text(json.dumps(collected), encoding="utf-8")
            campaign.check_preflight_receipt(manifest, receipt_path)
            tampered = json.loads(receipt)
            tampered["binding_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(tampered) + "\n", encoding="ascii")
            with self.assertRaisesRegex(campaign.CampaignError, "differs"):
                campaign.check_preflight_receipt(manifest, receipt_path)
            for noncanonical in (
                receipt.replace("\n", "\r\n").encode("ascii"),
                (receipt + "\n").encode("ascii"),
                receipt.encode("ascii") + b"\0",
            ):
                with self.subTest(noncanonical=noncanonical[-4:]):
                    receipt_path.write_bytes(noncanonical)
                    with self.assertRaisesRegex(campaign.CampaignError, "differs"):
                        campaign.check_preflight_receipt(manifest, receipt_path)
            receipt_path.write_bytes(
                b"x" * (campaign.PREFLIGHT_RECEIPT_MAX_BYTES + 1)
            )
            with self.assertRaisesRegex(campaign.CampaignError, "unreadable"):
                campaign.check_preflight_receipt(manifest, receipt_path)

            output = mock.Mock()
            output.buffer = io.BytesIO()
            output.isatty.return_value = False
            output.fileno.return_value = 1
            with mock.patch.object(
                campaign, "create_campaign", return_value=planned
            ), mock.patch.object(campaign.sys, "stdout", output):
                status = campaign.main([
                    "create",
                    "--repo", str(root),
                    "--output", str(manifest),
                    "--run-id", "run-1",
                    "--boots", "7",
                    "--toolprefix", "/tool/riscv-none-elf-",
                    "--qemu", "/tool/qemu-system-riscv64",
                    "--python-bin", "/usr/bin/python3",
                    "--shell-bin", "/usr/bin/bash",
                    "--host-cc", "/usr/bin/cc",
                    "--duration-profile", "none",
                    "--timeout", "900",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(output.buffer.getvalue(), receipt.encode("ascii"))
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys;"
                        f"sys.path.insert(0,{str(PROJECT_ROOT / 'host_tools')!r});"
                        "import evaluation_campaign as c;"
                        f"c._write_ascii_stdout({'x' + chr(10)!r})"
                    ),
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout, b"x\n")

    def test_msys_micro_temporary_namespace_is_bound_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["platform"] = _msys_platform_proof(root)
            value["run"]["execution_domain"] = "native-msys2"
            value["environment"]["host_cc"] = dict(
                value["platform"]["tools"]["host_cc"]
            )
            for boot in value["boots"]:
                boot["command_environment"] = campaign._micro_boot_environment(
                    value["environment"],
                    value["platform"],
                    boot["challenge"],
                    boot["guest_log"],
                )
            campaign.validate_campaign(value)

            coordinated = json.loads(json.dumps(value))
            coordinated["platform"]["duration_profile"] = {
                "calibration_status": "calibrated_full_suite",
                "name": "local-e3",
                "profile_id": "forged-local-e3",
                "status": "matched",
            }
            for boot in coordinated["boots"]:
                boot["command_environment"][
                    "AGENT_TEST_DURATION_PROFILE"
                ] = "local-e3"
            with self.assertRaisesRegex(
                campaign.CampaignError,
                "recorded configuration|recorded hardware|recorded tool",
            ):
                campaign.validate_campaign(coordinated, contract_root=PROJECT_ROOT)

            for name in ("SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR"):
                tampered = json.loads(json.dumps(value))
                tampered["boots"][0]["command_environment"][name] = "/tmp/other"
                with self.subTest(name=name), self.assertRaisesRegex(
                    campaign.CampaignError, "environment differs"
                ):
                    campaign.validate_campaign(tampered)

            tampered = json.loads(json.dumps(value))
            tampered["boots"][0]["command_environment"]["CASE_TIMEOUT"] = "180s"
            with self.assertRaisesRegex(
                campaign.CampaignError, "environment differs"
            ):
                campaign.validate_campaign(tampered)

    def test_micro_artifacts_require_the_exact_canonical_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = _create(Path(temporary))
            original = json.loads(manifest.read_text(encoding="utf-8"))
            mutations = {
                "guest_log": [
                    "../../outside/raw/boot-01/guest.log",
                    "other/prefix/raw/boot-01/guest.log",
                    "C:/results/evaluation/runs/run-1/raw/boot-01/guest.log",
                    "results\\evaluation\\runs\\run-1\\raw\\boot-01\\guest.log",
                ],
                "runner_log": ["other/prefix/raw/boot-01/runner.log"],
                "kernel_path": ["other/prefix/raw/boot-01/kernel"],
                "image_input_path": ["other/prefix/raw/boot-01/fs.img"],
                "image_final_path": ["other/prefix/raw/boot-01/fs-copy.img"],
            }
            for field, invalid_values in mutations.items():
                for invalid in invalid_values:
                    with self.subTest(field=field, invalid=invalid):
                        value = json.loads(json.dumps(original))
                        value["boots"][0][field] = invalid
                        with self.assertRaisesRegex(
                            campaign.CampaignError, "artifact paths are not canonical"
                        ):
                            campaign.validate_campaign(value)
            for invalid_root in (
                "../../outside",
                "C:/evaluation/runs/run-1",
                "custom\\evaluation\\runs\\run-1",
            ):
                with self.subTest(artifact_root=invalid_root):
                    value = json.loads(json.dumps(original))
                    value["run"]["artifact_root"] = invalid_root
                    with self.assertRaisesRegex(
                        campaign.CampaignError, "artifact root.*canonical"
                    ):
                        campaign.validate_campaign(value)
            value = json.loads(json.dumps(original))
            value["run"]["artifact_root"] = "custom/evaluation/runs/not-run-1"
            with self.assertRaisesRegex(
                campaign.CampaignError, "not bound to the run identity"
            ):
                campaign.validate_campaign(value)

    def test_custom_artifact_root_is_bound_to_the_manifest_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            custom_root = "custom/evaluation-output/runs/run-1"
            manifest = _create(root, artifact_root=custom_root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(value["run"]["artifact_root"], custom_root)
            self.assertEqual(
                value["boots"][0]["guest_log"],
                f"{custom_root}/raw/boot-01/guest.log",
            )
            campaign.validate_campaign(value)

            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign, "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                scenario = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=manifest,
                    output=manifest.parent / "scenario" / "scenario-plan.json",
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            self.assertEqual(scenario["run"]["artifact_root"], custom_root)
            self.assertEqual(
                scenario["boots"][0]["work_dir"],
                f"{custom_root}/scenario/raw/boot-01",
            )
            scenario_manifest = manifest.parent / "scenario" / "scenario-plan.json"
            scenario_receipt = campaign.format_preflight_receipt(scenario)
            scenario_receipt_path = manifest.parent / "scenario-preflight.log"
            scenario_receipt_path.write_text(
                scenario_receipt, encoding="ascii", newline="\n"
            )
            campaign.check_preflight_receipt(
                scenario_manifest, scenario_receipt_path
            )
            collected_scenario = json.loads(json.dumps(scenario))
            collected_scenario["phase"] = "collected"
            for boot in collected_scenario["boots"]:
                boot["exit_code"] = 0
                boot["finished_at_utc"] = "2026-07-30T00:09:00Z"
                boot["host_summary_sha256"] = "a" * 64
                boot["runner_log_sha256"] = "b" * 64
                boot["status"] = "passed"
            collected_scenario["report"]["sha256"] = "c" * 64
            collected_scenario["report"]["status"] = "recorded"
            campaign.validate_scenario_campaign(collected_scenario)
            for invalid_schema in (5.0, True, "5"):
                forged_schema = json.loads(json.dumps(scenario))
                forged_schema["schema_version"] = invalid_schema
                with self.subTest(scenario_schema=invalid_schema):
                    with self.assertRaisesRegex(
                        campaign.CampaignError, "unsupported scenario"
                    ):
                        campaign.validate_scenario_campaign(forged_schema)
            self.assertEqual(
                scenario_receipt,
                campaign.format_preflight_receipt(collected_scenario),
            )
            scenario_receipt_path.unlink()
            with mock.patch.object(campaign, "_run_micro_process") as run:
                with self.assertRaisesRegex(
                    campaign.CampaignError, "preflight receipt"
                ):
                    campaign.execute_and_record_scenario_boot(
                        repo=root,
                        manifest_path=scenario_manifest,
                        boot_id="boot-01",
                    )
                run.assert_not_called()
            scenario_receipt_path.write_text(
                scenario_receipt, encoding="ascii", newline="\n"
            )
            output = mock.Mock()
            output.buffer = io.BytesIO()
            output.isatty.return_value = False
            output.fileno.return_value = 1
            with mock.patch.object(
                campaign, "create_scenario_campaign", return_value=scenario
            ), mock.patch.object(campaign.sys, "stdout", output):
                status = campaign.main([
                    "create-scenario",
                    "--repo", str(root),
                    "--micro-manifest", str(manifest),
                    "--output", str(scenario_manifest),
                    "--boots", "7",
                    "--timeout", "600",
                    "--wsl-distro", "Ubuntu",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(
                output.buffer.getvalue(), scenario_receipt.encode("ascii")
            )
            relocated_scenario = root / "relocated" / "scenario-plan.json"
            relocated_scenario.parent.mkdir(parents=True)
            relocated_scenario.write_bytes(scenario_manifest.read_bytes())
            with self.assertRaisesRegex(
                campaign.CampaignError, "manifest parent differs"
            ):
                campaign.check_scenario_campaign(root, relocated_scenario)
            with self.assertRaisesRegex(
                campaign.CampaignError, "manifest parent differs"
            ):
                campaign._record_scenario_boot_result(
                    repo=root,
                    manifest_path=relocated_scenario,
                    boot_id="boot-01",
                    exit_code=1,
                    runner_log=root / "unused-scenario-runner.log",
                    host_summary=root / "unused-scenario-summary.json",
                )

            relocated = root / "relocated" / "campaign.json"
            relocated.write_bytes(manifest.read_bytes())
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                with self.assertRaisesRegex(
                    campaign.CampaignError, "manifest parent differs"
                ):
                    campaign.check_campaign(
                        root, relocated, require_collected=False
                    )
                with self.assertRaisesRegex(
                    campaign.CampaignError, "manifest parent differs"
                ):
                    campaign._record_boot_result(
                        repo=root,
                        manifest_path=relocated,
                        boot_id="boot-01",
                        exit_code=1,
                        guest_log=root / "unused-guest.log",
                        runner_log=root / "unused-runner.log",
                        kernel=root / "unused-kernel",
                        input_image=root / "unused-input",
                        final_image=root / "unused-final",
                    )

    def test_scenario_artifacts_require_the_exact_canonical_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            with (
                mock.patch.object(campaign, "repository_identity", return_value=(COMMIT, True)),
                mock.patch.object(
                    campaign, "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                original = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=micro.parent / "scenario" / "scenario-plan.json",
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )

            for invalid in (
                "../../outside/raw/boot-01",
                "other/prefix/scenario/raw/boot-01",
                "C:/results/evaluation/runs/run-1/scenario/raw/boot-01",
                "results\\evaluation\\runs\\run-1\\scenario\\raw\\boot-01",
            ):
                with self.subTest(work_dir=invalid):
                    value = json.loads(json.dumps(original))
                    value["boots"][0]["work_dir"] = invalid
                    with self.assertRaisesRegex(
                        campaign.CampaignError, "work directory is not canonical"
                    ):
                        campaign.validate_scenario_campaign(value)

            for invalid in (
                "../../outside/scenario/report.json",
                "other/prefix/scenario/report.json",
                "C:/results/evaluation/runs/run-1/scenario/report.json",
            ):
                with self.subTest(report=invalid):
                    value = json.loads(json.dumps(original))
                    value["report"]["path"] = invalid
                    with self.assertRaisesRegex(
                        campaign.CampaignError, "report path is not canonical"
                    ):
                        campaign.validate_scenario_campaign(value)

    def test_run_boot_holds_lock_through_artifact_archival(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            boot = value["boots"][0]
            (root / "build").mkdir()
            (root / "nfs").mkdir(exist_ok=True)
            (root / "build/kernel").write_bytes(b"kernel")
            (root / "nfs/fs.img").write_bytes(
                b"input:" + boot["challenge"].encode("ascii")
            )
            (root / "nfs/fs-copy.img").write_bytes(b"final")

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
                    self.assertEqual(timeout, campaign.FORMAL_MICRO_TIMEOUT_SECONDS)
                    return "runner completed\n", None

            def launch(command: list[str], **kwargs: object) -> FakeProcess:
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                challenge = str(environment["AGENT_EVAL_CHALLENGE_HEX"])
                guest_ref = str(environment["AGENT_TEST_GUEST_LOG_FILE"])
                self.assertEqual(command, boot["command_argv"])
                (root / guest_ref).write_text(_guest_log(challenge), encoding="utf-8")
                return FakeProcess()

            with (
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(campaign.subprocess, "Popen", side_effect=launch),
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
            ):
                self.assertEqual(
                    campaign.execute_and_record_boot(
                        repo=root,
                        manifest_path=manifest,
                        boot_id=boot["boot_id"],
                        timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                    ),
                    0,
                )
            recorded = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            self.assertEqual(recorded["status"], "passed")
            self.assertEqual((root / recorded["kernel_path"]).read_bytes(), b"kernel")
            self.assertEqual(
                (root / recorded["image_input_path"]).read_bytes(),
                b"input:" + boot["challenge"].encode("ascii"),
            )

    def test_run_boot_rejects_timeout_not_sealed_by_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            with (
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(campaign.subprocess, "Popen") as launch,
            ):
                with self.assertRaisesRegex(
                    campaign.CampaignError, "differs from the sealed campaign"
                ):
                    campaign.execute_and_record_boot(
                        repo=root,
                        manifest_path=manifest,
                        boot_id="boot-01",
                        timeout_seconds=180,
                    )
            launch.assert_not_called()

    def test_micro_timeout_kills_group_retains_output_and_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            boot = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            popen_kwargs: dict[str, object] = {}

            class TimedOutProcess:
                returncode = -9
                stdout = None
                communicate_calls = 0

                def communicate(
                    process: "TimedOutProcess", timeout: int | None = None
                ) -> tuple[str, None]:
                    process.communicate_calls += 1
                    if process.communicate_calls == 1:
                        self.assertEqual(
                            timeout, campaign.FORMAL_MICRO_TIMEOUT_SECONDS
                        )
                        raise subprocess.TimeoutExpired(
                            boot["command_argv"], timeout, output=b"partial runner output\n"
                        )
                    self.assertEqual(timeout, 5)
                    return "partial runner output\n", None

            def launch(_command: list[str], **kwargs: object) -> TimedOutProcess:
                popen_kwargs.update(kwargs)
                return TimedOutProcess()

            with (
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(campaign.subprocess, "Popen", side_effect=launch),
                mock.patch.object(campaign, "_terminate_micro_process") as terminate,
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
            ):
                rc = campaign.execute_and_record_boot(
                    repo=root,
                    manifest_path=manifest,
                    boot_id=boot["boot_id"],
                    timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                )

            self.assertEqual(rc, 124)
            terminate.assert_called_once()
            if os.name == "nt":
                self.assertEqual(
                    popen_kwargs["creationflags"],
                    subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                self.assertNotIn("start_new_session", popen_kwargs)
            else:
                self.assertIs(popen_kwargs["start_new_session"], True)
                self.assertNotIn("creationflags", popen_kwargs)
            runner_log = root / boot["runner_log"]
            retained = runner_log.read_text(encoding="utf-8")
            self.assertIn("partial runner output", retained)
            self.assertIn(
                f"exceeded {campaign.FORMAL_MICRO_TIMEOUT_SECONDS}s total deadline",
                retained,
            )
            recorded = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            self.assertEqual(recorded["status"], "failed")
            self.assertEqual(recorded["exit_code"], 124)
            self.assertEqual(recorded["runner_log_sha256"], campaign._sha256(runner_log))

    def test_micro_execution_ignores_later_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            boot = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            (root / "build").mkdir()
            (root / "nfs").mkdir(exist_ok=True)
            (root / "build/kernel").write_bytes(b"kernel")
            (root / "nfs/fs.img").write_bytes(b"input")
            (root / "nfs/fs-copy.img").write_bytes(b"final")
            attacker = root / "attacker"
            attacker.mkdir()
            (attacker / "bash.exe").write_bytes(b"not the preflighted executable")

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
                    return "completed\n", None

            def launch(command: list[str], **kwargs: object) -> FakeProcess:
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                self.assertEqual(command, boot["command_argv"])
                self.assertTrue(Path(command[0]).is_absolute())
                self.assertEqual(environment, boot["command_environment"])
                self.assertNotIn(str(attacker), str(environment["PATH"]).split(os.pathsep))
                guest = root / boot["guest_log"]
                guest.write_text(_guest_log(boot["challenge"]), encoding="utf-8")
                return FakeProcess()

            with (
                mock.patch.dict(os.environ, {"PATH": str(attacker)}),
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(campaign.subprocess, "Popen", side_effect=launch),
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
            ):
                self.assertEqual(
                    campaign.execute_and_record_boot(
                        repo=root,
                        manifest_path=manifest,
                        boot_id="boot-01",
                        timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                    ),
                    0,
                )

    def test_source_change_at_execution_boundary_fails_closed_even_if_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            boot = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
                    return "completed\n", None

            with (
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(campaign.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(
                    campaign,
                    "repository_identity",
                    side_effect=[(COMMIT, True), (COMMIT, False)],
                ),
            ):
                with self.assertRaisesRegex(campaign.CampaignError, "after boot"):
                    campaign.execute_and_record_boot(
                        repo=root,
                        manifest_path=manifest,
                        boot_id=boot["boot_id"],
                        timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                    )
            recorded = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            self.assertEqual(recorded["status"], "planned")
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                campaign._require_repository_identity(root, COMMIT, "after restore")

    def test_preflighted_tool_replacement_after_process_fails_before_archival(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            make_path = Path(value["environment"]["make"]["path"])
            original = make_path.read_bytes()

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
                    make_path.write_bytes(b"replaced during boot\n")
                    return "completed\n", None

            try:
                with (
                    mock.patch.object(
                        action_runner,
                        "_run_lock_path",
                        return_value=root / ".evaluation-test.lock",
                    ),
                    mock.patch.object(
                        campaign.subprocess, "Popen", return_value=FakeProcess()
                    ),
                    mock.patch.object(
                        campaign, "repository_identity", return_value=(COMMIT, True)
                    ),
                ):
                    with self.assertRaisesRegex(
                        campaign.CampaignError, "executable changed before boot: make"
                    ):
                        campaign.execute_and_record_boot(
                            repo=root,
                            manifest_path=manifest,
                            boot_id="boot-01",
                            timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                        )
            finally:
                make_path.write_bytes(original)
            recorded = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            self.assertEqual(recorded["status"], "planned")

    def test_pending_state_is_reloaded_inside_lock_before_logs_are_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            boot = json.loads(manifest.read_text(encoding="utf-8"))["boots"][0]
            guest = root / boot["guest_log"]
            runner = root / boot["runner_log"]
            guest.parent.mkdir(parents=True)
            guest.write_text("competitor guest\n", encoding="utf-8")
            runner.write_text("competitor runner\n", encoding="utf-8")

            @contextmanager
            def competing_record(_repo: Path):
                campaign._record_boot_result(
                    repo=root,
                    manifest_path=manifest,
                    boot_id=boot["boot_id"],
                    exit_code=9,
                    guest_log=guest,
                    runner_log=runner,
                    kernel=root / "build/kernel",
                    input_image=root / "nfs/fs.img",
                    final_image=root / "nfs/fs-copy.img",
                )
                yield

            with (
                mock.patch.object(
                    action_runner, "exclusive_repo_run_lock", new=competing_record
                ),
                mock.patch.object(campaign.subprocess, "Popen") as launch,
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
            ):
                with self.assertRaisesRegex(campaign.CampaignError, "planned and pending"):
                    campaign.execute_and_record_boot(
                        repo=root,
                        manifest_path=manifest,
                        boot_id=boot["boot_id"],
                        timeout_seconds=campaign.FORMAL_MICRO_TIMEOUT_SECONDS,
                    )
            launch.assert_not_called()
            self.assertEqual(guest.read_text(encoding="utf-8"), "competitor guest\n")
            self.assertEqual(runner.read_text(encoding="utf-8"), "competitor runner\n")

    def test_formal_campaign_lock_is_named_separately_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo_lock = root / ".per-boot-repo.lock"
            with mock.patch.object(
                action_runner, "_run_lock_path", return_value=repo_lock
            ):
                campaign_lock = campaign._campaign_lock_path(root)
                self.assertNotEqual(campaign_lock, repo_lock)
                self.assertEqual(
                    campaign_lock.name, ".agentos-evaluation-campaign.lock"
                )
                with campaign.exclusive_evaluation_campaign_lock(root):
                    with self.assertRaisesRegex(
                        campaign.CampaignBusy, "another formal evaluation"
                    ):
                        with campaign.exclusive_evaluation_campaign_lock(root):
                            self.fail("a second campaign acquired the named lock")

    def test_scenario_coordination_lock_is_distinct_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo_lock = root / ".per-target-repo.lock"
            with mock.patch.object(
                action_runner, "_run_lock_path", return_value=repo_lock
            ):
                scenario_lock = campaign._scenario_coordination_lock_path(root)
                self.assertNotEqual(scenario_lock, repo_lock)
                self.assertNotEqual(scenario_lock, campaign._campaign_lock_path(root))
                self.assertEqual(
                    scenario_lock.name, ".agentos-evaluation-scenario.lock"
                )
                with campaign.exclusive_scenario_coordination_lock(root):
                    with self.assertRaisesRegex(
                        campaign.ScenarioBusy, "another scenario collector"
                    ):
                        with campaign.exclusive_scenario_coordination_lock(root):
                            self.fail("a second collector acquired the scenario lock")

    def test_campaign_command_starts_only_after_named_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lease = root / ".campaign.lease"
            events: list[str] = []

            @contextmanager
            def lock(_repo: Path):
                events.append("lock-enter")
                yield
                events.append("lock-exit")

            def run(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(events, ["lock-enter"])
                self.assertEqual(command, ["bash", "suite.sh", "__run_locked"])
                environment = kwargs["env"]
                self.assertIsInstance(environment, dict)
                token = environment[campaign.CAMPAIGN_LOCK_TOKEN_ENV]  # type: ignore[index]
                self.assertEqual(token, "1" * 64)
                self.assertEqual(lease.read_text(encoding="ascii"), token + "\n")
                events.append("command")
                return subprocess.CompletedProcess(command, 0)

            with (
                mock.patch.object(
                    campaign, "exclusive_evaluation_campaign_lock", new=lock
                ),
                mock.patch.object(
                    campaign, "_campaign_lease_path", return_value=lease
                ),
                mock.patch.object(campaign.secrets, "token_hex", return_value="1" * 64),
                mock.patch.object(campaign.subprocess, "run", side_effect=run),
            ):
                self.assertEqual(
                    campaign.execute_under_campaign_lock(
                        repo=root,
                        command=["--", "bash", "suite.sh", "__run_locked"],
                    ),
                    0,
                )
            self.assertEqual(events, ["lock-enter", "command", "lock-exit"])
            self.assertFalse(lease.exists())

    def test_private_campaign_mode_requires_a_live_lock_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lease = root / ".campaign.lease"
            token = "2" * 64
            lease.write_text(token + "\n", encoding="ascii")

            @contextmanager
            def busy(_repo: Path):
                raise campaign.CampaignBusy("synthetic held lock")
                yield

            with (
                mock.patch.object(campaign, "_campaign_lease_path", return_value=lease),
                mock.patch.object(
                    campaign, "exclusive_evaluation_campaign_lock", new=busy
                ),
            ):
                campaign.verify_campaign_lock_lease(repo=root, token=token)
                with self.assertRaisesRegex(campaign.CampaignError, "token differs"):
                    campaign.verify_campaign_lock_lease(repo=root, token="3" * 64)

            @contextmanager
            def unlocked(_repo: Path):
                yield

            with (
                mock.patch.object(campaign, "_campaign_lease_path", return_value=lease),
                mock.patch.object(
                    campaign, "exclusive_evaluation_campaign_lock", new=unlocked
                ),
                self.assertRaisesRegex(campaign.CampaignError, "without a held lock"),
            ):
                campaign.verify_campaign_lock_lease(repo=root, token=token)

        bash = shutil.which("bash")
        if sys.platform == "cygwin":
            runtime_bash = Path(sys.executable).resolve().parent / "bash.exe"
            bash = str(runtime_bash) if runtime_bash.is_file() else None
        if os.name == "nt":
            git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
            bash = str(git_bash) if git_bash.is_file() else None
        if bash:
            result = subprocess.run(
                [bash, "scripts/run-evaluation-suite.sh", "__run_locked"],
                cwd=Path(__file__).resolve().parents[1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key != campaign.CAMPAIGN_LOCK_TOKEN_ENV
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires a verified campaign lock", result.stderr)

    def test_record_seal_export_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            kernel = root / "build" / "kernel"
            input_image = root / "nfs" / "fs.img"
            final_image = root / "nfs" / "fs-copy.img"
            kernel.parent.mkdir(parents=True)
            input_image.parent.mkdir(parents=True, exist_ok=True)
            kernel.write_bytes(b"kernel")
            input_image.write_bytes(b"input image")
            final_image.write_bytes(b"final image")

            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                for boot in value["boots"]:
                    input_image.write_bytes(
                        b"input image:" + boot["challenge"].encode("ascii")
                    )
                    guest = root / boot["guest_log"]
                    runner = root / boot["runner_log"]
                    guest.parent.mkdir(parents=True)
                    guest.write_text(_guest_log(boot["challenge"]), encoding="utf-8")
                    runner.write_text("runner completed\n", encoding="utf-8")
                    campaign._record_boot_result(
                        repo=root,
                        manifest_path=manifest,
                        boot_id=boot["boot_id"],
                        exit_code=0,
                        guest_log=guest,
                        runner_log=runner,
                        kernel=kernel,
                        input_image=input_image,
                        final_image=final_image,
                    )

            campaign.seal_campaign(manifest)
            run_plan = manifest.parent / "run-plan.json"
            plan = campaign.export_run_plan(manifest, run_plan)
            self.assertEqual(plan["kind"], "agentos-evaluation-run-plan")
            self.assertEqual(plan["schema_version"], 2)
            self.assertEqual(plan["stop_rule"], measurement_source.STOP_RULE)
            self.assertEqual(
                plan["measurement_source_receipt"],
                value["measurement_source_receipt"],
            )
            self.assertEqual(len(plan["logs"]), 7)
            self.assertEqual(plan["logs"][0]["path"], "boot-01/guest.log")
            self.assertEqual(plan["logs"][0]["status"], "supported")
            self.assertIsNone(plan["logs"][0]["detail"])
            first_input = root / value["boots"][0]["image_input_path"]
            self.assertEqual(plan["logs"][0]["image_input_sha256"], campaign._sha256(first_input))
            self.assertEqual(plan["logs"][0]["image_final_sha256"], campaign._sha256(final_image))
            self.assertEqual(len(plan["campaign_sha256"]), 64)
            self.assertEqual(len(plan["logs"][0]["command_sha256"]), 64)
            loaded_plan, _ = contract.load_run_plan(run_plan)
            self.assertEqual(loaded_plan, plan)

            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                campaign.check_campaign(root, manifest, require_collected=True)
                original_manifest = manifest.read_bytes()
                tampered_count = json.loads(original_manifest)
                tampered_count["protocol"]["expected_samples_per_boot"] = 181
                for boot in tampered_count["boots"]:
                    boot["sample_count"] = 181
                manifest.write_text(json.dumps(tampered_count), encoding="utf-8")
                with self.assertRaisesRegex(
                    campaign.CampaignError, "sample count differs"
                ):
                    campaign.check_campaign(root, manifest, require_collected=True)
                manifest.write_bytes(original_manifest)
                suite = root / "ci" / "evaluation-suite.json"
                original_suite = suite.read_text(encoding="utf-8")
                suite.write_text('{"schema_version":2}\n', encoding="utf-8")
                with self.assertRaisesRegex(
                    campaign.CampaignError,
                    "suite changed|measurement timing sources changed",
                ):
                    campaign.check_campaign(root, manifest, require_collected=True)
                suite.write_text(original_suite, encoding="utf-8")
                archived_kernel = root / value["boots"][0]["kernel_path"]
                archived_kernel.write_bytes(b"tampered kernel")
                with self.assertRaisesRegex(campaign.CampaignError, "changed"):
                    campaign.check_campaign(root, manifest, require_collected=True)
                archived_kernel.write_bytes(b"kernel")
                first_log = root / value["boots"][0]["guest_log"]
                first_log.write_text(first_log.read_text() + "tampered\n")
                with self.assertRaisesRegex(campaign.CampaignError, "changed"):
                    campaign.check_campaign(root, manifest, require_collected=True)

    def test_wrong_challenge_and_failed_boot_cannot_be_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            boot = value["boots"][0]
            guest = root / boot["guest_log"]
            runner = root / boot["runner_log"]
            guest.parent.mkdir(parents=True)
            guest.write_text(_guest_log("f" * 16), encoding="utf-8")
            runner.write_text("runner failed\n", encoding="utf-8")
            kernel = root / "build" / "kernel"
            input_image = root / "nfs" / "fs.img"
            final_image = root / "nfs" / "fs-copy.img"
            kernel.parent.mkdir(parents=True)
            input_image.parent.mkdir(parents=True, exist_ok=True)
            kernel.write_bytes(b"kernel")
            input_image.write_bytes(b"input image")
            final_image.write_bytes(b"final image")
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                with self.assertRaisesRegex(campaign.CampaignError, "challenge"):
                    campaign._record_boot_result(
                        repo=root,
                        manifest_path=manifest,
                        boot_id=boot["boot_id"],
                        exit_code=0,
                        guest_log=guest,
                        runner_log=runner,
                        kernel=kernel,
                        input_image=input_image,
                        final_image=final_image,
                    )

            guest.write_text("partial guest output\n", encoding="utf-8")
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                campaign._record_boot_result(
                    repo=root,
                    manifest_path=manifest,
                    boot_id=boot["boot_id"],
                    exit_code=17,
                    guest_log=guest,
                    runner_log=runner,
                    kernel=kernel,
                    input_image=input_image,
                    final_image=final_image,
                )
            self.assertTrue(guest.exists())
            self.assertTrue(runner.exists())
            with self.assertRaisesRegex(campaign.CampaignError, "incomplete"):
                campaign.seal_campaign(manifest)

    def test_scenario_plan_precommits_unique_challenges_and_balanced_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            value = json.loads(micro.read_text(encoding="utf-8"))
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            scenario_plan = micro.parent / "scenario" / "scenario-plan.json"
            with (
                mock.patch.object(campaign, "repository_identity", return_value=(COMMIT, True)),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=campaign.CampaignError("scenario probe failed"),
                ),
            ):
                with self.assertRaisesRegex(campaign.CampaignError, "probe failed"):
                    campaign.create_scenario_campaign(
                        repo=root,
                        micro_manifest=micro,
                        output=scenario_plan,
                        requested_boots=7,
                        timeout_seconds=600,
                        wsl_distro="Ubuntu",
                    )
            self.assertFalse(scenario_plan.exists())
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ), mock.patch.object(
                campaign,
                "_probe_scenario_environment",
                side_effect=_bound_scenario_environment,
            ):
                scenario = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=scenario_plan,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            self.assertEqual(value["phase"], "collecting")
            value["phase"] = "collected"
            value["run"]["completed_at_utc"] = "2026-01-01T00:00:00Z"
            for boot in value["boots"]:
                boot.update(
                    {
                        "exit_code": 0,
                        "finished_at_utc": "2026-01-01T00:00:00Z",
                        "guest_log_sha256": "1" * 64,
                        "image_final_sha256": "2" * 64,
                        "image_input_sha256": "3" * 64,
                        "kernel_sha256": "4" * 64,
                        "observed_sample_orders": ["AB", "BA"],
                        "runner_log_sha256": "5" * 64,
                        "sample_count": value["protocol"]["expected_samples_per_boot"],
                        "status": "passed",
                    }
                )
            micro.write_text(json.dumps(value), encoding="utf-8")
            challenges = [boot["challenge"] for boot in scenario["boots"]]
            self.assertEqual(len(challenges), len(set(challenges)))
            self.assertTrue(all(campaign.SCENARIO_CHALLENGE_RE.fullmatch(item) for item in challenges))
            self.assertEqual(
                challenges,
                [
                    campaign._derive_scenario_challenge(COMMIT, number)
                    for number in range(1, 8)
                ],
            )
            self.assertEqual(
                [boot["target_order"] for boot in scenario["boots"]],
                ["plain-agentos", "agentos-plain", "plain-agentos", "agentos-plain", "plain-agentos", "agentos-plain", "plain-agentos"],
            )
            self.assertIn("--challenge", scenario["boots"][0]["command_argv"])
            self.assertEqual(
                scenario["boots"][0]["command_environment"]["QEMU"],
                "/usr/bin/qemu-system-riscv64",
            )
            for name in ("CC", "HOSTCC", "HOST_CC"):
                self.assertEqual(
                    scenario["boots"][0]["command_environment"][name],
                    scenario["platform"]["tools"]["host_cc"]["path"],
                )
            self.assertTrue(Path(scenario["boots"][0]["command_argv"][0]).is_absolute())
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                for boot in scenario["boots"]:
                    runner = root / boot["runner_log"]
                    summary = root / boot["host_summary"]
                    runner.parent.mkdir(parents=True, exist_ok=True)
                    runner.write_text("scenario runner completed\n", encoding="utf-8")
                    summary.write_text(
                        json.dumps(
                            {
                                "status": "ready",
                                "challenge": boot["challenge"],
                                "target_order": boot["target_order"],
                                "plain": {"status": "ready"},
                                "agentos": {"status": "ready"},
                            }
                        ),
                        encoding="utf-8",
                    )
                    campaign._record_scenario_boot_result(
                        repo=root,
                        manifest_path=scenario_plan,
                        boot_id=boot["boot_id"],
                        exit_code=0,
                        runner_log=runner,
                        host_summary=summary,
                    )
            report = scenario_plan.parent / "report.json"
            report_payload = {
                "status": "unknown",
                "source_commit": COMMIT,
                "run_id": "run-1",
                "summary": {
                    "independent_boots": 7,
                    "unique_challenges": 7,
                    "target_order_balanced": True,
                },
            }
            report.write_text(json.dumps(report_payload), encoding="utf-8")
            with self.assertRaisesRegex(
                campaign.CampaignError, "did not produce a bound report"
            ):
                campaign.record_scenario_report(
                    repo=root, manifest_path=scenario_plan, report_path=report
                )
            report_payload["status"] = "regressed"
            report.write_text(json.dumps(report_payload), encoding="utf-8")
            campaign.record_scenario_report(
                repo=root, manifest_path=scenario_plan, report_path=report
            )
            campaign.seal_scenario_campaign(scenario_plan)
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ), mock.patch.object(
                campaign,
                "_probe_scenario_environment",
                side_effect=_bound_scenario_environment,
            ):
                campaign.check_scenario_campaign(root, scenario_plan, micro)
                changed_environment = _scenario_environment()
                changed_environment["tools"]["qemu"]["sha256"] = "b" * 64
                with mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    return_value=changed_environment,
                ):
                    with self.assertRaisesRegex(campaign.CampaignError, "environment changed"):
                        campaign.check_scenario_campaign(root, scenario_plan, micro)
                first_runner = root / scenario["boots"][0]["runner_log"]
                first_runner.write_text("tampered\n", encoding="utf-8")
                with self.assertRaisesRegex(campaign.CampaignError, "changed"):
                    campaign.check_scenario_campaign(root, scenario_plan, micro)
            scenario["boots"][1]["challenge"] = scenario["boots"][0]["challenge"]
            with self.assertRaisesRegex(campaign.CampaignError, "unique"):
                campaign.validate_scenario_campaign(scenario)

    def test_shell_wiring_is_fail_closed_and_keeps_independent_logs(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "run-evaluation-suite.sh"
        ).read_text(encoding="utf-8")
        campaign_source = Path(campaign.__file__).read_text(encoding="utf-8")
        self.assertIn("FORMAL_MICRO_BOOTS=7", script)
        self.assertIn("EVALUATION_BOOTS:-${FORMAL_MICRO_BOOTS}", script)
        self.assertIn("EVALUATION_INCLUDE_SCENARIO:-1", script)
        self.assertIn("FORMAL_SCENARIO_BOOTS=7", script)
        self.assertIn(
            "EVALUATION_SCENARIO_BOOTS:-${FORMAL_SCENARIO_BOOTS}", script
        )
        self.assertIn('formal_id="formal-${commit}"', script)
        self.assertIn(
            "formal EVALUATION_RUN_ID must equal ${formal_id}", script
        )
        self.assertIn("write_measurement_source_receipt \"${commit}\"", script)
        self.assertIn("verify_measurement_source_receipt", script)
        self.assertIn('"AGENT_TEST_CASE": "agenteval_ucore"', campaign_source)
        self.assertIn(
            '"AGENT_TEST_DURATION_PROFILE": _bound_duration_profile_name(',
            campaign_source,
        )
        self.assertIn(
            '"${AGENT_TEST_DURATION_PROFILE}" == "local-e3" &&\n'
            '      -z "${AGENT_TEST_CASE:-}"',
            (PROJECT_ROOT / "scripts" / "run-agent-tests.sh").read_text(encoding="utf-8"),
        )
        self.assertIn('"AGENT_EVAL_CHALLENGE_HEX": challenge', campaign_source)
        self.assertIn("get-boot-field", script)
        self.assertIn("run-boot", script)
        self.assertNotIn('subparsers.add_parser("record")', campaign_source)
        self.assertIn("EVALUATION_MICRO_TIMEOUT:-900", script)
        self.assertIn("FORMAL_MICRO_TIMEOUT=900", script)
        self.assertIn(
            '"CASE_TIMEOUT": f"{timeout_seconds}s"', campaign_source
        )
        self.assertIn("micro_timeout_seconds", campaign_source)
        self.assertIn("with-campaign-lock", script)
        self.assertIn("__run_locked", script)
        self.assertIn("--timeout \"${EVALUATION_MICRO_TIMEOUT}\"", script)
        self.assertLess(
            script.index('"${CAMPAIGN_TOOL}" create'),
            script.index('--timeout "${EVALUATION_MICRO_TIMEOUT}"'),
        )
        self.assertIn("exclusive_repo_run_lock", campaign_source)
        self.assertIn("exclusive_evaluation_campaign_lock", campaign_source)
        self.assertIn("CREATE_NEW_PROCESS_GROUP", campaign_source)
        self.assertIn("start_new_session", campaign_source)
        self.assertIn('deadline_label: str = "micro boot"', campaign_source)
        self.assertIn("scenario_pair_deadline_contract", campaign_source)
        self.assertIn('"${CONTRACT_TOOL}" build', script)
        self.assertIn('"${CONTRACT_TOOL}" verify', script)
        self.assertIn('input_image=repo / "nfs/fs.img"', campaign_source)
        self.assertIn('final_image=repo / "nfs/fs-copy.img"', campaign_source)
        self.assertIn("create-scenario", script)
        self.assertEqual(script.count('"${CAMPAIGN_TOOL}" check-preflight'), 2)
        micro_preflight_check = (
            '"${CAMPAIGN_TOOL}" check-preflight \\\n'
            '\t\t--repo "${ROOT}" --manifest "${manifest}" \\\n'
            '\t\t--receipt "${RUN_DIR}/preflight.log"'
        )
        scenario_preflight_check = (
            '--repo "${ROOT}" \\\n'
            '\t\t\t--manifest "${RUN_DIR}/scenario/scenario-plan.json" \\\n'
            '\t\t\t--receipt "${RUN_DIR}/scenario-preflight.log"'
        )
        first_boot_loop = (
            "for ((number = 1; number <= EVALUATION_BOOTS; number++))"
        )
        self.assertIn(micro_preflight_check, script)
        self.assertIn(scenario_preflight_check, script)
        self.assertLess(
            script.index(micro_preflight_check),
            script.index('"${CAMPAIGN_TOOL}" create-scenario'),
        )
        self.assertLess(
            script.index(scenario_preflight_check), script.index(first_boot_loop)
        )
        self.assertIn("run-scenario-boot", script)
        self.assertNotIn('subparsers.add_parser("record-scenario")', campaign_source)
        self.assertIn("check-scenario", script)
        self.assertNotIn('"QEMU=${QEMU}"', script)
        self.assertIn("scenario report differs from a raw-source replay", script)
        self.assertIn("--scenario-report", script)
        self.assertIn('--contract-root "${ROOT}"', script)
        self.assertLess(
            script.index('"${CAMPAIGN_TOOL}" create-scenario'),
            script.index("for ((number = 1; number <= EVALUATION_BOOTS; number++))"),
        )
        self.assertNotIn('${PIPESTATUS[0]}', script)
        self.assertNotIn('${PIPESTATUS[1]}', script)
        self.assertEqual(script.count('pipeline_status=("${PIPESTATUS[@]}")'), 4)
        self.assertIn("pipeline_status_selftest", script)
        self.assertIn("failed; raw logs and campaign state were retained", script)
        self.assertIn("normalize_tool_path_for_python", script)
        self.assertIn("cygpath_path", script)
        self.assertIn('--make-tool "${make_python_path}"', script)
        self.assertIn('--size-tool "${size_python_path}"', script)
        self.assertNotIn("|| true", script)

    def test_scenario_coordination_allows_child_target_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            scenario_path = micro.parent / "scenario" / "scenario-plan.json"
            host_cc = json.loads(micro.read_text(encoding="utf-8"))["environment"][
                "host_cc"
            ]["path"]
            execution_environment = _scenario_environment(host_cc)
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    return_value=execution_environment,
                ),
            ):
                scenario = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=scenario_path,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            _write_scenario_preflight(scenario_path, scenario)
            boot = scenario["boots"][0]
            repo_lock = root / ".per-target-repo.lock"
            child_lock_acquired: list[bool] = []

            def run_scenario(**kwargs: object) -> int:
                self.assertEqual(
                    campaign._scenario_coordination_lock_path(root).name,
                    ".agentos-evaluation-scenario.lock",
                )
                with action_runner.exclusive_repo_run_lock(root):
                    child_lock_acquired.append(True)
                    with self.assertRaises(action_runner.RepoRunBusy):
                        with action_runner.exclusive_repo_run_lock(root):
                            self.fail("a concurrent target runner acquired the repo lock")
                    runner_log = kwargs["runner_log"]
                    self.assertIsInstance(runner_log, Path)
                    runner_log.write_text(
                        "scenario child completed\n", encoding="utf-8"
                    )
                    host_summary = root / str(boot["host_summary"])
                    host_summary.write_text(
                        json.dumps(
                            {
                                "status": "ready",
                                "challenge": boot["challenge"],
                                "target_order": boot["target_order"],
                                "plain": {"status": "ready"},
                                "agentos": {"status": "ready"},
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                return 0

            with (
                mock.patch.object(
                    action_runner, "_run_lock_path", return_value=repo_lock
                ),
                mock.patch.object(
                    campaign, "_run_micro_process", side_effect=run_scenario
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    return_value=execution_environment,
                ),
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
            ):
                self.assertEqual(
                    campaign.execute_and_record_scenario_boot(
                        repo=root,
                        manifest_path=scenario_path,
                        boot_id="boot-01",
                    ),
                    0,
                )

            self.assertEqual(child_lock_acquired, [True])
            recorded = json.loads(scenario_path.read_text(encoding="utf-8"))[
                "boots"
            ][0]
            self.assertEqual(recorded["status"], "passed")

    def test_scenario_boot_rechecks_source_after_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            scenario_path = micro.parent / "scenario" / "scenario-plan.json"
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                scenario = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=scenario_path,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            _write_scenario_preflight(scenario_path, scenario)

            observed_timeouts: list[int | None] = []

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
                    observed_timeouts.append(timeout)
                    return "scenario completed\n", None

            with (
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(campaign.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
                mock.patch.object(
                    campaign,
                    "repository_identity",
                    side_effect=[(COMMIT, True), (COMMIT, False)],
                ),
            ):
                with self.assertRaisesRegex(campaign.CampaignError, "after boot"):
                    campaign.execute_and_record_scenario_boot(
                        repo=root,
                        manifest_path=scenario_path,
                        boot_id="boot-01",
                    )
            planned = json.loads(scenario_path.read_text(encoding="utf-8"))["boots"][0]
            self.assertEqual(planned["status"], "planned")
            self.assertEqual(observed_timeouts, [3860])

    def test_scenario_pair_timeout_uses_derived_hard_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            micro = _create(root)
            for name in ("evaluation_scenario.py", "check_seeded_action_state.py"):
                source = root / "host_tools" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"# {name}\n", encoding="utf-8")
            scenario_path = micro.parent / "scenario" / "scenario-plan.json"
            with (
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
            ):
                scenario = campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=scenario_path,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )
            _write_scenario_preflight(scenario_path, scenario)

            class TimedOutProcess:
                returncode = -9
                stdout = None
                communicate_calls = 0

                def communicate(
                    process: "TimedOutProcess", timeout: int | None = None
                ) -> tuple[str, None]:
                    process.communicate_calls += 1
                    if process.communicate_calls == 1:
                        self.assertEqual(timeout, 3860)
                        raise subprocess.TimeoutExpired(
                            scenario["boots"][0]["command_argv"],
                            timeout,
                            output=b"partial scenario output\n",
                        )
                    self.assertEqual(timeout, 5)
                    return "partial scenario output\n", None

            with (
                mock.patch.object(
                    action_runner,
                    "_run_lock_path",
                    return_value=root / ".evaluation-test.lock",
                ),
                mock.patch.object(
                    campaign.subprocess, "Popen", return_value=TimedOutProcess()
                ),
                mock.patch.object(campaign, "_terminate_micro_process") as terminate,
                mock.patch.object(
                    campaign,
                    "_probe_scenario_environment",
                    side_effect=_bound_scenario_environment,
                ),
                mock.patch.object(
                    campaign, "repository_identity", return_value=(COMMIT, True)
                ),
            ):
                rc = campaign.execute_and_record_scenario_boot(
                    repo=root,
                    manifest_path=scenario_path,
                    boot_id="boot-01",
                )

            self.assertEqual(rc, 124)
            terminate.assert_called_once()
            runner_log = root / scenario["boots"][0]["runner_log"]
            retained = runner_log.read_text(encoding="utf-8")
            self.assertIn("partial scenario output", retained)
            self.assertIn("scenario pair exceeded 3860s total deadline", retained)
            recorded = json.loads(scenario_path.read_text(encoding="utf-8"))["boots"][0]
            self.assertEqual(recorded["status"], "failed")
            self.assertEqual(recorded["exit_code"], 124)


if __name__ == "__main__":
    unittest.main()
