#!/usr/bin/env python3
"""Contract tests for the offline evaluation dashboard."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import evaluation_kernel_cost as kernel_cost
from test_evaluation_kernel_cost import Fixture as KernelCostFixture
from test_evaluation_kernel_cost import _write_json as write_kernel_json

from render_evaluation_dashboard import (
    DashboardError,
    _binding_sha256,
    _bootstrap_interval,
    render,
    validate_summary,
)


HOST_TOOLS = Path(__file__).resolve().parent
ASSETS = HOST_TOOLS / "assets"
TEST_RUN_PLAN_BYTES = b'{"kind":"dashboard-test-run-plan","schema_version":1}\n'
TEST_RUN_PLAN_SHA256 = hashlib.sha256(TEST_RUN_PLAN_BYTES).hexdigest()


def _test_percentile(values: list[int], quantile: float) -> float | int:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = lower if position == lower else lower + 1
    if lower == upper:
        return ordered[lower]
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _scenario_report_payload(summary: dict, evidence_id: str) -> bytes:
    candidates = [
        scenario
        for scenario in summary["scenarios"]
        if evidence_id in scenario["evidence_ids"]
    ]
    scenario = next(
        (candidate for candidate in candidates if candidate["performance"] is not None),
        candidates[0],
    )
    report_status = (
        scenario["performance_status"]
        if scenario["performance_status"] in {"supported", "inconclusive"}
        else "inconclusive"
    )
    programs = ["rp_runner", "rp_artifact", "rp_llm_resp"]
    samples = []
    for index in range(1, 8):
        challenge = f"{index:016x}"
        outcome = {
            "research_rerun": {
                "host_action_rerun": f"usable-run:{index}",
                "parent": f"run-{index}",
                "status": "completed",
            },
            "workflow_stage": {
                "host_workflow_stage_action": "align",
                "attempt": "2",
                "status": "failed",
                "command": "align_reads",
                "duration_ms": "1200",
            },
            "artifact_derivation": {
                "host_artifact_derive": "raw-counts.csv",
                "output": "normalized-counts.csv",
                "operation": "normalize",
                "stage": "analyze",
                "sha256": hashlib.sha256(f"artifact-{index}".encode()).hexdigest(),
            },
            "llm_response": {
                "host_llm_response_id": f"response-{index}",
                "host_llm_response_request": f"request-{index}",
                "host_llm_response_provider": "fixture",
                "host_llm_response_mode": "offline",
                "host_llm_response_summary": "bounded",
                "host_llm_response_citations": "2",
            },
            "challenge": challenge,
            "workflow": {"run_id": f"run-{index}", "workflow_id": f"workflow-{index}"},
            "artifact_input": {
                "host_artifact_input": "reads_R1.fastq",
                "kind": "fastq",
                "sha256": hashlib.sha256(f"input-{index}".encode()).hexdigest(),
                "bytes": "2048",
                "source": "upload",
            },
        }
        outcome_fingerprint = _binding_sha256(outcome, "research-platform-outcome-v2")
        receipts = {
            target: hashlib.sha256(f"{target}-{index}".encode()).hexdigest()
            for target in ("plain", "agentos")
        }
        binding = {
            "source_commit": summary["run"]["commit"],
            "run_id": summary["run"]["id"],
            "boot_id": f"boot-{index:02d}",
            "boot_order": index,
            "target_order": "AB" if index % 2 else "BA",
            "challenge": challenge,
            "program_order": programs,
            "outcome_fingerprint": outcome_fingerprint,
            "source_receipts": receipts,
        }
        binding["sha256"] = _binding_sha256(binding, "scenario-sample-v1")
        targets = {}
        for target, advantage in (("plain", 0), ("agentos", 5)):
            rows = [
                {"program": program, "elapsed_ms": 100 + program_index * 10 + index - advantage}
                for program_index, program in enumerate(programs)
            ]
            targets[target] = {
                "makespan_ms": sum(row["elapsed_ms"] for row in rows) + 37,
                "programs": rows,
                "raw_source_receipt": {"sha256": receipts[target]},
            }
        samples.append({
            "sample_id": f"{summary['run']['id']}:boot-{index:02d}",
            "binding": binding,
            "outcome": outcome,
            "outcome_fingerprint": outcome_fingerprint,
            "targets": targets,
        })

    target_summaries = {}
    for target in ("plain", "agentos"):
        makespans = [sample["targets"][target]["makespan_ms"] for sample in samples]
        target_summaries[target] = {
            "successful_boots": len(samples),
            "success_rate": 1.0,
            "makespan_ms": {
                "p50": _test_percentile(makespans, 0.50),
                "p95": _test_percentile(makespans, 0.95),
                "min": min(makespans),
                "max": max(makespans),
            },
            "programs": {
                program: {
                    "p50_ms": _test_percentile(
                        [
                            next(row["elapsed_ms"] for row in sample["targets"][target]["programs"] if row["program"] == program)
                            for sample in samples
                        ],
                        0.50,
                    ),
                    "p95_ms": _test_percentile(
                        [
                            next(row["elapsed_ms"] for row in sample["targets"][target]["programs"] if row["program"] == program)
                            for sample in samples
                        ],
                        0.95,
                    ),
                }
                for program in programs
            },
        }
    modules = ["context", "structured_tool", "metadata_query", "observation"]
    functional = {
        "status": "passed",
        "required_target": "agentos",
        "required_modules": modules,
        "verified_boots": len(samples),
        "boot_receipts": [
            {
                "sample_id": sample["sample_id"],
                "challenge": sample["binding"]["challenge"],
                "module_receipt_sha256": hashlib.sha256(f"modules-{index}".encode()).hexdigest(),
                "binding_sha256": hashlib.sha256(f"binding-{index}".encode()).hexdigest(),
                "raw_source_receipt_sha256": sample["targets"]["agentos"]["raw_source_receipt"]["sha256"],
            }
            for index, sample in enumerate(samples, 1)
        ],
    }
    report = {
        "schema_version": 1,
        "scenario_id": scenario["id"],
        "source_commit": summary["run"]["commit"],
        "run_id": summary["run"]["id"],
        "status": report_status,
        "samples": samples,
        "summary": {
            "independent_boots": len(samples),
            "minimum_supported_boots": 7,
            "unique_challenges": len(samples),
            "paired_success_rate": 1.0,
            "target_order_counts": {"AB": 4, "BA": 3},
            "target_order_balanced": True,
            "paired_improvement": copy.deepcopy(scenario["performance"] or {}),
            "functional_acceptance": functional,
            "targets": target_summaries,
        },
    }
    report["report_sha256"] = _binding_sha256(report, "scenario-report-v1")
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _evidence_payload(evidence_id: str, summary: dict) -> bytes:
    if evidence_id == "run-plan":
        return TEST_RUN_PLAN_BYTES
    if evidence_id == "raw-perf":
        return ("\n".join(f"AGENT_EVAL marker line {index:03d}" for index in range(1, 101)) + "\n").encode(
            "utf-8"
        )
    if evidence_id == "raw-scenario":
        return _scenario_report_payload(summary, evidence_id)
    return (
        json.dumps(
            {"kind": "dashboard-test-evidence", "evidence_id": evidence_id},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def write_render_input(root: Path, summary: dict, name: str = "summary.json") -> Path:
    for item in summary["evidence"]:
        path = root.joinpath(*item["path"].split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _evidence_payload(item["id"], summary)
        path.write_bytes(data)
        item["sha256"] = hashlib.sha256(data).hexdigest()
        receipt = item.setdefault("receipt", {})
        receipt["bytes"] = len(data)
        if item["id"] == "raw-scenario":
            receipt["report_sha256"] = json.loads(data)["report_sha256"]
        if item["id"] == "raw-perf":
            line_numbers = [41, 44, 47]
            lines = data.decode("utf-8").splitlines()
            receipt["line_numbers"] = line_numbers
            receipt["marker_sha256s"] = [
                hashlib.sha256(lines[line_number - 1].encode("utf-8")).hexdigest()
                for line_number in line_numbers
            ]
    (root / "metrics.jsonl").write_text("{}\n", encoding="utf-8")
    if (root / "scenario/report.json").is_file():
        (root / "scenario/scenario-plan.json").write_text("{}\n", encoding="utf-8")
    source = root / name
    source.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return source


def rewrite_summary(source: Path, summary: dict) -> None:
    source.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def write_kernel_cost_sidecar(root: Path, *, fail_measurement: bool = False) -> dict:
    fixture = KernelCostFixture()
    try:
        config = root / "kernel-cost-config.json"
        environment = root / "kernel-build/environment.json"
        build_config = root / "kernel-build/kernel-build-config.json"
        build_log = root / "kernel-build/raw/kernel-build.log"
        build_manifest = root / "kernel-build/kernel-build.json"
        for source, destination in (
            (fixture.config, config),
            (fixture.environment, environment),
            (fixture.build_config, build_config),
            (fixture.build_log, build_log),
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        build = copy.deepcopy(fixture.build)
        build["build_config"] = {
            "path": "kernel-build/kernel-build-config.json",
            "sha256": kernel_cost._file_sha(build_config),
        }
        build["build_log"] = {
            "path": "kernel-build/raw/kernel-build.log",
            "sha256": kernel_cost._file_sha(build_log),
        }
        write_kernel_json(build_manifest, build)
        runner = fixture.runner
        if fail_measurement:
            def runner(argv, timeout, maximum):
                if list(argv)[-1] == "--version":
                    return fixture.runner(argv, timeout, maximum)
                return kernel_cost.ToolExecution(1, b"", b"size failed\n")
        report = kernel_cost.collect_report(
            config_path=config,
            repository_root=fixture.root,
            environment_manifest_path=environment,
            build_manifest_path=build_manifest,
            size_tool=fixture.tool,
            evidence_root=root,
            runner=runner,
            repository_reader=fixture.repository,
        )
        report_path = root / "kernel-cost-report.json"
        fragment_path = root / "kernel-cost-fragment.json"
        write_kernel_json(report_path, report)
        write_kernel_json(
            fragment_path,
            kernel_cost.build_dashboard_fragment(report_path, config, root),
        )
        return report
    finally:
        fixture.close()


def fixture() -> dict:
    evidence = [
        {
            "id": "raw-perf",
            "label": "Guest 性能原始日志",
            "status": "verified",
            "kind": "guest-log",
            "source": "raw/boot-01/guest.log:88",
            "path": "raw/boot-01/guest.log",
            "sha256": "a" * 64,
        },
        {
            "id": "raw-scenario",
            "label": "科研场景回执",
            "status": "verified",
            "kind": "research-platform-scenario",
            "source": "scenario/report.json",
            "path": "scenario/report.json",
            "sha256": "b" * 64,
        },
        {
            "id": "missing-task6",
            "label": "任务 6 未运行原因",
            "status": "unavailable",
            "kind": "absence-receipt",
            "source": "scenario/preflight.json",
            "path": "scenario/preflight.json",
            "sha256": "c" * 64,
            "reason": "qemu unavailable",
        },
        {
            "id": "run-plan",
            "label": "评测运行计划",
            "status": "verified",
            "kind": "evaluation-run-plan",
            "source": "strict run-plan JSON",
            "path": "run-plan.json",
            "sha256": TEST_RUN_PLAN_SHA256,
            "receipt": {"environment_sha256": "e" * 64},
        },
    ]
    estimates = []
    samples = []
    values = {
        ("reference", "128"): (120.0, 119.2, 120.9),
        ("agentos", "128"): (71.0, 70.8, 71.5),
        ("reference", "1024"): (79.0, 75.0, 83.0),
        ("agentos", "1024"): (13.4, 12.8, 14.2),
    }
    for (target_id, load), (value, lower, upper) in values.items():
        deltas = (-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3)
        estimates.append(
            {
                "target_id": target_id,
                "load": load,
                "value": value,
                "lower": value + min(deltas),
                "upper": value + max(deltas),
                "p95": value + max(deltas),
                "n": 7,
            }
        )
        for trial, delta in enumerate(deltas, start=1):
            samples.append(
                {
                    "target_id": target_id,
                    "load": load,
                    "trial": trial,
                    "value": value + delta,
                    "evidence_id": "raw-perf",
                }
            )
    paired = []
    for load in ("128", "1024"):
        baseline_value = values[("reference", load)][0]
        treatment_value = values[("agentos", load)][0]
        improvement = baseline_value - treatment_value
        deltas = (-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3)
        relative_samples = [improvement / abs(baseline_value + delta) * 100.0 for delta in deltas]
        relative_low, relative_high = _bootstrap_interval(
            relative_samples, f"{TEST_RUN_PLAN_SHA256}:metadata-query:{load}:paired-relative"
        )
        paired.append(
            {
                "load": load,
                "status": "measured",
                "n": 7,
                "median": improvement,
                "p95": improvement,
                "ci_low": improvement,
                "ci_high": improvement,
                "relative_median_percent": improvement / baseline_value * 100.0,
                "relative_ci_low": relative_low,
                "relative_ci_high": relative_high,
                "samples": [
                    {
                        "trial": trial,
                        "baseline_value": baseline_value + delta,
                        "treatment_value": treatment_value + delta,
                        "value": improvement,
                        "relative_percent": relative,
                        "inner_pairs": [
                            {
                                "pair": pair_id,
                                "baseline_value": baseline_value + delta,
                                "treatment_value": treatment_value + delta,
                                "value": improvement,
                                "relative_percent": improvement / abs(baseline_value + delta) * 100.0,
                            }
                            for pair_id in range(1, 8)
                        ],
                    }
                    for trial, (relative, delta) in enumerate(zip(relative_samples, deltas), start=1)
                ],
                "sign_test": {
                    "alternative": "treatment_better",
                    "wins": 7,
                    "losses": 0,
                    "ties": 0,
                    "n": 7,
                    "p_value": 1 / 128,
                    "numerator": 1,
                    "denominator": 128,
                },
            }
        )
    diagnostics = []
    for load in ("128", "1024"):
        diagnostic_samples = [
            {
                "boot_id": f"boot-{trial:02d}",
                "cache": "ready",
                "duration_us": trial,
                "work_units": int(load),
                "index_rebuild_records": 0,
                "evidence_id": "raw-perf",
                "source_line": 40 + trial,
            }
            for trial in range(1, 8)
        ]
        diagnostics.append(
            {
                "load": load,
                "status": "measured",
                "unit": "us",
                "cache_states": ["ready"],
                "duration_us": {"median": 4.0, "p95": 7.0, "n": 7},
                "work_units": {"median": float(load), "p95": float(load), "n": 7},
                "index_rebuild_records": {"median": 0.0, "p95": 0.0, "n": 7},
                "samples": diagnostic_samples,
            }
        )
    return {
        "schema_version": 1,
        "kind": "agentos-evaluation-summary",
        "run": {
            "id": "evaluation-20260730",
            "status": "unavailable",
            "run_plan_sha256": TEST_RUN_PLAN_SHA256,
            "label": "竞赛候选版本",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "generated_at": "2026-07-30T08:00:00+08:00",
            "evidence_grade": "E2-local-raw",
            "cache_policy": "cold-per-pair",
            "conclusion": "只陈述证据支持的比较结论",
        },
        "targets": [
            {"id": "reference", "label": "安全机制对照 uCore", "role": "baseline"},
            {"id": "agentos", "label": "AgentOS", "role": "treatment"},
        ],
        "benchmarks": [
            {
                "id": "metadata-query",
                "label": "Metadata 索引查询",
                "task": "task4",
                "baseline": "reference",
                "treatment": "agentos",
                "unit": "us/query",
                "direction": "lower_is_better",
                "claim_gate": {
                    "minimum_absolute_improvement_us": 5,
                    "minimum_baseline_duration_us": 20,
                    "minimum_relative_improvement_percent": 5,
                },
                "loads": ["128", "1024"],
                "estimates": estimates,
                "samples": samples,
                "paired": paired,
                "diagnostics": diagnostics,
                "evidence_ids": ["raw-perf"],
                "status": "measured",
                "cache_policy": "cold-per-pair",
            },
            {
                "id": "scheduler-tail",
                "label": "调度尾延迟",
                "task": "task3",
                "baseline": "reference",
                "treatment": "agentos",
                "unit": "us",
                "direction": "lower_is_better",
                "claim_gate": None,
                "loads": [],
                "estimates": [],
                "samples": [],
                "paired": [],
                "diagnostics": [],
                "evidence_ids": [],
                "status": "unavailable",
            },
        ],
        "scenarios": [
            {
                "id": f"scenario-{task}",
                "label": f"任务 {task} 端到端场景",
                "task": f"task{task}",
                "functional_status": "pass" if task < 6 else "unavailable",
                "performance_status": "unavailable",
                "performance": None,
                "evidence_ids": ["raw-scenario"] if task < 6 else ["missing-task6"],
            }
            for task in range(1, 7)
        ],
        "methodology": {
            "design": "paired interleaved A/B",
            "cache_policy": "cold-per-pair",
            "repetitions": 3,
            "environment": {"machine": "QEMU virt", "clock": "rdtime"},
            "multiple_testing": {
                "family_id": "dashboard-test-headlines-v1",
                "method": "Bonferroni",
                "familywise_alpha": 0.05,
                "hypothesis_count": 3,
                "per_claim_alpha": 0.05 / 3,
                "headline_claims": ["metadata-query", "scheduler-tail", "reserved-headline"],
                "load_gate": "intersection (every preregistered load must pass)",
            },
            "limitations": ["单机实验", "任务 6 当前 unavailable"],
        },
        "evidence": evidence,
        "claims": [
            {
                "id": "claim-metadata",
                "title": "AgentOS 的 metadata 查询在该负载与环境下更快",
                "status": "supported",
                "effect": "两个负载的配对区间均支持更低延迟；不外推到总体 CPU 性能。",
                "benchmark_id": "metadata-query",
                "evidence_ids": ["raw-perf"],
            }
        ],
    }


def remove_one_paired_advantage(benchmark: dict) -> None:
    pair = benchmark["paired"][0]
    load = str(pair["load"])
    baseline_by_trial = {
        str(sample["trial"]): sample["value"]
        for sample in benchmark["samples"]
        if sample["target_id"] == benchmark["baseline"] and str(sample["load"]) == load
    }
    for sample in benchmark["samples"]:
        if sample["target_id"] == benchmark["treatment"] and str(sample["load"]) == load:
            sample["value"] = baseline_by_trial[str(sample["trial"])]
    baseline_estimate = next(
        estimate
        for estimate in benchmark["estimates"]
        if estimate["target_id"] == benchmark["baseline"] and str(estimate["load"]) == load
    )
    treatment_estimate = next(
        estimate
        for estimate in benchmark["estimates"]
        if estimate["target_id"] == benchmark["treatment"] and str(estimate["load"]) == load
    )
    for field in ("value", "lower", "upper", "p95", "n"):
        treatment_estimate[field] = baseline_estimate[field]
    for sample in pair["samples"]:
        sample["treatment_value"] = sample["baseline_value"]
        for inner in sample["inner_pairs"]:
            inner["treatment_value"] = inner["baseline_value"]
            inner["value"] = 0.0
            inner["relative_percent"] = 0.0
        sample["value"] = 0.0
        sample["relative_percent"] = 0.0
    pair.update(
        median=0.0,
        p95=0.0,
        ci_low=0.0,
        ci_high=0.0,
        relative_median_percent=0.0,
        relative_ci_low=0.0,
        relative_ci_high=0.0,
        sign_test={
            "alternative": "treatment_better",
            "wins": 0,
            "losses": 0,
            "ties": 7,
            "n": 0,
            "p_value": 1.0,
            "numerator": 1,
            "denominator": 1,
        },
    )


def attach_supported_scenario(summary: dict) -> dict:
    samples = []
    for index in range(7):
        plain = 100.0 + index
        agentos = 80.0 + index
        improvement = plain - agentos
        samples.append(
            {
                "sample_id": f"scenario:boot-{index + 1:02d}",
                "boot_id": f"boot-{index + 1:02d}",
                "target_order": "AB" if index % 2 == 0 else "BA",
                "plain_ms": plain,
                "agentos_ms": agentos,
                "improvement_ms": improvement,
                "relative_improvement_percent": improvement * 100.0 / plain,
            }
        )
    seed = _binding_sha256(samples, "scenario-paired-bootstrap-seed-v1")
    improvements = [item["improvement_ms"] for item in samples]
    relatives = [item["relative_improvement_percent"] for item in samples]
    ci_low, ci_high = _bootstrap_interval(improvements, f"{seed}:absolute")
    relative_low, relative_high = _bootstrap_interval(relatives, f"{seed}:relative")
    performance = {
        "direction": "plain_minus_agentos_positive_is_better",
        "lower_is_better": True,
        "unit": "ms",
        "claim_gate": {
            "minimum_absolute_improvement_ms": 10,
            "minimum_baseline_makespan_ms": 50,
            "minimum_relative_improvement_percent": 5,
        },
        "n": 7,
        "median": 20.0,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "relative_median_percent": sorted(relatives)[3],
        "relative_ci_low": relative_low,
        "relative_ci_high": relative_high,
        "sign_test": {
            "alternative": "agentos_lower_makespan",
            "wins": 7,
            "losses": 0,
            "ties": 0,
            "n": 7,
            "p_value": 1 / 128,
            "numerator": 1,
            "denominator": 128,
        },
        "bootstrap": {
            "method": "deterministic_percentile_median",
            "confidence": 0.95,
            "repetitions": 2000,
            "seed_sha256": seed,
        },
        "samples": samples,
    }
    scenario = summary["scenarios"][-1]
    scenario.update(
        functional_status="pass",
        performance_status="supported",
        performance=performance,
        evidence_ids=["raw-scenario"],
    )
    return scenario


class DashboardContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract_replay = mock.patch(
            "render_evaluation_dashboard.verify_evaluation_contract",
            return_value={},
        )
        self.contract_replay_mock = self.contract_replay.start()
        self.addCleanup(self.contract_replay.stop)

    def test_render_writes_offline_dashboard_and_machine_outputs(self) -> None:
        summary = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "site"
            source = write_render_input(root, summary)
            render(source, output)

            expected = {
                "index.html",
                "evaluation-summary.json",
                "dashboard-verification.json",
                "metrics.csv",
                "assets/evaluation-dashboard.css",
                "assets/evaluation-dashboard.js",
            }
            actual = {str(path.relative_to(output)).replace("\\", "/") for path in output.rglob("*") if path.is_file()}
            self.assertEqual(actual, expected)
            page = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("https://", page)
            self.assertNotIn("http://", page)
            for tab in ("总览", "性能", "系统成本", "科研场景", "可信证据", "方法学"):
                self.assertIn(tab, page)
            for task in range(1, 7):
                self.assertIn(f"任务 {task}", page)
            self.assertIn("0123456789abcdef", page)
            self.assertIn("cold-per-pair", page)
            self.assertIn("E2-local-raw", page)
            self.assertIn('class="chart-scroll"', page)
            self.assertIn('tabindex="0"', page)
            self.assertGreaterEqual(page.count("索引准备成本与缓存状态"), 2)
            self.assertIn("ready 查询优势不包含未披露的索引建立成本", page)
            self.assertIn("重建记录", page)
            self.assertIn("任务 1-6 证据状态", page)
            self.assertNotIn("任务 1-6 动态验收", page)
            self.assertIn('href="../raw/boot-01/guest.log"', page)
            self.assertIn('href="../scenario/report.json"', page)
            self.assertIn("场景明细", page)
            self.assertIn("逐程序耗时", page)
            self.assertIn("rp_runner", page)
            self.assertIn("structured_tool", page)
            self.assertIn("预注册 key outcome 一致性", page)
            self.assertIn("不代表完整最终状态相同", page)
            self.assertIn("系统成本 unavailable", page)
            verification = json.loads(
                (output / "dashboard-verification.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verification["verified_evidence_count"], 4)
            self.assertEqual(verification["verified_marker_count"], 3)
            self.assertEqual(verification["kernel_cost"]["status"], "unavailable")
            self.assertIn(verification["evidence_set_sha256"], page)
            self.assertIn("source_lines", page)
            self.assertIn("environment_sha256", page)
            self.contract_replay_mock.assert_called_once()

            with (output / "metrics.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["unit"], "us/query")
            self.assertEqual(rows[0]["n"], "7")
            self.assertEqual(rows[0]["evidence_ids"], "raw-perf")

    def test_render_requires_existing_evidence_with_matching_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            validate_summary(summary)
            summary["evidence"][0]["sha256"] = "0" * 64
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(DashboardError, "sha256 does not match"):
                render(source, root / "forged-site")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            missing = root.joinpath(*summary["evidence"][0]["path"].split("/"))
            missing.unlink()
            with self.assertRaisesRegex(DashboardError, "missing or inaccessible"):
                render(source, root / "missing-site")

    def test_render_rejects_noncanonical_evidence_paths(self) -> None:
        attacks = (
            "../outside.log",
            "/absolute/evidence.log",
            "C:/absolute/evidence.log",
            "raw\\boot-01\\guest.log",
            "raw//boot-01/guest.log",
            "raw/./boot-01/guest.log",
        )
        for attack in attacks:
            with self.subTest(path=attack), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                summary = fixture()
                source = write_render_input(root, summary)
                summary["evidence"][0]["path"] = attack
                rewrite_summary(source, summary)
                with self.assertRaisesRegex(DashboardError, "canonical|backslashes"):
                    render(source, root / "site")

    def test_render_rejects_forged_byte_and_line_receipts(self) -> None:
        mutations = (
            (
                "bytes",
                lambda summary: summary["evidence"][0]["receipt"].__setitem__(
                    "bytes", summary["evidence"][0]["receipt"]["bytes"] + 1
                ),
                "receipt.bytes does not match",
            ),
            (
                "line number",
                lambda summary: summary["evidence"][0]["receipt"]["line_numbers"].__setitem__(0, 42),
                "does not match the referenced evidence line",
            ),
            (
                "marker hash",
                lambda summary: summary["evidence"][0]["receipt"]["marker_sha256s"].__setitem__(0, "0" * 64),
                "does not match the referenced evidence line",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                summary = fixture()
                source = write_render_input(root, summary)
                mutate(summary)
                rewrite_summary(source, summary)
                with self.assertRaisesRegex(DashboardError, message):
                    render(source, root / "site")

    def test_render_rejects_symlinked_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            evidence = root.joinpath(*summary["evidence"][0]["path"].split("/"))
            outside = root / "outside.log"
            outside.write_bytes(evidence.read_bytes())
            evidence.unlink()
            try:
                os.symlink(outside, evidence)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error}")
            with self.assertRaisesRegex(DashboardError, "symlink or junction"):
                render(source, root / "site")

    def test_verification_receipt_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            render(source, root / "site-a")
            render(source, root / "site-b")
            first = (root / "site-a" / "dashboard-verification.json").read_bytes()
            second = (root / "site-b" / "dashboard-verification.json").read_bytes()
            self.assertEqual(first, second)

    def test_complete_kernel_cost_sidecar_is_reverified_and_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            summary["run"]["id"] = "kernel-cost-run-1"
            summary["run"]["commit"] = "a" * 40
            source = write_render_input(root, summary)
            report = write_kernel_cost_sidecar(root)
            metric_ids = {
                metric["id"]
                for target in report["targets"]
                for metric in target["metrics"]
            }
            self.assertIn("elf_file_bytes", metric_ids)
            render(source, root / "site")
            page = (root / "site/index.html").read_text(encoding="utf-8")
            verification = json.loads(
                (root / "site/dashboard-verification.json").read_text(encoding="utf-8")
            )
            self.assertIn('id="tab-cost"', page)
            for label in ("ELF 文件", ".text", ".data", ".bss"):
                self.assertIn(label, page)
            for value in ("200 B", "176 B", "-24 B", "100 B", "90 B", "-10 B"):
                self.assertIn(value, page)
            self.assertIn("不是 CPU 性能证据", page)
            self.assertIn("不会生成 performance claim", page)
            self.assertEqual(verification["kernel_cost"]["status"], "verified")
            self.assertEqual(verification["kernel_cost"]["evidence_file_count"], 7)
            self.assertEqual(verification["verified_evidence_count"], 11)
            cost_paths = {
                item["path"]
                for item in verification["evidence"]
                if item["id"].startswith("kernel-cost:")
            }
            self.assertEqual(
                cost_paths,
                {
                    "kernel-cost-config.json",
                    "kernel-cost-report.json",
                    "kernel-cost-fragment.json",
                    "kernel-build/environment.json",
                    "kernel-build/kernel-build-config.json",
                    "kernel-build/kernel-build.json",
                    "kernel-build/raw/kernel-build.log",
                },
            )

    def test_failed_kernel_cost_sidecar_remains_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            summary["run"]["id"] = "kernel-cost-run-1"
            summary["run"]["commit"] = "a" * 40
            source = write_render_input(root, summary)
            report = write_kernel_cost_sidecar(root, fail_measurement=True)
            self.assertTrue(any(target["status"] == "failed" for target in report["targets"]))
            render(source, root / "site")
            page = (root / "site/index.html").read_text(encoding="utf-8")
            verification = json.loads(
                (root / "site/dashboard-verification.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verification["kernel_cost"]["status"], "failed")
            self.assertIn("运行状态", page)
            self.assertIn("失败", page)
            cost_panel = re.search(
                r'<section role="tabpanel" id="panel-cost".*?</section>',
                page,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(cost_panel)
            self.assertNotIn("已核验", cost_panel.group(0))

    def test_partial_kernel_cost_sidecar_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            shutil.copyfile(
                HOST_TOOLS.parent / "ci/evaluation-kernel-cost.json",
                root / "kernel-cost-config.json",
            )
            with self.assertRaisesRegex(DashboardError, "sidecar is incomplete"):
                render(source, root / "site")

    def test_tampered_kernel_cost_fragment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            summary["run"]["id"] = "kernel-cost-run-1"
            summary["run"]["commit"] = "a" * 40
            source = write_render_input(root, summary)
            write_kernel_cost_sidecar(root)
            fragment_path = root / "kernel-cost-fragment.json"
            fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
            fragment["benchmarks"][0]["label"] = "tampered cost label"
            write_kernel_json(fragment_path, fragment)
            with self.assertRaisesRegex(DashboardError, "fragment differs"):
                render(source, root / "site")

    def test_scenario_program_summary_is_recomputed_from_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            item = next(item for item in summary["evidence"] if item["id"] == "raw-scenario")
            report_path = root.joinpath(*item["path"].split("/"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["summary"]["targets"]["plain"]["programs"]["rp_runner"]["p50_ms"] += 1
            unsigned = dict(report)
            unsigned.pop("report_sha256")
            report["report_sha256"] = _binding_sha256(unsigned, "scenario-report-v1")
            data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
            report_path.write_bytes(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
            item["receipt"]["bytes"] = len(data)
            item["receipt"]["report_sha256"] = report["report_sha256"]
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(DashboardError, "p50/p95 differs"):
                render(source, root / "site")

    def test_scenario_report_uses_strict_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            item = next(item for item in summary["evidence"] if item["id"] == "raw-scenario")
            report_path = root.joinpath(*item["path"].split("/"))
            data = report_path.read_bytes().replace(
                b'"schema_version":1',
                b'"schema_version":1,"schema_version":1',
                1,
            )
            report_path.write_bytes(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
            item["receipt"]["bytes"] = len(data)
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(DashboardError, "strict bounded JSON"):
                render(source, root / "site")

    def test_scenario_key_outcome_fingerprint_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            item = next(item for item in summary["evidence"] if item["id"] == "raw-scenario")
            report_path = root.joinpath(*item["path"].split("/"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["samples"][0]["outcome"]["workflow_stage"]["status"] = "completed"
            unsigned = dict(report)
            unsigned.pop("report_sha256")
            report["report_sha256"] = _binding_sha256(unsigned, "scenario-report-v1")
            data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
            report_path.write_bytes(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
            item["receipt"]["bytes"] = len(data)
            item["receipt"]["report_sha256"] = report["report_sha256"]
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(DashboardError, "outcome_fingerprint does not bind"):
                render(source, root / "site")

    def test_headline_claim_without_evidence_is_rejected(self) -> None:
        summary = fixture()
        summary["claims"][0]["evidence_ids"] = []
        with self.assertRaisesRegex(DashboardError, "bind at least one evidence"):
            validate_summary(summary)

    def test_supported_result_requires_bound_identity_and_run_plan(self) -> None:
        for name, mutate, message in (
            (
                "commit",
                lambda value: value["run"].pop("commit"),
                "supported results require run.commit",
            ),
            (
                "grade",
                lambda value: value["run"].pop("evidence_grade"),
                "supported results require contract-derived",
            ),
            (
                "run plan",
                lambda value: next(
                    item for item in value["evidence"] if item["id"] == "run-plan"
                ).__setitem__("sha256", "f" * 64),
                "run_plan_sha256 must match",
            ),
        ):
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

    def test_bonferroni_threshold_cannot_be_relaxed_to_point_zero_five(self) -> None:
        summary = fixture()
        summary["methodology"]["multiple_testing"]["per_claim_alpha"] = 0.05
        with self.assertRaisesRegex(DashboardError, "not Bonferroni-corrected"):
            validate_summary(summary)

    def test_supported_render_replays_raw_guest_contract(self) -> None:
        from evaluation_contract import (
            build as build_contract,
            load_suite,
            write_json,
            write_jsonl,
        )
        from test_evaluation_contract import SUITE_PATH, write_campaign

        self.contract_replay.stop()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw"
            raw.mkdir()
            generated_plan = write_campaign(raw, load_suite(SUITE_PATH))
            plan = root / "run-plan.json"
            shutil.move(generated_plan, plan)
            summary, rows = build_contract(SUITE_PATH, plan, raw)
            source = root / "summary.json"
            write_json(source, summary)
            write_jsonl(root / "metrics.jsonl", rows)
            render(source, root / "site")

            log_item = next(
                item for item in summary["evidence"]
                if item["kind"] == "guest-raw-log"
            )
            log_path = root.joinpath(*log_item["path"].split("/"))
            original = log_path.read_bytes()
            self.assertTrue(original.startswith(b"boot"))
            data = b"bo0t" + original[4:]
            log_path.write_bytes(data)
            log_item["sha256"] = hashlib.sha256(data).hexdigest()
            write_json(source, summary)
            with self.assertRaisesRegex(DashboardError, "raw Guest contract replay failed"):
                render(source, root / "tampered-site")

    def test_unsupported_claim_cannot_become_the_headline(self) -> None:
        summary = fixture()
        summary["claims"][0].update(id="claim-supported", title="有证据支持的结论")
        rejected_benchmark = copy.deepcopy(summary["benchmarks"][0])
        rejected_benchmark.update(id="metadata-query-no-gate", label="未过门的 Metadata 查询")
        remove_one_paired_advantage(rejected_benchmark)
        rejected = copy.deepcopy(summary["claims"][0])
        rejected.update(
            id="claim-rejected",
            title="AgentOS 无条件提升所有性能",
            status="not_supported",
            effect="至少一个预注册负载没有通过支持门。",
            benchmark_id="metadata-query-no-gate",
        )
        summary["benchmarks"].append(rejected_benchmark)
        summary["claims"].insert(0, rejected)
        summary["methodology"]["multiple_testing"]["headline_claims"][-1] = (
            "metadata-query-no-gate"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<h2 id="conclusion-title">(.*?)</h2>', page)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "AgentOS 的 Metadata 索引查询通过预注册性能支持门")

        summary["benchmarks"] = [rejected_benchmark, summary["benchmarks"][1]]
        summary["claims"] = [rejected]
        summary["run"].pop("conclusion")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<h2 id="conclusion-title">(.*?)</h2>', page)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "本轮没有达到支持门的性能结论")

    def test_unknown_status_is_rejected_in_every_status_domain(self) -> None:
        cases = (
            ("run", lambda value: value["run"].__setitem__("status", "maybe")),
            ("benchmark", lambda value: value["benchmarks"][0].__setitem__("status", "maybe")),
            (
                "scenario",
                lambda value: value["scenarios"][0].__setitem__("functional_status", "maybe"),
            ),
            ("claim", lambda value: value["claims"][0].__setitem__("status", "maybe")),
            ("evidence", lambda value: value["evidence"][0].__setitem__("status", "maybe")),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, "unknown .* status"):
                    validate_summary(summary)

    def test_measured_benchmark_cannot_hide_a_missing_measurement(self) -> None:
        summary = fixture()
        summary["benchmarks"][0]["estimates"].pop()
        with self.assertRaisesRegex(DashboardError, "measured estimates incomplete"):
            validate_summary(summary)

        unavailable = fixture()
        benchmark = unavailable["benchmarks"][0]
        benchmark.update(status="unavailable", estimates=[], samples=[])
        for pair in benchmark["paired"]:
            pair.update(
                status="unavailable",
                n=0,
                median=None,
                p95=None,
                ci_low=None,
                ci_high=None,
                relative_median_percent=None,
                relative_ci_low=None,
                relative_ci_high=None,
                sign_test=None,
                samples=[],
            )
        for diagnostic in benchmark["diagnostics"]:
            diagnostic.update(
                status="unavailable",
                cache_states=[],
                duration_us=None,
                work_units=None,
                index_rebuild_records=None,
                samples=[],
            )
        unavailable["claims"][0].update(
            status="unavailable",
            title="本轮没有可用测量",
            effect="原始测量不可用，没有填补数值。",
        )
        validate_summary(unavailable)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, unavailable)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("性能图 unavailable", page)
            self.assertIn("不绘制推断图", page)

    def test_forged_supported_statistics_and_evidence_are_rejected(self) -> None:
        mutations = (
            (
                "paired gate",
                lambda value: remove_one_paired_advantage(value["benchmarks"][0]),
                "status is forged",
            ),
            (
                "weakened MCID",
                lambda value: value["benchmarks"][0]["claim_gate"].__setitem__(
                    "minimum_absolute_improvement_us", 0.01
                ),
                "weakens the dashboard's registered MCID",
            ),
            (
                "sign fraction",
                lambda value: value["benchmarks"][0]["paired"][0]["sign_test"].__setitem__("numerator", 2),
                "exact fraction",
            ),
            (
                "benchmark evidence",
                lambda value: value["evidence"][0].__setitem__("status", "invalid"),
                "require verified evidence",
            ),
            (
                "scenario evidence",
                lambda value: value["evidence"][1].__setitem__("status", "unverified"),
                "functional status requires verified evidence",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

    def test_unavailable_benchmark_cannot_carry_measurements(self) -> None:
        summary = fixture()
        summary["benchmarks"][0]["status"] = "unavailable"
        with self.assertRaisesRegex(DashboardError, "must not contain measurements"):
            validate_summary(summary)

    def test_diagnostic_readiness_cannot_be_forged(self) -> None:
        mutations = (
            (
                "summary",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["duration_us"].__setitem__("median", 999),
                "does not match diagnostic samples",
            ),
            (
                "cache",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("cache", "cold-rebuild"),
                "conflicts with rebuild records",
            ),
            (
                "evidence",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("evidence_id", "raw-scenario"),
                "requires benchmark-bound verified evidence",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

    def test_every_dynamic_value_is_html_escaped(self) -> None:
        summary = fixture()
        attack = '<script data-x="1">alert(1)</script>'
        summary["targets"][1]["label"] = attack
        summary["claims"][0]["title"] = attack
        summary["evidence"][0]["label"] = attack
        summary["evidence"][0]["source"] = r"C:\private\guest.log"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn(attack, page)
            self.assertNotIn('<script data-x="1">', page)
            self.assertNotIn(r"C:\private\guest.log", page)
            self.assertGreaterEqual(page.count("&lt;script data-x=&quot;1&quot;&gt;"), 2)

    def test_responsive_css_and_safe_dom_contract(self) -> None:
        css = (ASSETS / "evaluation-dashboard.css").read_text(encoding="utf-8")
        javascript = (ASSETS / "evaluation-dashboard.js").read_text(encoding="utf-8")
        self.assertIn("1440px", css)
        self.assertIn("@media (max-width: 1024px)", css)
        self.assertIn("@media (max-width: 390px)", css)
        self.assertIn("border-radius: 6px", css)
        self.assertIn("border-radius: 8px", css)
        self.assertRegex(css, r"\.chart-scroll\s*\{[^}]*overflow-x:\s*auto")
        self.assertRegex(css, r"\.chart-scroll svg\s*\{[^}]*min-width:\s*760px")
        self.assertRegex(css, r"\.overview-grid > div\s*\{[^}]*min-width:\s*0")
        self.assertIn("@media (max-width: 700px)", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', javascript)
        self.assertNotRegex(css.lower(), r"gradient|\borb\b")
        self.assertNotIn("innerHTML", javascript)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, fixture())
            render(source, root / "site")
            page = (root / "site/index.html").read_text(encoding="utf-8")
        table_regions = re.findall(r'<div class="table-scroll"[^>]*>', page)
        self.assertGreaterEqual(len(table_regions), 1)
        for region in table_regions:
            self.assertIn('role="region"', region)
            self.assertIn('tabindex="0"', region)
            self.assertIn('aria-label="', region)

    def test_every_rendered_chart_declares_unit_n_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, fixture())
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        figures = re.findall(r'<figure class="interval-chart"[^>]*>.*?</figure>', page, flags=re.DOTALL)
        self.assertGreaterEqual(len(figures), 2)
        for figure in figures:
            self.assertRegex(figure, r'data-chart-unit="[^"]+"')
            self.assertRegex(figure, r'data-chart-n="[^"]+"')
            self.assertRegex(figure, r'data-chart-source="[^"]+"')
            self.assertIn("<strong>单位</strong>", figure)
            self.assertIn("<strong>n</strong>", figure)
            self.assertIn("<strong>来源</strong>", figure)

    def test_paired_effect_is_recomputed_from_raw_inner_pairs(self) -> None:
        mutations = (
            (
                "missing raw pairs",
                lambda value: value["benchmarks"][0]["paired"][0]["samples"][0].pop("inner_pairs"),
                "raw inner-pair baseline/treatment",
            ),
            (
                "forged inner effect",
                lambda value: value["benchmarks"][0]["paired"][0]["samples"][0]["inner_pairs"][0].__setitem__(
                    "value", 999
                ),
                "does not match raw values and benchmark direction",
            ),
            (
                "forged boot aggregation",
                lambda value: value["benchmarks"][0]["paired"][0]["samples"][0].__setitem__(
                    "value", 999
                ),
                "does not match the inner-pair median",
            ),
            (
                "forged relative effect",
                lambda value: value["benchmarks"][0]["paired"][0]["samples"][0]["inner_pairs"][0].__setitem__(
                    "relative_percent", 999
                ),
                "does not match raw baseline value",
            ),
            (
                "insufficient inner pairs",
                lambda value: value["benchmarks"][0]["paired"][0]["samples"][0].__setitem__(
                    "inner_pairs",
                    value["benchmarks"][0]["paired"][0]["samples"][0]["inner_pairs"][:1],
                ),
                "at least 7 pairs",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

    def test_headline_chart_is_bound_to_the_same_benchmark(self) -> None:
        summary = fixture()
        rejected_benchmark = copy.deepcopy(summary["benchmarks"][0])
        rejected_benchmark.update(id="first-but-rejected", label="首个但未过门的测量")
        remove_one_paired_advantage(rejected_benchmark)
        rejected_claim = copy.deepcopy(summary["claims"][0])
        rejected_claim.update(
            id="first-rejected-claim",
            benchmark_id="first-but-rejected",
            status="not_supported",
        )
        summary["benchmarks"].insert(0, rejected_benchmark)
        summary["claims"].insert(0, rejected_claim)
        summary["methodology"]["multiple_testing"]["headline_claims"][-1] = (
            "first-but-rejected"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<title id="chart-title-overview">Metadata 索引查询配对区间图</title>', page
        )
        self.assertNotIn(
            '<title id="chart-title-overview">首个但未过门的测量配对区间图</title>', page
        )

    def test_scenario_function_and_performance_are_separate_and_bound(self) -> None:
        summary = fixture()
        attach_supported_scenario(summary)
        validate_summary(summary)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("功能状态", page)
        self.assertIn("性能状态", page)
        self.assertIn("配对 makespan 改善中位数 20 ms", page)

    def test_scenario_statistics_cannot_be_forged(self) -> None:
        mutations = (
            (
                "raw makespan",
                lambda value: value["scenarios"][-1]["performance"]["samples"][0].__setitem__(
                    "plain_ms", 999
                ),
                "improvement_ms does not match raw makespans",
            ),
            (
                "bootstrap interval",
                lambda value: value["scenarios"][-1]["performance"].__setitem__("ci_low", 999),
                "bootstrap interval does not match raw scenario samples",
            ),
            (
                "sign test",
                lambda value: value["scenarios"][-1]["performance"]["sign_test"].__setitem__(
                    "wins", 6
                ),
                "counts do not match paired n",
            ),
            (
                "performance conclusion",
                lambda value: value["scenarios"][-1].__setitem__(
                    "performance_status", "inconclusive"
                ),
                "performance_status is forged",
            ),
            (
                "weakened scenario MCID",
                lambda value: value["scenarios"][-1]["performance"]["claim_gate"].__setitem__(
                    "minimum_absolute_improvement_ms", 0.01
                ),
                "weakens the registered scenario MCID",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                summary = fixture()
                attach_supported_scenario(summary)
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

    def test_free_text_cannot_override_structured_conclusions(self) -> None:
        summary = fixture()
        attack = "自报：AgentOS 在所有场景性能提升一万倍"
        summary["claims"][0]["title"] = attack
        summary["claims"][0]["effect"] = attack
        summary["run"]["conclusion"] = attack
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
            exported = (root / "site" / "evaluation-summary.json").read_text(encoding="utf-8")
        self.assertNotIn(attack, page)
        self.assertNotIn(attack, exported)
        self.assertIn("通过预注册性能支持门", page)
        self.assertNotIn('"conclusion"', exported)

        summary = fixture()
        summary["run"]["evidence_grade"] = "E9-self-asserted"
        with self.assertRaisesRegex(DashboardError, "contract-derived"):
            validate_summary(summary)

    def test_scenario_free_text_metric_is_rejected(self) -> None:
        summary = fixture()
        summary["scenarios"][0]["metric"] = "自报：性能无限提升"
        with self.assertRaisesRegex(DashboardError, "fields mismatch"):
            validate_summary(summary)


if __name__ == "__main__":
    unittest.main()
