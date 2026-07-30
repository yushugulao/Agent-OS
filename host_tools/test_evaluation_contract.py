#!/usr/bin/env python3
"""Regression tests for the independent-boot evaluation evidence contract."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import tempfile
from unittest import mock
from pathlib import Path

from evaluation_contract import (
    EvaluationError,
    _binding_sha256,
    _expected_result,
    _expected_workload,
    _file_target_meta,
    _fnv_bytes,
    _format_functional_receipt,
    _functional_semantic,
    _headline_significance_threshold,
    _load_supports_headline_claim,
    _operations_for,
    _semantic_token,
    _task2_schema_fingerprint,
    _task3_semantic,
    build,
    load_run_plan,
    load_scenario_report,
    load_suite,
    verify,
    write_json,
    write_jsonl,
)
from render_evaluation_dashboard import validate_summary
from evaluation_scenario import _summarize as summarize_scenario


ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "ci" / "evaluation-suite.json"
COMMIT = "a" * 40
ENVIRONMENT = "e" * 64


def expect_rejected(action, message: str) -> None:
    try:
        action()
    except EvaluationError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid evidence: {message}")


def assert_guest_sample_call_contract() -> None:
    source = (ROOT / "user" / "src" / "agenteval_ucore.c").read_text(
        encoding="utf-8"
    )
    calls = re.findall(r"(?<!void )print_sample\((.*?)\);", source, re.DOTALL)
    assert len(calls) == 12
    for call in calls:
        arguments = [argument.strip() for argument in call.split(",")]
        assert len(arguments) == 10, arguments
        assert arguments[-2] == "workload", arguments


def marker(
    experiment: dict,
    load: int,
    pair: int,
    role: str,
    challenge: str,
    boot_number: int,
    improvement: int = 100,
) -> str:
    variant = experiment[role]
    order = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
    baseline = 1000 + load + boot_number
    per_operation_duration = baseline if role == "baseline" else baseline - improvement
    operations = _operations_for(experiment, load)
    duration = (
        per_operation_duration * operations
        if experiment["unit"] == "us/query"
        else per_operation_duration
    )
    workload = _expected_workload(experiment, load, pair, challenge)
    result = _expected_result(experiment, load, pair, challenge)
    dataset_size = load if experiment["id"] == "file_query" else 0
    if experiment["id"] == "file_query":
        work_units = operations * load if role == "baseline" else operations
        records_examined = work_units
    else:
        work_units = operations
        records_examined = 0
    return (
        "agenteval_ucore: sample schema=1 "
        f"experiment={experiment['id']} load={load} pair={pair} "
        f"variant={variant['id']} order={order} cache={variant['cache']} "
        f"operations={operations} dataset_size={dataset_size} "
        f"work_units={work_units} records_examined={records_examined} "
        f"result_items={operations} duration_us={duration} "
        f"workload_fingerprint={workload} result_fingerprint={result} status=measured"
    )


def functional_lines(challenge: str, boot_number: int) -> tuple[str, list[str]]:
    launcher_pid = 100 + boot_number
    agent_pid = 200 + boot_number
    launcher_values = [launcher_pid, 0, 0, 0, 0]
    launcher_semantic = _functional_semantic(
        "task1-launcher-semantic-v1", challenge, launcher_values
    )
    launcher = _format_functional_receipt(
        "launcher", challenge, launcher_values, launcher_semantic, launcher=True
    )

    context_base = 0x3FFFFE8000
    task1_values = [
        agent_pid, launcher_pid, 1, 4, 1000 + boot_number, context_base,
        6 * 4096, 0x4147435458543031, 8, 128, 128, 2, 20480, 4096,
        int(challenge, 16) ^ agent_pid ^ context_base,
    ]
    task2_values = [
        25, 23, _task2_schema_fingerprint(), 1001, len("eval-v2"),
        int(challenge, 16) ^ agent_pid,
        int(challenge, 16) ^ 0xA5A5A5A5A5A5A5A5,
        _fnv_bytes(1469598103934665603, b"eval-v2"),
        1002, 1, 1, 1, 1003, 1, 4, (1 << 10) - 1,
        0, -2, 0, -15,
    ]
    task3_values = [6, 6, 6, 1, 3, 3, 9, 10, 0, 128, 128, 5, 6, 133, 1]
    load = 96
    pair = 7
    target = _file_target_meta(load, pair, challenge)
    result = int(_expected_result(
        next(item for item in load_suite(SUITE_PATH)["experiments"] if item["id"] == "file_query"),
        load, pair, challenge,
    ), 16)
    task4_values = [
        load, pair, target, 2000 + target, load * 16, 16, 16, result,
        3000 + boot_number, 4000 + boot_number, 5000 + boot_number, 1, 1,
    ]
    task5_values = [
        agent_pid, 300 + boot_number, 300 + boot_number, agent_pid,
        _semantic_token("task5-event-v1", 2, 0, 0, challenge),
        6000 + boot_number, 7000 + boot_number, 10, 11, 20, 21,
        7100 + boot_number, 7101 + boot_number, 2, 2, -7, 0, 2,
    ]
    values_by_task = {
        "task1": task1_values,
        "task2": task2_values,
        "task3": task3_values,
        "task4": task4_values,
        "task5": task5_values,
    }
    semantic_by_task = {
        task: (
            _task3_semantic(challenge)
            if task == "task3"
            else _functional_semantic(f"{task}-semantic-v1", challenge, values)
        )
        for task, values in values_by_task.items()
    }
    return launcher, [
        _format_functional_receipt(
            task, challenge, values_by_task[task], semantic_by_task[task]
        )
        for task in ("task1", "task2", "task3", "task4", "task5")
    ]
def make_log(suite: dict, boot_number: int, improvement: int = 100, same_order: bool = False) -> tuple[str, str]:
    challenge = f"{boot_number + 1:016x}"
    launcher, receipts = functional_lines(challenge, boot_number)
    lines = ["boot", f"agenteval_ucore: challenge={challenge}", launcher]
    for experiment in suite["experiments"]:
        for load in experiment["loads"]:
            if experiment["id"] == "file_query":
                lines.append(
                    "agenteval_ucore: diagnostic schema=1 experiment=file_query "
                    f"load={load} cache=ready operations=1 work_units=1 duration_us=1 "
                    "index_rebuild_records=0 status=measured"
                )
            for pair in range(1, 8):
                order = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
                if same_order:
                    order = "AB"
                roles = ("baseline", "treatment") if order == "AB" else ("treatment", "baseline")
                for role in roles:
                    value = marker(experiment, load, pair, role, challenge, boot_number, improvement)
                    if same_order:
                        expected = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
                        value = value.replace(f"order={expected}", "order=AB")
                    lines.append(value)
    lines.extend((*receipts, "agenteval_ucore: worker passed", "agenteval_ucore: parent passed"))
    return challenge, "\n".join(lines) + "\n"


def write_campaign(
    root: Path,
    suite: dict,
    count: int = 7,
    improvement_by_boot: list[int] | None = None,
    same_order: bool = False,
    unavailable_last: bool = False,
    all_status: str | None = None,
) -> Path:
    logs = []
    for index in range(count):
        improvement = improvement_by_boot[index] if improvement_by_boot else 100
        challenge, content = make_log(suite, index, improvement, same_order)
        ref = f"boot-{index + 1:02d}/guest.log"
        path = root / ref
        path.parent.mkdir(parents=True)
        status = all_status or ("unavailable" if unavailable_last and index == count - 1 else "supported")
        if status != "supported":
            content = f"runner: {status} boot={index + 1} challenge={challenge}\n"
        path.write_text(content, encoding="utf-8")
        command = [
            "env",
            f"AGENT_EVAL_CHALLENGE_HEX={challenge}",
            f"AGENT_TEST_GUEST_LOG_FILE=results/evaluation/runs/contract-test/raw/{ref}",
            "bash",
            "scripts/run-agent-tests.sh",
        ]
        command_sha256 = hashlib.sha256(
            json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        logs.append({
            "path": ref,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "boot_id": f"boot-{index + 1:02d}",
            "commit": COMMIT,
            "challenge": challenge,
            "kernel_sha256": "b" * 64,
            "image_input_sha256": hashlib.sha256(
                ("pristine-image-" + challenge).encode("ascii")
            ).hexdigest(),
            "image_final_sha256": "d" * 64,
            "runner_log_sha256": hashlib.sha256(("runner-" + challenge).encode("ascii")).hexdigest(),
            "command_argv": command,
            "command_sha256": command_sha256,
            "status": status,
            "detail": None if status == "supported" else f"capture status: {status}",
        })
    plan = {
        "schema_version": 1,
        "kind": "agentos-evaluation-run-plan",
        "run_id": "contract-test",
        "environment_sha256": ENVIRONMENT,
        "campaign_sha256": "f" * 64,
        "suite_sha256": __import__("hashlib").sha256(SUITE_PATH.read_bytes()).hexdigest(),
        "logs": logs,
    }
    path = root / "run-plan.json"
    path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    return path


def refresh_plan_hash(plan_path: Path, source_root: Path) -> None:
    import hashlib
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for item in plan["logs"]:
        item["sha256"] = hashlib.sha256((source_root / item["path"]).read_bytes()).hexdigest()
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")


def main() -> int:
    assert_guest_sample_call_contract()
    suite = load_suite(SUITE_PATH)
    assert suite["claim_family"] == {
        "familywise_alpha": 0.05,
        "hypotheses": ["file_query", "tool_batch", "context_access"],
        "id": "agentos-mechanism-headlines-v1",
        "load_gate": "intersection",
        "method": "bonferroni",
    }
    headline_alpha = _headline_significance_threshold(suite)
    assert abs(headline_alpha - (0.05 / 3)) < 1e-15
    assert {
        experiment["id"]: _expected_workload(experiment, 24, 1, "0000000000000001")
        for experiment in suite["experiments"]
    } == {
        "file_query": "fbfea49331161512",
        "tool_batch": "03dcdc53ddad7ecf",
        "context_access": "56b98203222b1384",
    }
    assert all(
        _expected_result(experiment, 24, 1, "0000000000000001")
        != _expected_result(experiment, 24, 1, "0000000000000002")
        for experiment in suite["experiments"]
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        plan_path = write_campaign(root, suite)
        summary, rows = build(SUITE_PATH, plan_path, root)
        validate_summary(summary)
        assert summary["run"]["status"] == "measured"
        assert all(
            set(item) == {
                "id", "label", "task", "functional_status",
                "performance_status", "performance", "evidence_ids",
            }
            for item in summary["scenarios"]
        )
        assert [
            item["functional_status"] for item in summary["scenarios"]
        ] == ["pass", "pass", "pass", "pass", "pass", "unavailable"]
        assert all(
            item["performance_status"] == "unavailable"
            and item["performance"] is None
            for item in summary["scenarios"]
        )
        assert len(rows) == 7 * (3 * 3 * 7 * 2 + 3)
        benchmark = summary["benchmarks"][0]
        assert benchmark["status"] == "measured"
        assert {estimate["n"] for estimate in benchmark["estimates"]} == {7}
        assert {pair["n"] for pair in benchmark["paired"]} == {7}
        assert all(pair["median"] == 100 for pair in benchmark["paired"])
        assert all(pair["ci_low"] > 0 for pair in benchmark["paired"])
        assert all(pair["sign_test"]["numerator"] == 1 for pair in benchmark["paired"])
        assert all(pair["sign_test"]["denominator"] == 128 for pair in benchmark["paired"])
        assert summary["claims"][0]["status"] == "supported"
        assert summary["methodology"]["multiple_testing"] == {
            "family_id": "agentos-mechanism-headlines-v1",
            "method": "Bonferroni",
            "familywise_alpha": 0.05,
            "hypothesis_count": 3,
            "per_claim_alpha": 0.05 / 3,
            "headline_claims": ["file_query", "tool_batch", "context_access"],
            "load_gate": "intersection (every preregistered load must pass)",
        }
        assert "Bonferroni" in summary["claims"][0]["effect"]
        adjusted = copy.deepcopy(benchmark["paired"][0])
        adjusted["sign_test"]["p_value"] = 0.02
        assert not _load_supports_headline_claim(
            adjusted, benchmark["claim_gate"], [1000.0], headline_alpha
        )
        adjusted["sign_test"]["p_value"] = 0.01
        assert _load_supports_headline_claim(
            adjusted, benchmark["claim_gate"], [1000.0], headline_alpha
        )
        assert all(pair["relative_ci_low"] > 0 for pair in benchmark["paired"])
        first_paired_sample = benchmark["paired"][0]["samples"][0]
        assert set(first_paired_sample) == {
            "trial", "baseline_value", "treatment_value", "value",
            "relative_percent", "inner_pairs",
        }
        assert len(first_paired_sample["inner_pairs"]) == 7
        assert all(
            set(inner) == {
                "pair", "baseline_value", "treatment_value", "value",
                "relative_percent",
            }
            for inner in first_paired_sample["inner_pairs"]
        )
        assert first_paired_sample["value"] == 100
        assert first_paired_sample["baseline_value"] - first_paired_sample[
            "treatment_value"
        ] == 100
        assert len(benchmark["diagnostics"]) == 3
        assert all(len(item["samples"]) == 7 for item in benchmark["diagnostics"])
        diagnostic_rows = [
            row for row in rows
            if row["kind"] == "agentos-evaluation-diagnostic-row"
        ]
        assert len(diagnostic_rows) == 21
        assert all(
            row["metric"] == "index_readiness_duration"
            and row["source_line"] > 0
            and row["source_marker_sha256"] != "0" * 64
            for row in diagnostic_rows
        )
        first_file_pair = {
            row["role"]: row
            for row in rows
            if row["kind"] == "agentos-evaluation-metric-row"
            and row["boot_id"] == "boot-01"
            and row["experiment"] == "file_query"
            and row["load"] == 24
            and row["inner_pair"] == 1
        }
        assert first_file_pair["baseline"]["work_units"] == 24 * 16
        assert first_file_pair["treatment"]["work_units"] == 16
        assert first_file_pair["baseline"]["work_units"] == first_file_pair[
            "baseline"
        ]["records_examined"]
        assert first_file_pair["treatment"]["work_units"] == first_file_pair[
            "treatment"
        ]["records_examined"]
        pair_one_orders = {
            row["boot_id"]: row["order"]
            for row in rows
            if row["kind"] == "agentos-evaluation-metric-row"
            and row["experiment"] == "file_query"
            and row["load"] == 24
            and row["inner_pair"] == 1
            and row["role"] == "baseline"
        }
        assert pair_one_orders["boot-01"] == "AB"
        assert pair_one_orders["boot-02"] == "BA"
        evidence_paths = {item["id"]: item["path"] for item in summary["evidence"]}
        assert evidence_paths["run-plan"] == "run-plan.json"
        assert all(
            evidence_paths[f"raw-boot-{index:03d}"]
            == f"raw/boot-{index:02d}/guest.log"
            for index in range(1, 8)
        )

        missing_receipt_root = root / "missing-functional-receipt"
        missing_receipt_root.mkdir()
        missing_receipt_plan = write_campaign(missing_receipt_root, suite)
        missing_receipt_log = missing_receipt_root / "boot-01/guest.log"
        missing_receipt_log.write_text(
            "\n".join(
                line for line in missing_receipt_log.read_text(
                    encoding="utf-8"
                ).splitlines()
                if not line.startswith(
                    "agenteval_ucore: functional schema=1 task=task3 "
                )
            ) + "\n",
            encoding="utf-8",
        )
        refresh_plan_hash(missing_receipt_plan, missing_receipt_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, missing_receipt_plan, missing_receipt_root
            ),
            "exactly one receipt for every Task1-5",
        )

        reordered_receipt_root = root / "reordered-functional-receipt"
        reordered_receipt_root.mkdir()
        reordered_receipt_plan = write_campaign(reordered_receipt_root, suite)
        reordered_receipt_log = reordered_receipt_root / "boot-01/guest.log"
        reordered_lines = reordered_receipt_log.read_text(
            encoding="utf-8"
        ).splitlines()
        task2_position = next(
            index for index, line in enumerate(reordered_lines)
            if line.startswith("agenteval_ucore: functional schema=1 task=task2 ")
        )
        task3_position = next(
            index for index, line in enumerate(reordered_lines)
            if line.startswith("agenteval_ucore: functional schema=1 task=task3 ")
        )
        reordered_lines[task2_position], reordered_lines[task3_position] = (
            reordered_lines[task3_position], reordered_lines[task2_position]
        )
        reordered_receipt_log.write_text(
            "\n".join(reordered_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(reordered_receipt_plan, reordered_receipt_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, reordered_receipt_plan, reordered_receipt_root
            ),
            "missing or out of order",
        )

        forged_receipt_root = root / "forged-functional-receipt"
        forged_receipt_root.mkdir()
        forged_receipt_plan = write_campaign(forged_receipt_root, suite)
        forged_receipt_log = forged_receipt_root / "boot-01/guest.log"
        forged_text = forged_receipt_log.read_text(encoding="utf-8")
        forged_text = forged_text.replace(
            "values=6,6,6,1,3,3,9,10,0,128,128,5,6,133,1",
            "values=6,6,6,1,3,3,9,10,0,128,128,6,6,133,1",
            1,
        )
        forged_receipt_log.write_text(forged_text, encoding="utf-8")
        refresh_plan_hash(forged_receipt_plan, forged_receipt_root)
        expect_rejected(
            lambda: build(SUITE_PATH, forged_receipt_plan, forged_receipt_root),
            "functional receipt hash differs",
        )

        forged_semantic_root = root / "forged-functional-semantic"
        forged_semantic_root.mkdir()
        forged_semantic_plan = write_campaign(forged_semantic_root, suite)
        forged_semantic_log = forged_semantic_root / "boot-01/guest.log"
        forged_semantic_lines = forged_semantic_log.read_text(
            encoding="utf-8"
        ).splitlines()
        challenge = "0000000000000001"
        equal_tick_values = [
            200, 300, 300, 200,
            _semantic_token("task5-event-v1", 2, 0, 0, challenge),
            6000, 7000, 10, 11, 20, 21, 7100, 7100, 2, 2, -7, 0, 2,
        ]
        equal_tick_line = _format_functional_receipt(
            "task5", challenge, equal_tick_values,
            _functional_semantic(
                "task5-semantic-v1", challenge, equal_tick_values
            ),
        )
        forged_semantic_lines = [
            equal_tick_line
            if line.startswith(
                "agenteval_ucore: functional schema=1 task=task5 "
            )
            else line
            for line in forged_semantic_lines
        ]
        forged_semantic_log.write_text(
            "\n".join(forged_semantic_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(forged_semantic_plan, forged_semantic_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, forged_semantic_plan, forged_semantic_root
            ),
            "Task5 sleep/wake lifecycle receipt is inconsistent",
        )

        failed_receipt_root = root / "failed-functional-receipt"
        failed_receipt_root.mkdir()
        failed_receipt_plan = write_campaign(failed_receipt_root, suite)
        failed_receipt_log = failed_receipt_root / "boot-01/guest.log"
        failed_receipt_log.write_text(
            failed_receipt_log.read_text(encoding="utf-8").replace(
                "status=passed\nagenteval_ucore: functional schema=1 task=task3 ",
                "status=failed\nagenteval_ucore: functional schema=1 task=task3 ",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(failed_receipt_plan, failed_receipt_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, failed_receipt_plan, failed_receipt_root
            ),
            "functional receipt status differs",
        )

        partial_boot_root = root / "partial-functional-boots"
        partial_boot_root.mkdir()
        partial_boot_plan = write_campaign(
            partial_boot_root, suite, count=8, unavailable_last=True
        )
        partial_summary, _ = build(
            SUITE_PATH, partial_boot_plan, partial_boot_root
        )
        assert partial_summary["run"]["status"] == "unavailable"
        assert all(
            item["functional_status"] == "unavailable"
            for item in partial_summary["scenarios"]
            if item["task"] != "task6"
        )

        scenario_samples = []
        required_modules = [
            "context", "structured_tool", "metadata_query", "observation"
        ]
        for index in range(1, 8):
            boot_id = f"boot-{index:02d}"
            challenge = f"ch-{index:012d}"
            outcome = {"status": "completed", "challenge": challenge}
            outcome_fingerprint = _binding_sha256(
                outcome, "research-platform-outcome-v2"
            )
            plain_raw = {
                "functional_acceptance": {
                    "required": False,
                    "status": "not_applicable",
                }
            }
            plain_raw["sha256"] = _binding_sha256(
                plain_raw, "scenario-raw-source-receipt-v1"
            )
            module_receipt_sha = hashlib.sha256(
                f"agentos-modules-{index}".encode("ascii")
            ).hexdigest()
            challenge_source_sha = hashlib.sha256(
                f"challenge-source-{index}".encode("ascii")
            ).hexdigest()
            run_summary_sha = hashlib.sha256(
                f"run-summary-{index}".encode("ascii")
            ).hexdigest()
            state_inventory_sha = hashlib.sha256(
                f"state-inventory-{index}".encode("ascii")
            ).hexdigest()
            acceptance_binding = {
                "schema": "agentos-task6-functional-binding-v1",
                "challenge": challenge,
                "challenge_source_sha256": challenge_source_sha,
                "run_summary_sha256": run_summary_sha,
                "state_inventory_sha256": state_inventory_sha,
                "module_receipt_sha256": module_receipt_sha,
                "required_modules": required_modules,
            }
            acceptance_binding["sha256"] = _binding_sha256(
                acceptance_binding, "agentos-task6-functional-binding-v1"
            )
            acceptance_receipt = {
                "required": True,
                "status": "verified",
                "path": "state-extracted/rp_agentos_acceptance",
                "bytes": 128,
                "sha256": module_receipt_sha,
                "acceptance": {
                    "schema": "agentos_task6_acceptance_v2",
                    "required_modules": required_modules,
                    "modules": [
                        {"module": module, "status": "verified"}
                        for module in required_modules
                    ],
                },
                "binding": acceptance_binding,
            }
            agentos_raw = {
                "challenge_source": {"sha256": challenge_source_sha},
                "run_summary": {"sha256": run_summary_sha},
                "state_inventory": {
                    "sha256": state_inventory_sha,
                    "files": [{
                        "path": "rp_agentos_acceptance",
                        "bytes": 128,
                        "sha256": module_receipt_sha,
                    }],
                },
                "functional_acceptance": acceptance_receipt,
            }
            agentos_raw["sha256"] = _binding_sha256(
                agentos_raw, "scenario-raw-source-receipt-v1"
            )
            binding = {
                "source_commit": COMMIT,
                "run_id": "contract-test",
                "boot_id": boot_id,
                "boot_order": index,
                "target_order": "AB" if index % 2 else "BA",
                "challenge": challenge,
                "program_order": ["rp_runner"],
                "outcome_fingerprint": outcome_fingerprint,
                "source_receipts": {
                    "plain": plain_raw["sha256"],
                    "agentos": agentos_raw["sha256"],
                },
            }
            binding["sha256"] = _binding_sha256(binding, "scenario-sample-v1")
            scenario_samples.append({
                "sample_id": f"contract-test:{boot_id}",
                "binding": binding,
                "outcome": outcome,
                "outcome_fingerprint": outcome_fingerprint,
                "targets": {
                    "plain": {
                        "makespan_ms": 100 + index,
                        "programs": [{"program": "rp_runner", "elapsed_ms": 100 + index}],
                        "raw_source_receipt": plain_raw,
                    },
                    "agentos": {
                        "makespan_ms": 80 + index,
                        "programs": [{"program": "rp_runner", "elapsed_ms": 80 + index}],
                        "raw_source_receipt": agentos_raw,
                    },
                },
            })
        scenario = {
            "schema_version": 1,
            "scenario_id": "research-platform-seeded",
            "source_commit": COMMIT,
            "run_id": "contract-test",
            "status": "supported",
            "samples": scenario_samples,
            "summary": summarize_scenario(scenario_samples),
        }
        scenario["report_sha256"] = _binding_sha256(scenario, "scenario-report-v1")
        scenario_dir = root / "scenario"
        scenario_dir.mkdir()
        scenario_path = scenario_dir / "report.json"
        scenario_path.write_text(json.dumps(scenario) + "\n", encoding="utf-8")
        expect_rejected(
            lambda: build(SUITE_PATH, plan_path, root, scenario_path),
            "supplied together",
        )
        scenario_plan_value = {
            "phase": "collected",
            "report": {
                "status": "recorded",
                "sha256": __import__("hashlib").sha256(scenario_path.read_bytes()).hexdigest(),
            },
            "run": {
                "id": "contract-test",
                "commit": COMMIT,
                "environment_sha256": ENVIRONMENT,
            },
            "boots": [
                {
                    "boot_id": f"boot-{index:02d}",
                    "status": "passed",
                    "challenge": f"ch-{index:012d}",
                    "target_order": "plain-agentos" if index % 2 else "agentos-plain",
                }
                for index in range(1, 8)
            ],
        }
        scenario_plan_path = scenario_dir / "scenario-plan.json"
        scenario_plan_path.write_text(json.dumps(scenario_plan_value), encoding="utf-8")
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            scenario_summary, _ = build(
                SUITE_PATH, plan_path, root, scenario_path, scenario_plan_path
            )
        validate_summary(scenario_summary)
        task6 = next(item for item in scenario_summary["scenarios"] if item["task"] == "task6")
        assert set(task6) == {
            "id", "label", "task", "functional_status", "performance_status",
            "performance", "evidence_ids",
        }
        assert task6["functional_status"] == "pass"
        assert task6["performance_status"] == "supported"
        assert task6["performance"] == scenario["summary"]["paired_improvement"]
        assert task6["evidence_ids"] == [
            "research-scenario-plan", "research-scenario-report"
        ]
        scenario_evidence_paths = {
            item["id"]: item["path"] for item in scenario_summary["evidence"]
        }
        assert scenario_evidence_paths["research-scenario-report"] == "scenario/report.json"
        assert scenario_evidence_paths["research-scenario-plan"] == "scenario/scenario-plan.json"

        failed_boot_plan = copy.deepcopy(scenario_plan_value)
        failed_boot_plan["boots"][0]["status"] = "failed"
        failed_boot_plan_path = root / "failed-boot-scenario-plan.json"
        failed_boot_plan_path.write_text(
            json.dumps(failed_boot_plan), encoding="utf-8"
        )
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            expect_rejected(
                lambda: build(
                    SUITE_PATH,
                    plan_path,
                    root,
                    scenario_path,
                    failed_boot_plan_path,
                ),
                "differs from its sealed boot plan",
            )

        inconclusive_scenario = copy.deepcopy(scenario)
        for sample in inconclusive_scenario["samples"]:
            plain_ms = sample["targets"]["plain"]["makespan_ms"]
            sample["targets"]["agentos"]["makespan_ms"] = plain_ms
            sample["targets"]["agentos"]["programs"][0]["elapsed_ms"] = plain_ms
        inconclusive_scenario["summary"] = summarize_scenario(
            inconclusive_scenario["samples"]
        )
        inconclusive_scenario["status"] = "inconclusive"
        inconclusive_scenario.pop("report_sha256")
        inconclusive_scenario["report_sha256"] = _binding_sha256(
            inconclusive_scenario, "scenario-report-v1"
        )
        inconclusive_path = root / "inconclusive-scenario.json"
        inconclusive_path.write_text(
            json.dumps(inconclusive_scenario) + "\n", encoding="utf-8"
        )
        inconclusive_plan = copy.deepcopy(scenario_plan_value)
        inconclusive_plan["report"]["sha256"] = hashlib.sha256(
            inconclusive_path.read_bytes()
        ).hexdigest()
        inconclusive_plan_path = root / "inconclusive-scenario-plan.json"
        inconclusive_plan_path.write_text(
            json.dumps(inconclusive_plan), encoding="utf-8"
        )
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            inconclusive_summary, _ = build(
                SUITE_PATH,
                plan_path,
                root,
                inconclusive_path,
                inconclusive_plan_path,
            )
        validate_summary(inconclusive_summary)
        inconclusive_task6 = next(
            item for item in inconclusive_summary["scenarios"]
            if item["task"] == "task6"
        )
        assert inconclusive_task6["functional_status"] == "pass"
        assert inconclusive_task6["performance_status"] == "inconclusive"
        assert inconclusive_task6["performance"] == inconclusive_scenario[
            "summary"
        ]["paired_improvement"]

        scenario_summary_path = root / "scenario-summary.json"
        scenario_rows_path = root / "scenario-metrics.jsonl"
        write_json(scenario_summary_path, inconclusive_summary)
        write_jsonl(scenario_rows_path, rows)
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            assert verify(
                SUITE_PATH,
                plan_path,
                root,
                scenario_summary_path,
                scenario_rows_path,
                inconclusive_path,
                inconclusive_plan_path,
            ) == inconclusive_summary
        forged_contract_summary = copy.deepcopy(inconclusive_summary)
        forged_task6 = next(
            item for item in forged_contract_summary["scenarios"]
            if item["task"] == "task6"
        )
        forged_task6["functional_status"] = "partial"
        forged_task6["performance_status"] = "supported"
        write_json(scenario_summary_path, forged_contract_summary)
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            expect_rejected(
                lambda: verify(
                    SUITE_PATH,
                    plan_path,
                    root,
                    scenario_summary_path,
                    scenario_rows_path,
                    inconclusive_path,
                    inconclusive_plan_path,
                ),
                "summary differs",
            )

        insufficient_scenario = copy.deepcopy(inconclusive_scenario)
        insufficient_scenario["samples"] = insufficient_scenario["samples"][:6]
        insufficient_scenario["summary"] = summarize_scenario(
            insufficient_scenario["samples"]
        )
        insufficient_scenario.pop("report_sha256")
        insufficient_scenario["report_sha256"] = _binding_sha256(
            insufficient_scenario, "scenario-report-v1"
        )
        insufficient_path = root / "insufficient-scenario.json"
        insufficient_path.write_text(
            json.dumps(insufficient_scenario) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(insufficient_path, load_run_plan(plan_path)[0]),
            "functional acceptance is incomplete",
        )

        reordered_scenario = copy.deepcopy(inconclusive_scenario)
        reordered_scenario["samples"][0], reordered_scenario["samples"][1] = (
            reordered_scenario["samples"][1], reordered_scenario["samples"][0]
        )
        reordered_scenario["summary"] = summarize_scenario(
            reordered_scenario["samples"]
        )
        reordered_scenario.pop("report_sha256")
        reordered_scenario["report_sha256"] = _binding_sha256(
            reordered_scenario, "scenario-report-v1"
        )
        reordered_path = root / "reordered-scenario.json"
        reordered_path.write_text(
            json.dumps(reordered_scenario) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(reordered_path, load_run_plan(plan_path)[0]),
            "not in sealed boot order",
        )

        forged_output = copy.deepcopy(scenario)
        forged_output["samples"][0]["outcome"]["status"] = "forged"
        forged_output.pop("report_sha256")
        forged_output["report_sha256"] = _binding_sha256(
            forged_output, "scenario-report-v1"
        )
        forged_output_path = root / "forged-output-scenario.json"
        forged_output_path.write_text(
            json.dumps(forged_output) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(forged_output_path, load_run_plan(plan_path)[0]),
            "normalized output differs from its fingerprint",
        )

        forged_receipt = copy.deepcopy(scenario)
        forged_receipt["samples"][0]["targets"]["plain"][
            "raw_source_receipt"
        ]["sha256"] = "3" * 64
        forged_receipt.pop("report_sha256")
        forged_receipt["report_sha256"] = _binding_sha256(
            forged_receipt, "scenario-report-v1"
        )
        forged_receipt_path = root / "forged-receipt-scenario.json"
        forged_receipt_path.write_text(
            json.dumps(forged_receipt) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(forged_receipt_path, load_run_plan(plan_path)[0]),
            "differs from its bound source receipt",
        )

        forged_scenario = copy.deepcopy(scenario)
        for sample in forged_scenario["samples"]:
            plain_ms = sample["targets"]["plain"]["makespan_ms"]
            sample["targets"]["agentos"]["makespan_ms"] = plain_ms + 10
            sample["targets"]["agentos"]["programs"][0]["elapsed_ms"] = plain_ms + 10
        forged_scenario["summary"] = summarize_scenario(forged_scenario["samples"])
        forged_scenario["status"] = "supported"
        forged_scenario.pop("report_sha256")
        forged_scenario["report_sha256"] = _binding_sha256(
            forged_scenario, "scenario-report-v1"
        )
        forged_scenario_path = root / "forged-scenario.json"
        forged_scenario_path.write_text(
            json.dumps(forged_scenario) + "\n", encoding="utf-8"
        )
        micro_plan, _ = load_run_plan(plan_path)
        expect_rejected(
            lambda: load_scenario_report(forged_scenario_path, micro_plan),
            "status differs from paired performance gate",
        )

        summary_path = root / "summary.json"
        rows_path = root / "metrics.jsonl"
        write_json(summary_path, summary)

        rebound_rows = copy.deepcopy(rows)
        rebound_rows[0]["environment_sha256"] = "f" * 64
        write_jsonl(rows_path, rebound_rows)
        expect_rejected(
            lambda: verify(SUITE_PATH, plan_path, root, summary_path, rows_path),
            "metrics JSONL differs",
        )
        write_jsonl(rows_path, rows)
        assert verify(SUITE_PATH, plan_path, root, summary_path, rows_path) == summary

        rebound = copy.deepcopy(summary)
        rebound["run"]["id"] = "rebound"
        write_json(summary_path, rebound)
        expect_rejected(
            lambda: verify(SUITE_PATH, plan_path, root, summary_path, rows_path),
            "summary differs",
        )
        write_json(summary_path, summary)

        extra_suite = copy.deepcopy(suite)
        extra_suite["unexpected"] = True
        extra_suite_path = root / "extra-suite.json"
        extra_suite_path.write_text(json.dumps(extra_suite), encoding="utf-8")
        expect_rejected(lambda: load_suite(extra_suite_path), "extra=['unexpected']")

        weakened_family = copy.deepcopy(suite)
        weakened_family["claim_family"]["method"] = "none"
        weakened_family_path = root / "weakened-family-suite.json"
        weakened_family_path.write_text(
            json.dumps(weakened_family), encoding="utf-8"
        )
        expect_rejected(
            lambda: load_suite(weakened_family_path),
            "headline claim family is invalid",
        )

        rebound_suite = copy.deepcopy(suite)
        rebound_suite["experiments"][0]["direction"] = "higher_is_better"
        rebound_suite_path = root / "rebound-suite.json"
        rebound_suite_path.write_text(json.dumps(rebound_suite), encoding="utf-8")
        expect_rejected(
            lambda: build(rebound_suite_path, plan_path, root),
            "suite differs",
        )

        duplicate_image_plan = root / "duplicate-image-plan.json"
        duplicate_image = json.loads(plan_path.read_text(encoding="utf-8"))
        duplicate_image["logs"][1]["image_input_sha256"] = (
            duplicate_image["logs"][0]["image_input_sha256"]
        )
        duplicate_image_plan.write_text(json.dumps(duplicate_image), encoding="utf-8")
        expect_rejected(
            lambda: build(SUITE_PATH, duplicate_image_plan, root),
            "challenge-specialized pristine image",
        )

        divergent_command_plan = root / "divergent-command-plan.json"
        divergent_command = json.loads(plan_path.read_text(encoding="utf-8"))
        divergent_command["logs"][1]["command_argv"].insert(-2, "EXTRA_BUILD_MODE=1")
        command_raw = json.dumps(
            divergent_command["logs"][1]["command_argv"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        divergent_command["logs"][1]["command_sha256"] = hashlib.sha256(command_raw).hexdigest()
        divergent_command_plan.write_text(json.dumps(divergent_command), encoding="utf-8")
        expect_rejected(
            lambda: build(SUITE_PATH, divergent_command_plan, root),
            "differ beyond planned challenge/log paths",
        )

        nan_plan = root / "nan-plan.json"
        nan_plan.write_text(plan_path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": NaN'), encoding="utf-8")
        expect_rejected(lambda: build(SUITE_PATH, nan_plan, root), "non-finite")

        one_boot = root / "one-boot"
        one_boot.mkdir()
        one_plan = write_campaign(one_boot, suite, count=1)
        expect_rejected(lambda: build(SUITE_PATH, one_plan, one_boot), "fewer than seven")

        missing = root / "missing"
        missing.mkdir()
        missing_plan = write_campaign(missing, suite)
        first_log = missing / "boot-01/guest.log"
        lines = first_log.read_text(encoding="utf-8").splitlines()
        lines.pop(next(i for i, line in enumerate(lines) if "experiment=file_query load=24 pair=1 variant=index" in line))
        first_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        refresh_plan_hash(missing_plan, missing)
        expect_rejected(
            lambda: build(SUITE_PATH, missing_plan, missing),
            "physical order differs from preregistration",
        )

        extra_marker = root / "extra-marker"
        extra_marker.mkdir()
        extra_plan = write_campaign(extra_marker, suite)
        extra_log = extra_marker / "boot-01/guest.log"
        extra_log.write_text(
            extra_log.read_text(encoding="utf-8").replace(
                "agenteval_ucore: sample schema=1",
                "agenteval_ucore: sample schema=1 extra=1",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(extra_plan, extra_marker)
        expect_rejected(lambda: build(SUITE_PATH, extra_plan, extra_marker), "schema/order mismatch")

        fingerprint = root / "fingerprint"
        fingerprint.mkdir()
        fingerprint_plan = write_campaign(fingerprint, suite)
        fingerprint_log = fingerprint / "boot-01/guest.log"
        fingerprint_log.write_text(
            re.sub(
                r"result_fingerprint=[0-9a-f]{16}",
                "result_fingerprint=ffffffffffffffff",
                fingerprint_log.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(fingerprint_plan, fingerprint)
        expect_rejected(lambda: build(SUITE_PATH, fingerprint_plan, fingerprint), "Host semantic oracle")

        workload = root / "workload-fingerprint"
        workload.mkdir()
        workload_plan = write_campaign(workload, suite)
        workload_log = workload / "boot-01/guest.log"
        workload_log.write_text(
            re.sub(
                r"workload_fingerprint=[0-9a-f]{16}",
                "workload_fingerprint=ffffffffffffffff",
                workload_log.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(workload_plan, workload)
        expect_rejected(lambda: build(SUITE_PATH, workload_plan, workload), "challenge-bound")

        reused = root / "reused-result"
        reused.mkdir()
        reused_plan = write_campaign(reused, suite)
        first_results = {}
        for line in (reused / "boot-01/guest.log").read_text(encoding="utf-8").splitlines():
            if line.startswith("agenteval_ucore: sample "):
                key = re.search(r"experiment=([^ ]+) load=([0-9]+) pair=([0-9]+)", line).groups()
                first_results[key] = re.search(r"result_fingerprint=([0-9a-f]{16})", line).group(1)
        second_log = reused / "boot-02/guest.log"
        rewritten = []
        for line in second_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("agenteval_ucore: sample "):
                key = re.search(r"experiment=([^ ]+) load=([0-9]+) pair=([0-9]+)", line).groups()
                line = re.sub(
                    r"result_fingerprint=[0-9a-f]{16}",
                    f"result_fingerprint={first_results[key]}",
                    line,
                )
            rewritten.append(line)
        second_log.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        refresh_plan_hash(reused_plan, reused)
        expect_rejected(lambda: build(SUITE_PATH, reused_plan, reused), "Host semantic oracle")

        challenge_receipt = root / "challenge-as-result"
        challenge_receipt.mkdir()
        challenge_receipt_plan = write_campaign(challenge_receipt, suite)
        for boot in range(1, 8):
            log_path = challenge_receipt / f"boot-{boot:02d}/guest.log"
            challenge = f"{boot:016x}"
            log_path.write_text(
                re.sub(
                    r"result_fingerprint=[0-9a-f]{16}",
                    f"result_fingerprint={challenge}",
                    log_path.read_text(encoding="utf-8"),
                ),
                encoding="utf-8",
            )
        refresh_plan_hash(challenge_receipt_plan, challenge_receipt)
        expect_rejected(
            lambda: build(SUITE_PATH, challenge_receipt_plan, challenge_receipt),
            "Host semantic oracle",
        )

        workload_as_result = root / "workload-as-result"
        workload_as_result.mkdir()
        workload_as_result_plan = write_campaign(workload_as_result, suite)
        result_log = workload_as_result / "boot-01/guest.log"
        result_lines = []
        for line in result_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("agenteval_ucore: sample "):
                workload_value = re.search(
                    r"workload_fingerprint=([0-9a-f]{16})", line
                ).group(1)
                line = re.sub(
                    r"result_fingerprint=[0-9a-f]{16}",
                    f"result_fingerprint={workload_value}",
                    line,
                )
            result_lines.append(line)
        result_log.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(workload_as_result_plan, workload_as_result)
        expect_rejected(
            lambda: build(SUITE_PATH, workload_as_result_plan, workload_as_result),
            "Host semantic oracle",
        )

        failed_marker = root / "guest-check-failed"
        failed_marker.mkdir()
        failed_plan = write_campaign(failed_marker, suite)
        failed_log = failed_marker / "boot-01/guest.log"
        failed_log.write_text(
            failed_log.read_text(encoding="utf-8").replace(
                "agenteval_ucore: worker passed",
                "agenteval_ucore: check failed: forged-success\nagenteval_ucore: worker passed",
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(failed_plan, failed_marker)
        expect_rejected(lambda: build(SUITE_PATH, failed_plan, failed_marker), "check failure")

        early_completion = root / "early-worker-completion"
        early_completion.mkdir()
        early_completion_plan = write_campaign(early_completion, suite)
        early_completion_log = early_completion / "boot-01/guest.log"
        completion_lines = early_completion_log.read_text(
            encoding="utf-8"
        ).splitlines()
        completion_lines.remove("agenteval_ucore: worker passed")
        challenge_position = next(
            index for index, line in enumerate(completion_lines)
            if line.startswith("agenteval_ucore: challenge=")
        )
        completion_lines.insert(
            challenge_position + 1, "agenteval_ucore: worker passed"
        )
        early_completion_log.write_text(
            "\n".join(completion_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(early_completion_plan, early_completion)
        expect_rejected(
            lambda: build(
                SUITE_PATH, early_completion_plan, early_completion
            ),
            "challenge/completion does not enclose samples",
        )

        diagnostic = root / "diagnostic"
        diagnostic.mkdir()
        diagnostic_plan = write_campaign(diagnostic, suite)
        diagnostic_log = diagnostic / "boot-01/guest.log"
        diagnostic_log.write_text(
            diagnostic_log.read_text(encoding="utf-8").replace(
                "cache=ready operations=1 work_units=1 duration_us=1 index_rebuild_records=0",
                "cache=ready operations=1 work_units=1 duration_us=1 index_rebuild_records=1",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(diagnostic_plan, diagnostic)
        expect_rejected(lambda: build(SUITE_PATH, diagnostic_plan, diagnostic), "readiness conflicts")

        order = root / "same-order"
        order.mkdir()
        order_plan = write_campaign(order, suite, same_order=True)
        expect_rejected(
            lambda: build(SUITE_PATH, order_plan, order),
            "physical order differs from preregistration",
        )

        pair_blocks = root / "swapped-pair-blocks"
        pair_blocks.mkdir()
        pair_blocks_plan = write_campaign(pair_blocks, suite)
        pair_blocks_log = pair_blocks / "boot-01/guest.log"
        pair_lines = pair_blocks_log.read_text(encoding="utf-8").splitlines()
        pair1 = [
            index for index, line in enumerate(pair_lines)
            if "experiment=file_query load=24 pair=1 " in line
        ]
        pair2 = [
            index for index, line in enumerate(pair_lines)
            if "experiment=file_query load=24 pair=2 " in line
        ]
        assert pair1 == list(range(pair1[0], pair1[0] + 2))
        assert pair2 == list(range(pair2[0], pair2[0] + 2))
        first_block = pair_lines[pair1[0]:pair1[0] + 2]
        second_block = pair_lines[pair2[0]:pair2[0] + 2]
        pair_lines[pair1[0]:pair1[0] + 2] = second_block
        pair_lines[pair2[0]:pair2[0] + 2] = first_block
        pair_blocks_log.write_text("\n".join(pair_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(pair_blocks_plan, pair_blocks)
        expect_rejected(
            lambda: build(SUITE_PATH, pair_blocks_plan, pair_blocks),
            "physical order differs from preregistration",
        )

        shallow_scan = root / "shallow-file-scan"
        shallow_scan.mkdir()
        shallow_scan_plan = write_campaign(shallow_scan, suite)
        shallow_scan_log = shallow_scan / "boot-01/guest.log"
        shallow_lines = shallow_scan_log.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(shallow_lines):
            if "experiment=file_query load=96 pair=1 variant=scan " in line:
                shallow_lines[index] = line.replace(
                    "work_units=1536 records_examined=1536",
                    "work_units=96 records_examined=96",
                )
                break
        shallow_scan_log.write_text("\n".join(shallow_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(shallow_scan_plan, shallow_scan)
        expect_rejected(
            lambda: build(SUITE_PATH, shallow_scan_plan, shallow_scan),
            "not measured traversal",
        )

        shallow_index = root / "shallow-file-index"
        shallow_index.mkdir()
        shallow_index_plan = write_campaign(shallow_index, suite)
        shallow_index_log = shallow_index / "boot-01/guest.log"
        shallow_lines = shallow_index_log.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(shallow_lines):
            if "experiment=file_query load=96 pair=1 variant=index " in line:
                shallow_lines[index] = line.replace(
                    "work_units=16 records_examined=16",
                    "work_units=1 records_examined=1",
                )
                break
        shallow_index_log.write_text("\n".join(shallow_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(shallow_index_plan, shallow_index)
        expect_rejected(
            lambda: build(SUITE_PATH, shallow_index_plan, shallow_index),
            "not measured candidates",
        )

        work = root / "wrong-work"
        work.mkdir()
        work_plan = write_campaign(work, suite)
        work_log = work / "boot-01/guest.log"
        work_log.write_text(work_log.read_text(encoding="utf-8").replace("operations=24 dataset_size=0 work_units=24", "operations=1 dataset_size=0 work_units=24", 2), encoding="utf-8")
        refresh_plan_hash(work_plan, work)
        expect_rejected(lambda: build(SUITE_PATH, work_plan, work), "configured load")

        crossing = root / "crossing"
        crossing.mkdir()
        crossing_plan = write_campaign(crossing, suite, improvement_by_boot=[100, -100, 100, -100, 100, -100, 100])
        crossing_summary, _ = build(SUITE_PATH, crossing_plan, crossing)
        assert all(claim["status"] == "not_supported" for claim in crossing_summary["claims"])
        assert any(pair["ci_low"] <= 0 for pair in crossing_summary["benchmarks"][0]["paired"])
        assert any(pair["relative_ci_low"] <= 0 for pair in crossing_summary["benchmarks"][0]["paired"])

        quantized = root / "timer-quantized"
        quantized.mkdir()
        quantized_plan = write_campaign(quantized, suite)
        baseline_variants = {
            experiment["baseline"]["id"] for experiment in suite["experiments"]
        }
        for boot in range(1, 8):
            log_path = quantized / f"boot-{boot:02d}/guest.log"
            rewritten = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("agenteval_ucore: sample "):
                    variant = re.search(r" variant=([a-z]+) ", line).group(1)
                    duration = 1 if variant in baseline_variants else 0
                    line = re.sub(r"duration_us=[0-9]+", f"duration_us={duration}", line)
                rewritten.append(line)
            log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        refresh_plan_hash(quantized_plan, quantized)
        quantized_summary, _ = build(SUITE_PATH, quantized_plan, quantized)
        validate_summary(quantized_summary)
        assert all(
            claim["status"] == "not_supported"
            for claim in quantized_summary["claims"]
        )

        heterogeneous = root / "heterogeneous-pairs"
        heterogeneous.mkdir()
        heterogeneous_plan = write_campaign(heterogeneous, suite)
        baseline_durations = [100, 2, 50, 3, 60, 4, 70]
        treatment_durations = [101, 3, 0, 4, 1, 5, 2]
        for boot in range(1, 8):
            log_path = heterogeneous / f"boot-{boot:02d}/guest.log"
            rewritten = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("agenteval_ucore: sample ") and "experiment=file_query load=24 " in line:
                    pair = int(re.search(r" pair=([1-7]) ", line).group(1))
                    values = baseline_durations if " variant=scan " in line else treatment_durations
                    line = re.sub(
                        r"duration_us=[0-9]+",
                        f"duration_us={values[pair - 1] * 16}",
                        line,
                    )
                rewritten.append(line)
            log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        refresh_plan_hash(heterogeneous_plan, heterogeneous)
        heterogeneous_summary, _ = build(SUITE_PATH, heterogeneous_plan, heterogeneous)
        validate_summary(heterogeneous_summary)
        heterogeneous_file = next(
            item for item in heterogeneous_summary["benchmarks"] if item["id"] == "file_query"
        )
        pair24 = next(item for item in heterogeneous_file["paired"] if item["load"] == 24)
        estimates24 = {
            item["target_id"]: item["value"]
            for item in heterogeneous_file["estimates"] if item["load"] == 24
        }
        assert pair24["median"] == -1
        assert estimates24["file_query:scan"] - estimates24["file_query:index"] == 47
        assert all(sample["value"] == -1 for sample in pair24["samples"])
        assert all(sample["baseline_value"] == 50 for sample in pair24["samples"])
        assert all(sample["treatment_value"] == 3 for sample in pair24["samples"])
        assert all(len(sample["inner_pairs"]) == 7 for sample in pair24["samples"])
        assert pair24["samples"][0]["inner_pairs"][0] == {
            "pair": 1,
            "baseline_value": 100,
            "treatment_value": 101,
            "value": -1,
            "relative_percent": -1.0,
        }

        unavailable = root / "unavailable"
        unavailable.mkdir()
        unavailable_plan = write_campaign(unavailable, suite, unavailable_last=True)
        unavailable_summary, unavailable_rows = build(SUITE_PATH, unavailable_plan, unavailable)
        validate_summary(unavailable_summary)
        assert unavailable_summary["run"]["status"] == "unavailable"
        assert all(item["status"] == "unavailable" for item in unavailable_summary["benchmarks"])
        assert all(not item["estimates"] and not item["samples"] for item in unavailable_summary["benchmarks"])
        assert all(row["value"] > 0 for row in unavailable_rows)

        for capture_status in ("unavailable", "failed"):
            status_root = root / f"all-{capture_status}"
            status_root.mkdir()
            status_plan = write_campaign(status_root, suite, all_status=capture_status)
            status_summary, status_rows = build(SUITE_PATH, status_plan, status_root)
            validate_summary(status_summary)
            assert status_summary["run"]["status"] == capture_status
            assert status_rows == []
            status_summary_path = status_root / "summary.json"
            status_rows_path = status_root / "metrics.jsonl"
            write_json(status_summary_path, status_summary)
            write_jsonl(status_rows_path, status_rows)
            assert status_rows_path.read_bytes() == b""
            assert verify(
                SUITE_PATH,
                status_plan,
                status_root,
                status_summary_path,
                status_rows_path,
            ) == status_summary

    print("test_evaluation_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
