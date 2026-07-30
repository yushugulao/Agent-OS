#!/usr/bin/env python3
"""Regression tests for portable AgentOS evaluation evidence bundles."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

import evaluation_bundle as bundle
from evaluation_campaign import (
    _micro_boot_environment,
    _scenario_boot_environment,
    export_run_plan,
    validate_campaign,
    validate_scenario_campaign,
)
from evaluation_contract import build, load_suite, write_json, write_jsonl
from evaluation_scenario import collect_scenario, read_expected_programs
from render_evaluation_dashboard import render
from test_evaluation_campaign import _scenario_environment
from test_evaluation_contract import COMMIT, SUITE_PATH, make_log
from test_evaluation_scenario import _write_target


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_strict(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def expect_rejected(action, message: str) -> None:
    try:
        action()
    except bundle.BundleError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid evaluation bundle: {message}")


def tool(argv0: str, marker: str) -> dict[str, str]:
    return {
        "argv0": argv0,
        "path": str((Path(__file__).resolve().parent / "fixture-tools" / Path(argv0).name).resolve()),
        "sha256": marker * 64,
        "version": f"{argv0} fixture 1",
    }


def make_run(
    root: Path,
    *,
    artifact_root: str = "results/evaluation/runs/contract-test",
) -> Path:
    run = root / "run"
    raw = run / "raw"
    raw.mkdir(parents=True)
    suite = load_suite(SUITE_PATH)
    environment = {
        "bash": tool("bash", "1"),
        "compiler": tool("riscv64-linux-gnu-gcc", "2"),
        "git": tool("git", "6"),
        "linker": tool("riscv64-linux-gnu-ld", "7"),
        "make": tool("make", "3"),
        "objcopy": tool("riscv64-linux-gnu-objcopy", "8"),
        "objdump": tool("riscv64-linux-gnu-objdump", "9"),
        "python": tool("python3", "4"),
        "qemu": tool("qemu-system-riscv64", "5"),
    }
    boots = []
    prefix = artifact_root
    for index in range(7):
        number = index + 1
        boot_id = f"boot-{number:02d}"
        challenge, guest_text = make_log(suite, index)
        boot_dir = raw / boot_id
        boot_dir.mkdir()
        artifacts = {
            "guest.log": guest_text.encode("utf-8"),
            "runner.log": f"runner {boot_id}\n".encode("ascii"),
            "kernel": b"shared fixture kernel\n",
            "fs.img": f"input {challenge}\n".encode("ascii"),
            "fs-copy.img": f"final {challenge}\n".encode("ascii"),
        }
        for name, data in artifacts.items():
            (boot_dir / name).write_bytes(data)
        raw_ref = f"{prefix}/raw/{boot_id}"
        command = [
            environment["bash"]["path"],
            str(
                (Path(__file__).resolve().parents[1] / "scripts" / "run-agent-tests.sh").resolve()
            ),
            f"AGENT_EVAL_CHALLENGE_HEX={challenge}",
            f"AGENT_TEST_GUEST_LOG_FILE={raw_ref}/guest.log",
        ]
        boots.append({
            "boot_id": boot_id,
            "challenge": challenge,
            "command_argv": command,
            "command_environment": _micro_boot_environment(
                environment, challenge, f"{raw_ref}/guest.log"
            ),
            "exit_code": 0,
            "finished_at_utc": f"2026-07-30T00:00:{number:02d}Z",
            "guest_log": f"{raw_ref}/guest.log",
            "guest_log_sha256": digest(boot_dir / "guest.log"),
            "image_final_path": f"{raw_ref}/fs-copy.img",
            "image_final_sha256": digest(boot_dir / "fs-copy.img"),
            "image_input_path": f"{raw_ref}/fs.img",
            "image_input_sha256": digest(boot_dir / "fs.img"),
            "kernel_path": f"{raw_ref}/kernel",
            "kernel_sha256": digest(boot_dir / "kernel"),
            "observed_sample_orders": ["AB", "BA"],
            "runner_log": f"{raw_ref}/runner.log",
            "runner_log_sha256": digest(boot_dir / "runner.log"),
            "sample_count": 126,
            "status": "passed",
        })
    campaign = {
        "boots": boots,
        "environment": environment,
        "kind": "agentos-evaluation-campaign",
        "phase": "collected",
        "protocol": {
            "fresh_filesystem_per_boot": True,
            "independent_unit": "fresh-qemu-boot",
            "minimum_boots": 7,
            "requested_boots": 7,
            "sample_order_policy": "guest-paired-alternating-ab-ba",
            "suite_path": "ci/evaluation-suite.json",
            "suite_sha256": digest(SUITE_PATH),
            "target": "agentos-same-kernel-ablation",
        },
        "run": {
            "artifact_root": artifact_root,
            "clean_worktree": True,
            "commit": COMMIT,
            "completed_at_utc": "2026-07-30T00:01:00Z",
            "id": "contract-test",
            "started_at_utc": "2026-07-30T00:00:00Z",
        },
        "schema_version": 1,
    }
    validate_campaign(campaign)
    write_strict(run / "campaign.json", campaign)
    export_run_plan(run / "campaign.json", run / "run-plan.json")
    summary, rows = build(SUITE_PATH, run / "run-plan.json", raw)
    write_json(run / "summary.json", summary)
    write_jsonl(run / "metrics.jsonl", rows)
    (run / "preflight.log").write_text("fixture preflight passed\n", encoding="utf-8")
    render(run / "summary.json", run / "dashboard")
    return run


def add_formal_scenario(run: Path) -> None:
    scenario_root = run / "scenario"
    raw_root = scenario_root / "raw"
    programs, roles = read_expected_programs()
    boot_dirs = []
    order_codes = []
    for number in range(1, 8):
        boot_id = f"boot-{number:02d}"
        boot_dir = raw_root / boot_id
        challenge = f"ch-{number:012d}"
        order = "AB" if number % 2 else "BA"
        _write_target(boot_dir, "plain", programs, roles, number, order, challenge)
        _write_target(boot_dir, "agentos", programs, roles, number, order, challenge)
        (boot_dir / "runner.log").write_text(f"scenario runner {boot_id}\n", encoding="utf-8")
        write_strict(boot_dir / "host-summary.json", {
            "status": "ready", "challenge": challenge,
            "target_order": "plain-agentos" if number % 2 else "agentos-plain",
        })
        boot_dirs.append(boot_dir)
        order_codes.append(order)
    report = collect_scenario(
        boot_dirs, source_commit=COMMIT, run_id="contract-test", target_orders=order_codes
    )
    assert report["summary"]["functional_acceptance"]["status"] == "passed"
    write_strict(scenario_root / "report.json", report)

    micro = json.loads((run / "campaign.json").read_text(encoding="utf-8"))
    execution = _scenario_environment()
    python_bin = micro["environment"]["python"]["path"]
    driver_path = str(
        (Path(__file__).resolve().parent / "check_seeded_action_state.py").resolve()
    )
    toolprefix = execution["tools"]["compiler"]["path"][:-3]
    protocol = {
        "collector_path": "host_tools/evaluation_scenario.py",
        "collector_sha256": "6" * 64,
        "execution_environment": execution,
        "git_bin": micro["environment"]["git"]["path"],
        "git_sha256": micro["environment"]["git"]["sha256"],
        "input_driver_path": "host_tools/check_seeded_action_state.py",
        "input_driver_sha256": "7" * 64,
        "minimum_boots": 7,
        "python_bin": python_bin,
        "python_sha256": micro["environment"]["python"]["sha256"],
        "requested_boots": 7,
        "timeout_seconds": 600,
        "toolprefix": toolprefix,
        "wsl_distro": "Ubuntu",
    }
    boots = []
    prefix = "results/evaluation/runs/contract-test/scenario"
    for number, boot_dir in enumerate(boot_dirs, 1):
        boot_id = f"boot-{number:02d}"
        challenge = f"ch-{number:012d}"
        target_order = "plain-agentos" if number % 2 else "agentos-plain"
        work_dir = f"{prefix}/raw/{boot_id}"
        host_summary = f"{work_dir}/host-summary.json"
        boots.append({
            "boot_id": boot_id,
            "challenge": challenge,
            "command_argv": [
                python_bin, driver_path, "--work-dir", work_dir,
                "--timeout", "600", "--wsl-distro", "Ubuntu", "--target-order",
                target_order, "--challenge", challenge, "--json-out", host_summary,
            ],
            "command_environment": _scenario_boot_environment(
                {
                    "python": micro["environment"]["python"],
                    "git": micro["environment"]["git"],
                },
                execution,
            ),
            "exit_code": 0,
            "finished_at_utc": f"2026-07-30T00:02:{number:02d}Z",
            "host_summary": host_summary,
            "host_summary_sha256": digest(boot_dir / "host-summary.json"),
            "runner_log": f"{work_dir}/runner.log",
            "runner_log_sha256": digest(boot_dir / "runner.log"),
            "status": "passed",
            "target_order": target_order,
            "work_dir": work_dir,
        })
    scenario_plan = {
        "boots": boots,
        "kind": "agentos-evaluation-scenario-campaign",
        "phase": "collected",
        "protocol": protocol,
        "report": {
            "path": f"{prefix}/report.json",
            "sha256": digest(scenario_root / "report.json"),
            "status": "recorded",
        },
        "run": {
            "artifact_root": micro["run"]["artifact_root"],
            "commit": COMMIT,
            "environment_sha256": bundle._environment_sha256(micro),
            "id": "contract-test",
            "scenario_environment_sha256": hashlib.sha256(
                bundle._canonical_bytes(execution)
            ).hexdigest(),
        },
        "schema_version": 1,
    }
    validate_scenario_campaign(scenario_plan)
    write_strict(scenario_root / "scenario-plan.json", scenario_plan)
    (scenario_root / "collector.log").write_text("collector passed\n", encoding="utf-8")
    (run / "scenario-preflight.log").write_text("scenario preflight passed\n", encoding="utf-8")
    summary, rows = build(
        SUITE_PATH, run / "run-plan.json", run / "raw",
        scenario_root / "report.json", scenario_root / "scenario-plan.json",
    )
    write_json(run / "summary.json", summary)
    write_jsonl(run / "metrics.jsonl", rows)
    render(run / "summary.json", run / "dashboard")


def main() -> int:
    expected = Path("raw/boot-01/guest.log")
    canonical = "results/evaluation/runs/contract-test/raw/boot-01/guest.log"
    assert bundle._campaign_artifact_relative(
        canonical, PurePosixPath(expected.as_posix()), "micro guest log",
        artifact_root="results/evaluation/runs/contract-test",
    ) == expected.as_posix()
    assert bundle._campaign_artifact_relative(
        "custom/evaluation-output/runs/contract-test/raw/boot-01/guest.log",
        PurePosixPath(expected.as_posix()), "custom micro guest log",
        artifact_root="custom/evaluation-output/runs/contract-test",
    ) == expected.as_posix()
    for invalid in (
        "../../outside/raw/boot-01/guest.log",
        "other/prefix/raw/boot-01/guest.log",
        "results/evaluation/runs/other/raw/boot-01/guest.log",
        "C:/results/evaluation/runs/contract-test/raw/boot-01/guest.log",
        "results\\evaluation\\runs\\contract-test\\raw\\boot-01\\guest.log",
    ):
        expect_rejected(
            lambda invalid=invalid: bundle._campaign_artifact_relative(
                invalid, PurePosixPath(expected.as_posix()),
                "micro guest log",
                artifact_root="results/evaluation/runs/contract-test",
            ),
            "canonical",
        )
    expect_rejected(
        lambda: bundle._campaign_artifact_relative(
            canonical, PurePosixPath(expected.as_posix()), "micro guest log",
            artifact_root="../../outside",
        ),
        "canonical relative",
    )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        development_run = make_run(
            root / "development",
            artifact_root="custom/evaluation-output/runs/contract-test",
        )
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run, suite_path=SUITE_PATH,
                output=root / "formal-missing-scenario",
            ),
            "formal evidence requires",
        )
        development = root / "development-bundle"
        manifest = bundle.create_bundle(
            run_dir=development_run, suite_path=SUITE_PATH,
            output=development, profile="development",
        )
        assert manifest["profile"] == {
            "name": "development", "formal": False,
            "warning": bundle.DEVELOPMENT_WARNING,
        }
        assert bundle.verify_bundle(development) == manifest

        extra = development_run / "raw" / "boot-01" / "extra.log"
        extra.write_text("unplanned\n", encoding="utf-8")
        failed_output = root / "must-not-publish"
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run, suite_path=SUITE_PATH,
                output=failed_output, profile="development",
            ),
            "raw inventory differs",
        )
        assert not failed_output.exists()
        extra.unlink()
        guest = development_run / "raw" / "boot-01" / "guest.log"
        guest.write_bytes(guest.read_bytes() + b"tamper\n")
        expect_rejected(
            lambda: bundle.create_bundle(
                run_dir=development_run, suite_path=SUITE_PATH,
                output=failed_output, profile="development",
            ),
            "hash differs",
        )

        formal_run = make_run(root / "formal")
        add_formal_scenario(formal_run)
        formal = root / "formal-bundle"
        formal_manifest = bundle.create_bundle(
            run_dir=formal_run, suite_path=SUITE_PATH, output=formal
        )
        assert formal_manifest["profile"] == {
            "name": "formal", "formal": True, "warning": None,
        }
        assert any(
            item["artifact_id"] == "micro/boot-01/kernel"
            for item in formal_manifest["artifacts"]
        )
        assert any(
            item["artifact_id"] == "scenario/boot-01/host-summary"
            for item in formal_manifest["artifacts"]
        )
        assert bundle.verify_bundle(formal) == formal_manifest
        weakened = json.loads(
            (formal_run / "summary.json").read_text(encoding="utf-8")
        )
        next(
            item for item in weakened["scenarios"] if item["task"] == "task1"
        )["functional_status"] = "unavailable"
        expect_rejected(
            lambda: bundle._verify_formal_summary(weakened),
            "Task 1-6 functional acceptance",
        )

        link_parent = root / "linked-parent"
        try:
            os.symlink(root / "real-parent", link_parent, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        else:
            expect_rejected(
                lambda: bundle.create_bundle(
                    run_dir=formal_run, suite_path=SUITE_PATH,
                    output=link_parent / "bundle",
                ),
                "link-backed",
            )
    print("test_evaluation_bundle: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
