#!/usr/bin/env python3
"""Contract tests for the offline evaluation dashboard."""

from __future__ import annotations

import copy
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from fractions import Fraction
from html.parser import HTMLParser
from unittest import mock
from pathlib import Path
from urllib.parse import unquote, urlsplit

import evaluation_kernel_cost as kernel_cost
import safe_host_paths
from windows_reparse_fixture import (
    create_directory_junction,
    remove_directory_junction,
)
from test_evaluation_kernel_cost import Fixture as KernelCostFixture
from test_evaluation_kernel_cost import _write_json as write_kernel_json

from render_evaluation_dashboard import (
    DashboardError,
    _binding_sha256,
    _bootstrap_interval,
    _overview_extension_slots,
    _read_evidence_file,
    main as dashboard_main,
    render as render_dashboard,
    validate_summary,
)
from evaluation_contract import derive_acceptance_gates
from evaluation_scenario import (
    RESOURCE_STABILITY_CHILD_ROUNDS,
    RESOURCE_STABILITY_FILE,
    RESOURCE_STABILITY_GROWTH_BOUNDS,
    RESOURCE_STABILITY_INTERPRETATION,
    RESOURCE_STABILITY_LOAD_WORKFLOWS,
    RESOURCE_STABILITY_MEASUREMENT_SCOPE,
    RESOURCE_STABILITY_RESOURCE_KINDS,
    RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
)


HOST_TOOLS = Path(__file__).resolve().parent
CONTRACT_ROOT = HOST_TOOLS.parent
ASSETS = HOST_TOOLS / "assets"
TEST_RUN_PLAN_BYTES = b'{"kind":"dashboard-test-run-plan","schema_version":1}\n'
TEST_RUN_PLAN_SHA256 = hashlib.sha256(TEST_RUN_PLAN_BYTES).hexdigest()
COMPETITION_CLAIMS = {
    "task4": {
        "benchmark_id": "file_query_path_index",
        "required_status": "supported",
    }
}


def render(summary_path: Path, output_dir: Path) -> None:
    render_dashboard(summary_path, output_dir, contract_root=CONTRACT_ROOT)


class _LocalLinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is not None and name in {"href", "src"}:
                self.references.append(value)


def assert_offline_links_resolve(page: Path) -> None:
    parser = _LocalLinkCollector()
    parser.feed(page.read_text(encoding="utf-8"))
    root = page.parent.resolve(strict=True)
    for reference in parser.references:
        parsed = urlsplit(reference)
        assert not parsed.scheme and not parsed.netloc, reference
        path = unquote(parsed.path)
        if not path:
            continue
        assert not path.startswith(("/", "\\")), reference
        target = (root / Path(*path.split("/"))).resolve(strict=True)
        target.relative_to(root)
        assert target.is_file(), reference


def _test_percentile(values: list[int], quantile: float) -> float | int:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = lower if position == lower else lower + 1
    if lower == upper:
        return ordered[lower]
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _resource_stability_summary(samples: list[dict], *, status: str) -> dict:
    if status not in {"passed", "unavailable"}:
        raise AssertionError("dashboard resource fixture status is invalid")
    measured = status == "passed"
    resources = []
    for kind in RESOURCE_STABILITY_RESOURCE_KINDS:
        bound = RESOURCE_STABILITY_GROWTH_BOUNDS[kind]
        resources.append(
            {
                "kind": kind,
                "status": "measured" if measured else "not_measured",
                "coverage": "configured_global_counter",
                "per_workflow_growth_bound": bound,
                "terminal_growth_bound": bound,
                "max_observed_per_workflow_growth": (
                    min(bound, 1) if measured else None
                ),
                "terminal_observed_growth": 0 if measured else None,
                "plateau_or_reclamation": (
                    True if measured and bound else None
                ),
                "exact_terminal_recovery": True if measured else None,
            }
        )
    return {
        "status": status,
        "required_target": "agentos",
        "measurement_scope": RESOURCE_STABILITY_MEASUREMENT_SCOPE,
        "verified_boots": len(samples) if measured else 0,
        "load_workflows_per_boot": RESOURCE_STABILITY_LOAD_WORKFLOWS,
        "terminal_workflows_per_boot": RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
        "child_rounds_per_load_workflow": RESOURCE_STABILITY_CHILD_ROUNDS,
        "global_observation": {
            "coverage": "configured_global_kind_counters",
            "measured_mask_semantics": (
                "configured_global_resource_kind_counters_only"
            ),
            "snapshot_consistency": (
                "single_core_irq_coherent" if measured else "not_measured"
            ),
            "account_counters": "not_measured",
            "rate_budgets": "not_measured",
            "growth_bound_semantics": "per_class_positive_delta_sum",
            "decrease_semantics": "reclamation_allowed",
            "free_pages": {
                "status": "measured" if measured else "not_measured",
                "exact_pair_recovery": True if measured else None,
                "exact_terminal_recovery": True if measured else None,
            },
            "resources": resources,
        },
        "interpretation": dict(RESOURCE_STABILITY_INTERPRETATION),
        "boot_receipts": [
            {
                "sample_id": sample["sample_id"],
                "challenge": sample["binding"]["challenge"],
                "resource_receipt_sha256": hashlib.sha256(
                    f"resource-{index}".encode()
                ).hexdigest(),
                "binding_sha256": hashlib.sha256(
                    f"resource-binding-{index}".encode()
                ).hexdigest(),
                "raw_source_receipt_sha256": sample["binding"][
                    "source_receipts"
                ]["agentos"],
            }
            for index, sample in enumerate(samples, 1)
        ] if measured else [],
    }


def attach_passed_resource_stability(report: dict) -> None:
    resource = _resource_stability_summary(report["samples"], status="passed")
    for sample, receipt in zip(report["samples"], resource["boot_receipts"]):
        sample["targets"]["agentos"]["raw_source_receipt"][
            "resource_stability"
        ] = {
            "required": True,
            "status": "verified",
            "path": f"state-extracted/{RESOURCE_STABILITY_FILE}",
            "bytes": 1,
            "sha256": receipt["resource_receipt_sha256"],
            "acceptance": {},
            "binding": {"sha256": receipt["binding_sha256"]},
        }
    report["summary"]["resource_stability"] = resource


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
        if scenario["performance_status"] in {"supported", "regressed", "inconclusive"}
        else "inconclusive"
    )
    performance_samples = (
        scenario["performance"]["samples"]
        if scenario["performance"] is not None
        else None
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
        performance_sample = (
            performance_samples[index - 1] if performance_samples is not None else None
        )
        boot_id = (
            performance_sample["boot_id"]
            if performance_sample is not None
            else f"boot-{index:02d}"
        )
        target_order = (
            performance_sample["target_order"]
            if performance_sample is not None
            else "AB" if index % 2 else "BA"
        )
        binding = {
            "source_commit": summary["run"]["commit"],
            "run_id": summary["run"]["id"],
            "boot_id": boot_id,
            "boot_order": index,
            "target_order": target_order,
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
                "makespan_ms": (
                    performance_sample[f"{target}_ms"]
                    if performance_sample is not None
                    else sum(row["elapsed_ms"] for row in rows) + 37
                ),
                "programs": rows,
                "raw_source_receipt": {"sha256": receipts[target]},
            }
        samples.append({
            "sample_id": (
                performance_sample["sample_id"]
                if performance_sample is not None
                else f"{summary['run']['id']}:boot-{index:02d}"
            ),
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
        "schema_version": 2,
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
            "resource_stability": _resource_stability_summary(
                samples, status="unavailable"
            ),
            "targets": target_summaries,
        },
    }
    report["report_sha256"] = _binding_sha256(report, "scenario-report-v2")
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
            lines = data.decode("utf-8").splitlines()
            diagnostic_samples = [
                sample
                for benchmark in summary["benchmarks"]
                for diagnostic in benchmark["diagnostics"]
                for sample in diagnostic["samples"]
                if sample["evidence_id"] == item["id"]
            ]
            for sample in diagnostic_samples:
                sample["source_log_sha256"] = item["sha256"]
                sample["source_marker_sha256"] = hashlib.sha256(
                    lines[sample["source_line"] - 1].encode("utf-8")
                ).hexdigest()
            line_numbers = sorted({sample["source_line"] for sample in diagnostic_samples})
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


def rewrite_scenario_report(
    root: Path,
    source: Path,
    summary: dict,
    mutate,
) -> dict:
    report_path = root / "scenario/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mutate(report)
    report.pop("report_sha256", None)
    report["report_sha256"] = _binding_sha256(report, "scenario-report-v2")
    data = (
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    report_path.write_bytes(data)
    evidence = next(item for item in summary["evidence"] if item["id"] == "raw-scenario")
    evidence["sha256"] = hashlib.sha256(data).hexdigest()
    evidence["receipt"]["bytes"] = len(data)
    evidence["receipt"]["report_sha256"] = report["report_sha256"]
    rewrite_summary(source, summary)
    return report


def write_kernel_cost_sidecar(
    root: Path,
    *,
    fail_measurement: bool = False,
    run_id: str = "kernel-cost-run-1",
    source_commit: str = "a" * 40,
) -> dict:
    fixture = KernelCostFixture(
        run_id=run_id, source_commit=source_commit
    )
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
                    "order": "boot-median",
                    "boot_id": str(trial),
                    "value": value + delta,
                    "evidence_id": "raw-perf",
                    "operations": 1,
                    "dataset_size": int(load),
                    "work_units": int(load) if target_id == "reference" else 1,
                    "records_examined": int(load) if target_id == "reference" else 1,
                    "result_items": 1,
                    "index_rebuild_records": 0,
                    "result_cache_hits": 0,
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
            relative_samples,
            f"{TEST_RUN_PLAN_SHA256}:file_query_path_index:{load}:paired-relative",
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
                "mcid_sign_test": {
                    "alternative": "joint_absolute_and_relative_mcid_exceeded",
                    "absolute_mcid_us": 5.0,
                    "relative_mcid_percent": 5.0,
                    "success_rule": "both_strictly_greater_per_boot",
                    "non_win_policy": "ties_missing_or_not_exceeding_either_mcid",
                    "wins": 7,
                    "non_wins": 0,
                    "n": 7,
                    "p_value": 1 / 128,
                    "numerator": 1,
                    "denominator": 128,
                },
            }
        )
    diagnostics = []
    for load_index, load in enumerate(("128", "1024")):
        diagnostic_samples = [
            {
                "boot_id": f"boot-{trial:02d}",
                "cache": "ready",
                "operations": 1,
                "dataset_size": int(load),
                "duration_us": trial,
                "work_units": 1,
                "result_items": 1,
                "index_rebuild_records": 0,
                "result_cache_hits": 0,
                "workload_fingerprint": f"{int(load) + trial:016x}",
                "result_fingerprint": f"{int(load) * 2 + trial:016x}",
                "evidence_id": "raw-perf",
                "source_log": "boot-01/guest.log",
                "source_line": 40 + load_index * 10 + trial,
                "source_log_sha256": "a" * 64,
                "source_marker_sha256": "d" * 64,
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
                "work_units": {"median": 1.0, "p95": 1.0, "n": 7},
                "index_rebuild_records": {"median": 0.0, "p95": 0.0, "n": 7},
                "result_cache_hits": {"median": 0.0, "p95": 0.0, "n": 7},
                "samples": diagnostic_samples,
            }
        )
    result = {
        "schema_version": 3,
        "kind": "agentos-evaluation-summary",
        "run": {
            "id": "evaluation-20260730",
            "suite_id": "agentos-evaluation-v3",
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
                "id": "file_query_path_index",
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
            "competition_claims": copy.deepcopy(COMPETITION_CLAIMS),
            "design": "paired interleaved A/B",
            "cache_policy": "cold-per-pair",
            "repetitions": 3,
            "environment": {"machine": "QEMU virt", "clock": "rdtime"},
            "interval_method": "descriptive deterministic percentile bootstrap (2000 resamples)",
            "inference_method": (
                "exact one-sided binomial sign test of per-boot joint MCID exceedance; "
                "a win strictly exceeds both absolute and relative MCIDs"
            ),
            "descriptive_interval": {
                "method": "percentile bootstrap of the boot-level median",
                "resamples": 2000,
                "role": "descriptive only; never used to support a headline claim",
            },
            "fwer_mcid": {
                "familywise_alpha": 0.05,
                "headline_count": 3,
                "per_headline_alpha": 0.05 / 3,
                "correction": "Bonferroni across headline claims",
                "per_boot_success": "absolute > MCID and relative > MCID",
                "non_win_policy": "ties, missing relative values, and either non-exceedance",
                "load_gate": "intersection; every preregistered load must pass",
            },
            "interpretation_boundaries": {
                "microbenchmark_design": "same-kernel-paired-comparison",
                "microbenchmark_causal_scope": (
                    "task-facing-path-vs-index-and-isolated-ablation-under-preregistered-workloads"
                ),
                "scenario_design": "full-stack",
                "scenario_attribution": "non-single-mechanism",
                "host_page_cache": "uncontrolled",
            },
            "multiple_testing": {
                "family_id": "dashboard-test-headlines-v1",
                "method": "Bonferroni",
                "familywise_alpha": 0.05,
                "hypothesis_count": 3,
                "per_claim_alpha": 0.05 / 3,
                "headline_claims": [
                    "file_query_path_index",
                    "scheduler-tail",
                    "reserved-headline",
                ],
                "load_gate": "intersection (every preregistered load must pass)",
            },
            "limitations": ["单机实验", "任务 6 负结果 fixture"],
        },
        "evidence": evidence,
        "claims": [
            {
                "id": "claim-metadata",
                "title": "AgentOS 的 metadata 查询在该负载与环境下更快",
                "status": "supported",
                "effect": "两个负载的配对区间均支持更低延迟；不外推到总体 CPU 性能。",
                "benchmark_id": "file_query_path_index",
                "evidence_ids": ["raw-perf"],
            }
        ],
    }
    attach_scenario(result, [8] * 7)
    result["acceptance"] = derive_acceptance_gates(
        result["scenarios"],
        result["claims"],
        COMPETITION_CLAIMS,
        suite_schema_version=result["schema_version"],
    )
    return result


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
        mcid_sign_test={
            "alternative": "joint_absolute_and_relative_mcid_exceeded",
            "absolute_mcid_us": 5.0,
            "relative_mcid_percent": 5.0,
            "success_rule": "both_strictly_greater_per_boot",
            "non_win_policy": "ties_missing_or_not_exceeding_either_mcid",
            "wins": 0,
            "non_wins": 7,
            "n": 7,
            "p_value": 1.0,
            "numerator": 1,
            "denominator": 1,
        },
    )


def attach_scenario(
    summary: dict, improvements: list[int] | None = None
) -> dict:
    if improvements is None:
        improvements = [20] * 7
    if len(improvements) != 7:
        raise AssertionError("scenario fixture requires exactly seven paired improvements")
    samples = []
    for index, improvement in enumerate(improvements):
        plain = 100 + index
        agentos = plain - improvement
        samples.append(
            {
                "sample_id": f"{summary['run']['id']}:boot-{index + 1:02d}",
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
    sign_wins = sum(value > 0 for value in improvements)
    sign_losses = sum(value < 0 for value in improvements)
    sign_ties = len(improvements) - sign_wins - sign_losses
    sign_n = sign_wins + sign_losses
    sign_exact = Fraction(
        sum(math.comb(sign_n, count) for count in range(sign_wins, sign_n + 1))
        if sign_n
        else 1,
        1 << sign_n if sign_n else 1,
    )
    joint_wins = sum(
        absolute > 10 and relative > 5
        for absolute, relative in zip(improvements, relatives)
    )
    joint_exact = Fraction(
        sum(math.comb(7, count) for count in range(joint_wins, 8)),
        1 << 7,
    )
    joint_losses = sum(
        absolute < -10 and relative < -5
        for absolute, relative in zip(improvements, relatives)
    )
    reverse_exact = Fraction(
        sum(math.comb(7, count) for count in range(joint_losses, 8)),
        1 << 7,
    )
    performance = {
        "direction": "plain_minus_agentos_positive_is_better",
        "lower_is_better": True,
        "unit": "ms",
        "paired_success_rate": 1.0,
        "inference": {
            "method": "exact_directional_binomial_with_bonferroni",
            "success_unit": "paired_boot",
            "sample_policy": "full_n_including_non_wins",
            "alpha": 0.05,
            "multiplicity": "two_directions_within_task6_scenario",
            "directional_hypothesis_count": 2,
            "correction": "Bonferroni",
            "per_direction_alpha": 0.025,
        },
        "interpretation": {
            "design": "full-stack",
            "causal_attribution": "non-single-mechanism",
            "host_page_cache": "uncontrolled",
        },
        "claim_gate": {
            "minimum_absolute_improvement_ms": 10,
            "minimum_baseline_makespan_ms": 50,
            "minimum_relative_improvement_percent": 5,
        },
        "n": 7,
        "median": sorted(improvements)[3],
        "ci_low": ci_low,
        "ci_high": ci_high,
        "relative_median_percent": sorted(relatives)[3],
        "relative_ci_low": relative_low,
        "relative_ci_high": relative_high,
        "sign_test": {
            "alternative": "agentos_lower_makespan",
            "wins": sign_wins,
            "losses": sign_losses,
            "ties": sign_ties,
            "n": sign_n,
            "p_value": float(sign_exact),
            "numerator": sign_exact.numerator,
            "denominator": sign_exact.denominator,
        },
        "mcid_sign_test": {
            "alternative": "joint_absolute_and_relative_mcid_exceeded",
            "absolute_mcid_ms": 10,
            "relative_mcid_percent": 5,
            "success_rule": "both_strictly_greater_per_boot",
            "non_win_policy": "ties_missing_or_not_exceeding_either_mcid",
            "wins": joint_wins,
            "non_wins": 7 - joint_wins,
            "n": 7,
            "p_value": float(joint_exact),
            "numerator": joint_exact.numerator,
            "denominator": joint_exact.denominator,
        },
        "regression_mcid_sign_test": {
            "alternative": "joint_absolute_and_relative_regression_mcid_exceeded",
            "absolute_mcid_ms": 10,
            "relative_mcid_percent": 5,
            "success_rule": "both_strictly_less_than_negative_thresholds_per_boot",
            "non_loss_policy": "ties_missing_or_not_exceeding_either_reverse_mcid",
            "losses": joint_losses,
            "non_losses": 7 - joint_losses,
            "n": 7,
            "p_value": float(reverse_exact),
            "numerator": reverse_exact.numerator,
            "denominator": reverse_exact.denominator,
        },
        "bootstrap": {
            "method": "deterministic_percentile_median",
            "confidence": 0.95,
            "repetitions": 2000,
            "seed_sha256": seed,
            "role": "descriptive_only",
        },
        "samples": samples,
    }
    scenario = summary["scenarios"][-1]
    performance_status = (
        "supported"
        if joint_exact <= Fraction(1, 40)
        else "regressed"
        if reverse_exact <= Fraction(1, 40)
        else "inconclusive"
    )
    scenario.update(
        functional_status="pass",
        performance_status=performance_status,
        performance=performance,
        evidence_ids=["raw-scenario"],
    )
    summary["acceptance"] = derive_acceptance_gates(
        summary["scenarios"],
        summary["claims"],
        COMPETITION_CLAIMS,
        suite_schema_version=summary["schema_version"],
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
            expected |= {
                f"evidence/{item['path']}" for item in summary["evidence"]
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
            self.assertEqual(page.count('data-overview-slot="mechanism"'), 3)
            self.assertEqual(page.count('data-overview-slot="task6"'), 1)
            self.assertEqual(page.count('data-extension-slot="'), 2)
            self.assertIn('data-extension-slot="resource-stability"', page)
            self.assertIn('data-extension-slot="compatibility-overhead"', page)
            self.assertIn("不选择单一胜者", page)
            self.assertIn("状态不互相替代，也不生成综合总分", page)
            self.assertNotIn('chart-title-overview', page)
            self.assertIn('data-raw-pairs="14"', page)
            self.assertEqual(page.count('class="raw-pair-link"'), 14)
            self.assertEqual(page.count('class="raw-pair-dot raw-pair-dot--'), 28)
            self.assertIn("小圆点和浅色连线展示每个独立 boot", page)
            self.assertGreaterEqual(page.count("索引准备成本与缓存状态"), 2)
            self.assertIn("独立诊断仅支持索引准备状态披露，不参与 headline 判定", page)
            self.assertIn("index_rebuild_records=0 与 result_cache_hits=0", page)
            self.assertIn("重建记录", page)
            self.assertIn("结果缓存命中", page)
            self.assertIn("实际工作量回执", page)
            self.assertIn("实际 N", page)
            self.assertIn("精确等于 N x operations", page)
            self.assertIn("不要求实验结果预先胜出", page)
            self.assertIn("发布与竞赛验收", page)
            self.assertIn("科学证据发布", page)
            self.assertIn("竞赛整体验收", page)
            self.assertIn("任务 1-6 竞赛状态", page)
            self.assertIn("file_query_path_index 结论", page)
            self.assertNotIn("任务 1-6 动态验收", page)
            self.assertIn("joint-MCID", page)
            self.assertIn("描述性 bootstrap 95% 区间", page)
            self.assertIn("Bonferroni", page)
            self.assertIn("same-kernel-paired-comparison", page)
            self.assertIn("full-stack / non-single-mechanism", page)
            self.assertIn("Host page cache", page)
            self.assertIn("uncontrolled", page)
            self.assertIn('href="evidence/raw/boot-01/guest.log"', page)
            self.assertIn('href="evidence/scenario/report.json"', page)
            assert_offline_links_resolve(output / "index.html")
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
            self.assertEqual(verification["verified_marker_count"], 14)
            self.assertEqual(verification["verified_diagnostic_provenance_count"], 14)
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

    def test_render_uses_explicit_contract_root_for_suite_and_assets(self) -> None:
        summary = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract_root = root / "trusted-contract"
            (contract_root / "ci").mkdir(parents=True)
            (contract_root / "host_tools" / "assets").mkdir(parents=True)
            shutil.copyfile(
                CONTRACT_ROOT / "ci" / "evaluation-suite.json",
                contract_root / "ci" / "evaluation-suite.json",
            )
            for name in ("evaluation-dashboard.css", "evaluation-dashboard.js"):
                shutil.copyfile(
                    ASSETS / name,
                    contract_root / "host_tools" / "assets" / name,
                )
            trusted_css = contract_root / "host_tools" / "assets" / "evaluation-dashboard.css"
            trusted_css.write_bytes(trusted_css.read_bytes() + b"\n/* explicit-contract-root */\n")

            run = root / "run"
            run.mkdir()
            source = write_render_input(run, summary)
            output = root / "site"
            render_dashboard(source, output, contract_root=contract_root)

            self.assertEqual(
                (output / "assets" / "evaluation-dashboard.css").read_bytes(),
                trusted_css.read_bytes(),
            )
            self.assertEqual(
                self.contract_replay_mock.call_args.args[0],
                contract_root.resolve(strict=True) / "ci" / "evaluation-suite.json",
            )
            self.assertEqual(
                self.contract_replay_mock.call_args.kwargs["contract_root"],
                contract_root.resolve(strict=True),
            )

    def test_cli_requires_explicit_contract_root(self) -> None:
        with mock.patch.object(sys, "stderr"):
            with self.assertRaises(SystemExit) as raised:
                dashboard_main(["summary.json", "dashboard"])
        self.assertEqual(raised.exception.code, 2)

    def test_optional_guardrail_slots_consume_normalized_verified_fragments(self) -> None:
        resource = _resource_stability_summary(
            [
                {
                    "sample_id": f"sample-{index}",
                    "binding": {
                        "challenge": f"{index:016x}",
                        "source_receipts": {
                            "agentos": hashlib.sha256(
                                f"source-{index}".encode()
                            ).hexdigest()
                        },
                    },
                }
                for index in range(1, 8)
            ],
            status="passed",
        )
        compatibility = {
            "status": "ready",
            "claim_scope": "traditional_ucore_compatibility_overhead_only",
            "aggregate_score": None,
            "aggregate_score_forbidden": True,
            "metrics": {
                name: {
                    "paired_boots": 7,
                    "median_agentos_over_plain_ratio": 1.05 + index / 10,
                }
                for index, name in enumerate(
                    ("fork_wait", "fork_exec_wait", "pipe_roundtrip", "seq_file_io")
                )
            },
        }
        content = _overview_extension_slots(
            {"compatibility_overhead": compatibility},
            [{"task_id": "task6", "resource_stability": resource}],
        )
        self.assertEqual(content.count('data-extension-slot="'), 2)
        self.assertIn('status--passed', content)
        self.assertIn('status--ready', content)
        self.assertIn("配置全局计数覆盖=8/8", content)
        self.assertIn("空闲页配对/终点精确恢复=是", content)
        self.assertIn("计时关系=excluded_from_task6_makespan", content)
        self.assertIn("全局无泄漏=not_claimed", content)
        self.assertIn("4 项传统 uCore 兼容路径配对成本", content)
        self.assertIn("AgentOS/plain 中位成本比 1.05x-1.35x", content)
        self.assertIn("aggregate score 禁止", content)

        unavailable = _overview_extension_slots({}, [])
        self.assertEqual(unavailable.count('status--unavailable'), 2)
        self.assertIn("不声称全局无泄漏", unavailable)
        self.assertIn("不并入性能优势或综合分", unavailable)

    def test_verified_scenario_resource_stability_reaches_overview_end_to_end(self) -> None:
        summary = fixture()
        attach_scenario(summary)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)

            rewrite_scenario_report(
                root, source, summary, attach_passed_resource_stability
            )
            output = root / "site"
            render(source, output)
            page = (output / "index.html").read_text(encoding="utf-8")
            slot = page.split('data-extension-slot="resource-stability"', 1)[1].split(
                "</article>", 1
            )[0]
            self.assertIn("status--passed", slot)
            self.assertIn("核验 boot=7", slot)
            self.assertIn("配置全局计数覆盖=8/8", slot)
            self.assertIn("空闲页配对/终点精确恢复=是", slot)
            self.assertIn("excluded_from_task6_makespan", slot)
            self.assertIn("全局无泄漏=not_claimed", slot)

            mutations = (
                (
                    "bound",
                    lambda report: report["summary"]["resource_stability"][
                        "global_observation"
                    ]["resources"][0].__setitem__("terminal_growth_bound", 1),
                    "registered guardrail",
                ),
                (
                    "negative growth",
                    lambda report: report["summary"]["resource_stability"][
                        "global_observation"
                    ]["resources"][3].__setitem__(
                        "max_observed_per_workflow_growth", -1
                    ),
                    "outside the registered growth bound",
                ),
                (
                    "missing plateau",
                    lambda report: report["summary"]["resource_stability"][
                        "global_observation"
                    ]["resources"][3].__setitem__("plateau_or_reclamation", False),
                    "prove a plateau or reclamation",
                ),
                (
                    "legacy atomic snapshot",
                    lambda report: report["summary"]["resource_stability"][
                        "global_observation"
                    ].__setitem__("snapshot_consistency", "single_core_irq_atomic"),
                    "measured status must bind every boot",
                ),
                (
                    "raw binding",
                    lambda report: report["summary"]["resource_stability"][
                        "boot_receipts"
                    ][0].__setitem__("raw_source_receipt_sha256", "0" * 64),
                    "corresponding scenario sample",
                ),
            )
            for name, mutate, message in mutations:
                with self.subTest(name=name):
                    def forge(report: dict) -> None:
                        attach_passed_resource_stability(report)
                        mutate(report)

                    rewrite_scenario_report(root, source, summary, forge)
                    with self.assertRaisesRegex(DashboardError, message):
                        render(source, root / f"forged-site-{name.replace(' ', '-')}")

    def test_verified_scenario_report_cannot_upgrade_an_unavailable_summary(self) -> None:
        summary = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            task6 = summary["scenarios"][-1]
            task6.update(
                functional_status="unavailable",
                performance_status="unavailable",
                performance=None,
            )
            summary["acceptance"] = derive_acceptance_gates(
                summary["scenarios"],
                summary["claims"],
                COMPETITION_CLAIMS,
                suite_schema_version=summary["schema_version"],
            )
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(
                DashboardError, "report status differs from the summary scenario"
            ):
                render(source, root / "forged-site")

    def test_evidence_byte_budget_is_checked_before_bulk_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(strict=True)
            evidence = root / "oversized.log"
            evidence.write_bytes(b"123456789")
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("bulk read must not run"),
            ):
                with self.assertRaisesRegex(DashboardError, "byte budget"):
                    _read_evidence_file(
                        root, ("oversized.log",), "test evidence", max_bytes=8
                    )

    def test_chart_describes_the_actual_suite_variants(self) -> None:
        suite = json.loads((HOST_TOOLS.parent / "ci/evaluation-suite.json").read_text(encoding="utf-8"))
        experiment = suite["experiments"][0]
        summary = fixture()
        summary["targets"][0]["label"] = experiment["baseline"]["label"]
        summary["targets"][1]["label"] = experiment["treatment"]["label"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site/index.html").read_text(encoding="utf-8")
        expected = (
            f'小圆点和浅色连线展示每个独立 boot 的 {experiment["baseline"]["label"]} 与 '
            f'{experiment["treatment"]["label"]} 原始配对值，粗区间和大圆点展示汇总估计。'
        )
        self.assertIn(expected, page)
        self.assertNotIn("展示基线与 AgentOS 的估计值及区间", page)

    def test_verified_campaign_environment_is_rendered_from_receipts(self) -> None:
        from test_evaluation_bundle import make_run

        with tempfile.TemporaryDirectory(dir=HOST_TOOLS) as temporary:
            run = make_run(Path(temporary))
            campaign = json.loads((run / "campaign.json").read_text(encoding="utf-8"))
            page = (run / "dashboard/index.html").read_text(encoding="utf-8")
            verification = json.loads(
                (run / "dashboard/dashboard-verification.json").read_text(encoding="utf-8")
            )

        platform = campaign["platform"]
        hardware = platform["hardware"]
        detail = verification["campaign_environment"]
        self.assertEqual(detail["status"], "verified")
        self.assertEqual(detail["source_commit"], campaign["run"]["commit"])
        self.assertEqual(detail["execution_domain"], platform["entry_domain"])
        self.assertEqual(detail["cpu_model"], hardware["cpu_model"])
        self.assertEqual(detail["logical_cpu_count"], hardware["logical_cpu_count"])
        self.assertEqual(detail["memory_total_bytes"], hardware["memory_total_bytes"])
        self.assertEqual(detail["hardware_source"], hardware["source"])
        self.assertIn('id="environment-title">实验环境</h2>', page)
        for value in (
            campaign["run"]["commit"],
            platform["entry_domain"],
            hardware["cpu_model"],
            str(hardware["logical_cpu_count"]),
            platform["tools"]["compiler"]["version"],
            platform["tools"]["qemu"]["version"],
        ):
            self.assertIn(html.escape(" ".join(str(value).split()), quote=True), page)
        self.assertIn(f'{hardware["memory_total_bytes"]:,} B', page)
        self.assertIn('href="../campaign.json"', page)
        self.assertIn("Task 4 竞赛主对照", page)
        self.assertIn("机制消融：固定容量 metadata table scan", page)
        self.assertEqual(page.count("<h4>实际工作量回执</h4>"), 2)
        self.assertIn("精确等于 N x operations", page)
        self.assertIn("固定为 512 x operations", page)
        campaign_records = [
            record
            for record in verification["evidence"]
            if record["id"] == "campaign-platform-proof"
        ]
        self.assertEqual(len(campaign_records), 1)
        self.assertTrue(campaign_records[0]["campaign_binding_checked"])

        javascript = (ASSETS / "evaluation-dashboard.js").read_text(encoding="utf-8")
        for browser_probe in ("hardwareConcurrency", "deviceMemory", "userAgent"):
            self.assertNotIn(browser_probe, javascript)

    def test_campaign_environment_rejects_broken_receipts_and_extra_hardware(self) -> None:
        from test_evaluation_bundle import make_run

        with tempfile.TemporaryDirectory(dir=HOST_TOOLS) as temporary:
            run = make_run(Path(temporary))
            summary_path = run / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            original_summary = copy.deepcopy(summary)

            summary["run"]["campaign_sha256"] = "0" * 64
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DashboardError, "campaign SHA-256 differs"):
                render(summary_path, run / "dashboard-broken-receipt")

            campaign_path = run / "campaign.json"
            campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
            campaign["platform"]["hardware"]["cpu_mhz"] = 4200
            campaign_path.write_text(
                json.dumps(campaign, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            campaign_sha256 = hashlib.sha256(campaign_path.read_bytes()).hexdigest()

            plan_path = run / "run-plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["campaign_sha256"] = campaign_sha256
            plan_path.write_text(
                json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()

            summary = original_summary
            summary["run"]["campaign_sha256"] = campaign_sha256
            summary["run"]["run_plan_sha256"] = plan_sha256
            run_plan_evidence = next(
                item
                for item in summary["evidence"]
                if item["kind"] == "evaluation-run-plan"
            )
            run_plan_evidence["sha256"] = plan_sha256
            run_plan_evidence["receipt"]["campaign_sha256"] = campaign_sha256
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DashboardError, "platform proof is invalid"):
                render(summary_path, run / "dashboard-extra-hardware")

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
            if safe_host_paths.path_is_link(evidence):
                with self.assertRaisesRegex(DashboardError, "symlink or junction"):
                    render(source, root / "site")
                return

            # Default MSYS winsymlinks mode may materialize a plain copy while
            # reporting os.symlink() success. Exercise the opaque-reparse path
            # without pretending that an indistinguishable copy is a link.
            with mock.patch(
                "safe_host_paths._msys_native_file_attributes",
                side_effect=lambda candidate: (
                    safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT
                    if candidate == evidence
                    else None
                ),
            ):
                with self.assertRaisesRegex(DashboardError, "symlink or junction"):
                    render(source, root / "site")

    def test_render_rejects_real_windows_junction_ancestor(self) -> None:
        # The formal MSYS harness maps its general temp root through a drive
        # alias; create the junction fixture on the repository's native drive.
        with tempfile.TemporaryDirectory(dir=HOST_TOOLS) as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            raw = root / "raw"
            raw_target = root / "raw-target"
            raw.rename(raw_target)
            created = create_directory_junction(raw_target, raw)
            if not created:
                raw_target.rename(raw)
                if sys.platform == "cygwin":
                    self.fail("native MSYS test could not create a verified junction")
                self.skipTest("runtime cannot create a detectable Windows junction")
            try:
                with self.assertRaisesRegex(DashboardError, "symlink or junction"):
                    render(source, root / "site")
            finally:
                remove_directory_junction(raw)
            self.assertTrue((raw_target / "boot-01" / "guest.log").is_file())

    def test_shared_path_guard_detects_reparse_attributes_without_following(self) -> None:
        class ReparseStat:
            st_mode = stat.S_IFDIR | 0o755
            st_file_attributes = safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT

        unresolved = Path("must-not-be-resolved")
        with mock.patch.object(Path, "is_junction", side_effect=AssertionError("followed")):
            self.assertTrue(
                safe_host_paths.path_is_link(unresolved, file_info=ReparseStat())
            )

    def test_shared_path_guard_detects_hidden_msys_reparse_attribute(self) -> None:
        class DirectoryStat:
            st_mode = stat.S_IFDIR | 0o755

        candidate = Path("opaque-msys-reparse")
        with mock.patch.object(Path, "is_junction", return_value=False), mock.patch(
            "safe_host_paths._msys_native_file_attributes",
            return_value=safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT,
        ):
            self.assertTrue(
                safe_host_paths.path_is_link(candidate, file_info=DirectoryStat())
            )

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
            for label in (
                "ELF 文件", ".text", ".data", ".bss", "struct proc",
                "最坏用户调用路径栈",
            ):
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
            offline_summary = json.loads(
                (root / "site/evaluation-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(offline_summary["kernel_cost"]["status"], "measured")
            self.assertEqual(
                {
                    item["id"]: (item["value"], item["limit"])
                    for item in offline_summary["kernel_cost"]["guardrails"]
                },
                {
                    "struct_proc_bytes": (26448, 27233),
                    "user_stack_call_path_bytes": (2944, 3072),
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
            report["report_sha256"] = _binding_sha256(unsigned, "scenario-report-v2")
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
                b'"schema_version":2',
                b'"schema_version":2,"schema_version":2',
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
            report["report_sha256"] = _binding_sha256(unsigned, "scenario-report-v2")
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
                "measured scientific results require run.commit",
            ),
            (
                "grade",
                lambda value: value["run"].pop("evidence_grade"),
                "measured scientific results require contract-derived",
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

    def test_four_claim_family_uses_dynamic_bonferroni_threshold(self) -> None:
        summary = fixture()
        multiple = summary["methodology"]["multiple_testing"]
        multiple["headline_claims"].append("fourth-reserved-headline")
        multiple["hypothesis_count"] = 4
        multiple["per_claim_alpha"] = 0.05 / 4
        fwer = summary["methodology"]["fwer_mcid"]
        fwer["headline_count"] = 4
        fwer["per_headline_alpha"] = 0.05 / 4

        validate_summary(summary)

    def test_supported_render_replays_raw_guest_contract(self) -> None:
        from evaluation_contract import write_json
        from test_evaluation_bundle import make_run

        self.contract_replay.stop()
        with tempfile.TemporaryDirectory(dir=HOST_TOOLS) as temporary:
            root = make_run(Path(temporary))
            source = root / "summary.json"
            summary = json.loads(source.read_text(encoding="utf-8"))

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
            with self.assertRaisesRegex(
                DashboardError, "source_log_sha256 is not bound|raw Guest contract replay failed"
            ):
                render(source, root / "tampered-site")

    def test_overview_keeps_registered_claim_family_without_selecting_a_winner(self) -> None:
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
        self.assertEqual(
            match.group(1),
            "3 个机制 claim 与 Task6 固定并列报告；状态不互相替代，也不生成综合总分",
        )
        overview = page.split('<div class="headline-result-grid">', 1)[1].split("</section>", 1)[0]
        self.assertEqual(
            re.findall(
                r'data-overview-slot="mechanism" data-benchmark-id="([^"]+)"',
                overview,
            ),
            summary["methodology"]["multiple_testing"]["headline_claims"],
        )
        self.assertIn('data-benchmark-id="file_query_path_index"', overview)
        self.assertIn('data-benchmark-id="metadata-query-no-gate"', overview)
        self.assertIn('status--supported', overview)
        self.assertIn('status--not_supported', overview)

        summary["benchmarks"] = summary["benchmarks"][:2]
        summary["claims"] = [
            claim
            for claim in summary["claims"]
            if claim["benchmark_id"] == "file_query_path_index"
        ]
        summary["run"].pop("conclusion")
        summary["acceptance"] = derive_acceptance_gates(
            summary["scenarios"],
            summary["claims"],
            COMPETITION_CLAIMS,
            suite_schema_version=summary["schema_version"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        match = re.search(r'<h2 id="conclusion-title">(.*?)</h2>', page)
        self.assertIsNotNone(match)
        self.assertEqual(
            match.group(1),
            "3 个机制 claim 与 Task6 固定并列报告；状态不互相替代，也不生成综合总分",
        )
        self.assertEqual(page.count('data-overview-slot="mechanism"'), 3)

    def test_negative_file_query_is_publishable_but_not_competition_ready(self) -> None:
        summary = fixture()
        attach_scenario(summary, [20, 20, 20, 20, 20, 20, 8])
        remove_one_paired_advantage(summary["benchmarks"][0])
        summary["claims"][0].update(
            status="not_supported",
            title="文件查询优势未通过支持门",
            effect="负结果仍应完整发布。",
        )
        summary["acceptance"] = derive_acceptance_gates(
            summary["scenarios"],
            summary["claims"],
            COMPETITION_CLAIMS,
            suite_schema_version=summary["schema_version"],
        )

        validate_summary(summary)
        self.assertEqual(
            summary["acceptance"]["scientific_evidence"]["status"],
            "publishable",
        )
        self.assertFalse(summary["acceptance"]["competition_ready"])
        self.assertEqual(summary["acceptance"]["tasks"]["task4"], "not_ready")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site/index.html").read_text(encoding="utf-8")
        self.assertIn("科学证据发布", page)
        self.assertIn("可发布", page)
        self.assertIn("竞赛整体验收", page)
        self.assertIn("未就绪", page)
        self.assertIn("任务四竞赛验收仅在功能回执通过", page)
        self.contract_replay_mock.assert_called_once()

        forged = copy.deepcopy(summary)
        forged["acceptance"]["competition_ready"] = True
        with self.assertRaisesRegex(DashboardError, "acceptance gates are forged"):
            validate_summary(forged)

    def test_task4_competition_claim_registration_fails_closed(self) -> None:
        cases = (
            (
                "missing task mapping",
                lambda value: value["methodology"].__setitem__(
                    "competition_claims", {}
                ),
                "register exactly Task 4",
            ),
            (
                "dangling benchmark",
                lambda value: value["methodology"]["competition_claims"][
                    "task4"
                ].__setitem__("benchmark_id", "missing-task4-benchmark"),
                "must bind one registered Task 4 headline claim",
            ),
            (
                "wrong task benchmark",
                lambda value: value["methodology"]["competition_claims"][
                    "task4"
                ].__setitem__("benchmark_id", "scheduler-tail"),
                "must bind one registered Task 4 headline claim",
            ),
            (
                "weakened required status",
                lambda value: value["methodology"]["competition_claims"][
                    "task4"
                ].__setitem__("required_status", "not_supported"),
                "must bind one registered Task 4 headline claim",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

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
                mcid_sign_test=None,
                samples=[],
            )
        for diagnostic in benchmark["diagnostics"]:
            diagnostic.update(
                status="unavailable",
                cache_states=[],
                duration_us=None,
                work_units=None,
                index_rebuild_records=None,
                result_cache_hits=None,
                samples=[],
            )
        unavailable["claims"][0].update(
            status="unavailable",
            title="本轮没有可用测量",
            effect="原始测量不可用，没有填补数值。",
        )
        unavailable["acceptance"] = derive_acceptance_gates(
            unavailable["scenarios"],
            unavailable["claims"],
            COMPETITION_CLAIMS,
            suite_schema_version=unavailable["schema_version"],
        )
        validate_summary(unavailable)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, unavailable)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn(
                'data-overview-slot="mechanism" '
                'data-benchmark-id="file_query_path_index"',
                page,
            )
            self.assertIn("Metadata 索引查询没有可用的合同测量", page)
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
                "joint MCID count",
                lambda value: value["benchmarks"][0]["paired"][0]["mcid_sign_test"].__setitem__("wins", 6),
                "counts do not match paired n",
            ),
            (
                "joint MCID fraction",
                lambda value: value["benchmarks"][0]["paired"][0]["mcid_sign_test"].__setitem__("numerator", 2),
                "exact fraction",
            ),
            (
                "bootstrap inference",
                lambda value: value["methodology"]["descriptive_interval"].__setitem__(
                    "role", "headline gate"
                ),
                "descriptive-only",
            ),
            (
                "causal boundary",
                lambda value: value["methodology"]["interpretation_boundaries"].__setitem__(
                    "host_page_cache", "controlled"
                ),
                "fixed causal limits",
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

    def test_timed_file_work_receipts_cannot_be_forged(self) -> None:
        mutations = (
            (
                "skip one baseline path",
                lambda value: value["benchmarks"][0]["samples"][0].__setitem__(
                    "work_units", 127
                ),
                "complete traversal",
            ),
            (
                "hide one examined record",
                lambda value: value["benchmarks"][0]["samples"][0].__setitem__(
                    "records_examined", 127
                ),
                "complete traversal",
            ),
            (
                "zero index work",
                lambda value: value["benchmarks"][0]["samples"][7].__setitem__(
                    "work_units", 0
                ),
                "ready-index receipt",
            ),
            (
                "unbounded index work",
                lambda value: value["benchmarks"][0]["samples"][7].__setitem__(
                    "work_units", 513
                ),
                "ready-index receipt",
            ),
            (
                "wrong corpus size",
                lambda value: value["benchmarks"][0]["samples"][0].__setitem__(
                    "dataset_size", 127
                ),
                "dataset_size must equal",
            ),
            (
                "operation drift",
                lambda value: value["benchmarks"][0]["samples"][1].update(
                    operations=2,
                    result_items=2,
                    work_units=256,
                    records_examined=256,
                ),
                "must be stable across independent boots",
            ),
            (
                "result count drift",
                lambda value: value["benchmarks"][0]["samples"][0].__setitem__(
                    "result_items", 2
                ),
                "one structured result per operation",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

    def test_diagnostic_readiness_cannot_be_forged(self) -> None:
        mutations = (
            (
                "summary",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["duration_us"].__setitem__("median", 999),
                "does not match diagnostic samples",
            ),
            (
                "result-cache summary",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["result_cache_hits"].__setitem__("median", 1),
                "does not match diagnostic samples",
            ),
            (
                "cache",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("cache", "cold-rebuild"),
                "conflicts with rebuild records",
            ),
            (
                "dataset",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("dataset_size", 127),
                "dataset_size must equal",
            ),
            (
                "result items",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("result_items", 2),
                "result_items must match",
            ),
            (
                "result cache",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("result_cache_hits", 1),
                "result_cache_hits must be zero",
            ),
            (
                "workload fingerprint",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("workload_fingerprint", "0" * 16),
                "non-zero lowercase 16-hex receipt",
            ),
            (
                "result fingerprint",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("result_fingerprint", "Z" * 16),
                "non-zero lowercase 16-hex receipt",
            ),
            (
                "evidence",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("evidence_id", "raw-scenario"),
                "requires benchmark-bound verified evidence",
            ),
            (
                "source log",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("source_log", "boot-02/guest.log"),
                "source_log is not bound",
            ),
            (
                "source log hash",
                lambda value: value["benchmarks"][0]["diagnostics"][0]["samples"][0].__setitem__("source_log_sha256", "b" * 64),
                "source_log_sha256 is not bound",
            ),
        )
        for name, mutate, message in mutations:
            with self.subTest(name=name):
                summary = fixture()
                mutate(summary)
                with self.assertRaisesRegex(DashboardError, message):
                    validate_summary(summary)

        missing = fixture()
        missing["benchmarks"][0]["diagnostics"] = []
        with self.assertRaisesRegex(DashboardError, "empty diagnostics cannot pass"):
            validate_summary(missing)

        for field in ("index_rebuild_records", "result_cache_hits"):
            with self.subTest(timed_sample=field):
                summary = fixture()
                summary["benchmarks"][0]["samples"][0][field] = 1
                with self.assertRaisesRegex(DashboardError, "actual timed sample guardrail"):
                    validate_summary(summary)

    def test_diagnostic_marker_provenance_is_replayed_from_raw_evidence(self) -> None:
        summary = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            summary["benchmarks"][0]["diagnostics"][0]["samples"][0][
                "source_marker_sha256"
            ] = "0" * 64
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(DashboardError, "differs from the evidence line"):
                render(source, root / "site")

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
        self.assertRegex(css, r"\.benchmark-block\s*\{[^}]*min-width:\s*0")
        self.assertRegex(css, r"\.table-scroll\s*\{[^}]*max-width:\s*100%")
        self.assertRegex(css, r"\.overview-grid > div\s*\{[^}]*min-width:\s*0")
        self.assertRegex(
            css,
            r"\.headline-result-grid\s*\{[^}]*grid-template-columns:\s*repeat\(4,",
        )
        self.assertRegex(css, r"\.raw-pair-link\s*\{[^}]*opacity:\s*0\.55")
        self.assertRegex(
            css,
            r"(?s)@media \(max-width: 1024px\).*?\.headline-result-grid\s*\{[^}]*repeat\(2,",
        )
        self.assertRegex(
            css,
            r"\.environment-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)",
        )
        self.assertRegex(css, r"\.environment-cell dd\s*\{[^}]*overflow-wrap:\s*anywhere")
        self.assertRegex(
            css,
            r"(?s)@media \(max-width: 1024px\).*?\.environment-grid\s*\{[^}]*repeat\(2,",
        )
        self.assertRegex(
            css,
            r"(?s)@media \(max-width: 700px\).*?\.environment-grid\s*\{[^}]*grid-template-columns:\s*1fr",
        )
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
        self.assertGreaterEqual(len(figures), 1)
        for figure in figures:
            self.assertRegex(figure, r'data-chart-unit="[^"]+"')
            self.assertRegex(figure, r'data-chart-n="[^"]+"')
            self.assertRegex(figure, r'data-raw-pairs="[1-9][0-9]*"')
            self.assertRegex(figure, r'data-chart-source="[^"]+"')
            self.assertIn("<strong>单位</strong>", figure)
            self.assertIn("<strong>n</strong>", figure)
            self.assertIn("<strong>原始配对</strong>", figure)
            self.assertIn("<strong>来源</strong>", figure)
            self.assertIn('class="raw-pair-link"', figure)
            self.assertIn('class="raw-pair-dot raw-pair-dot--baseline"', figure)
            self.assertIn('class="raw-pair-dot raw-pair-dot--treatment"', figure)

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

    def test_overview_slots_follow_registration_while_performance_keeps_every_chart(self) -> None:
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
        self.assertNotIn("chart-title-overview", page)
        overview = page.split('<div class="headline-result-grid">', 1)[1].split("</section>", 1)[0]
        self.assertEqual(
            re.findall(
                r'data-overview-slot="mechanism" data-benchmark-id="([^"]+)"',
                overview,
            ),
            summary["methodology"]["multiple_testing"]["headline_claims"],
        )
        self.assertIn(
            '<title id="chart-title-benchmark-0">首个但未过门的测量逐 boot 配对与区间图</title>',
            page,
        )
        self.assertIn(
            '<title id="chart-title-benchmark-1">Metadata 索引查询逐 boot 配对与区间图</title>',
            page,
        )

    def test_scenario_function_and_performance_are_separate_and_bound(self) -> None:
        summary = fixture()
        attach_scenario(summary)
        validate_summary(summary)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("功能状态", page)
        self.assertIn("性能状态", page)
        self.assertIn("signed delta 定义为 plain-AgentOS，中位数 +20 ms", page)
        self.assertIn("描述性 bootstrap 95% 区间", page)
        self.assertIn("正向 joint-MCID 10 ms 与 5%：胜场 7/7", page)
        self.assertIn("反向 joint-MCID -10 ms 与 -5%：负场 0/7", page)
        self.assertIn("one-sided exact p=0.007812", page)
        self.assertIn("Bonferroni 后每方向 alpha=0.025", page)
        overview = page.split('<div class="headline-result-grid">', 1)[1].split(
            "</section>", 1
        )[0]
        self.assertEqual(overview.count('data-overview-slot="task6"'), 1)
        self.assertIn("任务 6 端到端场景", overview)
        self.assertIn("status--supported", overview)
        self.assertIn("signed delta 定义为 plain-AgentOS，中位数 +20 ms", overview)
        scenario_panel = page.split('id="panel-scenarios"', 1)[1].split(
            'id="panel-evidence"', 1
        )[0]
        self.assertIn("full-stack / non-single-mechanism", scenario_panel)
        self.assertIn("Host page cache uncontrolled", scenario_panel)

    def test_scenario_joint_mcid_six_of_seven_is_inconclusive(self) -> None:
        summary = fixture()
        scenario = attach_scenario(summary, [20, 20, 20, 20, 20, 20, 8])
        performance = scenario["performance"]

        self.assertEqual(scenario["performance_status"], "inconclusive")
        self.assertGreaterEqual(performance["ci_low"], 10)
        self.assertGreaterEqual(performance["relative_ci_low"], 5)
        self.assertEqual(performance["sign_test"]["wins"], 7)
        self.assertLessEqual(performance["sign_test"]["p_value"], 0.05)
        self.assertEqual(performance["mcid_sign_test"]["wins"], 6)
        self.assertEqual(performance["mcid_sign_test"]["non_wins"], 1)
        self.assertEqual(performance["mcid_sign_test"]["n"], 7)
        self.assertEqual(performance["mcid_sign_test"]["p_value"], 1 / 16)
        validate_summary(summary)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("正向 joint-MCID 10 ms 与 5%：胜场 6/7", page)
        self.assertIn("one-sided exact p=0.0625", page)

    def test_unregistered_scenario_regression_is_explicit_and_diagnostic(self) -> None:
        summary = fixture()
        scenario = attach_scenario(summary, [-20] * 7)
        performance = scenario["performance"]

        self.assertEqual(scenario["performance_status"], "regressed")
        self.assertEqual(performance["sign_test"]["wins"], 0)
        self.assertEqual(performance["sign_test"]["losses"], 7)
        self.assertEqual(performance["regression_mcid_sign_test"]["losses"], 7)
        self.assertTrue(summary["acceptance"]["competition_ready"])
        self.assertEqual(summary["acceptance"]["tasks"]["task6"], "pass")
        validate_summary(summary)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, summary)
            render(source, root / "site")
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("status--regressed", page)
        self.assertIn("统计结论：显著回退 (regressed)", page)
        self.assertIn("signed delta 定义为 plain-AgentOS，中位数 -20 ms", page)
        self.assertIn("符号胜/负/平=0/7/0", page)
        self.assertIn("正向 joint-MCID 10 ms 与 5%：胜场 0/7", page)
        self.assertIn("反向 joint-MCID -10 ms 与 -5%：负场 7/7", page)
        self.assertIn("Bonferroni 后每方向 alpha=0.025", page)
        self.assertIn("任务 6 端到端场景 性能=显著回退", page)
        self.assertIn("但只作为诊断", page)
        self.assertIn("Schema evaluation-summary-v3", page)

        legacy = copy.deepcopy(summary)
        legacy["schema_version"] = 2
        legacy["run"]["suite_id"] = "agentos-evaluation-v2"
        legacy["acceptance"] = derive_acceptance_gates(
            legacy["scenarios"],
            legacy["claims"],
            COMPETITION_CLAIMS,
            suite_schema_version=2,
        )
        self.assertFalse(legacy["acceptance"]["competition_ready"])
        self.assertEqual(legacy["acceptance"]["tasks"]["task6"], "not_ready")
        validate_summary(legacy)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, legacy)
            render(source, root / "site")
            legacy_page = (root / "site" / "index.html").read_text(
                encoding="utf-8"
            )
        self.assertIn("suite v2 中场景若得到", legacy_page)
        self.assertIn("必须保持未就绪", legacy_page)
        self.assertNotIn("但只作为诊断", legacy_page)
        self.assertIn("Schema evaluation-summary-v2", legacy_page)

        mixed = copy.deepcopy(legacy)
        mixed["run"]["suite_id"] = "agentos-evaluation-v3"
        with self.assertRaisesRegex(
            DashboardError, "suite_id does not match"
        ):
            validate_summary(mixed)

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
                "joint MCID p-value",
                lambda value: value["scenarios"][-1]["performance"]["mcid_sign_test"].__setitem__(
                    "p_value", 0.01
                ),
                "p_value does not match the exact joint-MCID test",
            ),
            (
                "reverse joint MCID loss count",
                lambda value: value["scenarios"][-1]["performance"][
                    "regression_mcid_sign_test"
                ].__setitem__("losses", 7),
                "counts do not match the full paired n",
            ),
            (
                "scenario bootstrap inference role",
                lambda value: value["scenarios"][-1]["performance"]["bootstrap"].__setitem__(
                    "role", "headline_gate"
                ),
                "descriptive-only scenario interval",
            ),
            (
                "scenario interpretation boundary",
                lambda value: value["scenarios"][-1]["performance"]["interpretation"].__setitem__(
                    "host_page_cache", "controlled"
                ),
                "must state full-stack",
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
                attach_scenario(summary)
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
        self.assertIn("通过预注册 joint-MCID 精确检验门", page)
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
