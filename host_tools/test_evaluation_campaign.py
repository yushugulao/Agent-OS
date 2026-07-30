#!/usr/bin/env python3
"""Regression tests for the evaluation collection campaign."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import evaluation_campaign as campaign
import evaluation_contract as contract
import plain_ucore_action_runner as action_runner


COMMIT = "a" * 40


def _scenario_environment() -> dict[str, object]:
    def tool(argv0: str, number: str) -> dict[str, str]:
        return {
            "argv0": argv0,
            "path": f"/usr/bin/{Path(argv0).name}",
            "sha256": number * 64,
            "version": f"{argv0} 1",
        }

    if os.name == "nt":
        launcher = {
            "argv0": "wsl.exe",
            "path": r"C:\Windows\System32\wsl.exe",
            "sha256": "6" * 64,
            "version": "wsl.exe 1",
        }
        domain = "wsl"
        launcher_argv = ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc"]
    else:
        launcher = tool("bash", "6")
        domain = "native-login-shell"
        launcher_argv = ["bash", "-lc"]
    return {
        "domain": domain,
        "launcher": launcher,
        "launcher_argv": launcher_argv,
        "tools": {
            "bash": tool("bash", "7"),
            "compiler": tool("riscv64-linux-gnu-gcc", "8"),
            "linker": tool("riscv64-linux-gnu-ld", "c"),
            "make": tool("make", "9"),
            "objcopy": tool("riscv64-linux-gnu-objcopy", "d"),
            "objdump": tool("riscv64-linux-gnu-objdump", "e"),
            "qemu": tool("qemu-system-riscv64", "a"),
            "timeout": tool("timeout", "f"),
        },
    }


def _create(
    root: Path,
    *,
    boots: int = 7,
    clean: bool = True,
    artifact_root: str = "results/evaluation/runs/run-1",
) -> Path:
    manifest = root / artifact_root / "campaign.json"
    suite = root / "ci" / "evaluation-suite.json"
    suite.parent.mkdir(parents=True, exist_ok=True)
    suite.write_text('{"schema_version":1}\n', encoding="utf-8")

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

    with (
        mock.patch.object(campaign, "repository_identity", return_value=(COMMIT, clean)),
        mock.patch.object(
            campaign,
            "probe_executable",
            side_effect=fake_probe,
        ),
    ):
        campaign.create_campaign(
            repo=root,
            output=manifest,
            run_id="run-1",
            requested_boots=boots,
            toolprefix="riscv64-linux-gnu-",
            qemu="qemu-system-riscv64",
            python_bin="python3",
            shell_bin="bash",
        )
    return manifest


def _guest_log(challenge: str) -> str:
    lines = [f"agenteval_ucore: challenge={challenge}"]
    experiments = {
        "file_query": ("scan", "index"),
        "tool_batch": ("scalar", "batch"),
        "context_access": ("syscall", "direct"),
    }
    for experiment, variants in experiments.items():
        for load in (24, 64, 96):
            for pair in range(1, 8):
                order = (
                    "AB"
                    if (pair & 1) == (int(challenge, 16) & 1)
                    else "BA"
                )
                ordered = variants if order == "AB" else tuple(reversed(variants))
                for variant in ordered:
                    cache = "forced-scan" if variant == "scan" else "ready-index"
                    if experiment != "file_query":
                        cache = "warm"
                    work = load * load if experiment == "file_query" else load
                    lines.append(
                        "agenteval_ucore: sample schema=1 "
                        f"experiment={experiment} load={load} pair={pair} "
                        f"variant={variant} order={order} cache={cache} "
                        f"operations={load} work_units={work} duration_us=10 "
                        "workload_fingerprint=1111111111111111 "
                        "result_fingerprint=2222222222222222 status=measured"
                    )
    lines.append("agenteval_ucore: worker passed")
    return "\n".join(lines) + "\n"


class CampaignTests(unittest.TestCase):
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

    def test_campaign_schema_accepts_foreign_posix_execution_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = _create(Path(temporary))
            value = json.loads(manifest.read_text(encoding="utf-8"))
            paths = {
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
                    value["environment"], boot["challenge"], boot["guest_log"]
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
                    return_value=_scenario_environment(),
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
            protocol["execution_environment"]["domain"] = "native-login-shell"
            protocol["execution_environment"]["launcher"] = {
                "argv0": "bash",
                "path": "/usr/bin/bash",
                "sha256": "6" * 64,
                "version": "bash 1",
            }
            protocol["execution_environment"]["launcher_argv"] = ["bash", "-lc"]
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

    def test_command_timeout_fails_closed_with_diagnostics(self) -> None:
        error = subprocess.TimeoutExpired(
            ["probe-tool", "--version"], 3, output=b"partial output\n"
        )
        with mock.patch.object(campaign.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(
                campaign.CampaignError, "timed out.*partial output"
            ):
                campaign._run(["probe-tool", "--version"], Path.cwd(), timeout_seconds=3)

    def test_create_requires_seven_clean_boots_and_unique_challenges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(campaign.CampaignError, "at least 7"):
                _create(root, boots=6)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(campaign.CampaignError, "clean committed"):
                _create(root, clean=False)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = _create(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            challenges = [boot["challenge"] for boot in value["boots"]]
            self.assertEqual(
                set(value["environment"]),
                {"bash", "compiler", "git", "linker", "make", "objcopy", "objdump", "python", "qemu"},
            )
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
                self.assertTrue(Path(boot["command_argv"][0]).is_absolute())
                self.assertEqual(
                    boot["command_environment"]["MAKE_TOOL"],
                    value["environment"]["make"]["path"],
                )
                self.assertIn(
                    Path(value["environment"]["git"]["path"]).parent.resolve(),
                    [
                        Path(item).resolve()
                        for item in boot["command_environment"]["PATH"].split(os.pathsep)
                    ],
                )

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
                    return_value=_scenario_environment(),
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
                campaign.record_scenario_boot(
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
                    campaign.record_boot(
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
                    return_value=_scenario_environment(),
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
            (root / "nfs").mkdir()
            (root / "build/kernel").write_bytes(b"kernel")
            (root / "nfs/fs.img").write_bytes(
                b"input:" + boot["challenge"].encode("ascii")
            )
            (root / "nfs/fs-copy.img").write_bytes(b"final")

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
                    self.assertEqual(timeout, 60)
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
                        timeout_seconds=60,
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
                        self.assertEqual(timeout, 60)
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
                    timeout_seconds=60,
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
            self.assertIn("exceeded 60s total deadline", retained)
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
            (root / "nfs").mkdir()
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
                        timeout_seconds=60,
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
                        timeout_seconds=60,
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
                            timeout_seconds=60,
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
                campaign.record_boot(
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
                        timeout_seconds=60,
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
            input_image.parent.mkdir(parents=True)
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
                    campaign.record_boot(
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
                suite = root / "ci" / "evaluation-suite.json"
                original_suite = suite.read_text(encoding="utf-8")
                suite.write_text('{"schema_version":2}\n', encoding="utf-8")
                with self.assertRaisesRegex(campaign.CampaignError, "suite changed"):
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
            input_image.parent.mkdir(parents=True)
            kernel.write_bytes(b"kernel")
            input_image.write_bytes(b"input image")
            final_image.write_bytes(b"final image")
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ):
                with self.assertRaisesRegex(campaign.CampaignError, "challenge"):
                    campaign.record_boot(
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
                campaign.record_boot(
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
                return_value=_scenario_environment(),
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
                        "sample_count": 126,
                        "status": "passed",
                    }
                )
            micro.write_text(json.dumps(value), encoding="utf-8")
            challenges = [boot["challenge"] for boot in scenario["boots"]]
            self.assertEqual(len(challenges), len(set(challenges)))
            self.assertTrue(all(campaign.SCENARIO_CHALLENGE_RE.fullmatch(item) for item in challenges))
            self.assertEqual(
                [boot["target_order"] for boot in scenario["boots"]],
                ["plain-agentos", "agentos-plain", "plain-agentos", "agentos-plain", "plain-agentos", "agentos-plain", "plain-agentos"],
            )
            self.assertIn("--challenge", scenario["boots"][0]["command_argv"])
            self.assertEqual(
                scenario["boots"][0]["command_environment"]["QEMU"],
                "/usr/bin/qemu-system-riscv64",
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
                    campaign.record_scenario_boot(
                        repo=root,
                        manifest_path=scenario_plan,
                        boot_id=boot["boot_id"],
                        exit_code=0,
                        runner_log=runner,
                        host_summary=summary,
                    )
            report = scenario_plan.parent / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "status": "inconclusive",
                        "source_commit": COMMIT,
                        "run_id": "run-1",
                        "summary": {
                            "independent_boots": 7,
                            "unique_challenges": 7,
                            "target_order_balanced": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            campaign.record_scenario_report(
                repo=root, manifest_path=scenario_plan, report_path=report
            )
            campaign.seal_scenario_campaign(scenario_plan)
            with mock.patch.object(
                campaign, "repository_identity", return_value=(COMMIT, True)
            ), mock.patch.object(
                campaign,
                "_probe_scenario_environment",
                return_value=_scenario_environment(),
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
        self.assertIn("EVALUATION_BOOTS:-7", script)
        self.assertIn("EVALUATION_INCLUDE_SCENARIO:-1", script)
        self.assertIn("EVALUATION_SCENARIO_BOOTS:-7", script)
        self.assertIn('"AGENT_TEST_CASE": "agenteval_ucore"', campaign_source)
        self.assertIn('"AGENT_EVAL_CHALLENGE_HEX": challenge', campaign_source)
        self.assertIn("get-boot-field", script)
        self.assertIn("run-boot", script)
        self.assertIn("EVALUATION_MICRO_TIMEOUT:-900", script)
        self.assertIn("with-campaign-lock", script)
        self.assertIn("__run_locked", script)
        self.assertIn("--timeout \"${EVALUATION_MICRO_TIMEOUT}\"", script)
        self.assertIn("exclusive_repo_run_lock", campaign_source)
        self.assertIn("exclusive_evaluation_campaign_lock", campaign_source)
        self.assertIn("CREATE_NEW_PROCESS_GROUP", campaign_source)
        self.assertIn("start_new_session", campaign_source)
        self.assertIn("micro boot exceeded", campaign_source)
        self.assertIn('"${CONTRACT_TOOL}" build', script)
        self.assertIn('"${CONTRACT_TOOL}" verify', script)
        self.assertIn('input_image=repo / "nfs/fs.img"', campaign_source)
        self.assertIn('final_image=repo / "nfs/fs-copy.img"', campaign_source)
        self.assertIn("create-scenario", script)
        self.assertIn("run-scenario-boot", script)
        self.assertIn("check-scenario", script)
        self.assertNotIn('"QEMU=${QEMU}"', script)
        self.assertIn("scenario report differs from a raw-source replay", script)
        self.assertIn("--scenario-report", script)
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
                    return_value=_scenario_environment(),
                ),
            ):
                campaign.create_scenario_campaign(
                    repo=root,
                    micro_manifest=micro,
                    output=scenario_path,
                    requested_boots=7,
                    timeout_seconds=600,
                    wsl_distro="Ubuntu",
                )

            class FakeProcess:
                returncode = 0

                @staticmethod
                def communicate(timeout: int | None = None) -> tuple[str, None]:
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
                    return_value=_scenario_environment(),
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


if __name__ == "__main__":
    unittest.main()
