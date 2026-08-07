#!/usr/bin/env python3
"""离线评测仪表板的契约测试。"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
import unittest
from fractions import Fraction
from html.parser import HTMLParser
from unittest import mock
from pathlib import Path
from urllib.parse import unquote, urlsplit

import evaluation_kernel_cost as kernel_cost
import safe_host_paths
from test_evaluation_kernel_cost import Fixture as KernelCostFixture
from test_evaluation_kernel_cost import _write_json as write_kernel_json

from render_evaluation_dashboard import (
    DashboardError,
    _binding_sha256,
    _bootstrap_interval,
    _read_evidence_file,
    render as render_dashboard,
    validate_summary,
)
from evaluation_contract import derive_acceptance_gates
import agenteval_measurement_source_contract as measurement_source
import scenario_timing_source_contract
from evaluation_scenario import (
    PROGRAM_SOURCE_PAIR_DOMAIN,
    PROGRAM_SOURCE_RECEIPT_DOMAIN,
    RESOURCE_STABILITY_CHILD_ROUNDS,
    RESOURCE_STABILITY_FILE,
    RESOURCE_STABILITY_GROWTH_BOUNDS,
    RESOURCE_STABILITY_INTERPRETATION,
    RESOURCE_STABILITY_LOAD_WORKFLOWS,
    RESOURCE_STABILITY_MEASUREMENT_SCOPE,
    RESOURCE_STABILITY_RESOURCE_KINDS,
    RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
    _program_source_comparability_receipt_from_snapshot,
    read_snapshot_expected_programs,
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
_MEASUREMENT_SOURCE_RECEIPTS: dict[str, dict] = {}


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


def _measurement_source_receipt(source_commit: str) -> dict:
    if source_commit not in _MEASUREMENT_SOURCE_RECEIPTS:
        _MEASUREMENT_SOURCE_RECEIPTS[source_commit] = {
            "contract_versions": {
                "functional": measurement_source.FUNCTIONAL_CONTRACT_VERSION,
                "functional_compile": (
                    measurement_source.FUNCTIONAL_COMPILE_CONTRACT_VERSION
                ),
                "micro": measurement_source.CONTRACT_VERSION,
                "policy": measurement_source.POLICY_INVENTORY_SCHEMA,
                "scenario": scenario_timing_source_contract.CONTRACT_VERSION,
            },
            "formal_boot_count": measurement_source.FORMAL_BOOT_COUNT,
            "policy_inventory": measurement_source.measurement_source_policy_inventory(),
            "schema": measurement_source.RECEIPT_SCHEMA,
            "source_commit": source_commit,
            "sources": [
                {
                    "bytes": len(data),
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for path in measurement_source._receipt_source_paths()
                for data in [(CONTRACT_ROOT / path).read_bytes()]
            ],
            "stop_rule": measurement_source.STOP_RULE,
        }
    return copy.deepcopy(_MEASUREMENT_SOURCE_RECEIPTS[source_commit])


def _scenario_source_comparability_fixture(
    source_commit: str,
) -> tuple[list[str], dict, dict]:
    measurement_receipt = _measurement_source_receipt(source_commit)
    programs, _ = read_snapshot_expected_programs(
        CONTRACT_ROOT, source_commit, measurement_receipt
    )
    receipt = _program_source_comparability_receipt_from_snapshot(
        CONTRACT_ROOT, source_commit, programs, measurement_receipt
    )
    summary = {
        "schema": receipt["schema"],
        "source_commit": source_commit,
        "expected_programs": len(programs),
        "same_source_programs": receipt["same_source_programs"],
        "platform_specific_programs": receipt["platform_specific_programs"],
        "receipt_sha256": receipt["sha256"],
    }
    return list(programs), receipt, summary


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
    programs, source_receipt, source_comparability = (
        _scenario_source_comparability_fixture(summary["run"]["commit"])
    )
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
            makespan = (
                performance_sample[f"{target}_ms"]
                if performance_sample is not None
                else 100 + index - advantage
            )
            rows = [
                {
                    "program": program,
                    "elapsed_ms": makespan if program_index == 0 else 0,
                }
                for program_index, program in enumerate(programs)
            ]
            targets[target] = {
                "makespan_ms": makespan,
                "programs": rows,
                "raw_source_receipt": {
                    "sha256": receipts[target],
                    "program_source_comparability": copy.deepcopy(source_receipt),
                },
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
            "source_comparability": source_comparability,
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
    if any(item["id"] == "raw-scenario" for item in summary["evidence"]):
        (root / "measurement-source-receipt.json").write_text(
            json.dumps(
                _measurement_source_receipt(summary["run"]["commit"]),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
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
            def runner(argv, timeout, maximum, cwd):
                if list(argv)[-1] == "--version":
                    return fixture.runner(argv, timeout, maximum, cwd)
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
        "schema_version": 5,
        "kind": "agentos-evaluation-summary",
        "run": {
            "id": "evaluation-20260730",
            "suite_id": "agentos-evaluation-v5",
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
            "supplementary_evaluations": [{
                "id": "multi_identity_revisit_isolation",
                "label": "Multi-identity workflow revisit isolation",
                "task": "task1",
                "status": "unavailable",
                "performance_gate": None,
                "visit_sequence": ["A", "B", "C", "D", "A"],
                "concurrency_levels": [1, 2, 4],
                "rounds_per_level": 16,
                "latency_unit": "us",
                "throughput_unit": "milli_requests_per_second",
                "percentile_method": "nearest_rank",
                "qos_schema_version": 2,
                "latency_metrics": ["wait", "service", "turnaround"],
                "turnaround_definition": "worker_completed_minus_parent_submitted",
                "goodput_unit": "milli_requests_per_second",
                "fairness_scale": "parts_per_million",
                "fairness_basis": "per_identity_isolated_completions",
                "isolation_definition": (
                    "correct_and_zero_contamination_and_no_fallback"
                ),
                "digest": "fnv1a64_challenge_bound",
                "interpretation": "Descriptive current-schema fixture.",
                "boots": [],
            }],
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

    def test_render_publishes_offline_site_and_machine_outputs(self) -> None:
        summary = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "site"
            render(write_render_input(root, summary), output)

            expected = {
                "index.html",
                "evaluation-summary.json",
                "dashboard-verification.json",
                "metrics.csv",
                "assets/evaluation-dashboard.css",
                "assets/evaluation-dashboard.js",
            }
            actual = {
                path.relative_to(output).as_posix()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertTrue(expected <= actual)
            assert_offline_links_resolve(output / "index.html")

            verification = json.loads(
                (output / "dashboard-verification.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verification["verified_evidence_count"], 4)
            with (output / "metrics.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                (len(rows), rows[0]["unit"], rows[0]["n"]),
                (4, "us/query", "7"),
            )
            self.contract_replay_mock.assert_called_once()

    def test_render_rejects_symlinked_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, fixture())
            target = root / "site-target"
            target.mkdir()
            sentinel = target / "keep.txt"
            sentinel.write_text("old site\n", encoding="utf-8")
            output = root / "site"
            try:
                os.symlink(target, output, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlink creation unavailable: {error}")

            if safe_host_paths.path_is_link(output):
                with self.assertRaisesRegex(
                    DashboardError, "symlink or junction"
                ):
                    render(source, output)
            else:
                with mock.patch(
                    "safe_host_paths._msys_native_file_attributes",
                    side_effect=lambda candidate: (
                        safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT
                        if candidate == output
                        else None
                    ),
                ):
                    with self.assertRaisesRegex(
                        DashboardError, "symlink or junction"
                    ):
                        render(source, output)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "old site\n")

    def test_publish_failure_restores_complete_previous_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, fixture())
            output = root / "site"
            render(source, output)
            (output / "old-only.txt").write_text("keep me\n", encoding="utf-8")
            before = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            real_replace = os.replace

            def fail_new_site(source_path: Path, destination_path: Path) -> None:
                source_path = Path(source_path)
                destination_path = Path(destination_path)
                if (
                    ".staging-" in source_path.name
                    and not source_path.name.endswith(".previous")
                    and destination_path == output
                ):
                    raise OSError("simulated directory publication failure")
                real_replace(source_path, destination_path)

            with mock.patch(
                "render_evaluation_dashboard._replace_site_directory",
                side_effect=fail_new_site,
            ):
                with self.assertRaisesRegex(
                    DashboardError, "previous site restored"
                ):
                    render(source, output)

            after = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(list(root.glob(".site.staging-*")), [])

    def test_render_refuses_to_replace_unowned_non_dashboard_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = write_render_input(root, fixture())
            output = root / "site"
            output.mkdir()
            sentinel = output / "important.txt"
            sentinel.write_text("do not delete\n", encoding="utf-8")
            with self.assertRaisesRegex(
                DashboardError, "not an AgentOS Dashboard"
            ):
                render(source, output)
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "do not delete\n"
            )

    def test_render_refuses_evidence_source_inside_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            output = root / "site"
            render(source, output)

            item = next(
                entry for entry in summary["evidence"]
                if entry["id"] == "missing-task6"
            )
            original = root.joinpath(*item["path"].split("/"))
            protected = output / "source-preflight.json"
            protected.write_bytes(original.read_bytes())
            item["path"] = "site/source-preflight.json"
            item["sha256"] = hashlib.sha256(protected.read_bytes()).hexdigest()
            item["receipt"]["bytes"] = protected.stat().st_size
            rewrite_summary(source, summary)

            with self.assertRaisesRegex(DashboardError, r"evidence\[2\] input"):
                render(source, output)
            self.assertEqual(protected.read_bytes(), original.read_bytes())
            self.assertTrue((output / "index.html").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows path alias regression")
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

    def test_render_rejects_diagnostic_marker_not_on_raw_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = fixture()
            source = write_render_input(root, summary)
            sample = summary["benchmarks"][0]["diagnostics"][0]["samples"][0]
            sample["source_marker_sha256"] = "0" * 64
            rewrite_summary(source, summary)
            with self.assertRaisesRegex(
                DashboardError, "source_marker_sha256 differs from the evidence line"
            ):
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
                with self.assertRaisesRegex(
                    DashboardError, "(?:symbolic link|symlink) or junction"
                ):
                    render(source, root / "site")
                return

                # 默认 MSYS winsymlinks 模式可能生成普通副本，却报告 os.symlink()
                # 成功。应测试不透明重解析路径，不把无法区分的副本假装成链接。
            with mock.patch(
                "safe_host_paths._msys_native_file_attributes",
                side_effect=lambda candidate: (
                    safe_host_paths._FILE_ATTRIBUTE_REPARSE_POINT
                    if candidate == evidence
                    else None
                ),
            ):
                with self.assertRaisesRegex(
                    DashboardError, "(?:symbolic link|symlink) or junction"
                ):
                    render(source, root / "site")

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
            cost_slot = page.split('data-extension-slot="kernel-cost"', 1)[1].split(
                "</article>", 1
            )[0]
            self.assertIn("内存映像 -30.5% · .text -10.0%", cost_slot)
            self.assertIn("text+data+bss 0.000 → 0.000 MiB", cost_slot)
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

    def test_free_text_cannot_override_structured_conclusions(self) -> None:
        summary = fixture()
        attack = "self-reported: AgentOS improves every workload"
        summary["claims"][0]["title"] = attack
        summary["claims"][0]["effect"] = attack
        summary["run"]["conclusion"] = attack
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render(write_render_input(root, summary), root / "site")
            page = (root / "site/index.html").read_text(encoding="utf-8")
            exported = (root / "site/evaluation-summary.json").read_text(
                encoding="utf-8"
            )
        self.assertNotIn(attack, page)
        self.assertNotIn(attack, exported)
        self.assertNotIn('"conclusion"', exported)

        summary = fixture()
        summary["run"]["evidence_grade"] = "E9-self-asserted"
        with self.assertRaisesRegex(DashboardError, "contract-derived"):
            validate_summary(summary)

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


class DashboardReplayIntegrationTests(unittest.TestCase):
    def test_supported_render_replays_raw_guest_contract(self) -> None:
        from evaluation_contract import write_json
        from test_evaluation_bundle import make_run

        with tempfile.TemporaryDirectory(dir=HOST_TOOLS) as temporary:
            with mock.patch(
                "test_evaluation_bundle.materialize_compatibility_fixture"
            ):
                root = make_run(Path(temporary))
            self.assertTrue((root / "dashboard/index.html").is_file())
            source = root / "summary.json"
            summary = json.loads(source.read_text(encoding="utf-8"))
            evidence = next(
                item for item in summary["evidence"]
                if item["kind"] == "guest-raw-log"
            )
            log_path = root.joinpath(*evidence["path"].split("/"))
            tampered = log_path.read_bytes().replace(b"boot\n", b"bo0t\n", 1)
            log_path.write_bytes(tampered)
            digest = hashlib.sha256(tampered).hexdigest()
            evidence["sha256"] = digest
            for benchmark in summary["benchmarks"]:
                for diagnostic in benchmark["diagnostics"]:
                    for sample in diagnostic["samples"]:
                        if sample["evidence_id"] == evidence["id"]:
                            sample["source_log_sha256"] = digest
            write_json(source, summary)

            with self.assertRaisesRegex(
                DashboardError, "raw Guest contract replay failed"
            ):
                render(source, root / "tampered-site")


if __name__ == "__main__":
    unittest.main()
