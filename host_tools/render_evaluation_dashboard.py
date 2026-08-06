#!/usr/bin/env python3
"""Render a self-contained, evidence-bound AgentOS evaluation dashboard."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import io
import json
import math
import os
import random
import re
import shutil
import stat
import statistics
import tempfile
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote

from evaluation_kernel_cost import (
    KernelCostError,
    build_dashboard_fragment as build_kernel_cost_fragment,
    verify_portable as verify_kernel_cost_portable,
)
from evaluation_campaign import CampaignError, validate_campaign
from evaluation_contract import (
    CONCURRENCY_PUBLIC_FIELDS,
    EVALUATION_SCHEMA_VERSION,
    EVALUATION_SUITE_ID,
    EvaluationError as EvaluationContractError,
    QOS_REGISTRATION_FIELDS,
    derive_acceptance_gates,
    load_run_plan,
    verify as verify_evaluation_contract,
)
from strict_json import read_strict_json, strict_json_loads
from agenteval_measurement_source_policy import measurement_source_policy_inventory
from safe_host_paths import (
    absolute_lexical_path,
    atomic_write_bytes as safe_atomic_write_bytes,
    ensure_safe_directory,
    path_is_link,
    read_regular_file,
    reject_link_components,
    require_private_directory,
    require_regular_file,
    require_safe_directory,
    walk_directory_tree_no_links,
    walk_regular_files_no_links,
)
from compatibility_overhead import (
    CompatibilityRunError,
    verify_campaign_artifacts as verify_compatibility_artifacts,
)
from compatibility_overhead_contract import (
    METRICS as COMPATIBILITY_METRICS,
    CompatibilityContractError,
)
from evaluation_scenario import (
    GIT_OBJECT_ID_RE,
    MAX_PROGRAM_SOURCE_BYTES,
    PROGRAM_SOURCE_PAIR_DOMAIN,
    PROGRAM_SOURCE_RECEIPT_DOMAIN,
    PROGRAM_SOURCE_RECEIPT_SCHEMA,
    RESOURCE_STABILITY_CHILD_ROUNDS,
    RESOURCE_STABILITY_FILE,
    RESOURCE_STABILITY_GROWTH_BOUNDS,
    RESOURCE_STABILITY_INTERPRETATION,
    RESOURCE_STABILITY_LOAD_WORKFLOWS,
    RESOURCE_STABILITY_MEASUREMENT_SCOPE,
    RESOURCE_STABILITY_RESOURCE_KINDS,
    RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
    TASK6_EXPECTED_PROGRAM_COUNT,
    ScenarioEvidenceError,
    _program_source_comparability_receipt_from_snapshot,
    read_snapshot_expected_programs,
)


TOP_LEVEL_FIELDS = {
    "schema_version",
    "kind",
    "run",
    "targets",
    "benchmarks",
    "scenarios",
    "methodology",
    "evidence",
    "claims",
    "acceptance",
}
BENCHMARK_FIELDS = {
    "id",
    "label",
    "task",
    "baseline",
    "treatment",
    "unit",
    "direction",
    "claim_gate",
    "loads",
    "estimates",
    "samples",
    "paired",
    "diagnostics",
    "evidence_ids",
    "status",
}
PAIRED_FIELDS = {
    "load",
    "status",
    "n",
    "median",
    "p95",
    "ci_low",
    "ci_high",
    "relative_median_percent",
    "relative_ci_low",
    "relative_ci_high",
    "sign_test",
    "mcid_sign_test",
    "samples",
}
EVIDENCE_FIELDS = {"id", "kind", "label", "path", "sha256", "status", "source"}
DIAGNOSTIC_FIELDS = {
    "load",
    "status",
    "unit",
    "cache_states",
    "duration_us",
    "work_units",
    "index_rebuild_records",
    "result_cache_hits",
    "samples",
}
BENCHMARK_SAMPLE_FIELDS = {
    "target_id",
    "load",
    "value",
    "trial",
    "order",
    "boot_id",
    "evidence_id",
    "operations",
    "dataset_size",
    "work_units",
    "records_examined",
    "result_items",
    "index_rebuild_records",
    "result_cache_hits",
}
DIAGNOSTIC_SAMPLE_FIELDS = {
    "boot_id",
    "cache",
    "operations",
    "dataset_size",
    "work_units",
    "result_items",
    "duration_us",
    "index_rebuild_records",
    "result_cache_hits",
    "workload_fingerprint",
    "result_fingerprint",
    "evidence_id",
    "source_log",
    "source_line",
    "source_log_sha256",
    "source_marker_sha256",
}
CLAIM_FIELDS = {
    "id",
    "title",
    "status",
    "effect",
    "benchmark_id",
    "evidence_ids",
}

BENCHMARK_STATUSES = {"measured", "unavailable", "failed"}
SCENARIO_FUNCTIONAL_STATUSES = {"pass", "fail", "unavailable"}
SCENARIO_PERFORMANCE_STATUSES = {
    "supported", "regressed", "inconclusive", "failed", "unavailable"
}
CLAIM_STATUSES = {"supported", "not_supported", "unavailable"}
EVIDENCE_STATUSES = {"verified", "unverified", "unavailable", "invalid"}
RUN_STATUSES = {"measured", "unavailable", "failed"}
DIRECTIONS = {"lower_is_better", "higher_is_better", "neutral"}
TASK_IDS = tuple(f"task{index}" for index in range(1, 7))
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT16 = re.compile(r"^[0-9a-f]{16}$")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
BOOTSTRAP_REPETITIONS = 2_000
MINIMUM_SCENARIO_BOOTS = 7
MINIMUM_BENCHMARK_BOOTS = 7
MINIMUM_INNER_PAIRS = 7
FILE_META_CAPACITY = 512
FILE_QUERY_PATH_INDEX = "file_query_path_index"
FILE_QUERY_TABLE_ABLATION = "file_query_table_ablation"
FILE_QUERY_EXPERIMENTS = {
    FILE_QUERY_PATH_INDEX,
    FILE_QUERY_TABLE_ABLATION,
}
BENCHMARK_GATE_FLOORS = {
    "minimum_absolute_improvement_us": 5.0,
    "minimum_baseline_duration_us": 20.0,
    "minimum_relative_improvement_percent": 5.0,
}
SCENARIO_GATE_FLOORS = {
    "minimum_absolute_improvement_ms": 10.0,
    "minimum_baseline_makespan_ms": 50.0,
    "minimum_relative_improvement_percent": 5.0,
}
SCENARIO_ALPHA = 0.05
SCENARIO_DIRECTIONAL_ALPHA = SCENARIO_ALPHA / 2
SCENARIO_INFERENCE = {
    "method": "exact_directional_binomial_with_bonferroni",
    "success_unit": "paired_boot",
    "sample_policy": "full_n_including_non_wins",
    "alpha": SCENARIO_ALPHA,
    "multiplicity": "two_directions_within_task6_scenario",
    "directional_hypothesis_count": 2,
    "correction": "Bonferroni",
    "per_direction_alpha": SCENARIO_DIRECTIONAL_ALPHA,
}
SCENARIO_INTERPRETATION = {
    "design": "full-stack",
    "causal_attribution": "non-single-mechanism",
    "host_page_cache": "uncontrolled",
}
MAX_SCENARIO_REPORT_BYTES = 32 << 20
MAX_SCENARIO_SAMPLES = 128
MAX_SCENARIO_PROGRAMS = 128
MAX_SCENARIO_MODULES = 64
MAX_SCENARIO_DURATION_MS = 3_600_000
MAX_PORTABLE_EVIDENCE_FILES = 256
MAX_PORTABLE_EVIDENCE_FILE_BYTES = 32 << 20
MAX_PORTABLE_EVIDENCE_TOTAL_BYTES = 64 << 20
MAX_COMPATIBILITY_EVIDENCE_FILES = 256
MAX_COMPATIBILITY_EVIDENCE_DIRECTORIES = 128
MAX_COMPATIBILITY_EVIDENCE_FILE_BYTES = 64 << 20
MAX_COMPATIBILITY_EVIDENCE_TOTAL_BYTES = 256 << 20
MAX_COMPATIBILITY_EVIDENCE_DEPTH = 8
INFERENCE_METHOD = (
    "exact one-sided binomial sign test of per-boot joint MCID exceedance; "
    "a win strictly exceeds both absolute and relative MCIDs"
)
DESCRIPTIVE_INTERVAL_ROLE = (
    "descriptive only; never used to support a headline claim"
)
INTERPRETATION_BOUNDARIES = {
    "microbenchmark_design": "same-kernel-paired-comparison",
    "microbenchmark_causal_scope": (
        "task-facing-path-vs-index-and-isolated-ablation-under-preregistered-workloads"
    ),
    "scenario_design": "full-stack",
    "scenario_attribution": "non-single-mechanism",
    "host_page_cache": "uncontrolled",
}
SCENARIO_KEY_OUTCOMES = (
    "research_rerun",
    "workflow_stage",
    "artifact_derivation",
    "llm_response",
)
RESOURCE_STABILITY_SUMMARY_FIELDS = {
    "status",
    "required_target",
    "measurement_scope",
    "verified_boots",
    "load_workflows_per_boot",
    "terminal_workflows_per_boot",
    "child_rounds_per_load_workflow",
    "global_observation",
    "interpretation",
    "boot_receipts",
}
RESOURCE_STABILITY_OBSERVATION_FIELDS = {
    "coverage",
    "measured_mask_semantics",
    "snapshot_consistency",
    "account_counters",
    "rate_budgets",
    "growth_bound_semantics",
    "decrease_semantics",
    "free_pages",
    "resources",
}
RESOURCE_STABILITY_RESOURCE_FIELDS = {
    "kind",
    "status",
    "coverage",
    "per_workflow_growth_bound",
    "terminal_growth_bound",
    "max_observed_per_workflow_growth",
    "terminal_observed_growth",
    "plateau_or_reclamation",
    "exact_terminal_recovery",
}
RESOURCE_STABILITY_RECEIPT_FIELDS = {
    "sample_id",
    "challenge",
    "resource_receipt_sha256",
    "binding_sha256",
    "raw_source_receipt_sha256",
}
SCENARIO_OUTCOME_FIELDS = {
    *SCENARIO_KEY_OUTCOMES,
    "challenge",
    "workflow",
    "artifact_input",
}
KERNEL_COST_FILES = (
    "kernel-cost-config.json",
    "kernel-cost-report.json",
    "kernel-cost-fragment.json",
    "kernel-build/environment.json",
    "kernel-build/kernel-build-config.json",
    "kernel-build/kernel-build.json",
    "kernel-build/raw/kernel-build.log",
)
KERNEL_COST_METRICS = (
    ("elf_file_bytes", "ELF 文件"),
    ("text_bytes", ".text"),
    ("data_bytes", ".data"),
    ("bss_bytes", ".bss"),
)
KERNEL_COST_GUARDRAILS = (
    ("struct_proc_bytes", "struct proc"),
    ("user_stack_call_path_bytes", "最坏用户调用路径栈"),
)
SCENARIO_FIELDS = {
    "id",
    "label",
    "task",
    "functional_status",
    "performance_status",
    "performance",
    "evidence_ids",
}
SCENARIO_PERFORMANCE_FIELDS = {
    "direction",
    "lower_is_better",
    "unit",
    "paired_success_rate",
    "inference",
    "interpretation",
    "claim_gate",
    "n",
    "median",
    "ci_low",
    "ci_high",
    "relative_median_percent",
    "relative_ci_low",
    "relative_ci_high",
    "sign_test",
    "mcid_sign_test",
    "regression_mcid_sign_test",
    "bootstrap",
    "samples",
}

STATUS_ZH = {
    "measured": "已测量",
    "pass": "功能数据完整",
    "passed": "资源观测完整",
    "ready": "就绪",
    "partial": "部分观测",
    "supported": "证据支持",
    "regressed": "显著回退",
    "not_supported": "暂不支持",
    "inconclusive": "证据不足",
    "unverified": "未核验",
    "verified": "已核验",
    "unavailable": "unavailable",
    "failed": "失败",
    "fail": "失败",
    "invalid": "无效",
    "publishable": "可发布",
    "incomplete": "未完整",
    "not_ready": "未就绪",
}

CSV_FIELDS = (
    "benchmark_id",
    "benchmark_label",
    "task",
    "status",
    "target_id",
    "target_label",
    "load",
    "estimate",
    "lower",
    "upper",
    "unit",
    "n",
    "cache_policy",
    "evidence_ids",
    "sources",
)


class DashboardError(RuntimeError):
    """Raised when an evaluation summary cannot support a dashboard."""


def _fail(message: str) -> None:
    raise DashboardError(message)


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{path} must be an array")
    return value


def _require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(f"{path} must be a non-empty string")
    return value


def _require_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        _fail(f"{path} must be finite")
    return result


def _ids(items: list[Any], path: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(items):
        item = _require_object(raw, f"{path}[{index}]")
        item_id = _require_string(item.get("id"), f"{path}[{index}].id")
        if item_id in result:
            _fail(f"duplicate {path} id {item_id!r}")
        result[item_id] = item
    return result


def _references(raw: Any, path: str, known: set[str], *, required: bool) -> list[str]:
    values = _require_list(raw, path)
    if required and not values:
        _fail(f"{path} must bind at least one evidence item")
    result: list[str] = []
    for index, value in enumerate(values):
        evidence_id = _require_string(value, f"{path}[{index}]")
        if evidence_id not in known:
            _fail(f"{path}[{index}] references unknown evidence {evidence_id!r}")
        if evidence_id in result:
            _fail(f"{path} repeats evidence {evidence_id!r}")
        result.append(evidence_id)
    return result


def _task_id(value: Any, path: str) -> str:
    text = _require_string(value, path).lower().replace("_", "")
    aliases = {str(index): f"task{index}" for index in range(1, 7)}
    aliases.update({f"task{index}": f"task{index}" for index in range(1, 7)})
    if text not in aliases:
        _fail(f"{path} must identify task1 through task6")
    return aliases[text]


def _sample_key(item: dict[str, Any], path: str, targets: set[str], loads: set[str]) -> tuple[str, str]:
    target_id = _require_string(item.get("target_id"), f"{path}.target_id")
    if target_id not in targets:
        _fail(f"{path}.target_id references unknown target {target_id!r}")
    load = str(item.get("load", ""))
    if load not in loads:
        _fail(f"{path}.load references unknown load {load!r}")
    return target_id, load


def _nonnegative_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{path} must be a non-negative integer")
    return value


def _bootstrap_interval(values: list[float], seed_text: str) -> tuple[float, float]:
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    estimates = [
        float(statistics.median(values[rng.randrange(len(values))] for _ in values))
        for _ in range(BOOTSTRAP_REPETITIONS)
    ]
    estimates.sort()
    return (
        estimates[math.floor(0.025 * (len(estimates) - 1))],
        estimates[math.ceil(0.975 * (len(estimates) - 1))],
    )


def _validate_sign_test(
    raw: Any,
    path: str,
    pair_n: int,
    improvements: list[float],
    *,
    alternative: str = "treatment_better",
) -> float:
    value = _require_object(raw, path)
    required = {"alternative", "wins", "losses", "ties", "n", "p_value", "numerator", "denominator"}
    missing = required - set(value)
    if missing:
        _fail(f"{path} missing required fields {sorted(missing)}")
    if value["alternative"] != alternative:
        _fail(f"{path}.alternative must be {alternative!r}")
    wins = _nonnegative_int(value["wins"], f"{path}.wins")
    losses = _nonnegative_int(value["losses"], f"{path}.losses")
    ties = _nonnegative_int(value["ties"], f"{path}.ties")
    n = _nonnegative_int(value["n"], f"{path}.n")
    numerator = _nonnegative_int(value["numerator"], f"{path}.numerator")
    denominator = _nonnegative_int(value["denominator"], f"{path}.denominator")
    if n != wins + losses or pair_n != n + ties:
        _fail(f"{path} counts do not match paired n")
    expected_counts = (
        sum(value > 0 for value in improvements),
        sum(value < 0 for value in improvements),
        sum(value == 0 for value in improvements),
    )
    if (wins, losses, ties) != expected_counts:
        _fail(f"{path} counts do not match paired samples")
    if denominator == 0:
        _fail(f"{path}.denominator must be positive")
    expected = Fraction(sum(math.comb(n, count) for count in range(wins, n + 1)), 1 << n)
    if (numerator, denominator) != (expected.numerator, expected.denominator):
        _fail(f"{path} exact fraction does not match wins/losses")
    p_value = _require_number(value["p_value"], f"{path}.p_value")
    if not 0 <= p_value <= 1 or not math.isclose(p_value, float(expected), rel_tol=0, abs_tol=1e-15):
        _fail(f"{path}.p_value does not match exact sign test")
    return p_value


def _validate_mcid_sign_test(
    raw: Any,
    path: str,
    pair_n: int,
    improvements: list[float],
    relative_improvements: list[float | None],
    claim_gate: dict[str, float],
) -> float:
    value = _require_object(raw, path)
    required = {
        "alternative",
        "absolute_mcid_us",
        "relative_mcid_percent",
        "success_rule",
        "non_win_policy",
        "wins",
        "non_wins",
        "n",
        "p_value",
        "numerator",
        "denominator",
    }
    if set(value) != required:
        _fail(f"{path} fields do not match the joint-MCID sign-test schema")
    if value["alternative"] != "joint_absolute_and_relative_mcid_exceeded":
        _fail(f"{path}.alternative is not the registered joint-MCID hypothesis")
    if value["success_rule"] != "both_strictly_greater_per_boot":
        _fail(f"{path}.success_rule must require both MCIDs per boot")
    if value["non_win_policy"] != "ties_missing_or_not_exceeding_either_mcid":
        _fail(f"{path}.non_win_policy differs from the preregistered rule")
    absolute_mcid = _require_number(value["absolute_mcid_us"], f"{path}.absolute_mcid_us")
    relative_mcid = _require_number(
        value["relative_mcid_percent"], f"{path}.relative_mcid_percent"
    )
    if not math.isclose(
        absolute_mcid,
        claim_gate["minimum_absolute_improvement_us"],
        rel_tol=0,
        abs_tol=1e-15,
    ) or not math.isclose(
        relative_mcid,
        claim_gate["minimum_relative_improvement_percent"],
        rel_tol=0,
        abs_tol=1e-15,
    ):
        _fail(f"{path} MCIDs differ from the benchmark claim gate")
    wins = _nonnegative_int(value["wins"], f"{path}.wins")
    non_wins = _nonnegative_int(value["non_wins"], f"{path}.non_wins")
    n = _nonnegative_int(value["n"], f"{path}.n")
    if n != pair_n or wins + non_wins != n:
        _fail(f"{path} counts do not match paired n")
    expected_wins = sum(
        absolute > absolute_mcid
        and relative is not None
        and relative > relative_mcid
        for absolute, relative in zip(improvements, relative_improvements)
    )
    if wins != expected_wins or non_wins != n - expected_wins:
        _fail(f"{path} counts do not match paired joint-MCID samples")
    expected = Fraction(
        sum(math.comb(n, count) for count in range(wins, n + 1)), 1 << n
    )
    numerator = _nonnegative_int(value["numerator"], f"{path}.numerator")
    denominator = _nonnegative_int(value["denominator"], f"{path}.denominator")
    if denominator == 0 or (numerator, denominator) != (
        expected.numerator,
        expected.denominator,
    ):
        _fail(f"{path} exact fraction does not match joint-MCID wins")
    p_value = _require_number(value["p_value"], f"{path}.p_value")
    if not 0 <= p_value <= 1 or not math.isclose(
        p_value, float(expected), rel_tol=0, abs_tol=1e-15
    ):
        _fail(f"{path}.p_value does not match the exact joint-MCID sign test")
    return p_value


def _validate_paired(
    raw: Any,
    path: str,
    benchmark_status: str,
    loads: list[str],
    estimate_n: dict[tuple[str, str], int],
    sample_values: dict[tuple[str, str], dict[str, float]],
    baseline: str,
    treatment: str,
    direction: str,
    seed_prefix: str,
    claim_gate: dict[str, float] | None,
    headline_alpha: float,
) -> bool:
    items = _require_list(raw, path)
    pairs: dict[str, dict[str, Any]] = {}
    gates: list[bool] = []
    for index, raw_pair in enumerate(items):
        pair_path = f"{path}[{index}]"
        pair = _require_object(raw_pair, pair_path)
        missing = PAIRED_FIELDS - set(pair)
        if missing:
            _fail(f"{pair_path} missing required fields {sorted(missing)}")
        load = str(pair.get("load", ""))
        if load not in loads or load in pairs:
            _fail(f"{pair_path}.load must name one unique benchmark load")
        pairs[load] = pair
        status = _require_string(pair["status"], f"{pair_path}.status")
        if status != benchmark_status:
            _fail(f"{pair_path}.status must match benchmark status")
        n = _nonnegative_int(pair["n"], f"{pair_path}.n")
        if benchmark_status != "measured":
            if pair["samples"] != []:
                _fail(f"{pair_path}.samples must be empty when no measurement is available")
            nullable = PAIRED_FIELDS - {"load", "status", "n", "samples"}
            if n != 0 or any(pair[field] is not None for field in nullable):
                _fail(f"{pair_path} unavailable/failed statistics must be null with n=0")
            gates.append(False)
            continue
        if n <= 0:
            _fail(f"{pair_path}.n must be positive for a measured pair")
        for target_id in (baseline, treatment):
            key = (target_id, load)
            if estimate_n.get(key) != n or len(sample_values.get(key, {})) != n:
                _fail(f"{pair_path}.n does not match estimates and independent samples")
        baseline_samples = sample_values[(baseline, load)]
        treatment_samples = sample_values[(treatment, load)]
        if set(baseline_samples) != set(treatment_samples):
            _fail(f"{pair_path} baseline/treatment trials are not paired")
        paired_samples = _require_list(pair["samples"], f"{pair_path}.samples")
        paired_by_trial: dict[str, tuple[float, float | None]] = {}
        for sample_index, raw_sample in enumerate(paired_samples):
            sample_path = f"{pair_path}.samples[{sample_index}]"
            sample = _require_object(raw_sample, sample_path)
            expected_sample_fields = {
                "trial",
                "baseline_value",
                "treatment_value",
                "value",
                "relative_percent",
                "inner_pairs",
            }
            if set(sample) != expected_sample_fields:
                _fail(
                    f"{sample_path} must carry raw inner-pair baseline/treatment values; "
                    f"expected {sorted(expected_sample_fields)}"
                )
            trial_raw = sample["trial"]
            if isinstance(trial_raw, bool) or not isinstance(trial_raw, (str, int)) or str(trial_raw) == "":
                _fail(f"{sample_path}.trial must be a non-empty string or integer")
            trial = str(trial_raw)
            if trial in paired_by_trial:
                _fail(f"{sample_path} duplicates a paired trial")
            if trial not in baseline_samples or trial not in treatment_samples:
                _fail(f"{sample_path}.trial has no independent target observations")
            boot_baseline = _require_number(sample["baseline_value"], f"{sample_path}.baseline_value")
            boot_treatment = _require_number(sample["treatment_value"], f"{sample_path}.treatment_value")
            if not math.isclose(
                boot_baseline, baseline_samples[trial], rel_tol=1e-12, abs_tol=1e-12
            ) or not math.isclose(
                boot_treatment, treatment_samples[trial], rel_tol=1e-12, abs_tol=1e-12
            ):
                _fail(f"{sample_path} boot medians do not match independent target observations")
            inner_pairs = _require_list(sample["inner_pairs"], f"{sample_path}.inner_pairs")
            required_inner_pairs = MINIMUM_INNER_PAIRS if claim_gate is not None else 1
            if len(inner_pairs) < required_inner_pairs:
                _fail(
                    f"{sample_path}.inner_pairs must contain at least {required_inner_pairs} pairs"
                )
            inner_values: list[float] = []
            inner_relative: list[float] = []
            relative_available = True
            seen_inner_pairs: set[str] = set()
            for inner_index, raw_inner in enumerate(inner_pairs):
                inner_path = f"{sample_path}.inner_pairs[{inner_index}]"
                inner = _require_object(raw_inner, inner_path)
                expected_inner_fields = {
                    "pair", "baseline_value", "treatment_value", "value", "relative_percent",
                }
                if set(inner) != expected_inner_fields:
                    _fail(f"{inner_path} fields must be {sorted(expected_inner_fields)}")
                pair_raw = inner["pair"]
                if isinstance(pair_raw, bool) or not isinstance(pair_raw, (str, int)) or str(pair_raw) == "":
                    _fail(f"{inner_path}.pair must be a non-empty string or integer")
                pair_id = str(pair_raw)
                if pair_id in seen_inner_pairs:
                    _fail(f"{inner_path} duplicates an inner pair")
                seen_inner_pairs.add(pair_id)
                baseline_value = _require_number(inner["baseline_value"], f"{inner_path}.baseline_value")
                treatment_value = _require_number(inner["treatment_value"], f"{inner_path}.treatment_value")
                expected_inner_value = (
                    baseline_value - treatment_value
                    if direction == "lower_is_better"
                    else treatment_value - baseline_value
                    if direction == "higher_is_better"
                    else 0.0
                )
                inner_value = _require_number(inner["value"], f"{inner_path}.value")
                if not math.isclose(inner_value, expected_inner_value, rel_tol=1e-12, abs_tol=1e-12):
                    _fail(f"{inner_path}.value does not match raw values and benchmark direction")
                expected_inner_relative = (
                    expected_inner_value / abs(baseline_value) * 100.0
                    if baseline_value != 0 and direction != "neutral"
                    else None
                )
                inner_relative_value = inner["relative_percent"]
                if inner_relative_value is not None:
                    inner_relative_value = _require_number(
                        inner_relative_value, f"{inner_path}.relative_percent"
                    )
                if (inner_relative_value is None) != (expected_inner_relative is None) or (
                    inner_relative_value is not None
                    and not math.isclose(
                        inner_relative_value, expected_inner_relative, rel_tol=1e-12, abs_tol=1e-12
                    )
                ):
                    _fail(f"{inner_path}.relative_percent does not match raw baseline value")
                inner_values.append(expected_inner_value)
                if expected_inner_relative is None:
                    relative_available = False
                else:
                    inner_relative.append(expected_inner_relative)
            if not math.isclose(
                boot_baseline,
                float(statistics.median(
                    _require_number(item["baseline_value"], f"{sample_path}.inner_pairs[].baseline_value")
                    for item in inner_pairs
                )),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ) or not math.isclose(
                boot_treatment,
                float(statistics.median(
                    _require_number(item["treatment_value"], f"{sample_path}.inner_pairs[].treatment_value")
                    for item in inner_pairs
                )),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                _fail(f"{sample_path} boot medians do not match raw inner pairs")
            expected_value = float(statistics.median(inner_values))
            value = _require_number(sample["value"], f"{sample_path}.value")
            if not math.isclose(value, expected_value, rel_tol=1e-12, abs_tol=1e-12):
                _fail(f"{sample_path}.value does not match the inner-pair median")
            relative = sample["relative_percent"]
            expected_relative = (
                float(statistics.median(inner_relative))
                if relative_available
                else None
            )
            if relative is not None:
                relative = _require_number(relative, f"{sample_path}.relative_percent")
            if (relative is None) != (expected_relative is None) or (
                relative is not None
                and not math.isclose(relative, expected_relative, rel_tol=1e-12, abs_tol=1e-12)
            ):
                _fail(f"{sample_path}.relative_percent does not match the inner-pair median")
            paired_by_trial[trial] = (value, relative)
        if len(paired_by_trial) != n or set(paired_by_trial) != set(baseline_samples):
            _fail(f"{pair_path}.samples do not match independent target trials")
        improvements = [paired_by_trial[trial][0] for trial in sorted(paired_by_trial)]
        relative_samples_for_mcid = [
            paired_by_trial[trial][1] for trial in sorted(paired_by_trial)
        ]
        median = _require_number(pair["median"], f"{pair_path}.median")
        p95 = _require_number(pair["p95"], f"{pair_path}.p95")
        ci_low = _require_number(pair["ci_low"], f"{pair_path}.ci_low")
        ci_high = _require_number(pair["ci_high"], f"{pair_path}.ci_high")
        expected_median = float(statistics.median(improvements))
        ordered = sorted(improvements)
        expected_p95 = float(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])
        tolerance = 1e-12 * max(1.0, *(abs(value) for value in improvements))
        if not math.isclose(median, expected_median, rel_tol=1e-12, abs_tol=1e-12):
            _fail(f"{pair_path}.median does not match paired samples")
        if not math.isclose(p95, expected_p95, rel_tol=1e-12, abs_tol=1e-12):
            _fail(f"{pair_path}.p95 does not match paired samples")
        expected_low, expected_high = _bootstrap_interval(improvements, f"{seed_prefix}:{load}:paired")
        if not math.isclose(ci_low, expected_low, rel_tol=1e-12, abs_tol=tolerance) or not math.isclose(
            ci_high, expected_high, rel_tol=1e-12, abs_tol=tolerance
        ):
            _fail(f"{pair_path} absolute bootstrap interval does not match paired samples")
        relative = pair["relative_median_percent"]
        relative_low = pair["relative_ci_low"]
        relative_high = pair["relative_ci_high"]
        if (relative is None) != (relative_low is None) or (relative is None) != (relative_high is None):
            _fail(f"{pair_path} relative statistics must be all null or all numeric")
        if relative is not None:
            relative = _require_number(relative, f"{pair_path}.relative_median_percent")
            relative_low = _require_number(relative_low, f"{pair_path}.relative_ci_low")
            relative_high = _require_number(relative_high, f"{pair_path}.relative_ci_high")
            if any(value is None for value in relative_samples_for_mcid):
                _fail(f"{pair_path} numeric relative summary requires every paired relative sample")
            relative_samples = [float(value) for value in relative_samples_for_mcid]
            expected_relative = float(statistics.median(relative_samples))
            if not math.isclose(relative, expected_relative, rel_tol=1e-12, abs_tol=1e-12):
                _fail(f"{pair_path}.relative_median_percent does not match paired samples")
            relative_tolerance = 1e-12 * max(1.0, *(abs(value) for value in relative_samples))
            expected_relative_low, expected_relative_high = _bootstrap_interval(
                relative_samples, f"{seed_prefix}:{load}:paired-relative"
            )
            if not math.isclose(
                relative_low, expected_relative_low, rel_tol=1e-12, abs_tol=relative_tolerance
            ) or not math.isclose(
                relative_high, expected_relative_high, rel_tol=1e-12, abs_tol=relative_tolerance
            ):
                _fail(f"{pair_path} relative bootstrap interval does not match paired samples")
        elif any(value[1] is not None for value in paired_by_trial.values()):
            _fail(f"{pair_path} relative summary is null but paired relative samples are present")
        _validate_sign_test(pair["sign_test"], f"{pair_path}.sign_test", n, improvements)
        if claim_gate is None:
            if pair["mcid_sign_test"] is not None:
                _fail(f"{pair_path}.mcid_sign_test requires a benchmark claim gate")
            mcid_p_value = None
        else:
            mcid_p_value = _validate_mcid_sign_test(
                pair["mcid_sign_test"],
                f"{pair_path}.mcid_sign_test",
                n,
                improvements,
                relative_samples_for_mcid,
                claim_gate,
            )
        gates.append(
            claim_gate is not None
            and direction != "neutral"
            and n >= MINIMUM_BENCHMARK_BOOTS
            and min(baseline_samples.values())
            >= claim_gate["minimum_baseline_duration_us"]
            and mcid_p_value is not None
            and mcid_p_value <= headline_alpha
        )
    if set(pairs) != set(loads):
        _fail(f"{path} must contain exactly one record for every benchmark load")
    return bool(gates) and all(gates)


def _validate_claim_gate(raw: Any, path: str) -> dict[str, float] | None:
    if raw is None:
        return None
    value = _require_object(raw, path)
    expected = {
        "minimum_absolute_improvement_us",
        "minimum_baseline_duration_us",
        "minimum_relative_improvement_percent",
    }
    if set(value) != expected:
        _fail(f"{path} fields do not match the claim-gate schema")
    result = {
        field: _require_number(value[field], f"{path}.{field}")
        for field in expected
    }
    if any(number <= 0 for number in result.values()):
        _fail(f"{path} thresholds must be positive")
    if result["minimum_relative_improvement_percent"] > 100:
        _fail(f"{path}.minimum_relative_improvement_percent must be <= 100")
    if any(result[field] < floor for field, floor in BENCHMARK_GATE_FLOORS.items()):
        _fail(f"{path} weakens the dashboard's registered MCID/timing floors")
    return result


def _validate_multiple_testing(
    methodology: dict[str, Any],
    claim_benchmark_ids: set[str],
) -> float:
    if not claim_benchmark_ids:
        return 0.05
    value = _require_object(
        methodology.get("multiple_testing"), "methodology.multiple_testing"
    )
    expected = {
        "family_id", "method", "familywise_alpha", "hypothesis_count",
        "per_claim_alpha", "headline_claims", "load_gate",
    }
    if set(value) != expected:
        _fail("methodology.multiple_testing fields do not match the registered claim family")
    _require_string(value["family_id"], "methodology.multiple_testing.family_id")
    if value["method"] != "Bonferroni":
        _fail("methodology.multiple_testing.method must be Bonferroni")
    familywise_alpha = _require_number(
        value["familywise_alpha"], "methodology.multiple_testing.familywise_alpha"
    )
    if not math.isclose(familywise_alpha, 0.05, rel_tol=0, abs_tol=1e-15):
        _fail("methodology.multiple_testing.familywise_alpha must be 0.05")
    hypothesis_count = value["hypothesis_count"]
    if (
        isinstance(hypothesis_count, bool)
        or not isinstance(hypothesis_count, int)
        or hypothesis_count <= 0
    ):
        _fail("methodology.multiple_testing.hypothesis_count must be positive")
    headlines = _require_list(
        value["headline_claims"], "methodology.multiple_testing.headline_claims"
    )
    if (
        len(headlines) != hypothesis_count
        or len(set(headlines)) != len(headlines)
        or any(not isinstance(item, str) or not item for item in headlines)
    ):
        _fail("methodology.multiple_testing.headline_claims must match hypothesis_count")
    if not claim_benchmark_ids.issubset(set(headlines)):
        _fail("every benchmark claim must belong to the registered headline family")
    if value["load_gate"] != "intersection (every preregistered load must pass)":
        _fail("methodology.multiple_testing.load_gate must require every load")
    per_claim_alpha = _require_number(
        value["per_claim_alpha"], "methodology.multiple_testing.per_claim_alpha"
    )
    expected_alpha = familywise_alpha / hypothesis_count
    if not math.isclose(per_claim_alpha, expected_alpha, rel_tol=0, abs_tol=1e-15):
        _fail("methodology.multiple_testing.per_claim_alpha is not Bonferroni-corrected")
    return per_claim_alpha


def _validate_inference_methodology(
    methodology: dict[str, Any], per_claim_alpha: float, headline_count: int
) -> None:
    if methodology.get("inference_method") != INFERENCE_METHOD:
        _fail("methodology.inference_method must identify exact joint-MCID inference")
    expected_interval = {
        "method": "percentile bootstrap of the boot-level median",
        "resamples": BOOTSTRAP_REPETITIONS,
        "role": DESCRIPTIVE_INTERVAL_ROLE,
    }
    if methodology.get("descriptive_interval") != expected_interval:
        _fail("methodology.descriptive_interval must be explicitly descriptive-only")
    expected_interval_method = (
        f"descriptive deterministic percentile bootstrap ({BOOTSTRAP_REPETITIONS} resamples)"
    )
    if methodology.get("interval_method") != expected_interval_method:
        _fail("methodology.interval_method must label bootstrap intervals as descriptive")
    fwer = _require_object(methodology.get("fwer_mcid"), "methodology.fwer_mcid")
    expected_fields = {
        "familywise_alpha",
        "headline_count",
        "per_headline_alpha",
        "correction",
        "per_boot_success",
        "non_win_policy",
        "load_gate",
    }
    if set(fwer) != expected_fields:
        _fail("methodology.fwer_mcid fields differ from the registered inference contract")
    if (
        not math.isclose(
            _require_number(fwer["familywise_alpha"], "methodology.fwer_mcid.familywise_alpha"),
            0.05,
            rel_tol=0,
            abs_tol=1e-15,
        )
        or fwer["headline_count"] != headline_count
        or not math.isclose(
            _require_number(
                fwer["per_headline_alpha"],
                "methodology.fwer_mcid.per_headline_alpha",
            ),
            per_claim_alpha,
            rel_tol=0,
            abs_tol=1e-15,
        )
        or fwer["correction"] != "Bonferroni across headline claims"
        or fwer["per_boot_success"] != "absolute > MCID and relative > MCID"
        or fwer["non_win_policy"]
        != "ties, missing relative values, and either non-exceedance"
        or fwer["load_gate"] != "intersection; every preregistered load must pass"
    ):
        _fail("methodology.fwer_mcid differs from exact joint-MCID+Bonferroni inference")
    if methodology.get("interpretation_boundaries") != INTERPRETATION_BOUNDARIES:
        _fail(
            "methodology.interpretation_boundaries must state the fixed causal limits"
        )


def _binding_sha256(value: Any, domain: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _validate_scenario_mcid_sign_test(
    raw: Any,
    path: str,
    pair_n: int,
    improvements: list[float],
    relative_improvements: list[float | None],
    claim_gate: dict[str, float],
    *,
    regression: bool = False,
) -> Fraction:
    value = _require_object(raw, path)
    count_fields = (
        {"losses", "non_losses", "non_loss_policy"}
        if regression
        else {"wins", "non_wins", "non_win_policy"}
    )
    required = {
        "alternative",
        "absolute_mcid_ms",
        "relative_mcid_percent",
        "success_rule",
        "n",
        "p_value",
        "numerator",
        "denominator",
    } | count_fields
    if set(value) != required:
        _fail(f"{path} fields do not match the scenario joint-MCID schema")
    expected_alternative = (
        "joint_absolute_and_relative_regression_mcid_exceeded"
        if regression
        else "joint_absolute_and_relative_mcid_exceeded"
    )
    expected_success_rule = (
        "both_strictly_less_than_negative_thresholds_per_boot"
        if regression
        else "both_strictly_greater_per_boot"
    )
    if value["alternative"] != expected_alternative:
        _fail(f"{path}.alternative is not the registered directional joint-MCID hypothesis")
    if value["success_rule"] != expected_success_rule:
        _fail(f"{path}.success_rule does not match its registered direction")
    policy_field = "non_loss_policy" if regression else "non_win_policy"
    expected_policy = (
        "ties_missing_or_not_exceeding_either_reverse_mcid"
        if regression
        else "ties_missing_or_not_exceeding_either_mcid"
    )
    if value[policy_field] != expected_policy:
        _fail(f"{path}.{policy_field} differs from the preregistered rule")
    absolute_mcid = _require_number(value["absolute_mcid_ms"], f"{path}.absolute_mcid_ms")
    relative_mcid = _require_number(
        value["relative_mcid_percent"], f"{path}.relative_mcid_percent"
    )
    if not math.isclose(
        absolute_mcid,
        claim_gate["minimum_absolute_improvement_ms"],
        rel_tol=0,
        abs_tol=1e-15,
    ) or not math.isclose(
        relative_mcid,
        claim_gate["minimum_relative_improvement_percent"],
        rel_tol=0,
        abs_tol=1e-15,
    ):
        _fail(f"{path} MCIDs differ from the scenario claim gate")
    successes_field = "losses" if regression else "wins"
    non_successes_field = "non_losses" if regression else "non_wins"
    successes = _nonnegative_int(value[successes_field], f"{path}.{successes_field}")
    non_successes = _nonnegative_int(
        value[non_successes_field], f"{path}.{non_successes_field}"
    )
    n = _nonnegative_int(value["n"], f"{path}.n")
    if n != pair_n or successes + non_successes != n:
        _fail(f"{path} counts do not match the full paired n")
    expected_successes = sum(
        (
            absolute < -absolute_mcid
            and relative is not None
            and relative < -relative_mcid
        )
        if regression
        else (
            absolute > absolute_mcid
            and relative is not None
            and relative > relative_mcid
        )
        for absolute, relative in zip(improvements, relative_improvements)
    )
    if successes != expected_successes or non_successes != n - expected_successes:
        _fail(f"{path} counts do not match paired joint-MCID samples")
    expected = Fraction(
        sum(math.comb(n, count) for count in range(successes, n + 1)),
        1 << n,
    )
    numerator = _nonnegative_int(value["numerator"], f"{path}.numerator")
    denominator = _nonnegative_int(value["denominator"], f"{path}.denominator")
    if denominator == 0 or (numerator, denominator) != (
        expected.numerator,
        expected.denominator,
    ):
        _fail(f"{path} exact fraction does not match joint-MCID wins")
    p_value = _require_number(value["p_value"], f"{path}.p_value")
    if not 0 <= p_value <= 1 or not math.isclose(
        p_value, float(expected), rel_tol=0, abs_tol=1e-15
    ):
        _fail(f"{path}.p_value does not match the exact joint-MCID test")
    return expected


def _validate_scenario_performance(
    raw: Any,
    path: str,
    functional_status: str,
) -> str:
    value = _require_object(raw, path)
    if set(value) != SCENARIO_PERFORMANCE_FIELDS:
        _fail(
            f"{path} fields mismatch: missing={sorted(SCENARIO_PERFORMANCE_FIELDS - set(value))} "
            f"extra={sorted(set(value) - SCENARIO_PERFORMANCE_FIELDS)}"
        )
    if value["direction"] != "plain_minus_agentos_positive_is_better":
        _fail(f"{path}.direction is not the registered scenario direction")
    if value["lower_is_better"] is not True or value["unit"] != "ms":
        _fail(f"{path} must measure lower-is-better makespan in ms")
    success_rate = _require_number(
        value["paired_success_rate"], f"{path}.paired_success_rate"
    )
    if not 0 <= success_rate <= 1:
        _fail(f"{path}.paired_success_rate must be in [0, 1]")
    inference = _require_object(value["inference"], f"{path}.inference")
    if set(inference) != set(SCENARIO_INFERENCE):
        _fail(f"{path}.inference fields do not match the Task6 inference schema")
    for field in (
        "method", "success_unit", "sample_policy", "multiplicity", "correction"
    ):
        if inference[field] != SCENARIO_INFERENCE[field]:
            _fail(f"{path}.inference.{field} differs from the registered Task6 test")
    if not math.isclose(
        _require_number(inference["alpha"], f"{path}.inference.alpha"),
        SCENARIO_ALPHA,
        rel_tol=0,
        abs_tol=1e-15,
    ):
        _fail(f"{path}.inference.alpha must be the Task6 directional-family alpha 0.05")
    if (
        _nonnegative_int(
            inference["directional_hypothesis_count"],
            f"{path}.inference.directional_hypothesis_count",
        )
        != 2
        or not math.isclose(
            _require_number(
                inference["per_direction_alpha"],
                f"{path}.inference.per_direction_alpha",
            ),
            SCENARIO_DIRECTIONAL_ALPHA,
            rel_tol=0,
            abs_tol=1e-15,
        )
    ):
        _fail(f"{path}.inference must Bonferroni-correct the two Task6 directions")
    interpretation = _require_object(
        value["interpretation"], f"{path}.interpretation"
    )
    if interpretation != SCENARIO_INTERPRETATION:
        _fail(
            f"{path}.interpretation must state full-stack, non-single-mechanism, "
            "and uncontrolled Host page cache"
        )
    gate = _require_object(value["claim_gate"], f"{path}.claim_gate")
    gate_fields = {
        "minimum_absolute_improvement_ms",
        "minimum_baseline_makespan_ms",
        "minimum_relative_improvement_percent",
    }
    if set(gate) != gate_fields:
        _fail(f"{path}.claim_gate fields do not match the scenario gate schema")
    thresholds = {
        field: _require_number(gate[field], f"{path}.claim_gate.{field}")
        for field in gate_fields
    }
    if any(number <= 0 for number in thresholds.values()):
        _fail(f"{path}.claim_gate thresholds must be positive")
    if any(thresholds[field] < floor for field, floor in SCENARIO_GATE_FLOORS.items()):
        _fail(f"{path}.claim_gate weakens the registered scenario MCID/timing floors")
    if any(
        not math.isclose(thresholds[field], expected, rel_tol=0, abs_tol=1e-15)
        for field, expected in SCENARIO_GATE_FLOORS.items()
    ):
        _fail(f"{path}.claim_gate differs from the fixed scenario MCID/timing floors")

    samples = _require_list(value["samples"], f"{path}.samples")
    n = _nonnegative_int(value["n"], f"{path}.n")
    if n != len(samples) or n == 0:
        _fail(f"{path}.n must match non-empty scenario samples")
    sample_fields = {
        "sample_id",
        "boot_id",
        "target_order",
        "plain_ms",
        "agentos_ms",
        "improvement_ms",
        "relative_improvement_percent",
    }
    sample_ids: set[str] = set()
    boot_ids: set[str] = set()
    improvements: list[float] = []
    relatives: list[float | None] = []
    relative_available = True
    orders = {"AB": 0, "BA": 0}
    plain_values: list[float] = []
    for index, raw_sample in enumerate(samples):
        sample_path = f"{path}.samples[{index}]"
        sample = _require_object(raw_sample, sample_path)
        if set(sample) != sample_fields:
            _fail(f"{sample_path} fields do not match the paired scenario sample schema")
        sample_id = _require_string(sample["sample_id"], f"{sample_path}.sample_id")
        boot_id = _require_string(sample["boot_id"], f"{sample_path}.boot_id")
        if sample_id in sample_ids or boot_id in boot_ids:
            _fail(f"{sample_path} duplicates sample_id or boot_id")
        sample_ids.add(sample_id)
        boot_ids.add(boot_id)
        order = _require_string(sample["target_order"], f"{sample_path}.target_order")
        if order not in orders:
            _fail(f"{sample_path}.target_order must be AB or BA")
        orders[order] += 1
        plain = _require_number(sample["plain_ms"], f"{sample_path}.plain_ms")
        agentos = _require_number(sample["agentos_ms"], f"{sample_path}.agentos_ms")
        if plain < 0 or agentos < 0:
            _fail(f"{sample_path} makespans must be non-negative")
        expected_improvement = plain - agentos
        improvement = _require_number(sample["improvement_ms"], f"{sample_path}.improvement_ms")
        if not math.isclose(improvement, expected_improvement, rel_tol=1e-12, abs_tol=1e-12):
            _fail(f"{sample_path}.improvement_ms does not match raw makespans")
        expected_relative = expected_improvement * 100.0 / plain if plain > 0 else None
        relative = sample["relative_improvement_percent"]
        if relative is not None:
            relative = _require_number(relative, f"{sample_path}.relative_improvement_percent")
        if (relative is None) != (expected_relative is None) or (
            relative is not None
            and not math.isclose(relative, expected_relative, rel_tol=1e-12, abs_tol=1e-12)
        ):
            _fail(f"{sample_path}.relative_improvement_percent does not match plain_ms")
        improvements.append(expected_improvement)
        plain_values.append(plain)
        if expected_relative is None:
            relative_available = False
        relatives.append(expected_relative)

    expected_seed = _binding_sha256(samples, "scenario-paired-bootstrap-seed-v1")
    bootstrap = _require_object(value["bootstrap"], f"{path}.bootstrap")
    if set(bootstrap) != {
        "method", "confidence", "repetitions", "seed_sha256", "role"
    }:
        _fail(f"{path}.bootstrap fields do not match the scenario bootstrap schema")
    if (
        bootstrap["method"] != "deterministic_percentile_median"
        or _require_number(bootstrap["confidence"], f"{path}.bootstrap.confidence") != 0.95
        or _nonnegative_int(bootstrap["repetitions"], f"{path}.bootstrap.repetitions")
        != BOOTSTRAP_REPETITIONS
        or bootstrap["seed_sha256"] != expected_seed
        or bootstrap["role"] != "descriptive_only"
    ):
        _fail(
            f"{path}.bootstrap does not match the bound descriptive-only scenario interval"
        )

    median = _require_number(value["median"], f"{path}.median")
    ci_low = _require_number(value["ci_low"], f"{path}.ci_low")
    ci_high = _require_number(value["ci_high"], f"{path}.ci_high")
    expected_median = float(statistics.median(improvements))
    expected_low, expected_high = _bootstrap_interval(improvements, f"{expected_seed}:absolute")
    tolerance = 1e-12 * max(1.0, *(abs(item) for item in improvements))
    if not math.isclose(median, expected_median, rel_tol=1e-12, abs_tol=tolerance):
        _fail(f"{path}.median does not match raw scenario samples")
    if not math.isclose(ci_low, expected_low, rel_tol=1e-12, abs_tol=tolerance) or not math.isclose(
        ci_high, expected_high, rel_tol=1e-12, abs_tol=tolerance
    ):
        _fail(f"{path} bootstrap interval does not match raw scenario samples")

    relative = value["relative_median_percent"]
    relative_low = value["relative_ci_low"]
    relative_high = value["relative_ci_high"]
    if relative_available:
        available_relatives = [relative for relative in relatives if relative is not None]
        expected_relative = float(statistics.median(available_relatives))
        expected_relative_low, expected_relative_high = _bootstrap_interval(
            available_relatives, f"{expected_seed}:relative"
        )
        for field, supplied, expected in (
            ("relative_median_percent", relative, expected_relative),
            ("relative_ci_low", relative_low, expected_relative_low),
            ("relative_ci_high", relative_high, expected_relative_high),
        ):
            number = _require_number(supplied, f"{path}.{field}")
            if not math.isclose(number, expected, rel_tol=1e-12, abs_tol=1e-12):
                _fail(f"{path}.{field} does not match raw scenario samples")
    elif any(item is not None for item in (relative, relative_low, relative_high)):
        _fail(f"{path} relative scenario statistics must be null when a baseline is zero")
    _validate_sign_test(
        value["sign_test"],
        f"{path}.sign_test",
        n,
        improvements,
        alternative="agentos_lower_makespan",
    )
    forward_exact_p = _validate_scenario_mcid_sign_test(
        value["mcid_sign_test"],
        f"{path}.mcid_sign_test",
        n,
        improvements,
        relatives,
        thresholds,
    )
    reverse_exact_p = _validate_scenario_mcid_sign_test(
        value["regression_mcid_sign_test"],
        f"{path}.regression_mcid_sign_test",
        n,
        improvements,
        relatives,
        thresholds,
        regression=True,
    )
    eligible = (
        functional_status == "pass"
        and n >= MINIMUM_SCENARIO_BOOTS
        and math.isclose(success_rate, 1.0, rel_tol=0, abs_tol=1e-15)
        and abs(orders["AB"] - orders["BA"]) <= 1
        and min(plain_values) >= thresholds["minimum_baseline_makespan_ms"]
    )
    if not eligible:
        return "inconclusive"
    forward_significant = forward_exact_p <= Fraction(1, 40)
    reverse_significant = reverse_exact_p <= Fraction(1, 40)
    if forward_significant and reverse_significant:
        _fail(f"{path} cannot support forward and reverse joint-MCID conclusions together")
    if forward_significant:
        return "supported"
    if reverse_significant:
        return "regressed"
    return "inconclusive"


def _validate_diagnostic_stat(raw: Any, path: str, samples: list[float]) -> None:
    value = _require_object(raw, path)
    if set(value) != {"median", "p95", "n"}:
        _fail(f"{path} fields must be median/p95/n")
    n = value["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n != len(samples) or n <= 0:
        _fail(f"{path}.n must match diagnostic samples")
    median = _require_number(value["median"], f"{path}.median")
    p95 = _require_number(value["p95"], f"{path}.p95")
    expected_median = float(statistics.median(samples))
    ordered = sorted(samples)
    expected_p95 = float(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])
    if not math.isclose(median, expected_median, rel_tol=1e-12, abs_tol=1e-12):
        _fail(f"{path}.median does not match diagnostic samples")
    if not math.isclose(p95, expected_p95, rel_tol=1e-12, abs_tol=1e-12):
        _fail(f"{path}.p95 does not match diagnostic samples")


def _validate_diagnostics(
    raw: Any,
    path: str,
    benchmark_id: str,
    benchmark_status: str,
    loads: list[str],
    benchmark_evidence: list[str],
    evidence: dict[str, dict[str, Any]],
) -> None:
    diagnostics = _require_list(raw, path)
    seen_loads: set[str] = set()
    for index, raw_item in enumerate(diagnostics):
        item_path = f"{path}[{index}]"
        item = _require_object(raw_item, item_path)
        if set(item) != DIAGNOSTIC_FIELDS:
            _fail(
                f"{item_path} fields mismatch: missing={sorted(DIAGNOSTIC_FIELDS - set(item))} "
                f"extra={sorted(set(item) - DIAGNOSTIC_FIELDS)}"
            )
        load = str(item["load"])
        if load not in loads or load in seen_loads:
            _fail(f"{item_path}.load must name one unique benchmark load")
        seen_loads.add(load)
        status = _require_string(item["status"], f"{item_path}.status")
        if status != benchmark_status:
            _fail(f"{item_path}.status must match benchmark status")
        _require_string(item["unit"], f"{item_path}.unit")
        cache_states_raw = _require_list(item["cache_states"], f"{item_path}.cache_states")
        cache_states = [
            _require_string(value, f"{item_path}.cache_states[{state_index}]")
            for state_index, value in enumerate(cache_states_raw)
        ]
        samples_raw = _require_list(item["samples"], f"{item_path}.samples")
        if benchmark_status != "measured":
            if cache_states or samples_raw or any(
                item[field] is not None
                for field in (
                    "duration_us",
                    "work_units",
                    "index_rebuild_records",
                    "result_cache_hits",
                )
            ):
                _fail(f"{item_path} unavailable/failed diagnostic must not contain measurements")
            continue
        if not cache_states or len(cache_states) != len(set(cache_states)) or not samples_raw:
            _fail(f"{item_path} measured diagnostic requires cache states and samples")
        values_by_field: dict[str, list[float]] = {
            "duration_us": [],
            "work_units": [],
            "index_rebuild_records": [],
            "result_cache_hits": [],
        }
        observed_states: set[str] = set()
        boot_ids: set[str] = set()
        try:
            expected_dataset_size = int(load, 10)
        except ValueError:
            _fail(f"{item_path}.load must be a decimal dataset size for file-query diagnostics")
        for sample_index, raw_sample in enumerate(samples_raw):
            sample_path = f"{item_path}.samples[{sample_index}]"
            sample = _require_object(raw_sample, sample_path)
            if set(sample) != DIAGNOSTIC_SAMPLE_FIELDS:
                _fail(f"{sample_path} fields do not match diagnostic sample schema")
            boot_id = _require_string(sample["boot_id"], f"{sample_path}.boot_id")
            if boot_id in boot_ids:
                _fail(f"{sample_path} duplicates boot_id")
            boot_ids.add(boot_id)
            cache = _require_string(sample["cache"], f"{sample_path}.cache")
            if cache not in {"ready", "cold-rebuild"}:
                _fail(f"{sample_path}.cache has unknown readiness state")
            observed_states.add(cache)
            integers = {
                field: _nonnegative_int(sample[field], f"{sample_path}.{field}")
                for field in (
                    "operations",
                    "dataset_size",
                    "work_units",
                    "result_items",
                    "duration_us",
                    "index_rebuild_records",
                    "result_cache_hits",
                )
            }
            if integers["operations"] != 1:
                _fail(f"{sample_path}.operations must be 1 for the readiness diagnostic")
            if integers["dataset_size"] != expected_dataset_size:
                _fail(f"{sample_path}.dataset_size must equal its diagnostic load")
            if integers["result_items"] != integers["operations"]:
                _fail(f"{sample_path}.result_items must match completed diagnostic operations")
            if integers["result_cache_hits"] != 0:
                _fail(f"{sample_path}.result_cache_hits must be zero for readiness disclosure")
            for field in values_by_field:
                values_by_field[field].append(float(integers[field]))
            rebuild = integers["index_rebuild_records"]
            work_units = integers["work_units"]
            if (
                cache == "ready"
                and (
                    rebuild != 0
                    or work_units < integers["operations"]
                    or work_units > FILE_META_CAPACITY * integers["operations"]
                )
            ) or (
                cache == "cold-rebuild"
                and (rebuild <= 0 or work_units != rebuild)
            ):
                _fail(f"{sample_path} cache state conflicts with rebuild records")
            for field in ("workload_fingerprint", "result_fingerprint"):
                fingerprint = _require_string(sample[field], f"{sample_path}.{field}")
                if not FINGERPRINT16.fullmatch(fingerprint) or int(fingerprint, 16) == 0:
                    _fail(f"{sample_path}.{field} must be a non-zero lowercase 16-hex receipt")
            evidence_id = _require_string(sample["evidence_id"], f"{sample_path}.evidence_id")
            if evidence_id not in benchmark_evidence or evidence[evidence_id]["status"] != "verified":
                _fail(f"{sample_path} requires benchmark-bound verified evidence")
            source_log = _require_string(sample["source_log"], f"{sample_path}.source_log")
            _canonical_evidence_reference(source_log, f"{sample_path}.source_log")
            if evidence[evidence_id]["path"] != f"raw/{source_log}":
                _fail(f"{sample_path}.source_log is not bound to its evidence path")
            source_log_sha256 = _require_string(
                sample["source_log_sha256"], f"{sample_path}.source_log_sha256"
            )
            if (
                not SHA256.fullmatch(source_log_sha256)
                or source_log_sha256 != evidence[evidence_id]["sha256"]
            ):
                _fail(
                    f"{sample_path}.source_log_sha256 is not bound; "
                    "evidence sha256 does not match the diagnostic provenance"
                )
            source_marker_sha256 = _require_string(
                sample["source_marker_sha256"], f"{sample_path}.source_marker_sha256"
            )
            if not SHA256.fullmatch(source_marker_sha256):
                _fail(f"{sample_path}.source_marker_sha256 must be lowercase SHA-256")
            source_line = sample["source_line"]
            if isinstance(source_line, bool) or not isinstance(source_line, int) or source_line <= 0:
                _fail(f"{sample_path}.source_line must be a positive integer")
        if cache_states != sorted(observed_states):
            _fail(f"{item_path}.cache_states do not match diagnostic samples")
        for field, samples in values_by_field.items():
            _validate_diagnostic_stat(item[field], f"{item_path}.{field}", samples)
    if (
        benchmark_id in FILE_QUERY_EXPERIMENTS
        and benchmark_status == "measured"
        and not diagnostics
    ):
        _fail(
            f"{path} must cover every measured file-query load; "
            "empty diagnostics cannot pass"
        )
    if diagnostics and seen_loads != set(loads):
        _fail(f"{path} must cover every benchmark load when diagnostics are present")


def _validate_supplementary_evaluations(
    methodology: dict[str, Any], evidence: dict[str, dict[str, Any]],
) -> None:
    raw = methodology.get("supplementary_evaluations")
    evaluations = _require_list(raw, "methodology.supplementary_evaluations")
    if len(evaluations) != 1:
        _fail("evaluation summary must contain one supplementary evaluation")
    item = _require_object(evaluations[0], "methodology.supplementary_evaluations[0]")
    fields = {
        "id", "label", "task", "status", "performance_gate",
        "visit_sequence", "concurrency_levels", "rounds_per_level",
        "latency_unit", "throughput_unit", "percentile_method",
        "interpretation", "boots",
    }
    fields.update(QOS_REGISTRATION_FIELDS)
    if set(item) != fields:
        _fail("supplementary evaluation fields differ from schema")
    _require_string(item["id"], "supplementary.id")
    _require_string(item["label"], "supplementary.label")
    _task_id(item["task"], "supplementary.task")
    if item["status"] not in {"measured", "unavailable"}:
        _fail("supplementary status must be measured or unavailable")
    if item["performance_gate"] is not None:
        _fail("supplementary measurement cannot declare a performance gate")
    sequence = _require_list(item["visit_sequence"], "supplementary.visit_sequence")
    if (
        len(sequence) != 5 or sequence[0] != sequence[-1]
        or len(set(sequence[:-1])) != 4
        or any(type(identity) is not str or len(identity) != 1 for identity in sequence)
    ):
        _fail("supplementary visit sequence must be a four-identity revisit")
    levels = _require_list(item["concurrency_levels"], "supplementary.concurrency_levels")
    if (
        not levels or any(type(level) is not int or level < 1 for level in levels)
        or levels != sorted(set(levels))
    ):
        _fail("supplementary concurrency levels must be ordered unique integers")
    rounds = item["rounds_per_level"]
    if type(rounds) is not int or rounds < 1:
        _fail("supplementary rounds_per_level must be positive")
    for key in ("latency_unit", "throughput_unit", "percentile_method", "interpretation"):
        _require_string(item[key], f"supplementary.{key}")
    qos_schema = item["qos_schema_version"]
    if qos_schema != 2:
        _fail("supplementary QoS schema must be 2")
    for key in QOS_REGISTRATION_FIELDS:
        if key not in {"qos_schema_version", "latency_metrics"}:
            _require_string(item[key], f"supplementary.{key}")
    if item["latency_metrics"] != ["wait", "service", "turnaround"]:
        _fail("supplementary latency metrics differ from schema")
    boots = _require_list(item["boots"], "supplementary.boots")
    if (item["status"] == "measured") != bool(boots):
        _fail("supplementary status differs from its boot inventory")
    seen_boots: set[str] = set()
    for index, raw_boot in enumerate(boots):
        path = f"supplementary.boots[{index}]"
        boot = _require_object(raw_boot, path)
        boot_fields = {
            "boot_id", "evidence_id", "correct", "contamination",
            "return_visit", "fallback", "concurrency",
        }
        boot_fields.update({"qos_schema_version", "result_fingerprint"})
        if set(boot) != boot_fields:
            _fail(f"{path} fields differ from schema")
        if boot["qos_schema_version"] != qos_schema:
            _fail(f"{path} QoS schema differs from registration")
        if (
            type(boot["result_fingerprint"]) is not str
            or not re.fullmatch(r"[0-9a-f]{16}", boot["result_fingerprint"])
        ):
            _fail(f"{path}.result_fingerprint must be lowercase hex16")
        boot_id = _require_string(boot["boot_id"], f"{path}.boot_id")
        if boot_id in seen_boots:
            _fail("supplementary boot ids must be unique")
        seen_boots.add(boot_id)
        evidence_id = _require_string(boot["evidence_id"], f"{path}.evidence_id")
        if evidence_id not in evidence or evidence[evidence_id]["status"] != "verified":
            _fail(f"{path} must bind verified raw-log evidence")
        for key in ("correct", "contamination", "return_visit", "fallback"):
            if type(boot[key]) is not int or boot[key] < 0:
                _fail(f"{path}.{key} must be a non-negative integer")
        if boot["correct"] + boot["fallback"] != len(sequence):
            _fail(f"{path} visit totals differ from the registered sequence")
        concurrency = _require_list(boot["concurrency"], f"{path}.concurrency")
        if [entry.get("concurrency") for entry in concurrency if isinstance(entry, dict)] != levels:
            _fail(f"{path} concurrency levels differ from the registration")
        for entry_index, raw_entry in enumerate(concurrency):
            entry_path = f"{path}.concurrency[{entry_index}]"
            entry = _require_object(raw_entry, entry_path)
            expected_fields = set(CONCURRENCY_PUBLIC_FIELDS[qos_schema])
            if set(entry) != expected_fields:
                _fail(f"{entry_path} fields differ from schema")
            digest_fields = {"workload_digest", "result_fingerprint"}
            if any(
                type(entry[key]) is not int or entry[key] < 0
                for key in expected_fields - digest_fields
            ):
                _fail(f"{entry_path} values must be non-negative integers")
            if any(
                type(entry[key]) is not str
                or not re.fullmatch(r"[0-9a-f]{16}", entry[key])
                for key in expected_fields & digest_fields
            ):
                _fail(f"{entry_path} digests must be lowercase hex16")
            requests = rounds * entry["concurrency"]
            if (
                entry["rounds"] != rounds or entry["requests"] != requests
                or entry["completed"] != requests or entry["duration_us"] < 1
                or entry["correct"] + entry["fallback"] != requests
            ):
                _fail(f"{entry_path} aggregate counts differ from raw coverage")
            if (
                entry["goodput_milli_rps"] > entry["throughput_milli_rps"]
                or entry["isolated"] > entry["completed"]
                or entry["fairness_jain_ppm"] > 1_000_000
                or entry["max_min_fairness_ppm"] > 1_000_000
                or not entry["p50_us"] <= entry["p90_us"] <= entry["p99_us"]
                or not entry["wait_p50_us"] <= entry["wait_p90_us"] <= entry["wait_p99_us"]
                or not entry["service_p50_us"] <= entry["service_p90_us"] <= entry["service_p99_us"]
            ):
                _fail(f"{entry_path} QoS metrics are inconsistent")


def validate_summary(raw: Any) -> dict[str, Any]:
    """Validate the current summary and every evidence-bearing relation."""

    root = _require_object(raw, "summary")
    fields = set(root)
    if fields != TOP_LEVEL_FIELDS:
        _fail(
            "summary fields mismatch: "
            f"missing={sorted(TOP_LEVEL_FIELDS - fields)} extra={sorted(fields - TOP_LEVEL_FIELDS)}"
        )
    summary_schema = root["schema_version"]
    if type(summary_schema) is not int or summary_schema != EVALUATION_SCHEMA_VERSION:
        _fail(f"schema_version must be {EVALUATION_SCHEMA_VERSION}")
    if root["kind"] != "agentos-evaluation-summary":
        _fail("kind must be 'agentos-evaluation-summary'")

    run = _require_object(root["run"], "run")
    if run.get("suite_id") != EVALUATION_SUITE_ID:
        _fail("run.suite_id does not match the summary acceptance policy")
    _require_string(run.get("id"), "run.id")
    run_status = _require_string(run.get("status"), "run.status")
    if run_status not in RUN_STATUSES:
        _fail(f"unknown run status {run_status!r}")
    if "conclusion" in run:
        _require_string(run["conclusion"], "run.conclusion")
    if "evidence_grade" in run and run["evidence_grade"] != "E2-local-raw":
        _fail("run.evidence_grade must be the contract-derived 'E2-local-raw' grade")
    run_plan_sha256 = _require_string(run.get("run_plan_sha256"), "run.run_plan_sha256")
    if not SHA256.fullmatch(run_plan_sha256):
        _fail("run.run_plan_sha256 must be lowercase SHA-256")
    targets_list = _require_list(root["targets"], "targets")
    if len(targets_list) < 2:
        _fail("targets must contain at least two comparable targets")
    targets = _ids(targets_list, "targets")
    for index, target in enumerate(targets_list):
        _require_string(target.get("label"), f"targets[{index}].label")

    evidence_list = _require_list(root["evidence"], "evidence")
    evidence = _ids(evidence_list, "evidence")
    for index, item in enumerate(evidence_list):
        path = f"evidence[{index}]"
        missing = EVIDENCE_FIELDS - set(item)
        if missing:
            _fail(f"{path} missing required fields {sorted(missing)}")
        status = _require_string(item.get("status"), f"evidence[{index}].status")
        if status not in EVIDENCE_STATUSES:
            _fail(f"unknown evidence status {status!r}")
        _require_string(item["kind"], f"{path}.kind")
        _require_string(item["label"], f"{path}.label")
        _canonical_evidence_reference(item["path"], f"{path}.path")
        _require_string(item["source"], f"{path}.source")
        sha256 = _require_string(item["sha256"], f"{path}.sha256")
        if not SHA256.fullmatch(sha256):
            _fail(f"{path}.sha256 must be lowercase SHA-256")

    run_plan_evidence = [
        item for item in evidence_list if item.get("kind") == "evaluation-run-plan"
    ]
    if len(run_plan_evidence) != 1:
        _fail("summary must bind exactly one evaluation-run-plan evidence file")
    if (
        run_plan_evidence[0]["status"] != "verified"
        or run_plan_evidence[0]["sha256"] != run_plan_sha256
    ):
        _fail("run.run_plan_sha256 must match verified run-plan evidence")

    methodology = _require_object(root["methodology"], "methodology")
    _validate_supplementary_evaluations(methodology, evidence)
    raw_claims = _require_list(root["claims"], "claims")
    claim_benchmark_ids = {
        _require_string(claim.get("benchmark_id"), f"claims[{index}].benchmark_id")
        for index, claim in enumerate(raw_claims)
        if isinstance(claim, dict)
    }
    if len(claim_benchmark_ids) != len(raw_claims):
        _fail("every claim must be an object with a unique benchmark_id")
    headline_alpha = _validate_multiple_testing(methodology, claim_benchmark_ids)
    _validate_inference_methodology(
        methodology,
        headline_alpha,
        methodology["multiple_testing"]["hypothesis_count"],
    )

    benchmarks_list = _require_list(root["benchmarks"], "benchmarks")
    benchmarks = _ids(benchmarks_list, "benchmarks")
    benchmark_gates: dict[str, bool] = {}
    for index, benchmark in enumerate(benchmarks_list):
        path = f"benchmarks[{index}]"
        missing = BENCHMARK_FIELDS - set(benchmark)
        if missing:
            _fail(f"{path} missing required fields {sorted(missing)}")
        _require_string(benchmark["label"], f"{path}.label")
        _task_id(benchmark["task"], f"{path}.task")
        baseline = _require_string(benchmark["baseline"], f"{path}.baseline")
        treatment = _require_string(benchmark["treatment"], f"{path}.treatment")
        if baseline == treatment or baseline not in targets or treatment not in targets:
            _fail(f"{path} must name two distinct known targets")
        unit = _require_string(benchmark["unit"], f"{path}.unit")
        if unit.lower() in {"unknown", "none", "n/a"}:
            _fail(f"{path}.unit must be a real measurement unit")
        direction = _require_string(benchmark["direction"], f"{path}.direction")
        if direction not in DIRECTIONS:
            _fail(f"unknown benchmark direction {direction!r}")
        claim_gate = _validate_claim_gate(benchmark["claim_gate"], f"{path}.claim_gate")
        status = _require_string(benchmark["status"], f"{path}.status")
        if status not in BENCHMARK_STATUSES:
            _fail(f"unknown benchmark status {status!r}")
        benchmark_evidence = _references(
            benchmark["evidence_ids"],
            f"{path}.evidence_ids",
            set(evidence),
            required=status == "measured",
        )
        if status == "measured" and any(evidence[item_id]["status"] != "verified" for item_id in benchmark_evidence):
            _fail(f"{path} measured results require verified evidence")

        loads_raw = _require_list(benchmark["loads"], f"{path}.loads")
        loads = [str(value) for value in loads_raw]
        if any(not value for value in loads) or len(loads) != len(set(loads)):
            _fail(f"{path}.loads must contain unique, non-empty values")
        estimates = _require_list(benchmark["estimates"], f"{path}.estimates")
        samples = _require_list(benchmark["samples"], f"{path}.samples")
        if status == "measured" and (not loads or not estimates or not samples):
            _fail(f"{path} is measured but lacks loads, estimates, or samples")
        if status != "measured" and (estimates or samples):
            _fail(f"{path} unavailable/failed benchmark must not contain measurements")
        estimate_keys: set[tuple[str, str]] = set()
        estimate_n: dict[tuple[str, str], int] = {}
        estimate_values: dict[tuple[str, str], tuple[float, float, float, float]] = {}
        for estimate_index, raw_estimate in enumerate(estimates):
            estimate_path = f"{path}.estimates[{estimate_index}]"
            estimate = _require_object(raw_estimate, estimate_path)
            key = _sample_key(estimate, estimate_path, set(targets), set(loads))
            if key in estimate_keys:
                _fail(f"{estimate_path} duplicates target/load {key!r}")
            estimate_keys.add(key)
            value = _require_number(estimate.get("value"), f"{estimate_path}.value")
            lower = _require_number(estimate.get("lower"), f"{estimate_path}.lower")
            upper = _require_number(estimate.get("upper"), f"{estimate_path}.upper")
            if lower > value or value > upper:
                _fail(f"{estimate_path} interval must contain value")
            n = estimate.get("n")
            if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
                _fail(f"{estimate_path}.n must be a positive integer")
            estimate_n[key] = n
            p95 = _require_number(estimate.get("p95"), f"{estimate_path}.p95")
            estimate_values[key] = (value, p95, lower, upper)
        sample_values: dict[tuple[str, str], dict[str, float]] = {}
        receipt_constants: dict[tuple[str, str], tuple[int, int, int]] = {}
        table_ambient_by_boot: dict[str, int] = {}
        for sample_index, raw_sample in enumerate(samples):
            sample_path = f"{path}.samples[{sample_index}]"
            sample = _require_object(raw_sample, sample_path)
            if set(sample) != BENCHMARK_SAMPLE_FIELDS:
                _fail(f"{sample_path} fields do not match the timed benchmark sample schema")
            key = _sample_key(sample, sample_path, set(targets), set(loads))
            value = _require_number(sample.get("value"), f"{sample_path}.value")
            trial_raw = sample.get("trial")
            if isinstance(trial_raw, bool) or not isinstance(trial_raw, (str, int)) or str(trial_raw) == "":
                _fail(f"{sample_path}.trial must be a non-empty string or integer")
            trial = str(trial_raw)
            boot_id = _require_string(sample["boot_id"], f"{sample_path}.boot_id")
            if boot_id != trial:
                _fail(f"{sample_path}.boot_id must match its independent trial")
            if sample["order"] != "boot-median":
                _fail(f"{sample_path}.order must identify the contract-derived boot median")
            operations = _nonnegative_int(
                sample["operations"], f"{sample_path}.operations"
            )
            dataset_size = _nonnegative_int(
                sample["dataset_size"], f"{sample_path}.dataset_size"
            )
            work_units = _nonnegative_int(
                sample["work_units"], f"{sample_path}.work_units"
            )
            records_examined = _nonnegative_int(
                sample["records_examined"], f"{sample_path}.records_examined"
            )
            result_items = _nonnegative_int(
                sample["result_items"], f"{sample_path}.result_items"
            )
            if operations <= 0 or result_items != operations:
                _fail(
                    f"{sample_path} must report positive operations and one "
                    "structured result per operation"
                )
            stable_receipt = (operations, dataset_size, result_items)
            if key in receipt_constants and receipt_constants[key] != stable_receipt:
                _fail(
                    f"{sample_path} operations, dataset_size, and result_items "
                    "must be stable across independent boots"
                )
            receipt_constants[key] = stable_receipt

            if benchmark["id"] in FILE_QUERY_EXPERIMENTS:
                try:
                    expected_dataset_size = int(key[1], 10)
                except ValueError:
                    _fail(f"{sample_path}.load must be a decimal file corpus size")
                if dataset_size != expected_dataset_size:
                    _fail(f"{sample_path}.dataset_size must equal its file corpus load")
                if key[0] == baseline:
                    expected_work = (
                        dataset_size * operations
                        if benchmark["id"] == FILE_QUERY_PATH_INDEX
                        else FILE_META_CAPACITY * operations
                    )
                    if work_units != expected_work:
                        _fail(
                            f"{sample_path} baseline receipt does not prove the "
                            "registered complete traversal"
                        )
                    if benchmark["id"] == FILE_QUERY_PATH_INDEX:
                        if records_examined != dataset_size * operations:
                            _fail(
                                f"{sample_path} baseline receipt does not prove the "
                                "registered complete traversal"
                            )
                    else:
                        if records_examined % operations != 0:
                            _fail(
                                f"{sample_path} metadata ambient census is not integral"
                            )
                        ambient_records = records_examined // operations - dataset_size
                        if ambient_records < 0:
                            _fail(
                                f"{sample_path} metadata ambient census is below fixture load"
                            )
                        previous_ambient = table_ambient_by_boot.get(boot_id)
                        if (
                            previous_ambient is not None
                            and previous_ambient != ambient_records
                        ):
                            _fail(
                                f"{sample_path} metadata ambient census is inconsistent "
                                "within one boot"
                            )
                        table_ambient_by_boot[boot_id] = ambient_records
                elif (
                    work_units < operations
                    or work_units > FILE_META_CAPACITY * operations
                    or records_examined < operations
                    or records_examined > work_units
                ):
                    _fail(
                        f"{sample_path} ready-index receipt must disclose positive, "
                        "internally consistent measured work"
                    )
            elif records_examined > work_units:
                _fail(
                    f"{sample_path} records_examined cannot exceed measured work_units"
                )
            index_rebuild_records = _nonnegative_int(
                sample["index_rebuild_records"], f"{sample_path}.index_rebuild_records"
            )
            result_cache_hits = _nonnegative_int(
                sample["result_cache_hits"], f"{sample_path}.result_cache_hits"
            )
            if index_rebuild_records != 0 or result_cache_hits != 0:
                _fail(
                    f"{sample_path} actual timed sample guardrail requires "
                    "index_rebuild_records=0 and result_cache_hits=0"
                )
            by_trial = sample_values.setdefault(key, {})
            if trial in by_trial:
                _fail(f"{sample_path} duplicates target/load/trial")
            by_trial[trial] = value
            sample_evidence = _require_string(sample.get("evidence_id"), f"{sample_path}.evidence_id")
            if sample_evidence not in benchmark_evidence:
                _fail(f"{sample_path}.evidence_id is not bound by its benchmark")
            if evidence[sample_evidence]["status"] != "verified":
                _fail(f"{sample_path} requires verified evidence")
        if status == "measured":
            expected = {(target_id, load) for target_id in (baseline, treatment) for load in loads}
            if estimate_keys != expected:
                _fail(f"{path} measured estimates incomplete: expected={sorted(expected)} got={sorted(estimate_keys)}")
            if not expected.issubset(sample_values):
                _fail(f"{path} measured samples do not cover every target/load pair")
            for key in expected:
                observed = list(sample_values[key].values())
                observed.sort()
                expected_median = float(statistics.median(observed))
                expected_p95 = float(observed[max(0, math.ceil(0.95 * len(observed)) - 1)])
                value, p95, lower, upper = estimate_values[key]
                if not math.isclose(value, expected_median, rel_tol=1e-12, abs_tol=1e-12):
                    _fail(f"{path} estimate median does not match samples for {key!r}")
                if not math.isclose(p95, expected_p95, rel_tol=1e-12, abs_tol=1e-12):
                    _fail(f"{path} estimate p95 does not match samples for {key!r}")
                if lower < min(observed) or upper > max(observed):
                    _fail(f"{path} estimate interval exceeds sample range for {key!r}")
        benchmark_gates[benchmark["id"]] = _validate_paired(
            benchmark["paired"],
            f"{path}.paired",
            status,
            loads,
            estimate_n,
            sample_values,
            baseline,
            treatment,
            direction,
            f"{run_plan_sha256}:{benchmark['id']}",
            claim_gate,
            headline_alpha,
        )
        _validate_diagnostics(
            benchmark["diagnostics"],
            f"{path}.diagnostics",
            benchmark["id"],
            status,
            loads,
            benchmark_evidence,
            evidence,
        )

    expected_run_status = (
        "failed" if any(item["status"] == "failed" for item in benchmarks_list)
        else "unavailable" if any(item["status"] == "unavailable" for item in benchmarks_list)
        else "measured"
    )
    if run_status != expected_run_status:
        _fail(f"run.status must be {expected_run_status!r} for its benchmark statuses")

    scenarios_list = _require_list(root["scenarios"], "scenarios")
    _ids(scenarios_list, "scenarios")
    for index, scenario in enumerate(scenarios_list):
        path = f"scenarios[{index}]"
        if set(scenario) != SCENARIO_FIELDS:
            _fail(
                f"{path} fields mismatch: missing={sorted(SCENARIO_FIELDS - set(scenario))} "
                f"extra={sorted(set(scenario) - SCENARIO_FIELDS)}"
            )
        _require_string(scenario.get("label"), f"{path}.label")
        _task_id(scenario.get("task"), f"{path}.task")
        functional_status = _require_string(
            scenario.get("functional_status"), f"{path}.functional_status"
        )
        if functional_status not in SCENARIO_FUNCTIONAL_STATUSES:
            _fail(f"unknown scenario functional status {functional_status!r}")
        performance_status = _require_string(
            scenario.get("performance_status"), f"{path}.performance_status"
        )
        if performance_status not in SCENARIO_PERFORMANCE_STATUSES:
            _fail(f"unknown scenario performance status {performance_status!r}")
        scenario_evidence = _references(
            scenario.get("evidence_ids", []),
            f"{path}.evidence_ids",
            set(evidence),
            required=functional_status != "unavailable" or performance_status != "unavailable",
        )
        verified = all(evidence[item_id]["status"] == "verified" for item_id in scenario_evidence)
        if functional_status == "pass" and not verified:
            _fail(f"{path} functional status requires verified evidence")
        if performance_status in {"supported", "regressed", "inconclusive"}:
            if not verified:
                _fail(f"{path} performance status requires verified evidence")
            if not any(
                evidence[item_id]["kind"] == "research-platform-scenario"
                for item_id in scenario_evidence
            ):
                _fail(f"{path} performance statistics require bound scenario-report evidence")
            expected_performance_status = _validate_scenario_performance(
                scenario["performance"], f"{path}.performance", functional_status
            )
            if performance_status != expected_performance_status:
                _fail(
                    f"{path}.performance_status is forged; structured statistics require "
                    f"{expected_performance_status!r}"
                )
        elif scenario["performance"] is not None:
            _fail(f"{path}.performance must be null for {performance_status!r} status")

    claims_list = _require_list(root["claims"], "claims")
    _ids(claims_list, "claims")
    claimed_benchmarks: set[str] = set()
    for index, claim in enumerate(claims_list):
        path = f"claims[{index}]"
        missing = CLAIM_FIELDS - set(claim)
        if missing:
            _fail(f"{path} missing required fields {sorted(missing)}")
        _require_string(claim["title"], f"{path}.title")
        _require_string(claim["effect"], f"{path}.effect")
        status = _require_string(claim["status"], f"{path}.status")
        if status not in CLAIM_STATUSES:
            _fail(f"unknown claim status {status!r}")
        benchmark_id = _require_string(claim["benchmark_id"], f"{path}.benchmark_id")
        if benchmark_id not in benchmarks:
            _fail(f"{path}.benchmark_id references unknown benchmark {benchmark_id!r}")
        if benchmark_id in claimed_benchmarks:
            _fail(f"multiple claims target benchmark {benchmark_id!r}")
        claimed_benchmarks.add(benchmark_id)
        claim_evidence = _references(claim["evidence_ids"], f"{path}.evidence_ids", set(evidence), required=True)
        benchmark = benchmarks[benchmark_id]
        if set(claim_evidence) != set(benchmark["evidence_ids"]):
            _fail(f"{path}.evidence_ids must match its benchmark evidence")
        expected_status = (
            "unavailable" if benchmark["status"] != "measured"
            else "supported" if benchmark_gates[benchmark_id]
            else "not_supported"
        )
        if status != expected_status:
            _fail(f"{path}.status is forged; paired statistics require {expected_status!r}")
        if status == "supported" and any(evidence[item_id]["status"] != "verified" for item_id in claim_evidence):
            _fail(f"{path} supported status requires verified evidence")

    acceptance = _require_object(root["acceptance"], "acceptance")
    competition_claims = _require_object(
        methodology.get("competition_claims"), "methodology.competition_claims"
    )
    if set(competition_claims) != {"task4"}:
        _fail("methodology.competition_claims must register exactly Task 4")
    task4_registration = _require_object(
        competition_claims["task4"], "methodology.competition_claims.task4"
    )
    if set(task4_registration) != {"benchmark_id", "required_status"}:
        _fail("methodology.competition_claims.task4 fields do not match the contract")
    task4_benchmark_id = _require_string(
        task4_registration["benchmark_id"],
        "methodology.competition_claims.task4.benchmark_id",
    )
    if (
        task4_registration["required_status"] != "supported"
        or task4_benchmark_id not in benchmarks
        or benchmarks[task4_benchmark_id].get("task") != "task4"
        or task4_benchmark_id
        not in methodology["multiple_testing"]["headline_claims"]
        or task4_benchmark_id not in claim_benchmark_ids
    ):
        _fail(
            "methodology.competition_claims.task4 must bind one registered "
            "Task 4 headline claim and require supported"
        )
    try:
        expected_acceptance = derive_acceptance_gates(
            scenarios_list,
            claims_list,
            competition_claims,
        )
    except EvaluationContractError as error:
        _fail(f"methodology.competition_claims is invalid: {error}")
    if acceptance != expected_acceptance:
        _fail(
            "acceptance gates are forged; scientific publication and competition "
            "acceptance must be derived independently from functional receipts and "
            "the explicitly registered Task 4 claim"
        )

    has_scientific_result = any(
        claim["status"] in {"supported", "not_supported"}
        for claim in claims_list
    ) or any(
        scenario["performance_status"] in {"supported", "regressed", "inconclusive"}
        for scenario in scenarios_list
    )
    if has_scientific_result:
        commit = run.get("commit")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            _fail("measured scientific results require run.commit as a full lowercase commit")
        if run.get("evidence_grade") != "E2-local-raw":
            _fail("measured scientific results require contract-derived run.evidence_grade")

    return root


def _h(value: Any) -> str:
    if value is None or value == "":
        return "unavailable"
    if isinstance(value, bool):
        value = "是" if value else "否"
    return html.escape(str(value), quote=True)


def _status(value: str) -> str:
    return f'<span class="status status--{_h(value)}">{_h(STATUS_ZH.get(value, value))}</span>'


def _evidence_href(reference: str) -> str:
    parts = _canonical_evidence_reference(reference, "evidence.path")
    encoded = "/".join(quote(part, safe="-._~") for part in parts)
    return f"evidence/{encoded}"


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    try:
        safe_atomic_write_bytes(path, value)
    except (OSError, ValueError) as error:
        _fail(f"Dashboard output cannot be written safely: {path}: {error}")


def _canonical_evidence_reference(value: Any, path: str) -> tuple[str, ...]:
    reference = _require_string(value, path)
    if "\\" in reference:
        _fail(f"{path} must use canonical forward slashes, not backslashes")
    if "\0" in reference or reference.startswith("/") or WINDOWS_DRIVE.match(reference):
        _fail(f"{path} must be a canonical relative path")
    parts = reference.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(":" in part for part in parts):
        _fail(f"{path} must be a canonical relative path without empty, dot, or drive segments")
    if PurePosixPath(reference).as_posix() != reference:
        _fail(f"{path} must be a canonical relative path")
    return tuple(parts)


def _read_evidence_file(
    root: Path,
    parts: tuple[str, ...],
    path: str,
    *,
    max_bytes: int = MAX_PORTABLE_EVIDENCE_FILE_BYTES,
) -> bytes:
    candidate = root
    for part in parts:
        candidate /= part
        try:
            if path_is_link(candidate):
                _fail(f"{path} must not traverse a symlink or junction")
        except OSError as error:
            _fail(f"{path} cannot inspect path component: {error}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        _fail(f"{path} evidence file is missing or inaccessible: {error}")
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail(f"{path} escapes the summary evidence root")
    if not resolved.is_file():
        _fail(f"{path} must reference a regular evidence file")
    try:
        before = resolved.stat()
        if before.st_size > max_bytes:
            _fail(f"{path} exceeds its portable evidence byte budget")
        payload = bytearray()
        with resolved.open("rb") as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, max_bytes - len(payload) + 1))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    _fail(f"{path} exceeds its portable evidence byte budget")
        data = bytes(payload)
        after = resolved.stat()
    except OSError as error:
        _fail(f"{path} cannot read evidence file: {error}")
    identity_before = (
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_dev", None),
        getattr(before, "st_ino", None),
    )
    identity_after = (
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_dev", None),
        getattr(after, "st_ino", None),
    )
    if identity_before != identity_after or len(data) != after.st_size:
        _fail(f"{path} changed while it was being verified")
    return data


def _ensure_plain_directory(path: Path, label: str) -> None:
    try:
        if path_is_link(path):
            _fail(f"{label} must not be a symlink or junction")
        if path.exists():
            if not path.is_dir():
                _fail(f"{label} must be a directory")
            return
        path.mkdir()
    except OSError as error:
        _fail(f"{label} cannot be created safely: {error}")


def _write_portable_evidence(
    evidence_root: Path, output_dir: Path, summary: dict[str, Any]
) -> dict[str, tuple[str, int]]:
    """Copy the small, Dashboard-linked evidence into the offline site."""
    portable_root = output_dir / "evidence"
    _ensure_plain_directory(portable_root, "portable evidence root")
    expected: dict[str, str] = {}
    payloads: dict[str, tuple[tuple[str, ...], bytes]] = {}
    total_bytes = 0
    for index, item in enumerate(summary["evidence"]):
        parts = _canonical_evidence_reference(
            item["path"], f"evidence[{index}].path"
        )
        relative = "/".join(parts)
        data = _read_evidence_file(
            evidence_root,
            parts,
            f"evidence[{index}].path",
            max_bytes=min(
                MAX_PORTABLE_EVIDENCE_FILE_BYTES,
                MAX_PORTABLE_EVIDENCE_TOTAL_BYTES - total_bytes,
            ),
        )
        digest = hashlib.sha256(data).hexdigest()
        if digest != item["sha256"]:
            _fail(f"evidence[{index}] changed before portable publication")
        previous = expected.get(relative)
        if previous is not None:
            if previous != digest:
                _fail(f"portable evidence path {relative!r} has conflicting hashes")
            continue
        if len(expected) >= MAX_PORTABLE_EVIDENCE_FILES:
            _fail("portable Dashboard evidence contains too many files")
        if len(data) > MAX_PORTABLE_EVIDENCE_FILE_BYTES:
            _fail(f"portable Dashboard evidence is too large: {relative}")
        total_bytes += len(data)
        if total_bytes > MAX_PORTABLE_EVIDENCE_TOTAL_BYTES:
            _fail("portable Dashboard evidence exceeds its total byte budget")
        expected[relative] = digest
        payloads[relative] = (parts, data)

    for relative in sorted(payloads):
        parts, data = payloads[relative]
        parent = portable_root
        for part in parts[:-1]:
            parent /= part
            _ensure_plain_directory(parent, f"portable evidence directory {relative}")
        _atomic_write_bytes(parent / parts[-1], data)

    actual: set[str] = set()
    for directory, dirnames, filenames in os.walk(portable_root, followlinks=False):
        base = Path(directory)
        for name in [*dirnames, *filenames]:
            candidate = base / name
            try:
                if path_is_link(candidate):
                    _fail("portable Dashboard evidence contains a symlink or junction")
            except OSError as error:
                _fail(f"portable Dashboard evidence cannot be inspected: {error}")
        for name in filenames:
            candidate = base / name
            if not candidate.is_file():
                _fail("portable Dashboard evidence contains a non-file")
            actual.add(candidate.relative_to(portable_root).as_posix())
    if actual != set(expected):
        _fail("portable Dashboard evidence inventory differs from the summary")
    return {
        f"evidence/{relative}": (digest, len(payloads[relative][1]))
        for relative, digest in expected.items()
    }


def _scenario_string(value: Any, path: str, *, maximum: int = 256) -> str:
    result = _require_string(value, path)
    if len(result) > maximum:
        _fail(f"{path} exceeds the {maximum}-character bound")
    return result


def _scenario_uint(value: Any, path: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _fail(f"{path} must be an integer from 0 through {maximum}")
    return value


def _scenario_duration_metric(value: Any, path: str) -> float:
    result = _require_number(value, path)
    if not 0 <= result <= MAX_SCENARIO_DURATION_MS:
        _fail(f"{path} is outside the bounded scenario duration range")
    return result


def _scenario_percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower),
        3,
    ))


def _scenario_sha256(value: Any, path: str) -> str:
    result = _scenario_string(value, path, maximum=64)
    if not SHA256.fullmatch(result):
        _fail(f"{path} must be lowercase SHA-256")
    return result


def _normalize_scenario_resource_stability(
    report_summary: dict[str, Any],
    samples: list[Any],
    item_path: str,
) -> dict[str, Any]:
    path = f"{item_path}.report.summary.resource_stability"
    resource = _require_object(report_summary.get("resource_stability"), path)
    if set(resource) != RESOURCE_STABILITY_SUMMARY_FIELDS:
        _fail(f"{path} fields do not match the resource stability summary schema")

    status = _scenario_string(resource["status"], f"{path}.status")
    if status not in {"passed", "partial", "unavailable"}:
        _fail(f"{path}.status is invalid")
    if resource["required_target"] != "agentos":
        _fail(f"{path}.required_target must be agentos")
    if resource["measurement_scope"] != RESOURCE_STABILITY_MEASUREMENT_SCOPE:
        _fail(f"{path}.measurement_scope differs from the registered scope")
    verified_boots = _scenario_uint(
        resource["verified_boots"],
        f"{path}.verified_boots",
        maximum=MAX_SCENARIO_SAMPLES,
    )
    registered_counts = (
        (
            "load_workflows_per_boot",
            RESOURCE_STABILITY_LOAD_WORKFLOWS,
        ),
        (
            "terminal_workflows_per_boot",
            RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
        ),
        (
            "child_rounds_per_load_workflow",
            RESOURCE_STABILITY_CHILD_ROUNDS,
        ),
    )
    for field, expected in registered_counts:
        actual = _scenario_uint(resource[field], f"{path}.{field}", maximum=1_000_000)
        if actual != expected:
            _fail(f"{path}.{field} differs from the registered workload")

    interpretation = _require_object(resource["interpretation"], f"{path}.interpretation")
    if interpretation != RESOURCE_STABILITY_INTERPRETATION:
        _fail(f"{path}.interpretation differs from the registered claim boundary")

    observation_path = f"{path}.global_observation"
    observation = _require_object(resource["global_observation"], observation_path)
    if set(observation) != RESOURCE_STABILITY_OBSERVATION_FIELDS:
        _fail(f"{observation_path} fields do not match the observation schema")
    if (
        observation["coverage"] != "configured_global_kind_counters"
        or observation["measured_mask_semantics"]
        != "configured_global_resource_kind_counters_only"
        or observation["account_counters"] != "not_measured"
        or observation["rate_budgets"] != "not_measured"
        or observation["growth_bound_semantics"]
        != "per_class_positive_delta_sum"
        or observation["decrease_semantics"] != "reclamation_allowed"
    ):
        _fail(f"{observation_path} overstates the registered observation coverage")
    free_pages_path = f"{observation_path}.free_pages"
    free_pages = _require_object(observation["free_pages"], free_pages_path)
    if set(free_pages) != {
        "status",
        "exact_pair_recovery",
        "exact_terminal_recovery",
    }:
        _fail(f"{free_pages_path} fields do not match the free-page observation schema")
    free_pages_status = _scenario_string(free_pages["status"], f"{free_pages_path}.status")
    if free_pages_status == "measured":
        if (
            free_pages["exact_pair_recovery"] is not True
            or free_pages["exact_terminal_recovery"] is not True
        ):
            _fail(f"{free_pages_path} measured status requires exact recovery")
    elif free_pages_status == "not_measured":
        if (
            free_pages["exact_pair_recovery"] is not None
            or free_pages["exact_terminal_recovery"] is not None
        ):
            _fail(f"{free_pages_path} not_measured status cannot claim recovery")
    else:
        _fail(f"{free_pages_path}.status is invalid")

    raw_resources = _require_list(observation["resources"], f"{observation_path}.resources")
    if len(raw_resources) != len(RESOURCE_STABILITY_RESOURCE_KINDS):
        _fail(f"{observation_path}.resources does not cover every registered kind")
    measured_kinds = 0
    for index, (raw_resource, kind) in enumerate(
        zip(raw_resources, RESOURCE_STABILITY_RESOURCE_KINDS)
    ):
        resource_path = f"{observation_path}.resources[{index}]"
        observed = _require_object(raw_resource, resource_path)
        if set(observed) != RESOURCE_STABILITY_RESOURCE_FIELDS:
            _fail(f"{resource_path} fields do not match the resource observation schema")
        if observed["kind"] != kind:
            _fail(f"{resource_path}.kind differs from the registered resource order")
        if observed["coverage"] != "configured_global_counter":
            _fail(f"{resource_path}.coverage differs from the registered counter scope")
        bound = RESOURCE_STABILITY_GROWTH_BOUNDS[kind]
        for field in ("per_workflow_growth_bound", "terminal_growth_bound"):
            if (
                isinstance(observed[field], bool)
                or not isinstance(observed[field], int)
                or observed[field] != bound
            ):
                _fail(f"{resource_path}.{field} differs from the registered guardrail")
        observed_status = _scenario_string(observed["status"], f"{resource_path}.status")
        if observed_status == "measured":
            deltas: dict[str, int] = {}
            for field in (
                "max_observed_per_workflow_growth",
                "terminal_observed_growth",
            ):
                value = observed[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= bound
                ):
                    _fail(f"{resource_path}.{field} is outside the registered growth bound")
                deltas[field] = value
            plateau = observed["plateau_or_reclamation"]
            if bound == 0:
                if plateau is not None:
                    _fail(f"{resource_path}.plateau_or_reclamation must be null at a zero bound")
            elif plateau is not True:
                _fail(f"{resource_path}.plateau_or_reclamation must prove a plateau or reclamation")
            if not isinstance(observed["exact_terminal_recovery"], bool):
                _fail(f"{resource_path}.exact_terminal_recovery must be boolean when measured")
            if (
                observed["exact_terminal_recovery"]
                and deltas["terminal_observed_growth"] != 0
            ):
                _fail(f"{resource_path}.exact recovery conflicts with terminal growth")
            measured_kinds += 1
        elif observed_status == "not_measured":
            if (
                observed["max_observed_per_workflow_growth"] is not None
                or observed["terminal_observed_growth"] is not None
                or observed["plateau_or_reclamation"] is not None
                or observed["exact_terminal_recovery"] is not None
            ):
                _fail(f"{resource_path} not_measured status cannot claim a result")
        else:
            _fail(f"{resource_path}.status is invalid")

    receipts = _require_list(resource["boot_receipts"], f"{path}.boot_receipts")
    expected_receipts: list[tuple[str, str, str, str | None, str | None]] = []
    for index, raw_sample in enumerate(samples):
        sample_path = f"{item_path}.report.samples[{index}]"
        sample = _require_object(raw_sample, sample_path)
        binding = _require_object(sample["binding"], f"{sample_path}.binding")
        source_receipts = _require_object(
            binding["source_receipts"], f"{sample_path}.binding.source_receipts"
        )
        targets = _require_object(sample["targets"], f"{sample_path}.targets")
        agentos = _require_object(targets["agentos"], f"{sample_path}.targets.agentos")
        raw_source = _require_object(
            agentos["raw_source_receipt"],
            f"{sample_path}.targets.agentos.raw_source_receipt",
        )
        nested_resource_sha256: str | None = None
        nested_binding_sha256: str | None = None
        if status == "unavailable":
            if "resource_stability" in raw_source:
                _fail(f"{sample_path} unavailable resource summary conflicts with raw evidence")
        else:
            raw_resource_path = (
                f"{sample_path}.targets.agentos.raw_source_receipt.resource_stability"
            )
            raw_resource = _require_object(
                raw_source.get("resource_stability"), raw_resource_path
            )
            if set(raw_resource) != {
                "required",
                "status",
                "path",
                "bytes",
                "sha256",
                "acceptance",
                "binding",
            }:
                _fail(f"{raw_resource_path} fields do not match the raw resource receipt schema")
            if (
                raw_resource["required"] is not True
                or raw_resource["status"] != "verified"
                or raw_resource["path"]
                != f"state-extracted/{RESOURCE_STABILITY_FILE}"
                or isinstance(raw_resource["bytes"], bool)
                or not isinstance(raw_resource["bytes"], int)
                or raw_resource["bytes"] <= 0
            ):
                _fail(f"{raw_resource_path} is not a verified resource receipt")
            nested_resource_sha256 = _scenario_sha256(
                raw_resource["sha256"], f"{raw_resource_path}.sha256"
            )
            raw_binding = _require_object(
                raw_resource["binding"], f"{raw_resource_path}.binding"
            )
            nested_binding_sha256 = _scenario_sha256(
                raw_binding.get("sha256"), f"{raw_resource_path}.binding.sha256"
            )
        expected_receipts.append(
            (
                _scenario_string(sample["sample_id"], f"{sample_path}.sample_id", maximum=256),
                _scenario_string(binding["challenge"], f"{sample_path}.binding.challenge", maximum=64),
                _scenario_sha256(
                    source_receipts["agentos"],
                    f"{sample_path}.binding.source_receipts.agentos",
                ),
                nested_resource_sha256,
                nested_binding_sha256,
            )
        )

    resource_receipt_hashes: set[str] = set()
    for index, raw_receipt in enumerate(receipts):
        receipt_path = f"{path}.boot_receipts[{index}]"
        receipt = _require_object(raw_receipt, receipt_path)
        if set(receipt) != RESOURCE_STABILITY_RECEIPT_FIELDS:
            _fail(f"{receipt_path} fields do not match the resource boot receipt schema")
        sample_id = _scenario_string(receipt["sample_id"], f"{receipt_path}.sample_id", maximum=256)
        challenge = _scenario_string(receipt["challenge"], f"{receipt_path}.challenge", maximum=64)
        resource_sha256 = _scenario_sha256(
            receipt["resource_receipt_sha256"], f"{receipt_path}.resource_receipt_sha256"
        )
        binding_sha256 = _scenario_sha256(
            receipt["binding_sha256"], f"{receipt_path}.binding_sha256"
        )
        raw_source_sha256 = _scenario_sha256(
            receipt["raw_source_receipt_sha256"],
            f"{receipt_path}.raw_source_receipt_sha256",
        )
        if index >= len(expected_receipts) or (
            sample_id,
            challenge,
            raw_source_sha256,
            resource_sha256,
            binding_sha256,
        ) != expected_receipts[index]:
            _fail(f"{receipt_path} is not bound to the corresponding scenario sample")
        if resource_sha256 in resource_receipt_hashes:
            _fail(f"{receipt_path} replays a resource receipt across boots")
        resource_receipt_hashes.add(resource_sha256)

    if status == "unavailable":
        if (
            verified_boots != 0
            or receipts
            or free_pages_status != "not_measured"
            or measured_kinds != 0
            or observation["snapshot_consistency"] != "not_measured"
        ):
            _fail(f"{path} unavailable status cannot claim measured evidence")
    else:
        if (
            len(receipts) != len(samples)
            or free_pages_status != "measured"
            or observation["snapshot_consistency"] != "single_core_irq_coherent"
        ):
            _fail(f"{path} measured status must bind every boot and free-page observation")
        if status == "passed":
            if verified_boots != len(samples) or measured_kinds != len(raw_resources):
                _fail(f"{path} passed status must cover every boot and resource kind")
        elif verified_boots >= len(samples) or measured_kinds == len(raw_resources):
            _fail(f"{path} partial status must retain an explicit observation gap")

    return copy.deepcopy(resource)


def _normalize_scenario_source_comparability(
    value: Any,
    program_order: list[str],
    source_commit: str,
    committed_receipt: dict[str, object],
    path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _require_object(value, path)
    if receipt != committed_receipt:
        _fail(f"{path} differs from the committed manifests and source blobs")
    required = {
        "schema",
        "source_commit",
        "expected_programs",
        "manifest_binding",
        "same_source_programs",
        "platform_specific_programs",
        "programs",
        "sha256",
    }
    if set(receipt) != required:
        _fail(f"{path} fields do not match the program source receipt schema")
    supplied_sha256 = _scenario_sha256(receipt["sha256"], f"{path}.sha256")
    unsigned = dict(receipt)
    unsigned.pop("sha256")
    if (
        receipt["schema"] != PROGRAM_SOURCE_RECEIPT_SCHEMA
        or receipt["source_commit"] != source_commit
        or supplied_sha256
        != _binding_sha256(unsigned, PROGRAM_SOURCE_RECEIPT_DOMAIN)
    ):
        _fail(f"{path} binding or source identity is invalid")
    expected_count = receipt["expected_programs"]
    same_count = receipt["same_source_programs"]
    platform_count = receipt["platform_specific_programs"]
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in (expected_count, same_count, platform_count)
    ) or expected_count != TASK6_EXPECTED_PROGRAM_COUNT or len(program_order) != TASK6_EXPECTED_PROGRAM_COUNT:
        _fail(f"{path} program counts are invalid")
    pairs = _require_list(receipt["programs"], f"{path}.programs")
    if len(pairs) != len(program_order):
        _fail(f"{path}.programs does not cover the timed program order")

    observed_same = 0
    for index, expected_program in enumerate(program_order):
        pair_path = f"{path}.programs[{index}]"
        pair = _require_object(pairs[index], pair_path)
        if set(pair) != {"program", "relation", "agentos", "plain", "sha256"}:
            _fail(f"{pair_path} fields do not match the source pair schema")
        pair_sha256 = _scenario_sha256(pair["sha256"], f"{pair_path}.sha256")
        unsigned_pair = dict(pair)
        unsigned_pair.pop("sha256")
        relation = _scenario_string(pair["relation"], f"{pair_path}.relation")
        if (
            pair["program"] != expected_program
            or relation not in {"same_source", "platform_specific"}
            or pair_sha256
            != _binding_sha256(unsigned_pair, PROGRAM_SOURCE_PAIR_DOMAIN)
        ):
            _fail(f"{pair_path} identity, relation, or binding is invalid")

        target_records: dict[str, dict[str, Any]] = {}
        for target, expected_path in (
            ("agentos", f"user/src/{expected_program}.c"),
            ("plain", f"baseline_ucore/user/src/{expected_program}.c"),
        ):
            record_path = f"{pair_path}.{target}"
            record = _require_object(pair[target], record_path)
            if set(record) != {"path", "bytes", "sha256", "git_blob_oid"}:
                _fail(f"{record_path} fields do not match the source file receipt schema")
            byte_count = record["bytes"]
            if (
                record["path"] != expected_path
                or isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or not 0 < byte_count <= MAX_PROGRAM_SOURCE_BYTES
            ):
                _fail(f"{record_path} path or byte count is invalid")
            _scenario_sha256(record["sha256"], f"{record_path}.sha256")
            if (
                not isinstance(record["git_blob_oid"], str)
                or GIT_OBJECT_ID_RE.fullmatch(record["git_blob_oid"]) is None
            ):
                _fail(f"{record_path}.git_blob_oid is invalid")
            target_records[target] = record
        byte_equal = (
            target_records["agentos"]["bytes"] == target_records["plain"]["bytes"]
            and target_records["agentos"]["sha256"]
            == target_records["plain"]["sha256"]
        )
        if (relation == "same_source") != byte_equal:
            _fail(f"{pair_path}.relation differs from the paired source receipts")
        observed_same += int(byte_equal)

    observed_platform = len(program_order) - observed_same
    if same_count != observed_same or platform_count != observed_platform:
        _fail(f"{path} source comparability counts differ from the paired receipts")
    normalized = {
        "schema": PROGRAM_SOURCE_RECEIPT_SCHEMA,
        "source_commit": source_commit,
        "expected_programs": len(program_order),
        "same_source_programs": observed_same,
        "platform_specific_programs": observed_platform,
        "receipt_sha256": supplied_sha256,
    }
    return normalized, copy.deepcopy(receipt)


def _extract_scenario_detail(
    data: bytes,
    item: dict[str, Any],
    item_path: str,
    summary_document: dict[str, Any],
    *,
    source_tree: Path,
    measurement_source_receipt: object,
) -> dict[str, Any]:
    if len(data) > MAX_SCENARIO_REPORT_BYTES:
        _fail(f"{item_path} scenario report exceeds the bounded JSON size")
    try:
        report = _require_object(strict_json_loads(data), f"{item_path}.report")
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        _fail(f"{item_path} scenario report is not strict bounded JSON: {error}")

    required_report_fields = {
        "schema_version", "scenario_id", "source_commit", "run_id", "status",
        "samples", "summary", "report_sha256",
    }
    if set(report) != required_report_fields:
        _fail(
            f"{item_path} scenario report fields mismatch: "
            f"missing={sorted(required_report_fields - set(report))} "
            f"extra={sorted(set(report) - required_report_fields)}"
        )
    if report["schema_version"] != 2:
        _fail(f"{item_path} scenario report schema_version must be 2")
    scenario_id = _scenario_string(report["scenario_id"], f"{item_path}.report.scenario_id")
    source_commit = _scenario_string(
        report["source_commit"], f"{item_path}.report.source_commit", maximum=40
    )
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        _fail(f"{item_path} scenario report source_commit must be a full lowercase commit")
    run_id = _scenario_string(report["run_id"], f"{item_path}.report.run_id", maximum=128)
    report_status = _scenario_string(report["status"], f"{item_path}.report.status")
    if report_status not in {"supported", "regressed", "inconclusive"}:
        _fail(
            f"{item_path} verified scenario report must be supported, regressed, "
            "or inconclusive"
        )
    supplied_report_sha256 = _scenario_sha256(
        report["report_sha256"], f"{item_path}.report.report_sha256"
    )
    unsigned_report = dict(report)
    unsigned_report.pop("report_sha256")
    if supplied_report_sha256 != _binding_sha256(unsigned_report, "scenario-report-v2"):
        _fail(f"{item_path} scenario report binding SHA-256 is invalid")
    receipt = item.get("receipt")
    if isinstance(receipt, dict) and "report_sha256" in receipt:
        if receipt["report_sha256"] != supplied_report_sha256:
            _fail(f"{item_path}.receipt.report_sha256 differs from the bound report")

    run = summary_document["run"]
    if "commit" in run and run["commit"] != source_commit:
        _fail(f"{item_path} scenario report commit differs from the evaluation summary")
    if run["id"] != run_id:
        _fail(f"{item_path} scenario report run_id differs from the evaluation summary")
    try:
        committed_programs, _ = read_snapshot_expected_programs(
            source_tree, source_commit, measurement_source_receipt
        )
        committed_source_receipt = _program_source_comparability_receipt_from_snapshot(
            source_tree,
            source_commit,
            committed_programs,
            measurement_source_receipt,
        )
    except (OSError, ScenarioEvidenceError, TypeError, ValueError) as error:
        _fail(f"{item_path} committed Task 6 inventory is invalid: {error}")
    bound_scenarios = [
        scenario
        for scenario in summary_document["scenarios"]
        if item["id"] in scenario.get("evidence_ids", [])
    ]
    matching_scenarios = [scenario for scenario in bound_scenarios if scenario["id"] == scenario_id]
    if len(matching_scenarios) != 1:
        _fail(
            f"{item_path} scenario report must bind exactly one matching summary scenario"
        )
    bound_scenario = matching_scenarios[0]
    if bound_scenario["performance_status"] != report_status:
        _fail(f"{item_path} scenario report status differs from the summary scenario")

    samples = _require_list(report["samples"], f"{item_path}.report.samples")
    if not 0 < len(samples) <= MAX_SCENARIO_SAMPLES:
        _fail(
            f"{item_path} scenario report must contain 1 through {MAX_SCENARIO_SAMPLES} samples"
        )
    sample_ids: set[str] = set()
    boot_ids: set[str] = set()
    challenges: set[str] = set()
    raw_order_counts = {"AB": 0, "BA": 0}
    raw_paired_samples: list[dict[str, Any]] = []
    program_order = list(committed_programs)
    source_comparability: dict[str, Any] | None = None
    source_receipt_reference: dict[str, Any] | None = None
    timings: dict[str, dict[str, list[int]]] = {
        target: {program: [] for program in program_order}
        for target in ("plain", "agentos")
    }
    makespans: dict[str, list[int]] = {"plain": [], "agentos": []}
    for sample_index, raw_sample in enumerate(samples):
        sample_path = f"{item_path}.report.samples[{sample_index}]"
        sample = _require_object(raw_sample, sample_path)
        if set(sample) != {"sample_id", "binding", "outcome", "outcome_fingerprint", "targets"}:
            _fail(f"{sample_path} fields do not match the scenario sample schema")
        sample_id = _scenario_string(sample["sample_id"], f"{sample_path}.sample_id", maximum=256)
        if sample_id in sample_ids:
            _fail(f"{sample_path}.sample_id is duplicated")
        sample_ids.add(sample_id)

        outcome = _require_object(sample["outcome"], f"{sample_path}.outcome")
        if set(outcome) != SCENARIO_OUTCOME_FIELDS:
            _fail(f"{sample_path}.outcome does not contain the preregistered key outcome schema")
        for outcome_key in SCENARIO_KEY_OUTCOMES:
            record = outcome[outcome_key]
            if not isinstance(record, dict) or not 0 < len(record) <= 32:
                _fail(f"{sample_path}.outcome.{outcome_key} must be a non-empty object")
            for record_key, record_value in record.items():
                _scenario_string(
                    record_key, f"{sample_path}.outcome.{outcome_key} key", maximum=128
                )
                _scenario_string(
                    record_value,
                    f"{sample_path}.outcome.{outcome_key}.{record_key}",
                    maximum=4096,
                )
        challenge = _scenario_string(
            outcome["challenge"], f"{sample_path}.outcome.challenge", maximum=64
        )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", challenge):
            _fail(f"{sample_path}.outcome.challenge is not a bounded identifier")
        workflow = _require_object(outcome["workflow"], f"{sample_path}.outcome.workflow")
        if set(workflow) != {"run_id", "workflow_id"}:
            _fail(f"{sample_path}.outcome.workflow fields are invalid")
        for field in ("run_id", "workflow_id"):
            _scenario_string(workflow[field], f"{sample_path}.outcome.workflow.{field}")
        artifact_input = _require_object(
            outcome["artifact_input"], f"{sample_path}.outcome.artifact_input"
        )
        if set(artifact_input) != {"host_artifact_input", "kind", "sha256", "bytes", "source"}:
            _fail(f"{sample_path}.outcome.artifact_input fields are invalid")
        for field, value in artifact_input.items():
            _scenario_string(value, f"{sample_path}.outcome.artifact_input.{field}", maximum=256)
        outcome_fingerprint = _scenario_sha256(
            sample["outcome_fingerprint"], f"{sample_path}.outcome_fingerprint"
        )
        if outcome_fingerprint != _binding_sha256(outcome, "research-platform-outcome-v2"):
            _fail(f"{sample_path}.outcome_fingerprint does not bind the key outcome")

        binding = _require_object(sample["binding"], f"{sample_path}.binding")
        binding_sha256 = _scenario_sha256(binding.get("sha256"), f"{sample_path}.binding.sha256")
        unsigned_binding = dict(binding)
        unsigned_binding.pop("sha256", None)
        if binding_sha256 != _binding_sha256(unsigned_binding, "scenario-sample-v1"):
            _fail(f"{sample_path}.binding.sha256 is invalid")
        if binding.get("outcome_fingerprint") != outcome_fingerprint:
            _fail(f"{sample_path}.binding does not bind the key outcome fingerprint")
        if binding.get("challenge") != challenge:
            _fail(f"{sample_path}.binding challenge differs from the key outcome")
        if challenge in challenges:
            _fail(f"{sample_path}.binding challenge is duplicated")
        challenges.add(challenge)
        boot_id = _scenario_string(
            binding.get("boot_id"), f"{sample_path}.binding.boot_id", maximum=128
        )
        if boot_id in boot_ids:
            _fail(f"{sample_path}.binding.boot_id is duplicated")
        boot_ids.add(boot_id)
        target_order = _scenario_string(
            binding.get("target_order"),
            f"{sample_path}.binding.target_order",
            maximum=2,
        )
        if target_order not in raw_order_counts:
            _fail(f"{sample_path}.binding.target_order must be AB or BA")
        raw_order_counts[target_order] += 1
        raw_order = _require_list(binding.get("program_order"), f"{sample_path}.binding.program_order")
        current_order = [
            _scenario_string(value, f"{sample_path}.binding.program_order[{index}]", maximum=64)
            for index, value in enumerate(raw_order)
        ]
        if not current_order or len(current_order) > MAX_SCENARIO_PROGRAMS or len(current_order) != len(set(current_order)):
            _fail(f"{sample_path}.binding.program_order is empty, duplicated, or over the bound")
        if current_order != program_order:
            _fail(
                f"{sample_path}.binding.program_order differs from the committed manifests"
            )

        source_receipts = _require_object(
            binding.get("source_receipts"), f"{sample_path}.binding.source_receipts"
        )
        targets = _require_object(sample["targets"], f"{sample_path}.targets")
        if set(targets) != {"plain", "agentos"} or set(source_receipts) != {"plain", "agentos"}:
            _fail(f"{sample_path} must bind plain and agentos targets exactly")
        sample_makespans: dict[str, int] = {}
        for target in ("plain", "agentos"):
            target_path = f"{sample_path}.targets.{target}"
            measurement = _require_object(targets[target], target_path)
            makespan = _scenario_uint(
                measurement.get("makespan_ms"), f"{target_path}.makespan_ms",
                maximum=MAX_SCENARIO_DURATION_MS,
            )
            makespans[target].append(makespan)
            sample_makespans[target] = makespan
            rows = _require_list(measurement.get("programs"), f"{target_path}.programs")
            if len(rows) != len(program_order):
                _fail(f"{target_path}.programs does not match the preregistered program order")
            observed_order: list[str] = []
            for row_index, raw_row in enumerate(rows):
                row_path = f"{target_path}.programs[{row_index}]"
                row = _require_object(raw_row, row_path)
                if set(row) != {"program", "elapsed_ms"}:
                    _fail(f"{row_path} fields must be program/elapsed_ms")
                program = _scenario_string(row["program"], f"{row_path}.program", maximum=64)
                elapsed = _scenario_uint(
                    row["elapsed_ms"], f"{row_path}.elapsed_ms",
                    maximum=MAX_SCENARIO_DURATION_MS,
                )
                observed_order.append(program)
                if program not in timings[target]:
                    _fail(f"{row_path}.program is not preregistered")
                timings[target][program].append(elapsed)
            if observed_order != program_order:
                _fail(f"{target_path}.programs order differs from the preregistration")
            raw_receipt = _require_object(
                measurement.get("raw_source_receipt"), f"{target_path}.raw_source_receipt"
            )
            receipt_sha256 = _scenario_sha256(
                raw_receipt.get("sha256"), f"{target_path}.raw_source_receipt.sha256"
            )
            if source_receipts[target] != receipt_sha256:
                _fail(f"{target_path}.raw_source_receipt is not bound by the sample")
            current_source, current_receipt = _normalize_scenario_source_comparability(
                raw_receipt.get("program_source_comparability"),
                program_order,
                source_commit,
                committed_source_receipt,
                f"{target_path}.raw_source_receipt.program_source_comparability",
            )
            if source_comparability is None:
                source_comparability = current_source
                source_receipt_reference = current_receipt
            elif (
                current_source != source_comparability
                or current_receipt != source_receipt_reference
            ):
                _fail(
                    f"{target_path} program source comparability differs across targets or boots"
                )
        improvement = sample_makespans["plain"] - sample_makespans["agentos"]
        relative = (
            improvement * 100.0 / sample_makespans["plain"]
            if sample_makespans["plain"] > 0
            else None
        )
        raw_paired_samples.append(
            {
                "sample_id": sample_id,
                "boot_id": boot_id,
                "target_order": target_order,
                "plain_ms": sample_makespans["plain"],
                "agentos_ms": sample_makespans["agentos"],
                "improvement_ms": improvement,
                "relative_improvement_percent": relative,
            }
        )

    assert source_comparability is not None
    report_summary = _require_object(report["summary"], f"{item_path}.report.summary")
    summary_source = _require_object(
        report_summary.get("source_comparability"),
        f"{item_path}.report.summary.source_comparability",
    )
    if summary_source != source_comparability:
        _fail(
            f"{item_path}.report.summary.source_comparability differs from the raw receipts"
        )
    independent_boots = _scenario_uint(
        report_summary.get("independent_boots"),
        f"{item_path}.report.summary.independent_boots",
        maximum=MAX_SCENARIO_SAMPLES,
    )
    if independent_boots != len(samples):
        _fail(f"{item_path} scenario summary boot count differs from its samples")
    if report_summary.get("minimum_supported_boots") != MINIMUM_SCENARIO_BOOTS:
        _fail(f"{item_path} scenario summary minimum boot count differs")
    if report_summary.get("unique_challenges") != len(challenges):
        _fail(f"{item_path} scenario summary unique challenge count differs")
    report_success_rate = _require_number(
        report_summary.get("paired_success_rate"),
        f"{item_path}.report.summary.paired_success_rate",
    )
    if not math.isclose(report_success_rate, 1.0, rel_tol=0, abs_tol=1e-15):
        _fail(f"{item_path} scenario paired success rate must be 1.0")
    if report_summary.get("target_order_counts") != raw_order_counts:
        _fail(f"{item_path} scenario target order counts differ from raw samples")
    expected_balanced = abs(raw_order_counts["AB"] - raw_order_counts["BA"]) <= 1
    if report_summary.get("target_order_balanced") is not expected_balanced:
        _fail(f"{item_path} scenario target order balance differs from raw samples")
    target_summary = _require_object(
        report_summary.get("targets"), f"{item_path}.report.summary.targets"
    )
    if set(target_summary) != {"plain", "agentos"}:
        _fail(f"{item_path} scenario target summary must contain plain and agentos")
    display_programs: list[dict[str, Any]] = []
    for program in program_order:
        display_programs.append({"program": program})
    for target in ("plain", "agentos"):
        target_path = f"{item_path}.report.summary.targets.{target}"
        target_metrics = _require_object(target_summary[target], target_path)
        if _scenario_uint(
            target_metrics.get("successful_boots"), f"{target_path}.successful_boots",
            maximum=MAX_SCENARIO_SAMPLES,
        ) != len(samples):
            _fail(f"{target_path}.successful_boots differs from the samples")
        if not math.isclose(_require_number(target_metrics.get("success_rate"), f"{target_path}.success_rate"), 1.0):
            _fail(f"{target_path}.success_rate must be 1.0 for accepted samples")
        programs = _require_object(target_metrics.get("programs"), f"{target_path}.programs")
        if set(programs) != set(program_order):
            _fail(f"{target_path}.programs differs from the preregistered program set")
        for program_index, program in enumerate(program_order):
            metric_path = f"{target_path}.programs.{program}"
            metrics = _require_object(programs[program], metric_path)
            supplied_p50 = _scenario_duration_metric(metrics.get("p50_ms"), f"{metric_path}.p50_ms")
            supplied_p95 = _scenario_duration_metric(metrics.get("p95_ms"), f"{metric_path}.p95_ms")
            expected_p50 = _scenario_percentile(timings[target][program], 0.50)
            expected_p95 = _scenario_percentile(timings[target][program], 0.95)
            if not math.isclose(supplied_p50, expected_p50, abs_tol=1e-12) or not math.isclose(
                supplied_p95, expected_p95, abs_tol=1e-12
            ):
                _fail(f"{metric_path} p50/p95 differs from raw scenario samples")
            display_programs[program_index][f"{target}_p50_ms"] = supplied_p50
            display_programs[program_index][f"{target}_p95_ms"] = supplied_p95
        makespan_metrics = _require_object(
            target_metrics.get("makespan_ms"), f"{target_path}.makespan_ms"
        )
        for quantile, key in ((0.50, "p50"), (0.95, "p95")):
            supplied = _scenario_duration_metric(makespan_metrics.get(key), f"{target_path}.makespan_ms.{key}")
            expected = _scenario_percentile(makespans[target], quantile)
            if not math.isclose(supplied, expected, abs_tol=1e-12):
                _fail(f"{target_path}.makespan_ms.{key} differs from raw scenario samples")

    functional_path = f"{item_path}.report.summary.functional_acceptance"
    functional = _require_object(report_summary.get("functional_acceptance"), functional_path)
    required_functional_fields = {
        "status", "required_target", "required_modules", "verified_boots", "boot_receipts",
    }
    if set(functional) != required_functional_fields:
        _fail(f"{functional_path} fields do not match the functional acceptance schema")
    functional_status = _scenario_string(functional["status"], f"{functional_path}.status")
    if functional_status not in {"passed", "unavailable"} or functional["required_target"] != "agentos":
        _fail(f"{functional_path} has an invalid status or required target")
    modules_raw = _require_list(functional["required_modules"], f"{functional_path}.required_modules")
    modules = [
        _scenario_string(module, f"{functional_path}.required_modules[{index}]", maximum=64)
        for index, module in enumerate(modules_raw)
    ]
    if not modules or len(modules) > MAX_SCENARIO_MODULES or len(modules) != len(set(modules)):
        _fail(f"{functional_path}.required_modules is empty, duplicated, or over the bound")
    verified_boots = _scenario_uint(
        functional["verified_boots"], f"{functional_path}.verified_boots",
        maximum=MAX_SCENARIO_SAMPLES,
    )
    boot_receipts = _require_list(functional["boot_receipts"], f"{functional_path}.boot_receipts")
    if functional_status == "passed":
        if verified_boots != len(samples) or len(boot_receipts) != len(samples):
            _fail(f"{functional_path} passed status does not cover every sample")
        receipt_sample_ids: set[str] = set()
        for receipt_index, raw_receipt in enumerate(boot_receipts):
            receipt_path = f"{functional_path}.boot_receipts[{receipt_index}]"
            receipt = _require_object(raw_receipt, receipt_path)
            if set(receipt) != {
                "sample_id", "challenge", "module_receipt_sha256", "binding_sha256",
                "raw_source_receipt_sha256",
            }:
                _fail(f"{receipt_path} fields do not match the functional boot receipt schema")
            receipt_sample_ids.add(_scenario_string(receipt["sample_id"], f"{receipt_path}.sample_id"))
            _scenario_string(receipt["challenge"], f"{receipt_path}.challenge", maximum=64)
            for field in ("module_receipt_sha256", "binding_sha256", "raw_source_receipt_sha256"):
                _scenario_sha256(receipt[field], f"{receipt_path}.{field}")
        if receipt_sample_ids != sample_ids:
            _fail(f"{functional_path}.boot_receipts do not cover every scenario sample exactly")
    elif verified_boots != 0 or boot_receipts:
        _fail(f"{functional_path} unavailable status must not claim verified boot receipts")
    expected_functional_status = {
        "pass": "passed",
        "unavailable": "unavailable",
    }.get(bound_scenario["functional_status"])
    if expected_functional_status is None or functional_status != expected_functional_status:
        _fail(f"{item_path} scenario functional status differs from the summary scenario")

    resource_stability = _normalize_scenario_resource_stability(
        report_summary, samples, item_path
    )
    paired_improvement = report_summary.get("paired_improvement")
    if bound_scenario["performance"] is not None and paired_improvement != bound_scenario["performance"]:
        _fail(f"{item_path} scenario report performance differs from the summary scenario")
    if bound_scenario["performance"] is not None:
        if paired_improvement.get("samples") != raw_paired_samples:
            _fail(f"{item_path} scenario performance samples differ from raw target timings")
        if not math.isclose(
            _require_number(
                paired_improvement.get("paired_success_rate"),
                f"{item_path}.report.summary.paired_improvement.paired_success_rate",
            ),
            report_success_rate,
            rel_tol=0,
            abs_tol=1e-15,
        ):
            _fail(f"{item_path} scenario performance success rate differs from raw summary")
    return {
        "evidence_id": item["id"],
        "scenario_id": scenario_id,
        "task_id": _task_id(bound_scenario["task"], f"{item_path}.scenario.task"),
        "status": report_status,
        "independent_boots": independent_boots,
        "programs": display_programs,
        "source_comparability": copy.deepcopy(source_comparability),
        "functional": {
            "status": functional_status,
            "required_modules": modules,
            "verified_boots": verified_boots,
        },
        "resource_stability": resource_stability,
        "outcomes": [
            {"key": key, "verified_pairs": len(samples), "status": "verified"}
            for key in SCENARIO_KEY_OUTCOMES
        ],
    }


def _verify_kernel_cost_sidecar(
    evidence_root: Path,
    summary: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    paths = {relative: evidence_root.joinpath(*relative.split("/")) for relative in KERNEL_COST_FILES}
    kernel_directory = evidence_root / "kernel-build"
    present = {
        relative
        for relative, path in paths.items()
        if path.exists() or path.is_symlink() or path_is_link(path)
    }
    directory_present = (
        kernel_directory.exists()
        or kernel_directory.is_symlink()
        or path_is_link(kernel_directory)
    )
    if not present and not directory_present:
        return None, []
    if present != set(KERNEL_COST_FILES):
        missing = sorted(set(KERNEL_COST_FILES) - present)
        _fail(f"kernel-cost sidecar is incomplete; missing={missing}")
    if path_is_link(kernel_directory) or not kernel_directory.is_dir():
        _fail("kernel-build must be a regular, non-link directory")

    expected_files = set(KERNEL_COST_FILES)
    expected_directories = {"kernel-build", "kernel-build/raw"}
    try:
        descendants = list(kernel_directory.rglob("*"))
    except OSError as error:
        _fail(f"cannot inventory kernel-build sidecar: {error}")
    for descendant in descendants:
        relative = descendant.relative_to(evidence_root).as_posix()
        if path_is_link(descendant):
            _fail(f"kernel-cost sidecar must not contain links: {relative}")
        if descendant.is_dir():
            if relative not in expected_directories:
                _fail(f"kernel-cost sidecar contains an unexpected directory: {relative}")
        elif descendant.is_file():
            if relative not in expected_files:
                _fail(f"kernel-cost sidecar contains an unexpected file: {relative}")
        else:
            _fail(f"kernel-cost sidecar contains a non-regular entry: {relative}")

    records: list[dict[str, Any]] = []
    file_data: dict[str, bytes] = {}
    for relative in KERNEL_COST_FILES:
        data = _read_evidence_file(
            evidence_root,
            tuple(relative.split("/")),
            f"kernel-cost[{relative}]",
        )
        file_data[relative] = data
        records.append({
            "id": f"kernel-cost:{relative}",
            "path": relative,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "receipt_bytes_checked": True,
            "marker_receipts_verified": 0,
        })

    config_path = paths["kernel-cost-config.json"]
    report_path = paths["kernel-cost-report.json"]
    fragment_path = paths["kernel-cost-fragment.json"]
    try:
        report, _environment, _build = verify_kernel_cost_portable(
            report_path, config_path, evidence_root
        )
        expected_fragment = build_kernel_cost_fragment(
            report_path, config_path, evidence_root
        )
        supplied_fragment = strict_json_loads(file_data["kernel-cost-fragment.json"])
    except (KernelCostError, OSError, UnicodeError, ValueError, RecursionError) as error:
        _fail(f"kernel-cost sidecar verification failed: {error}")
    if supplied_fragment != expected_fragment:
        _fail("kernel-cost fragment differs from verified portable evidence")

    fragment_run = _require_object(expected_fragment.get("run"), "kernel-cost.fragment.run")
    if (
        fragment_run.get("id") != summary["run"]["id"]
        or fragment_run.get("commit") != summary["run"].get("commit")
    ):
        _fail("kernel-cost fragment run identity differs from the evaluation summary")
    fragment_targets = _require_list(expected_fragment.get("targets"), "kernel-cost.fragment.targets")
    baseline_target = next(
        (target for target in fragment_targets if target.get("role") == "baseline"), None
    )
    treatment_target = next(
        (target for target in fragment_targets if target.get("role") == "treatment"), None
    )
    if baseline_target is None or treatment_target is None:
        _fail("kernel-cost fragment lacks baseline/treatment targets")
    report_targets = {
        target.get("id"): target
        for target in _require_list(report.get("targets"), "kernel-cost.report.targets")
        if isinstance(target, dict)
    }
    baseline_id = baseline_target["id"]
    treatment_id = treatment_target["id"]
    if baseline_id not in report_targets or treatment_id not in report_targets:
        _fail("kernel-cost report lacks baseline/treatment target measurements")

    metric_rows: list[dict[str, Any]] = []
    for metric_id, label in KERNEL_COST_METRICS:
        values: dict[str, int | None] = {}
        statuses: dict[str, str] = {}
        for role, target_id in (("baseline", baseline_id), ("agentos", treatment_id)):
            metrics = _require_list(
                report_targets[target_id].get("metrics"),
                f"kernel-cost.report.targets.{target_id}.metrics",
            )
            metric = next(
                (
                    item for item in metrics
                    if isinstance(item, dict) and item.get("id") == metric_id
                ),
                None,
            )
            if metric is None:
                _fail(f"kernel-cost report lacks {target_id}/{metric_id}")
            status = _require_string(
                metric.get("status"), f"kernel-cost.{target_id}.{metric_id}.status"
            )
            value = metric.get("value")
            if status == "measured":
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    _fail(f"kernel-cost {target_id}/{metric_id} has an invalid measured value")
            elif status not in {"unavailable", "failed"}:
                _fail(f"kernel-cost {target_id}/{metric_id} has an unknown status")
            elif value is not None:
                _fail(f"kernel-cost {target_id}/{metric_id} non-measured value must be null")
            statuses[role] = status
            values[role] = value
        measured = statuses == {"baseline": "measured", "agentos": "measured"}
        row_status = (
            "measured" if measured
            else "failed" if "failed" in statuses.values()
            else "unavailable"
        )
        metric_rows.append({
            "id": metric_id,
            "label": label,
            "unit": "bytes",
            "status": row_status,
            "baseline": values["baseline"],
            "agentos": values["agentos"],
            "delta": (
                int(values["agentos"]) - int(values["baseline"])
                if measured
                else None
            ),
        })
    fragment_guardrails = _require_list(
        expected_fragment.get("guardrails"), "kernel-cost.fragment.guardrails"
    )
    if len(fragment_guardrails) != len(KERNEL_COST_GUARDRAILS):
        _fail("kernel-cost fragment guardrail count differs from policy")
    guardrail_rows: list[dict[str, Any]] = []
    for index, ((guardrail_id, label), raw_guardrail) in enumerate(
        zip(KERNEL_COST_GUARDRAILS, fragment_guardrails)
    ):
        path = f"kernel-cost.fragment.guardrails[{index}]"
        guardrail = _require_object(raw_guardrail, path)
        if guardrail.get("id") != guardrail_id or guardrail.get("status") != "measured":
            _fail(f"{path} identity/status differs from policy")
        value = guardrail.get("value")
        limit = guardrail.get("limit")
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            or value > limit
        ):
            _fail(f"{path} value/limit is invalid")
        guardrail_rows.append({
            "id": guardrail_id,
            "label": label,
            "unit": "bytes",
            "status": "measured",
            "value": value,
            "limit": limit,
            "headroom": limit - value,
            "source": guardrail.get("source"),
            "source_command_sha256": guardrail.get("source_command_sha256"),
        })
    return {
        "status": fragment_run["status"],
        "run_id": fragment_run["id"],
        "commit": fragment_run["commit"],
        "baseline": {"id": baseline_id, "label": baseline_target["label"]},
        "agentos": {"id": treatment_id, "label": treatment_target["label"]},
        "metrics": metric_rows,
        "guardrails": guardrail_rows,
        "evidence_file_count": len(records),
    }, records


def _campaign_environment_sha256(campaign: dict[str, Any]) -> str:
    encoded = json.dumps(
        campaign["environment"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_campaign_environment(
    evidence_root: Path,
    summary: dict[str, Any],
    *,
    contract_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Recover displayable Host identity only through the sealed campaign chain."""

    declared_campaign_sha256 = summary["run"].get("campaign_sha256")
    if declared_campaign_sha256 is None:
        return None, None
    if (
        not isinstance(declared_campaign_sha256, str)
        or not SHA256.fullmatch(declared_campaign_sha256)
    ):
        _fail("run.campaign_sha256 must be lowercase SHA-256 when provided")

    run_plan_item = next(
        item
        for item in summary["evidence"]
        if item["kind"] == "evaluation-run-plan"
    )
    receipt = _require_object(run_plan_item.get("receipt"), "run-plan receipt")
    expected_receipt_fields = {"run_id", "environment_sha256", "campaign_sha256"}
    if set(receipt) != expected_receipt_fields:
        _fail("run-plan receipt fields do not match the campaign binding schema")

    run_plan_parts = _canonical_evidence_reference(
        run_plan_item["path"], "run-plan evidence.path"
    )
    run_plan_path = evidence_root.joinpath(*run_plan_parts)
    plan_bytes = _read_evidence_file(
        evidence_root, run_plan_parts, "run-plan evidence.path"
    )
    try:
        plan, plan_sha256 = load_run_plan(run_plan_path)
    except (EvaluationContractError, OSError, UnicodeError, ValueError) as error:
        _fail(f"campaign environment requires a strict run plan: {error}")
    if (
        plan_sha256 != hashlib.sha256(plan_bytes).hexdigest()
        or plan_sha256 != run_plan_item["sha256"]
        or plan_sha256 != summary["run"]["run_plan_sha256"]
    ):
        _fail("campaign environment run-plan bytes changed or are not summary-bound")

    campaign_data = _read_evidence_file(
        evidence_root, ("campaign.json",), "campaign.path"
    )
    campaign_sha256 = hashlib.sha256(campaign_data).hexdigest()
    if (
        campaign_sha256 != declared_campaign_sha256
        or campaign_sha256 != plan["campaign_sha256"]
        or receipt["campaign_sha256"] != campaign_sha256
    ):
        _fail("campaign SHA-256 differs from the summary, run plan, or receipt")
    try:
        campaign = strict_json_loads(campaign_data)
        if not isinstance(campaign, dict):
            raise CampaignError("campaign must be an object")
        validate_campaign(campaign, contract_root=contract_root)
    except (CampaignError, UnicodeError, ValueError) as error:
        _fail(f"campaign platform proof is invalid: {error}")
    if campaign["phase"] != "collected":
        _fail("campaign platform proof requires a collected campaign")

    run = campaign["run"]
    if (
        run["id"] != summary["run"]["id"]
        or run["id"] != plan["run_id"]
        or receipt["run_id"] != run["id"]
        or run["commit"] != summary["run"].get("commit")
        or {item["commit"] for item in plan["logs"]} != {run["commit"]}
    ):
        _fail("campaign run identity differs from the summary or run plan")

    environment_sha256 = _campaign_environment_sha256(campaign)
    if (
        environment_sha256 != plan["environment_sha256"]
        or environment_sha256 != summary["run"].get("environment_sha256")
        or environment_sha256 != receipt["environment_sha256"]
    ):
        _fail("campaign environment SHA-256 differs from its receipts")

    platform = campaign["platform"]
    for label in ("compiler", "qemu"):
        platform_tool = platform["tools"][label]
        campaign_tool = campaign["environment"][label]
        if any(
            platform_tool[field] != campaign_tool[field]
            for field in ("path", "sha256", "version")
        ):
            _fail(f"campaign {label} identity differs from the platform proof")

    hardware = platform["hardware"]
    detail = {
        "status": "verified",
        "source_commit": run["commit"],
        "execution_domain": platform["entry_domain"],
        "hardware_source": hardware["source"],
        "cpu_model": hardware["cpu_model"],
        "logical_cpu_count": hardware["logical_cpu_count"],
        "memory_total_bytes": hardware["memory_total_bytes"],
        "gcc_version": platform["tools"]["compiler"]["version"],
        "qemu_version": platform["tools"]["qemu"]["version"],
        "campaign_sha256": campaign_sha256,
        "environment_sha256": environment_sha256,
        "path": "campaign.json",
    }
    record = {
        "id": "campaign-platform-proof",
        "path": "campaign.json",
        "bytes": len(campaign_data),
        "sha256": campaign_sha256,
        "receipt_bytes_checked": False,
        "marker_receipts_verified": 0,
        "campaign_binding_checked": True,
    }
    return detail, record


def _compatibility_file_receipt(path: Path) -> tuple[int, str]:
    safe_path = require_regular_file(
        path, maximum_bytes=MAX_COMPATIBILITY_EVIDENCE_FILE_BYTES
    )
    expected = safe_path.lstat()
    digest = hashlib.sha256()
    total = 0
    with safe_path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
        ):
            _fail("compatibility evidence changed before it was hashed")
        while chunk := handle.read(1 << 20):
            total += len(chunk)
            if total > MAX_COMPATIBILITY_EVIDENCE_FILE_BYTES:
                _fail("compatibility evidence file exceeds its byte limit")
            digest.update(chunk)
        final = os.fstat(handle.fileno())
    current = require_regular_file(
        safe_path, maximum_bytes=MAX_COMPATIBILITY_EVIDENCE_FILE_BYTES
    ).lstat()
    expected_identity = (
        expected.st_dev,
        expected.st_ino,
        expected.st_size,
        expected.st_mtime_ns,
    )
    if (
        total != expected.st_size
        or (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        )
        != expected_identity
        or (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        )
        != expected_identity
    ):
        _fail("compatibility evidence changed while it was hashed")
    return total, digest.hexdigest()


def _compatibility_tree_receipt(root: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        files = walk_regular_files_no_links(
            root,
            max_files=MAX_COMPATIBILITY_EVIDENCE_FILES,
            max_directories=MAX_COMPATIBILITY_EVIDENCE_DIRECTORIES,
            max_total_bytes=MAX_COMPATIBILITY_EVIDENCE_TOTAL_BYTES,
            max_depth=MAX_COMPATIBILITY_EVIDENCE_DEPTH,
        )
    except (OSError, ValueError) as error:
        _fail(f"compatibility evidence tree is unsafe or exceeds its budget: {error}")
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    for path in files:
        size, sha256 = _compatibility_file_receipt(path)
        total_bytes += size
        if total_bytes > MAX_COMPATIBILITY_EVIDENCE_TOTAL_BYTES:
            _fail("compatibility evidence tree exceeds its byte limit")
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": sha256,
            }
        )
    if not entries:
        _fail("compatibility evidence tree is empty")
    return _binding_sha256(entries, "compatibility-evidence-tree-v1"), entries


def _verify_compatibility_sidecar(
    evidence_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    root = evidence_root / "compatibility"
    if not root.exists() and not path_is_link(root):
        return None, None
    before_sha256, before_entries = _compatibility_tree_receipt(root)
    summary_path = root / "compatibility-overhead.json"
    try:
        value = verify_compatibility_artifacts(
            summary_path, micro_manifest=evidence_root / "campaign.json"
        )
    except (
        CompatibilityContractError,
        CompatibilityRunError,
        OSError,
        ValueError,
    ) as error:
        _fail(f"compatibility-overhead evidence is invalid: {error}")
    after_sha256, after_entries = _compatibility_tree_receipt(root)
    if before_sha256 != after_sha256 or before_entries != after_entries:
        _fail("compatibility evidence changed while it was replayed")
    summary = value["summary"]
    metrics = summary["metrics"]
    normalized_metrics: dict[str, dict[str, Any]] = {}
    for spec in COMPATIBILITY_METRICS:
        metric = str(spec["id"])
        normalized = {
            field: metrics[metric][field]
            for field in (
                "paired_boots",
                "operation_unit",
                "plain_median_microseconds_per_operation",
                "agentos_median_microseconds_per_operation",
                "median_agentos_over_plain_ratio",
                "pairs",
            )
        }
        if "workload" in spec:
            normalized["workload"] = copy.deepcopy(metrics[metric]["workload"])
            normalized["attribution"] = metrics[metric]["attribution"]
        normalized_metrics[metric] = normalized
    fragment = {
        "status": "ready",
        "claim_scope": summary["claim_scope"],
        "aggregate_score": summary["aggregate_score"],
        "aggregate_score_forbidden": summary["aggregate_score_forbidden"],
        "workload_equivalence": copy.deepcopy(summary["workload_equivalence"]),
        "measurement_state": copy.deepcopy(value["source"]["measurement_state"]),
        "metrics": normalized_metrics,
        "artifact_tree_sha256": after_sha256,
        "artifact_file_count": len(after_entries),
    }
    summary_data = summary_path.read_bytes()
    summary_entry = next(
        item for item in after_entries if item["path"] == "compatibility-overhead.json"
    )
    if (
        len(summary_data) != summary_entry["bytes"]
        or hashlib.sha256(summary_data).hexdigest() != summary_entry["sha256"]
    ):
        _fail("compatibility summary changed after tree replay")
    record = {
        "id": "compatibility-overhead",
        "path": "compatibility/compatibility-overhead.json",
        "bytes": len(summary_data),
        "sha256": summary_entry["sha256"],
        "receipt_bytes_checked": True,
        "marker_receipts_verified": 0,
        "compatibility_tree_sha256": after_sha256,
        "compatibility_files_verified": len(after_entries),
    }
    return fragment, record


def _verify_evidence_files(
    evidence_root: Path,
    summary: dict[str, Any],
    source_summary: bytes,
    *,
    contract_root: Path,
    measurement_source_tree: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    records: list[dict[str, Any]] = []
    scenario_details: list[dict[str, Any]] = []
    marker_count = 0
    diagnostic_provenance_count = 0
    diagnostic_samples_by_evidence: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for benchmark_index, benchmark in enumerate(summary["benchmarks"]):
        for diagnostic_index, diagnostic in enumerate(benchmark["diagnostics"]):
            for sample_index, sample in enumerate(diagnostic["samples"]):
                sample_path = (
                    f"benchmarks[{benchmark_index}].diagnostics[{diagnostic_index}]"
                    f".samples[{sample_index}]"
                )
                diagnostic_samples_by_evidence.setdefault(sample["evidence_id"], []).append(
                    (sample_path, sample)
                )
    measurement_source_receipt: object | None = None
    for index, item in enumerate(summary["evidence"]):
        item_path = f"evidence[{index}]"
        parts = _canonical_evidence_reference(item["path"], f"{item_path}.path")
        data = _read_evidence_file(evidence_root, parts, f"{item_path}.path")
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if actual_sha256 != item["sha256"]:
            _fail(
                f"{item_path}.sha256 does not match the evidence file "
                f"{item['path']!r}"
            )
        diagnostic_samples = diagnostic_samples_by_evidence.get(item["id"], [])
        diagnostic_provenance_verified = 0
        if diagnostic_samples:
            try:
                source_lines = data.decode("utf-8", errors="strict").splitlines()
            except UnicodeDecodeError as error:
                _fail(f"{item_path} diagnostic provenance requires strict UTF-8 evidence: {error}")
            for sample_path, sample in diagnostic_samples:
                if sample["source_log_sha256"] != actual_sha256:
                    _fail(f"{sample_path}.source_log_sha256 differs from the evidence bytes")
                source_line = sample["source_line"]
                if source_line > len(source_lines):
                    _fail(f"{sample_path}.source_line references a missing evidence line")
                actual_marker_sha256 = hashlib.sha256(
                    source_lines[source_line - 1].encode("utf-8")
                ).hexdigest()
                if sample["source_marker_sha256"] != actual_marker_sha256:
                    _fail(f"{sample_path}.source_marker_sha256 differs from the evidence line")
                diagnostic_provenance_verified += 1
            diagnostic_provenance_count += diagnostic_provenance_verified
        if item["kind"] == "research-platform-scenario" and item["status"] == "verified":
            if measurement_source_receipt is None:
                receipt_path = evidence_root / "measurement-source-receipt.json"
                try:
                    measurement_source_receipt = read_strict_json(
                        require_regular_file(receipt_path)
                    )
                except (OSError, UnicodeError, ValueError) as error:
                    _fail(
                        "verified Task 6 evidence requires a portable "
                        f"measurement-source receipt: {error}"
                    )
            scenario_details.append(
                _extract_scenario_detail(
                    data,
                    item,
                    item_path,
                    summary,
                    source_tree=measurement_source_tree,
                    measurement_source_receipt=measurement_source_receipt,
                )
            )

        receipt = item.get("receipt")
        receipt_bytes_checked = False
        verified_markers = 0
        if receipt is not None:
            receipt = _require_object(receipt, f"{item_path}.receipt")
            if "bytes" in receipt and receipt["bytes"] is not None:
                expected_bytes = receipt["bytes"]
                if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
                    _fail(f"{item_path}.receipt.bytes must be a non-negative integer or null")
                if expected_bytes != len(data):
                    _fail(f"{item_path}.receipt.bytes does not match the evidence file")
                receipt_bytes_checked = True

            has_lines = "line_numbers" in receipt
            has_hashes = "marker_sha256s" in receipt
            if has_lines != has_hashes:
                _fail(
                    f"{item_path}.receipt must provide line_numbers and marker_sha256s together"
                )
            if has_lines:
                line_numbers = _require_list(
                    receipt["line_numbers"], f"{item_path}.receipt.line_numbers"
                )
                marker_hashes = _require_list(
                    receipt["marker_sha256s"], f"{item_path}.receipt.marker_sha256s"
                )
                if len(line_numbers) != len(marker_hashes):
                    _fail(
                        f"{item_path}.receipt line_numbers and marker_sha256s lengths differ"
                    )
                previous = 0
                lines: list[str] = []
                if line_numbers:
                    try:
                        lines = data.decode("utf-8", errors="strict").splitlines()
                    except UnicodeDecodeError as error:
                        _fail(f"{item_path} marker receipt requires strict UTF-8 evidence: {error}")
                for marker_index, (line_number, marker_sha256) in enumerate(zip(line_numbers, marker_hashes)):
                    marker_path = f"{item_path}.receipt.marker_sha256s[{marker_index}]"
                    if (
                        isinstance(line_number, bool)
                        or not isinstance(line_number, int)
                        or line_number <= previous
                    ):
                        _fail(
                            f"{item_path}.receipt.line_numbers must be strictly increasing positive integers"
                        )
                    previous = line_number
                    if line_number > len(lines):
                        _fail(f"{item_path}.receipt.line_numbers references a missing line")
                    if not isinstance(marker_sha256, str) or not SHA256.fullmatch(marker_sha256):
                        _fail(f"{marker_path} must be lowercase SHA-256")
                    actual_marker_sha256 = hashlib.sha256(
                        lines[line_number - 1].encode("utf-8")
                    ).hexdigest()
                    if marker_sha256 != actual_marker_sha256:
                        _fail(f"{marker_path} does not match the referenced evidence line")
                verified_markers = len(line_numbers)
                marker_count += verified_markers

        records.append({
            "id": item["id"],
            "path": item["path"],
            "bytes": len(data),
            "sha256": actual_sha256,
            "receipt_bytes_checked": receipt_bytes_checked,
            "marker_receipts_verified": verified_markers,
            "diagnostic_provenance_verified": diagnostic_provenance_verified,
        })

    run_plan_records = [
        record
        for record in records
        if next(
            item for item in summary["evidence"] if item["id"] == record["id"]
        )["kind"] == "evaluation-run-plan"
    ]
    if len(run_plan_records) != 1 or run_plan_records[0]["sha256"] != summary["run"]["run_plan_sha256"]:
        _fail("run.run_plan_sha256 is not bound to the actual run-plan evidence bytes")

    campaign_environment, campaign_record = _verify_campaign_environment(
        evidence_root, summary, contract_root=contract_root
    )
    if campaign_record is not None:
        records.append(campaign_record)
    kernel_cost, kernel_cost_records = _verify_kernel_cost_sidecar(
        evidence_root, summary
    )
    records.extend(kernel_cost_records)
    compatibility_overhead, compatibility_record = _verify_compatibility_sidecar(
        evidence_root
    )
    if compatibility_record is not None:
        records.append(compatibility_record)
    records.sort(key=lambda item: item["id"])
    evidence_set_sha256 = _binding_sha256(records, "dashboard-evidence-set-v1")
    verification = {
        "schema_version": 1,
        "kind": "agentos-dashboard-evidence-verification",
        "source_summary": {
            "bytes": len(source_summary),
            "sha256": hashlib.sha256(source_summary).hexdigest(),
        },
        "evidence_root": ".",
        "verified_evidence_count": len(records),
        "verified_marker_count": marker_count,
        "verified_diagnostic_provenance_count": diagnostic_provenance_count,
        "kernel_cost": {
            "status": (
                "unavailable" if kernel_cost is None
                else "verified" if kernel_cost["status"] == "measured"
                else kernel_cost["status"]
            ),
            "evidence_file_count": len(kernel_cost_records),
        },
        "compatibility_overhead": (
            {"status": "unavailable"}
            if compatibility_overhead is None
            else compatibility_overhead
        ),
        "campaign_environment": (
            {"status": "unavailable"}
            if campaign_environment is None
            else copy.deepcopy(campaign_environment)
        ),
        "evidence_set_sha256": evidence_set_sha256,
        "evidence": records,
    }
    scenario_details.sort(key=lambda detail: detail["scenario_id"])
    scenario_ids = [detail["scenario_id"] for detail in scenario_details]
    if len(scenario_ids) != len(set(scenario_ids)):
        _fail("verified scenario reports repeat a scenario_id")
    return verification, scenario_details, kernel_cost, campaign_environment


def _has_scientific_result(summary: dict[str, Any]) -> bool:
    return any(
        claim["status"] in {"supported", "not_supported"}
        for claim in summary["claims"]
    ) or any(
        scenario["performance_status"] in {"supported", "regressed", "inconclusive"}
        for scenario in summary["scenarios"]
    ) or any(
        item["kind"] == "research-platform-scenario"
        and item["status"] == "verified"
        for item in summary["evidence"]
    ) or any(
        item.get("status") == "measured" and bool(item.get("boots"))
        for item in summary["methodology"].get(
            "supplementary_evaluations", []
        )
    )


def _replay_scientific_contract(
    evidence_root: Path,
    summary_path: Path,
    summary: dict[str, Any],
    *,
    contract_root: Path,
    measurement_source_tree: Path,
) -> None:
    """Rebuild positive and negative measured results before rendering."""

    if not _has_scientific_result(summary):
        return
    repository_suite = contract_root / "ci" / "evaluation-suite.json"
    suite_path = evidence_root / "suite.json"
    if not suite_path.exists():
        suite_path = repository_suite
    required = {
        "suite": suite_path,
        "run plan": evidence_root / "run-plan.json",
        "metrics": evidence_root / "metrics.jsonl",
    }
    for label, path in required.items():
        if not path.is_file() or path_is_link(path):
            _fail(f"measured scientific results require a regular {label} file for contract replay")
    raw_root = evidence_root / "raw"
    if not raw_root.is_dir() or path_is_link(raw_root):
        _fail("measured scientific results require a regular raw Guest evidence directory")
    scenario_report = evidence_root / "scenario" / "report.json"
    scenario_plan = evidence_root / "scenario" / "scenario-plan.json"
    scenario_args: tuple[Path | None, Path | None]
    if scenario_report.exists() or scenario_plan.exists():
        if (
            not scenario_report.is_file()
            or not scenario_plan.is_file()
            or path_is_link(scenario_report)
            or path_is_link(scenario_plan)
        ):
            _fail("scenario contract replay requires a complete non-link plan/report pair")
        scenario_args = (scenario_report, scenario_plan)
    else:
        scenario_args = (None, None)
    try:
        verify_evaluation_contract(
            suite_path,
            required["run plan"],
            raw_root,
            summary_path,
            required["metrics"],
            scenario_args[0],
            scenario_args[1],
            contract_root=contract_root,
            scenario_source_tree=measurement_source_tree,
        )
    except (EvaluationContractError, OSError, UnicodeError, ValueError) as error:
        _fail(f"raw Guest contract replay failed: {error}")


def _value(value: float) -> str:
    return f"{value:.6g}"


def _estimate_map(benchmark: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["target_id"], str(item["load"])): item for item in benchmark["estimates"]}


def _evidence_sources(ids: Iterable[str], evidence: dict[str, dict[str, Any]]) -> str:
    values = []
    for evidence_id in ids:
        item = evidence[evidence_id]
        values.append(str(item.get("path") or evidence_id))
    return "; ".join(values) if values else "unavailable"


def _evidence_entry_link(ids: Iterable[str]) -> str:
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        return "暂无证据"
    if len(unique_ids) == 1:
        return (
            f'<button class="evidence-link" type="button" '
            f'data-evidence-ref="{_h(unique_ids[0])}">查看证据</button>'
        )
    buttons = "".join(
        f'<button class="evidence-link" type="button" data-evidence-ref="{_h(evidence_id)}">'
        f'证据 {index}</button>'
        for index, evidence_id in enumerate(unique_ids, 1)
    )
    return (
        '<details class="evidence-menu"><summary>'
        f'{len(unique_ids)} 份证据</summary><div>{buttons}</div></details>'
    )


def _chart(benchmark: dict[str, Any], targets: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]], *, suffix: str) -> str:
    if benchmark["status"] != "measured":
        return (
            '<section class="unavailable-block" aria-label="无可用图表">'
            '<strong>unavailable</strong><span>当前没有满足证据合同的测量，不绘制推断图。</span></section>'
        )
    estimates = _estimate_map(benchmark)
    baseline = benchmark["baseline"]
    treatment = benchmark["treatment"]
    baseline_label = targets[baseline]["label"]
    treatment_label = targets[treatment]["label"]
    loads = [str(value) for value in benchmark["loads"]]
    paired_samples = {
        str(pair["load"]): sorted(pair["samples"], key=lambda sample: str(sample["trial"]))
        for pair in benchmark["paired"]
        if pair["status"] == "measured"
    }
    values = [float(item[key]) for item in estimates.values() for key in ("lower", "upper")]
    values.extend(
        float(sample[field])
        for samples in paired_samples.values()
        for sample in samples
        for field in ("baseline_value", "treatment_value")
    )
    low, high = min(values), max(values)
    observed_nonnegative = low >= 0
    if low == high:
        pad = abs(low) * 0.1 or 1.0
        low -= pad
        high += pad
    else:
        pad = (high - low) * 0.08
        low -= pad
        high += pad
    if observed_nonnegative:
        low = max(0.0, low)
    plot_left, plot_right = 176.0, 748.0
    row_height = 104
    height = 70 + len(loads) * row_height

    def x(value: float) -> float:
        return plot_left + (value - low) / (high - low) * (plot_right - plot_left)

    svg: list[str] = [
        f'<svg viewBox="0 0 800 {height}" role="img" aria-labelledby="chart-title-{_h(suffix)} chart-desc-{_h(suffix)}">',
        f'<title id="chart-title-{_h(suffix)}">{_h(benchmark["label"])}逐 boot 配对与区间图</title>',
        f'<desc id="chart-desc-{_h(suffix)}">单位 {_h(benchmark["unit"])}；小圆点和浅色连线展示每个独立 boot 的 {_h(baseline_label)} 与 {_h(treatment_label)} 原始配对值，粗区间和大圆点展示汇总估计。</desc>',
        f'<line class="axis" x1="{plot_left}" y1="36" x2="{plot_right}" y2="36" />',
        f'<text class="axis-label" x="{plot_left}" y="22">{_h(_value(low))}</text>',
        f'<text class="axis-label" x="{plot_right}" y="22" text-anchor="end">{_h(_value(high))} {_h(benchmark["unit"])}</text>',
    ]
    ns: list[int] = []
    for row, load in enumerate(loads):
        row_top = 64 + row * row_height
        y_base = row_top + 26
        svg.append(f'<text class="load-label" x="8" y="{row_top}">负载 {_h(load)}</text>')
        first_x = x(float(estimates[(baseline, load)]["value"]))
        second_x = x(float(estimates[(treatment, load)]["value"]))
        raw_pairs = paired_samples.get(load, [])
        for sample_index, sample in enumerate(raw_pairs):
            jitter = 0.0 if len(raw_pairs) == 1 else -7.0 + 14.0 * sample_index / (len(raw_pairs) - 1)
            baseline_x = x(float(sample["baseline_value"]))
            treatment_x = x(float(sample["treatment_value"]))
            baseline_y = y_base + jitter
            treatment_y = y_base + 30 + jitter
            trial = str(sample["trial"])
            svg.extend(
                [
                    f'<g class="raw-pair" data-trial="{_h(trial)}">',
                    f'<title>boot {_h(trial)}：{_h(baseline_label)} {_h(_value(float(sample["baseline_value"])))}，{_h(treatment_label)} {_h(_value(float(sample["treatment_value"])))}</title>',
                    f'<line class="raw-pair-link" x1="{baseline_x:.2f}" y1="{baseline_y:.2f}" x2="{treatment_x:.2f}" y2="{treatment_y:.2f}" />',
                    f'<circle class="raw-pair-dot raw-pair-dot--baseline" cx="{baseline_x:.2f}" cy="{baseline_y:.2f}" r="2.5" />',
                    f'<circle class="raw-pair-dot raw-pair-dot--treatment" cx="{treatment_x:.2f}" cy="{treatment_y:.2f}" r="2.5" />',
                    "</g>",
                ]
            )
        svg.append(
            f'<line class="estimate-pair-link" x1="{first_x:.2f}" y1="{y_base}" '
            f'x2="{second_x:.2f}" y2="{y_base + 30}" />'
        )
        for offset, target_id, css_class in ((0, baseline, "baseline"), (30, treatment, "treatment")):
            estimate = estimates[(target_id, load)]
            ns.append(int(estimate["n"]))
            y = y_base + offset
            left = x(float(estimate["lower"]))
            center = x(float(estimate["value"]))
            right = x(float(estimate["upper"]))
            label = targets[target_id]["label"]
            svg.extend(
                [
                    f'<line class="interval interval--{css_class}" x1="{left:.2f}" y1="{y}" x2="{right:.2f}" y2="{y}" />',
                    f'<circle class="dot dot--{css_class}" cx="{center:.2f}" cy="{y}" r="5" />',
                    f'<text class="series-label" x="168" y="{y + 4}" text-anchor="end">{_h(label)}</text>',
                ]
            )
    svg.append("</svg>")
    raw_pair_count = sum(len(samples) for samples in paired_samples.values())
    n_text = str(ns[0]) if ns and len(set(ns)) == 1 else f"{min(ns)}-{max(ns)}"
    source = _evidence_sources(benchmark["evidence_ids"], evidence)
    evidence_buttons = _evidence_entry_link(benchmark["evidence_ids"])
    return (
        f'<figure class="interval-chart" data-chart-unit="{_h(benchmark["unit"])}" '
        f'data-chart-n="{_h(n_text)}" data-raw-pairs="{raw_pair_count}" data-chart-source="{_h(source)}">'
        f'<div class="chart-scroll" role="region" tabindex="0" aria-label="{_h(benchmark["label"])}图表，可横向滚动">'
        + "".join(svg)
        + "</div>"
        + '<figcaption><span><strong>单位</strong> '
        + _h(benchmark["unit"])
        + '</span><span><strong>n</strong> '
        + _h(n_text)
        + '</span><span><strong>原始配对</strong> '
        + _h(raw_pair_count)
        + " 个独立 boot"
        + '</span><div class="caption-source"><strong>来源</strong> '
        + evidence_buttons
        + '</div></figcaption></figure>'
    )


def _work_receipts_table(
    benchmark: dict[str, Any], targets: dict[str, dict[str, Any]]
) -> str:
    if (
        benchmark["id"] not in FILE_QUERY_EXPERIMENTS
        or benchmark["status"] != "measured"
        or not benchmark.get("samples")
    ):
        return ""

    def distribution(values: list[int]) -> str:
        ordered = sorted(values)
        median = int(statistics.median(ordered))
        if ordered[0] == ordered[-1]:
            return str(ordered[0])
        return f"{ordered[0]}-{ordered[-1]} (median {median})"

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sample in benchmark["samples"]:
        grouped.setdefault((str(sample["load"]), sample["target_id"]), []).append(sample)
    rows: list[str] = []
    for load in (str(value) for value in benchmark["loads"]):
        for target_id in (benchmark["baseline"], benchmark["treatment"]):
            samples = grouped.get((load, target_id), [])
            if not samples:
                continue
            first = samples[0]
            role = "baseline" if target_id == benchmark["baseline"] else "treatment"
            rows.append(
                '<tr>'
                f'<th scope="row">{_h(load)}</th>'
                f'<td><strong>{_h(targets[target_id]["label"])}</strong><br><code>{role}</code></td>'
                f'<td>{_h(len(samples))}</td>'
                f'<td>{_h(first["operations"])}</td>'
                f'<td>{_h(first["dataset_size"])}</td>'
                f'<td>{_h(distribution([int(item["work_units"]) for item in samples]))}</td>'
                f'<td>{_h(distribution([int(item["records_examined"]) for item in samples]))}</td>'
                f'<td>{_h(first["result_items"])}</td>'
                '<td><code>rebuild=0 / cache-hit=0</code></td>'
                '</tr>'
            )
    if not rows:
        return ""
    note = (
        "主对照 baseline 的工作量必须精确等于 N x operations，证明每次均检查全部实际路径。"
        if benchmark["id"] == FILE_QUERY_PATH_INDEX
        else "消融 baseline 的工作量固定为 512 x operations；检查到的有效记录仍按实际 N 披露。"
        if benchmark["id"] == FILE_QUERY_TABLE_ABLATION
        else "工作回执按独立 boot 聚合；原始 inner-pair 数值仍由合同逐项重放。"
    )
    return (
        '<section class="diagnostic-block work-receipt-block">'
        '<h4>实际工作量回执</h4>'
        f'<p class="diagnostic-note">{_h(note)} 不要求实验结果预先胜出。</p>'
        '<div class="table-scroll" role="region" tabindex="0" '
        'aria-label="实际工作量回执表，可横向滚动"><table class="diagnostic-table"><thead><tr>'
        '<th>负载</th><th>路径</th><th>独立 boot</th><th>每 pair 操作数</th><th>实际 N</th>'
        '<th>工作单元</th><th>检查记录</th><th>结构化结果</th><th>计时护栏</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _diagnostics_table(benchmark: dict[str, Any], *, heading_level: int = 4) -> str:
    diagnostics = benchmark.get("diagnostics", [])
    if not diagnostics:
        return ""

    def stat(value: Any, unit: str) -> str:
        if value is None:
            return "unavailable"
        return (
            f'<span class="diagnostic-value">中位数 {_h(_value(float(value["median"])))} {_h(unit)}</span>'
            f'<span>p95 {_h(_value(float(value["p95"])))} · n={_h(value["n"])}</span>'
        )

    rows = []
    for item in diagnostics:
        evidence_ids = list(dict.fromkeys(sample["evidence_id"] for sample in item["samples"]))
        evidence_links = _evidence_entry_link(evidence_ids)
        cache = " / ".join(item["cache_states"]) if item["cache_states"] else "unavailable"
        rows.append(
            '<tr>'
            f'<th scope="row">{_h(item["load"])}</th><td>{_status(item["status"])}</td>'
            f'<td><code>{_h(cache)}</code></td>'
            f'<td>{stat(item["duration_us"], item["unit"])}</td>'
            f'<td>{stat(item["work_units"], "work units")}</td>'
            f'<td>{stat(item["index_rebuild_records"], "records")}</td>'
            f'<td>{stat(item["result_cache_hits"], "hits")}</td>'
            f'<td>{evidence_links}</td></tr>'
        )
    heading = min(max(heading_level, 2), 6)
    return (
        '<section class="diagnostic-block">'
        f'<h{heading}>索引准备成本与缓存状态</h{heading}>'
        '<p class="diagnostic-note">独立诊断仅支持索引准备状态披露，不参与 headline 判定；'
        'ready-index 护栏来自实际计时样本中的 index_rebuild_records=0 与 result_cache_hits=0。</p>'
        '<div class="table-scroll" role="region" tabindex="0" '
        'aria-label="索引准备成本与缓存状态表，可横向滚动"><table class="diagnostic-table"><thead><tr>'
        '<th>负载</th><th>状态</th><th>Cache</th><th>准备耗时</th><th>工作量</th>'
        '<th>重建记录</th><th>结果缓存命中</th><th>原始证据</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _evidence_boot_ids(
    ids: Iterable[str], evidence: dict[str, dict[str, Any]]
) -> set[str]:
    boot_ids: set[str] = set()
    for evidence_id in ids:
        item = evidence.get(evidence_id)
        receipt = item.get("receipt") if isinstance(item, dict) else None
        boot_id = receipt.get("boot_id") if isinstance(receipt, dict) else None
        if isinstance(boot_id, str) and boot_id:
            boot_ids.add(boot_id)
    return boot_ids


def _scenario_sample_count(
    scenario: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> tuple[int, str]:
    performance = scenario.get("performance")
    if isinstance(performance, dict):
        return int(performance["n"]), "独立启动"
    boot_count = len(_evidence_boot_ids(scenario.get("evidence_ids", []), evidence))
    if boot_count:
        return boot_count, "独立启动"
    return len(set(scenario.get("evidence_ids", []))), "证据文件"


def _task_matrix(
    scenarios: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]
) -> str:
    cells: list[str] = []
    for task in TASK_IDS:
        task_items = [item for item in scenarios if _task_id(item["task"], "scenario.task") == task]
        if not task_items:
            detail = "尚无动态样本"
            sample_text = "0/0"
        else:
            sample_counts: list[tuple[int, str]] = []
            for item in task_items:
                sample_counts.append(_scenario_sample_count(item, evidence))
            sample_count, sample_kind = max(
                sample_counts, key=lambda item: item[0], default=(0, "证据文件")
            )
            functional = {item["functional_status"] for item in task_items}
            if functional == {"pass"}:
                sample_text = f"{sample_count}/{sample_count}" if sample_count else "0/0"
                detail = f"{sample_kind}结果一致" if sample_count else "尚无动态样本"
            elif "fail" in functional:
                sample_text = f"{sample_count} 组" if sample_count else "0/0"
                detail = f"{sample_kind}出现功能差异"
            else:
                sample_text = f"{sample_count} 组" if sample_count else "0/0"
                detail = "尚未完成动态测量"
        number = task.removeprefix("task")
        cells.append(
            f'<div class="task-cell"><span class="task-number">任务 {number} · '
            f'{_h(OVERVIEW_TASK_LABELS[task])}</span><strong class="task-count">'
            f'{_h(sample_text)}</strong><span class="task-detail">{_h(detail)}</span></div>'
        )
    return '<div class="task-matrix" aria-label="赛题任务一至六动态复现数据">' + "".join(cells) + "</div>"


def _claim_text(
    claim: dict[str, Any],
    benchmarks: dict[str, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    benchmark = benchmarks[claim["benchmark_id"]]
    if claim["status"] == "supported":
        title = (
            f"{targets[benchmark['treatment']]['label']} 的 {benchmark['label']}"
            "在全部预注册负载达到 joint-MCID 门槛"
        )
    elif claim["status"] == "not_supported":
        title = f"{benchmark['label']}至少有一个预注册负载未达到 joint-MCID 门槛"
    else:
        title = f"{benchmark['label']}没有可用的合同测量"
    details: list[str] = []
    for pair in benchmark["paired"]:
        if pair["status"] != "measured":
            continue
        relative = (
            f"，相对描述性 bootstrap 95% 区间 {_value(float(pair['relative_ci_low']))}%.."
            f"{_value(float(pair['relative_ci_high']))}%"
            if pair["relative_ci_low"] is not None
            else "，相对改善 unavailable"
        )
        mcid = pair["mcid_sign_test"]
        details.append(
            f"负载 {pair['load']}：改善中位数 {_value(float(pair['median']))} {benchmark['unit']}，"
            f"描述性 bootstrap 95% 区间 {_value(float(pair['ci_low']))}.."
            f"{_value(float(pair['ci_high']))}{relative}；推断采用每 boot 同时严格超过 "
            f"{_value(float(mcid['absolute_mcid_us']))} us 与 "
            f"{_value(float(mcid['relative_mcid_percent']))}% 的 joint-MCID 胜场 "
            f"{mcid['wins']}/{mcid['n']}，exact p={_value(float(mcid['p_value']))}，"
            "并按 headline family 做 Bonferroni 校正"
        )
    if details:
        effect = "；".join(details) + "。"
    elif claim["status"] == "unavailable":
        effect = "原始测量 unavailable；未填补或推断效果值。"
    else:
        effect = "没有可复核的配对统计可供展示。"
    return title, effect


def _scenario_metric(scenario: dict[str, Any]) -> str:
    performance = scenario["performance"]
    if performance is None:
        return {
            "failed": "性能测量失败",
            "unavailable": "未提供合同化性能测量",
        }[scenario["performance_status"]]
    forward_mcid = performance["mcid_sign_test"]
    reverse_mcid = performance["regression_mcid_sign_test"]
    signs = performance["sign_test"]
    inference = performance["inference"]
    interpretation = performance["interpretation"]
    relative_delta = (
        "unavailable"
        if performance["relative_median_percent"] is None
        else f"{float(performance['relative_median_percent']):+.6g}%"
    )
    return (
        f"统计结论：{STATUS_ZH.get(scenario['performance_status'], scenario['performance_status'])} "
        f"({scenario['performance_status']})；n={performance['n']}；"
        "signed delta 定义为 plain-AgentOS，"
        f"中位数 {float(performance['median']):+.6g} {performance['unit']}，"
        f"相对中位数 {relative_delta}；"
        f"描述性 bootstrap 95% 区间 "
        f"{_value(float(performance['ci_low']))}..{_value(float(performance['ci_high']))} "
        f"{performance['unit']}；符号胜/负/平="
        f"{signs['wins']}/{signs['losses']}/{signs['ties']}；"
        f"正向 joint-MCID {forward_mcid['absolute_mcid_ms']} ms 与 "
        f"{_value(float(forward_mcid['relative_mcid_percent']))}%：胜场 "
        f"{forward_mcid['wins']}/{forward_mcid['n']}，"
        f"one-sided exact p={_value(float(forward_mcid['p_value']))}；"
        f"反向 joint-MCID -{reverse_mcid['absolute_mcid_ms']} ms 与 "
        f"-{_value(float(reverse_mcid['relative_mcid_percent']))}%：负场 "
        f"{reverse_mcid['losses']}/{reverse_mcid['n']}，"
        f"one-sided exact p={_value(float(reverse_mcid['p_value']))}；"
        f"Task6 directional family alpha={_value(float(inference['alpha']))}，"
        f"Bonferroni 后每方向 alpha={_value(float(inference['per_direction_alpha']))}；"
        f"{interpretation['design']} / {interpretation['causal_attribution']}；"
        f"Host page cache {interpretation['host_page_cache']}"
    )


def _scenario_overview_metric(scenario: dict[str, Any]) -> str:
    performance = scenario.get("performance")
    if not isinstance(performance, dict):
        return {
            "failed": "完整链路性能测量失败",
            "unavailable": "未采集合同化性能数据",
        }.get(scenario["performance_status"], "未采集合同化性能数据")
    samples = performance.get("samples", [])
    if not samples:
        return "合同化性能样本为空"
    plain = [float(sample["plain_ms"]) for sample in samples]
    agentos = [float(sample["agentos_ms"]) for sample in samples]
    plain_p50 = float(statistics.median(plain))
    agentos_p50 = float(statistics.median(agentos))
    plain_p95 = _scenario_percentile(plain, 0.95)
    agentos_p95 = _scenario_percentile(agentos, 0.95)
    ratio = agentos_p50 / plain_p50 if plain_p50 > 0 else None
    ratio_text = (
        f"；AgentOS 延迟为 Plain 的 {ratio:.1f} 倍" if ratio is not None else ""
    )
    return (
        f"Plain p50 {_duration_label(plain_p50, 'ms')} / p95 "
        f"{_duration_label(plain_p95, 'ms')}；AgentOS p50 "
        f"{_duration_label(agentos_p50, 'ms')} / p95 "
        f"{_duration_label(agentos_p95, 'ms')}；n={performance['n']}{ratio_text}"
    )


OVERVIEW_BENCHMARK_LABELS = {
    "file_query_path_index": ("文件对象查询", "文件"),
    "file_query_table_ablation": ("元数据索引消融", "对象"),
    "tool_batch": ("结构化工具批处理", "项"),
    "context_access": ("Context 映射读取", "条"),
}

OVERVIEW_ENDPOINT_LABELS = {
    "file_query_path_index": ("传统逐路径查询", "就绪元数据索引"),
    "file_query_table_ablation": ("固定元数据表扫描", "就绪元数据索引"),
    "tool_batch": ("逐项工具调用", "批量工具调用"),
    "context_access": ("Context 系统调用读取", "Context 映射读取"),
}

OVERVIEW_TASK_LABELS = {
    "task1": "Agent 进程",
    "task2": "工具协议",
    "task3": "Context Path",
    "task4": "文件对象",
    "task5": "协作调度",
    "task6": "科研工作流",
}

COMPATIBILITY_METRIC_LABELS = {
    "fork_wait": "fork / wait",
    "fork_exec_wait": "fork / exec / wait",
    "pipe_roundtrip": "pipe 往返",
    "seq_file_io": "顺序文件 I/O",
    "research_artifact_pipeline": "科研工件流水线",
}


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _compatibility_p95(metric: dict[str, Any], field: str) -> float | None:
    pairs = metric.get("pairs")
    expected = _positive_int(metric.get("paired_boots"))
    if not isinstance(pairs, list) or expected is None or len(pairs) != expected:
        return None
    values: list[float] = []
    for pair in pairs:
        if not isinstance(pair, dict):
            return None
        value = _finite_float(pair.get(field))
        if value is None or value <= 0:
            return None
        values.append(value)
    return _scenario_percentile(values, 0.95) if values else None


def _compatibility_workload_html(verification: dict[str, Any]) -> str:
    compatibility = verification.get("compatibility_overhead")
    ready = isinstance(compatibility, dict) and compatibility.get("status") == "ready"
    metrics = compatibility.get("metrics", {}) if ready else {}
    if not isinstance(metrics, dict):
        metrics = {}

    if ready:
        program_mix = "同源程序 1 · 平台特定程序 0"
        equivalence = compatibility.get("workload_equivalence")
        if (
            isinstance(equivalence, dict)
            and equivalence.get("all_paired_outcomes_equal") is True
            and (paired_boots := _positive_int(equivalence.get("paired_boots"))) is not None
        ):
            output_text = f"{paired_boots}/{paired_boots} 配对输出一致"
        else:
            output_text = "输出一致性 unavailable"
        application_paths = sum(
            isinstance(metric, dict)
            and metric.get("attribution") == "guest_application_full_path_not_pure_kernel"
            for metric in metrics.values()
        )
        interface_paths = len(metrics) - application_paths
        scope_text = f"传统接口 {interface_paths} · 应用全路径 {application_paths}"
        state = compatibility.get("measurement_state")
        measurement_state_text = (
            "warm Guest path · challenge 派生轮换顺序"
            if isinstance(state, dict)
            and state.get("cache_state") == "warm_guest_paths"
            and state.get("schedule") == "challenge_rotated_v1"
            and state.get("untimed_warmup") is True
            else "测量状态 unavailable"
        )
    else:
        program_mix = "同源/平台特定程序数 unavailable"
        output_text = "输出一致性 unavailable"
        scope_text = "兼容负载 unavailable"
        measurement_state_text = "测量状态 unavailable"

    rows: list[str] = []
    ordered_ids = [
        metric_id for metric_id in COMPATIBILITY_METRIC_LABELS if metric_id in metrics
    ]
    ordered_ids.extend(sorted(
        metric_id
        for metric_id in metrics
        if isinstance(metric_id, str) and metric_id not in ordered_ids
    ))
    for metric_id in ordered_ids:
        metric = metrics.get(metric_id)
        if not isinstance(metric, dict):
            continue
        plain_p50 = _finite_float(
            metric.get("plain_median_microseconds_per_operation")
        )
        agentos_p50 = _finite_float(
            metric.get("agentos_median_microseconds_per_operation")
        )
        plain_p95 = _compatibility_p95(
            metric, "plain_microseconds_per_operation"
        )
        agentos_p95 = _compatibility_p95(
            metric, "agentos_microseconds_per_operation"
        )
        ratio = _finite_float(metric.get("median_agentos_over_plain_ratio"))
        n = _positive_int(metric.get("paired_boots"))
        operation_unit = metric.get("operation_unit")
        operation_text = (
            str(operation_unit) if isinstance(operation_unit, str) and operation_unit else "operation"
        )
        attribution = metric.get("attribution")
        attribution_text = (
            "应用全路径，非纯内核成本"
            if attribution == "guest_application_full_path_not_pure_kernel"
            else "传统接口路径"
        )

        def duration(value: float | None) -> str:
            return "unavailable" if value is None else _duration_label(value, "us/op")

        rows.append(
            '<tr>'
            f'<th scope="row"><span>{_h(COMPATIBILITY_METRIC_LABELS.get(metric_id, metric_id))}</span>'
            f'<code>{_h(metric_id)}</code><small>{_h(attribution_text)} · 每 {_h(operation_text)}</small></th>'
            f'<td>{_h(duration(plain_p50))}</td>'
            f'<td>{_h(duration(plain_p95))}</td>'
            f'<td>{_h(duration(agentos_p50))}</td>'
            f'<td>{_h(duration(agentos_p95))}</td>'
            f'<td>{_h("unavailable" if ratio is None else f"{ratio:.2f}x")}</td>'
            f'<td>{_h("unavailable" if n is None else f"n={n}")}</td>'
            '</tr>'
        )

    measurement = (
        '<div class="table-scroll compatibility-table" role="region" tabindex="0" '
        'aria-label="同源兼容负载耗时表，可横向滚动"><table><thead><tr>'
        '<th>同源负载</th><th>Plain p50</th><th>Plain p95</th>'
        '<th>AgentOS p50</th><th>AgentOS p95</th><th>AgentOS/Plain</th><th>样本</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="diagnostic-note">p50/p95 汇总各独立 boot 的 Guest 内轮次中位数；'
        '倍率为逐 boot AgentOS/Plain 比值的中位数。</p>'
        if rows
        else '<div class="unavailable-block"><strong>同源兼容负载 unavailable</strong>'
        '<span>没有可信配对数据；p50、p95、倍率与样本量均不填补。</span></div>'
    )
    return (
        '<section class="evaluation-track" data-evaluation-track="same-source-compatibility" '
        'aria-labelledby="same-source-compatibility-title">'
        '<header class="evaluation-track-heading"><div><p class="track-kicker">同一份 compatbench.c · 双目标分别编译</p>'
        '<h2 id="same-source-compatibility-title">同源兼容负载</h2></div>'
        '<p>只回答传统 uCore 接口的兼容成本，不计作 AgentOS 机制优势。</p></header>'
        '<dl class="track-summary">'
        f'<div><dt>程序构成</dt><dd>{_h(program_mix)}</dd></div>'
        f'<div><dt>输出一致性</dt><dd>{_h(output_text)}</dd></div>'
        f'<div><dt>测量范围</dt><dd>{_h(scope_text)}</dd></div></dl>'
        f'{measurement}<p class="diagnostic-note">{_h(measurement_state_text)}</p></section>'
    )


def _duration_label(value: float, unit: str) -> str:
    if unit.startswith("us"):
        suffix = unit[2:]
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:.3f} s{suffix}"
        if abs(value) >= 100:
            return f"{value / 1_000:.3f} ms{suffix}"
        return f"{value:.3f} us{suffix}"
    if unit.startswith("ms"):
        suffix = unit[2:]
        if abs(value) >= 1_000:
            return f"{value / 1_000:.3f} s{suffix}"
        return f"{value:.3f} ms{suffix}"
    return f"{_value(value)} {unit}"


def _relative_effect_label(relative: float | None, direction: str, median: float, unit: str) -> str:
    if relative is None or direction == "neutral":
        return f"配对中位差 {median:+.3f} {unit}"
    if direction == "lower_is_better":
        if relative >= 0:
            return f"逐 boot 延迟降幅中位数 {relative:.3f}%"
        return f"逐 boot 延迟倍率中位数 {1 + abs(relative) / 100:.2f}x"
    if relative >= 0:
        return f"逐 boot 吞吐增幅中位数 {relative:.3f}%"
    return f"逐 boot 吞吐降幅中位数 {abs(relative):.3f}%"


def _overview_benchmark_measurement(benchmark: dict[str, Any]) -> dict[str, Any] | None:
    measured_pairs = [pair for pair in benchmark["paired"] if pair["status"] == "measured"]
    if not measured_pairs or not benchmark["loads"]:
        return None
    load = benchmark["loads"][-1]
    pair = next((item for item in measured_pairs if str(item["load"]) == str(load)), None)
    if pair is None:
        return None
    estimates = {
        estimate["target_id"]: estimate
        for estimate in benchmark["estimates"]
        if str(estimate["load"]) == str(load)
    }
    baseline = estimates.get(benchmark["baseline"])
    treatment = estimates.get(benchmark["treatment"])
    if baseline is None or treatment is None:
        return None
    return {
        "load": load,
        "pair": pair,
        "baseline": float(baseline["value"]),
        "baseline_p95": float(baseline["p95"]),
        "treatment": float(treatment["value"]),
        "treatment_p95": float(treatment["p95"]),
        "n": int(pair["n"]),
        "measured_pairs": measured_pairs,
    }


def _overview_claim_slots(
    summary: dict[str, Any],
    benchmarks: dict[str, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
) -> str:
    registered = summary["methodology"]["multiple_testing"]["headline_claims"]
    claims = {claim["benchmark_id"]: claim for claim in summary["claims"]}
    slots: list[str] = []
    for benchmark_id in registered:
        benchmark = benchmarks.get(benchmark_id)
        claim = claims.get(benchmark_id)
        friendly = OVERVIEW_BENCHMARK_LABELS.get(benchmark_id)
        label = friendly[0] if friendly is not None else (
            benchmark["label"] if benchmark is not None else benchmark_id
        )
        load_unit = friendly[1] if friendly is not None else "负载"
        endpoint_labels = OVERVIEW_ENDPOINT_LABELS.get(benchmark_id)
        target_text = (
            f"{endpoint_labels[0]} → {endpoint_labels[1]}"
            if benchmark is not None and endpoint_labels is not None
            else f"{targets[benchmark['baseline']]['label']} → "
            f"{targets[benchmark['treatment']]['label']}"
            if benchmark is not None
            else "比较端点暂无"
        )
        measurement = (
            _overview_benchmark_measurement(benchmark)
            if benchmark is not None
            else None
        )
        if benchmark is None:
            value_text = "暂无测量"
            effect_text = "该预注册位置没有 benchmark 数据"
            metric = "不会使用其他 benchmark 补位"
            n_text = "n=0"
            load_text = "-"
            p95_text = "p95 unavailable"
        elif measurement is None:
            value_text = "暂无测量"
            effect_text = "本轮没有完整的配对数据"
            metric = f"{len(benchmark['loads'])} 个预注册负载"
            n_text = "n=0"
            load_text = "-"
            p95_text = "p95 unavailable"
        else:
            pair = measurement["pair"]
            relative = (
                float(pair["relative_median_percent"])
                if pair["relative_median_percent"] is not None
                else None
            )
            value_text = (
                f"p50 {_duration_label(measurement['baseline'], benchmark['unit'])} → "
                f"{_duration_label(measurement['treatment'], benchmark['unit'])}"
            )
            p95_text = (
                f"p95 {_duration_label(measurement['baseline_p95'], benchmark['unit'])} → "
                f"{_duration_label(measurement['treatment_p95'], benchmark['unit'])}"
            )
            effect_text = _relative_effect_label(
                relative,
                benchmark["direction"],
                float(pair["median"]),
                benchmark["unit"],
            )
            all_relative = [
                float(item["relative_median_percent"])
                for item in measurement["measured_pairs"]
                if item["relative_median_percent"] is not None
            ]
            relative_count = len(all_relative)
            pair_count = len(measurement["measured_pairs"])
            if benchmark_id == "tool_batch" and all_relative:
                faster_loads = sum(value > 0 for value in all_relative)
                coverage = (
                    f"{relative_count}/{pair_count} 个负载有相对口径，其中"
                    if relative_count != pair_count
                    else ""
                )
                metric = "上方为最高负载两端中位数；" + coverage + (
                    f"{faster_loads}/{relative_count} 个负载降低延迟"
                )
            elif all_relative:
                scope = (
                    "全负载"
                    if relative_count == pair_count
                    else f"{relative_count}/{pair_count} 个负载的"
                )
                if benchmark["direction"] == "lower_is_better" and min(all_relative) >= 0:
                    metric = (
                        f"{scope}逐 boot 延迟降幅中位数 {min(all_relative):.3f}%–"
                        f"{max(all_relative):.3f}%"
                    )
                elif benchmark["direction"] == "higher_is_better" and min(all_relative) >= 0:
                    metric = (
                        f"{scope}逐 boot 吞吐增幅中位数 {min(all_relative):.3f}%–"
                        f"{max(all_relative):.3f}%"
                    )
                else:
                    metric = (
                        f"{scope}逐 boot 相对效果中位数 {min(all_relative):.3f}%–"
                        f"{max(all_relative):.3f}%"
                    )
            else:
                metric = f"{pair_count} 个负载没有可用的相对口径"
            if claim is None:
                metric += "；本轮未登记性能结论"
            n_text = f"每负载 n={measurement['n']}"
            load_text = str(measurement["load"])
        slots.append(
            f'<article class="headline-result-slot" data-overview-slot="mechanism" '
            f'data-benchmark-id="{_h(benchmark_id)}">'
            f'<header><span>{_h(label)}</span><code>{_h(load_text)} {_h(load_unit)}</code></header>'
            f'<p class="slot-value">{_h(value_text)}</p>'
            f'<p class="slot-p95">{_h(p95_text)}</p>'
            f'<p class="slot-targets">{_h(target_text)}</p>'
            f'<p class="slot-effect">{_h(effect_text)}</p>'
            f'<p class="slot-metric">{_h(metric)}</p>'
            f'<footer><code>{_h(benchmark_id)}</code><span>{_h(n_text)}</span></footer>'
            "</article>"
        )
    return "".join(slots)


def _overview_task6_slot(
    scenarios: list[dict[str, Any]],
    scenario_details: list[dict[str, Any]] | None = None,
) -> str:
    task6 = [scenario for scenario in scenarios if _task_id(scenario["task"], "scenario.task") == "task6"]
    task6_details = [
        detail
        for detail in (scenario_details or [])
        if detail.get("task_id") == "task6"
    ]
    if len(task6_details) == 1:
        detail = task6_details[0]
        source = detail.get("source_comparability")
        functional = detail.get("functional")
        modules = functional.get("required_modules") if isinstance(functional, dict) else None
        composition = (
            f"计时程序 {int(source['expected_programs'])} 项："
            f"同源 {int(source['same_source_programs'])} · "
            f"平台特定 {int(source['platform_specific_programs'])}；"
            f"AgentOS 专属诊断模块 {len(modules)} 项"
            if isinstance(source, dict)
            and all(
                type(source.get(field)) is int and int(source[field]) >= 0
                for field in (
                    "expected_programs",
                    "same_source_programs",
                    "platform_specific_programs",
                )
            )
            and isinstance(modules, list)
            else "程序来源与专属诊断模块 unavailable"
        )
    else:
        composition = "程序来源与专属诊断模块 unavailable"
    if not task6:
        content = (
            '<div class="task6-slot-row"><strong>暂无 Task6 动态测量</strong>'
            '<p>不会使用机制微基准替代完整科研流程；'
            f'{_h(composition)}。</p></div>'
        )
    else:
        rows: list[str] = []
        for scenario in task6:
            performance = scenario.get("performance")
            if not isinstance(performance, dict) or not performance.get("samples"):
                absence = (
                    "完整链路测量失败，未生成耗时数据。"
                    if scenario["performance_status"] == "failed"
                    else "尚未采集完整链路耗时。"
                )
                rows.append(
                    '<div class="task6-slot-row"><strong>'
                    f'{_h(scenario["label"])}</strong><p>{_h(absence)}</p></div>'
                )
                continue
            plain = [float(sample["plain_ms"]) for sample in performance["samples"]]
            agentos = [float(sample["agentos_ms"]) for sample in performance["samples"]]
            plain_p50 = float(statistics.median(plain))
            agentos_p50 = float(statistics.median(agentos))
            plain_p95 = _scenario_percentile(plain, 0.95)
            agentos_p95 = _scenario_percentile(agentos, 0.95)
            ratio = agentos_p50 / plain_p50 if plain_p50 > 0 else None
            n = int(performance["n"])
            consistent = round(n * float(performance.get("paired_success_rate", 0.0)))
            ratio_text = (
                f"AgentOS 延迟为 Plain 的 {ratio:.2f} 倍"
                if ratio is not None
                else "倍率暂无数据"
            )
            conclusion_text = {
                "regressed": "显著回退",
                "inconclusive": "差异未达统计门槛",
                "failed": "测量失败",
                "unavailable": "未测量",
            }.get(scenario["performance_status"], "配对数据")
            rows.append(
                '<div class="task6-slot-row">'
                f'<div class="task6-slot-heading"><strong>{_h(OVERVIEW_TASK_LABELS["task6"])}</strong>'
                f'<span>{consistent}/{n} 预注册 outcome 一致</span></div>'
                '<div class="task6-comparison">'
                f'<div><span>Plain p50</span><strong>{_h(_duration_label(plain_p50, "ms"))}</strong>'
                f'<small>p95 {_h(_duration_label(plain_p95, "ms"))}</small></div>'
                f'<div><span>AgentOS p50</span><strong>{_h(_duration_label(agentos_p50, "ms"))}</strong>'
                f'<small>p95 {_h(_duration_label(agentos_p95, "ms"))}</small></div>'
                f'<div class="task6-ratio"><span>端到端延迟（越低越好）</span><strong>{_h(ratio_text)}</strong>'
                f'<small>{_h(conclusion_text)} · n={n} 配对启动</small></div></div>'
                f'<p class="workflow-composition">{_h(composition)}</p></div>'
            )
        content = "".join(rows)
    return (
        '<section class="evaluation-track workflow-track" '
        'data-overview-slot="task6" data-evaluation-track="full-research-workflow" '
        'data-benchmark-id="task6" aria-labelledby="full-workflow-title">'
        '<header class="evaluation-track-heading"><div><p class="track-kicker">同输入 · 不同 backend</p>'
        '<h2 id="full-workflow-title">完整科研流程诊断</h2></div>'
        '<p>展示完整 AgentOS 栈的端到端表现，不归因给单一内核机制。</p></header>'
        f'<div class="task6-slot-list">{content}</div></section>'
    )


def _overview_extension_slots(
    verification: dict[str, Any], scenario_details: list[dict[str, Any]],
    kernel_cost: dict[str, Any] | None = None,
) -> str:
    task6_resources = [
        (detail["resource_stability"], int(detail.get("independent_boots", 0)))
        for detail in scenario_details
        if detail.get("task_id") == "task6"
        and isinstance(detail.get("resource_stability"), dict)
    ]
    if len(task6_resources) > 1:
        _fail("resource stability must bind at most one verified Task6 scenario report")
    resource, expected_boots = task6_resources[0] if task6_resources else (None, 0)
    if resource is None or resource.get("status") not in {"passed", "partial"}:
        resource_value = "暂无动态数据"
        resource_text = "本轮未提供资源回收测量"
    else:
        observation = resource.get("global_observation", {})
        observed_resources = (
            observation.get("resources", [])
            if isinstance(observation, dict)
            else []
        )
        measured_kinds = sum(
            isinstance(item, dict) and item.get("status") == "measured"
            for item in observed_resources
        )
        free_pages = (
            observation.get("free_pages", {})
            if isinstance(observation, dict)
            else {}
        )
        exact_free_pages = (
            isinstance(free_pages, dict)
            and free_pages.get("exact_pair_recovery") is True
            and free_pages.get("exact_terminal_recovery") is True
        )
        boots = int(resource.get("verified_boots", 0))
        load_workflows = int(resource.get("load_workflows_per_boot", 0))
        child_rounds = int(resource.get("child_rounds_per_load_workflow", 0))
        stress_rounds = boots * load_workflows * child_rounds
        resource_value = f"{stress_rounds} 轮压力 · {measured_kinds}/{len(observed_resources)} 类计数"
        boot_coverage = f"{boots}/{expected_boots}" if expected_boots else str(boots)
        resource_text = (
            f"{boot_coverage} 次独立启动有资源回执；空闲页配对与终点"
            f"{'精确恢复' if exact_free_pages else '未确认精确恢复'}；"
            "buffer cache 仅声明有界增长与可回收"
        )

    compatibility = verification.get("compatibility_overhead")
    if not isinstance(compatibility, dict) or compatibility.get("status") != "ready":
        compatibility_value = "暂无动态数据"
        compatibility_text = "本轮未提供传统 uCore 路径配对成本"
    else:
        metrics = compatibility.get("metrics", {})
        metric_count = len(metrics) if isinstance(metrics, dict) else 0
        ratios = [
            float(metric["median_agentos_over_plain_ratio"])
            for metric in metrics.values()
            if isinstance(metric, dict)
            and isinstance(metric.get("median_agentos_over_plain_ratio"), (int, float))
            and not isinstance(metric.get("median_agentos_over_plain_ratio"), bool)
            and math.isfinite(float(metric["median_agentos_over_plain_ratio"]))
        ] if isinstance(metrics, dict) else []
        compatibility_value = (
            f"{min(ratios):.2f}x–{max(ratios):.2f}x" if ratios else "暂无倍率"
        )
        paired_boots = sorted({
            int(metric["paired_boots"])
            for metric in metrics.values()
            if isinstance(metric, dict) and isinstance(metric.get("paired_boots"), int)
        }) if isinstance(metrics, dict) else []
        paired_text = str(paired_boots[0]) if len(paired_boots) == 1 else "多组"
        compatibility_text = (
            f"{metric_count} 项传统路径 · {paired_text} 次配对启动；"
            "数值是兼容成本，不计作机制性能优势"
        )

    cost_value = "暂无静态数据"
    cost_text = "本轮未提供绑定同一提交的内核成本"
    if isinstance(kernel_cost, dict):
        metrics_by_id = {item["id"]: item for item in kernel_cost.get("metrics", [])}
        required = [metrics_by_id.get(item) for item in ("text_bytes", "data_bytes", "bss_bytes")]
        text_metric = metrics_by_id.get("text_bytes")
        elf_metric = metrics_by_id.get("elf_file_bytes")
        if all(item is not None and item.get("status") == "measured" for item in required):
            baseline_static = sum(int(item["baseline"]) for item in required if item is not None)
            agentos_static = sum(int(item["agentos"]) for item in required if item is not None)
            static_delta = (agentos_static - baseline_static) * 100.0 / baseline_static
            text_delta = (
                (int(text_metric["agentos"]) - int(text_metric["baseline"]))
                * 100.0 / int(text_metric["baseline"])
                if text_metric is not None and int(text_metric["baseline"]) > 0
                else 0.0
            )
            cost_value = f"内存映像 {static_delta:+.1f}% · .text {text_delta:+.1f}%"
            elf_text = ""
            if elf_metric is not None and elf_metric.get("status") == "measured":
                elf_text = (
                    f"；ELF {int(elf_metric['baseline']) / 1024**2:.3f} → "
                    f"{int(elf_metric['agentos']) / 1024**2:.3f} MiB"
                )
            cost_text = (
                f"text+data+bss {baseline_static / 1024**2:.3f} → "
                f"{agentos_static / 1024**2:.3f} MiB{elf_text}"
            )

    slots = (
        (
            "resource-stability",
            "资源回收",
            resource_value,
            resource_text,
            "Task6 场景结束后测量",
        ),
        (
            "compatibility-overhead",
            "传统路径成本",
            compatibility_value,
            compatibility_text,
            "fork / exec / pipe / sequential I/O / research pipeline",
        ),
        (
            "kernel-cost",
            "内核静态成本",
            cost_value,
            cost_text,
            "同一提交的确定性构建，n=1",
        ),
    )
    return "".join(
        '<article class="extension-result-slot" data-extension-slot="{}">'
        '<header><h3>{}</h3></header><strong class="extension-value">{}</strong>'
        '<p>{}</p><footer>{}</footer></article>'.format(
            _h(slot_id),
            _h(title),
            _h(value),
            _h(text),
            _h(scope),
        )
        for slot_id, title, value, text, scope in slots
    )


def _canonical_dashboard_summary(
    summary: dict[str, Any], kernel_cost: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Remove narrative authority from caller-supplied presentation fields."""

    canonical = copy.deepcopy(summary)
    canonical["run"].pop("conclusion", None)
    benchmarks = {item["id"]: item for item in canonical["benchmarks"]}
    targets = {item["id"]: item for item in canonical["targets"]}
    for claim in canonical["claims"]:
        claim["title"], claim["effect"] = _claim_text(claim, benchmarks, targets)
    canonical["kernel_cost"] = (
        {"status": "unavailable", "reason": "kernel-cost sidecar absent"}
        if kernel_cost is None
        else copy.deepcopy(kernel_cost)
    )
    return canonical


def _methodology_rows(value: Any, prefix: str = "") -> list[str]:
    rows: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (dict, list)):
                rows.extend(_methodology_rows(child, label))
            else:
                rows.append(f'<dt>{_h(label)}</dt><dd>{_h(child)}</dd>')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            label = f"{prefix}[{index}]"
            if isinstance(child, (dict, list)):
                rows.extend(_methodology_rows(child, label))
            else:
                rows.append(f'<dt>{_h(label)}</dt><dd>{_h(child)}</dd>')
    else:
        rows.append(f'<dt>{_h(prefix or "value")}</dt><dd>{_h(value)}</dd>')
    return rows


def _interpretation_boundaries_html(methodology: dict[str, Any]) -> str:
    boundary = methodology["interpretation_boundaries"]
    rows = (
        (
            "微基准因果边界",
            boundary["microbenchmark_design"],
            "同一内核内的机制消融，只支持预注册工作负载下被切换机制的局部因果解释。",
        ),
        (
            "科研场景归因边界",
            f"{boundary['scenario_design']} / {boundary['scenario_attribution']}",
            "端到端结果同时包含内核、用户态和执行器差异，不能归因于单一机制。",
        ),
        (
            "Host page cache",
            boundary["host_page_cache"],
            "宿主机页缓存未受控，不据此声称真实存储硬件上的因果性能优势。",
        ),
    )
    cells = "".join(
        '<div><dt>{}</dt><dd><code>{}</code><span>{}</span></dd></div>'.format(
            _h(label), _h(value), _h(detail)
        )
        for label, value, detail in rows
    )
    return (
        '<section aria-labelledby="interpretation-boundaries-title">'
        '<h3 id="interpretation-boundaries-title" class="subsection-title">解释边界</h3>'
        f'<dl class="summary-strip">{cells}</dl></section>'
    )


def _scenario_details_html(details: list[dict[str, Any]]) -> str:
    if not details:
        return (
            '<div class="unavailable-block"><strong>场景明细 unavailable</strong>'
            '<span>没有已核验的 research-platform-scenario 报告。</span></div>'
        )
    blocks: list[str] = []
    for detail in details:
        source = detail.get("source_comparability", {})
        if not isinstance(source, dict):
            source = {}
        source_summary = (
            '<dl class="track-summary scenario-source-summary" aria-label="计时程序源码可比性">'
            f'<div><dt>计时程序</dt><dd>{_h(source.get("expected_programs", "unavailable"))}</dd></div>'
            f'<div><dt>同源程序</dt><dd>{_h(source.get("same_source_programs", "unavailable"))}</dd></div>'
            f'<div><dt>平台特定程序</dt><dd>{_h(source.get("platform_specific_programs", "unavailable"))}</dd></div></dl>'
        )
        program_rows = "".join(
            '<tr>'
            f'<th scope="row"><code>{_h(program["program"])}</code></th>'
            f'<td>{_h(_value(float(program["plain_p50_ms"])))}</td>'
            f'<td>{_h(_value(float(program["plain_p95_ms"])))}</td>'
            f'<td>{_h(_value(float(program["agentos_p50_ms"])))}</td>'
            f'<td>{_h(_value(float(program["agentos_p95_ms"])))}</td>'
            '</tr>'
            for program in detail["programs"]
        )
        module_rows = "".join(
            '<tr>'
            f'<th scope="row"><code>{_h(module)}</code></th>'
            f'<td>{_h(detail["functional"]["verified_boots"])} / {_h(detail["independent_boots"])}</td>'
            '</tr>'
            for module in detail["functional"]["required_modules"]
        )
        outcome_rows = "".join(
            '<tr>'
            f'<th scope="row"><code>{_h(outcome["key"])}</code></th>'
            f'<td>{_h(outcome["verified_pairs"])} / {_h(detail["independent_boots"])}</td>'
            '</tr>'
            for outcome in detail["outcomes"]
        )
        blocks.append(
            '<article class="benchmark-block scenario-detail">'
            '<header><div>'
            f'<p class="eyebrow">{_h(detail["independent_boots"])} 个独立 Guest boot</p>'
            f'<h3>{_h(detail["scenario_id"])}</h3></div>{_status(detail["status"])}</header>'
            f'{source_summary}'
            '<p class="diagnostic-note">同源表示 Plain 与 AgentOS 对应 C 源文件字节一致；'
            '平台特定表示两侧实现源码不同，不把 backend 差异误写为纯内核效果。</p>'
            '<section class="diagnostic-block" aria-label="逐程序耗时">'
            '<h4>逐程序耗时</h4><p class="diagnostic-note">来自已核验场景报告；单位 ms，不提升为新的 headline claim。</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="逐程序耗时表，可横向滚动"><table><thead><tr><th>程序</th><th>Plain p50</th><th>Plain p95</th>'
            f'<th>{_h("AgentOS")} p50</th><th>{_h("AgentOS")} p95</th></tr></thead><tbody>{program_rows}</tbody></table></div>'
            '</section>'
            '<section class="diagnostic-block" aria-label="AgentOS 功能模块">'
            '<h4>AgentOS 功能模块</h4><p class="diagnostic-note">required_modules 的逐 boot 验收回执。</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="AgentOS 功能模块表，可横向滚动"><table><thead><tr><th>模块</th><th>核验 boot</th>'
            f'</tr></thead><tbody>{module_rows}</tbody></table></div></section>'
            '<section class="diagnostic-block" aria-label="关键 outcome 一致性">'
            '<h4>预注册 key outcome 一致性</h4>'
            '<p class="diagnostic-note">仅覆盖预注册关键 outcome 的配对回执，不代表完整最终状态相同。</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="关键 outcome 一致性表，可横向滚动"><table><thead><tr><th>Key outcome</th><th>一致配对</th>'
            f'</tr></thead><tbody>{outcome_rows}</tbody></table></div></section>'
            f'<footer><strong>绑定证据</strong> <button class="evidence-link" type="button" data-evidence-ref="{_h(detail["evidence_id"])}">{_h(detail["evidence_id"])}</button></footer>'
            '</article>'
        )
    return "".join(blocks)


def _supplementary_evaluations_html(methodology: dict[str, Any]) -> str:
    evaluations = methodology.get("supplementary_evaluations", [])
    if not evaluations:
        return ""
    blocks: list[str] = []
    for evaluation in evaluations:
        rows: list[str] = []
        visit_rows: list[str] = []
        for boot in evaluation["boots"]:
            visit_rows.append(
                "<tr>"
                f'<th scope="row"><code>{_h(boot["boot_id"])}</code></th>'
                f'<td>{_h(boot["correct"])} / {_h(len(evaluation["visit_sequence"]))}</td>'
                f'<td>{_h(boot["contamination"])}</td>'
                f'<td>{_h(boot["return_visit"])}</td>'
                f'<td>{_h(boot["fallback"])}</td>'
                f'<td><button class="evidence-link" type="button" '
                f'data-evidence-ref="{_h(boot["evidence_id"])}">原始日志</button></td>'
                "</tr>"
            )
            for sample in boot["concurrency"]:
                throughput = sample["throughput_milli_rps"] / 1000
                rows.append(
                    "<tr>"
                    f'<th scope="row"><code>{_h(boot["boot_id"])}</code></th>'
                    f'<td>{_h(sample["concurrency"])}</td>'
                    f'<td>{_h(sample["completed"])} / {_h(sample["requests"])}</td>'
                    f'<td>{_h(_value(throughput))}</td>'
                    f'<td>{_h(_value(sample["goodput_milli_rps"] / 1000))}</td>'
                    f'<td>{_h(sample["p90_us"])}</td>'
                    f'<td>{_h(sample["wait_p90_us"])}</td>'
                    f'<td>{_h(_value(sample["fairness_jain_ppm"] / 1_000_000))}</td>'
                    f'<td>{_h(_value(sample["max_min_fairness_ppm"] / 1_000_000))}</td>'
                    f'<td>{_h(sample["isolated"])} / {_h(sample["requests"])}</td>'
                    f'<td>{_h(sample["contamination"])}</td>'
                    f'<td>{_h(sample["fallback"])}</td>'
                    f'<td><code>{_h(sample["workload_digest"])}</code></td>'
                    "</tr>"
                )
        sequence = " → ".join(evaluation["visit_sequence"])
        concurrency_head = (
            '<th>Guest boot</th><th>并发度</th><th>完成请求</th>'
            '<th>吞吐率 req/s</th><th>Goodput req/s</th><th>周转 p90 us</th>'
            '<th>等待 p90 us</th><th>Jain 公平度</th><th>Min/max 公平度</th>'
            '<th>隔离完成</th><th>污染记录</th><th>回退</th><th>工作负载摘要</th>'
        )
        blocks.append(
            '<article class="benchmark-block supplementary-evaluation">'
            '<header><div><p class="eyebrow">描述性补充测量</p>'
            f'<h3>{_h(evaluation["label"])}</h3></div>'
            f'{_status(evaluation["status"])}</header>'
            f'<p class="diagnostic-note">回访顺序 {_h(sequence)}；每个并发度 '
            f'{_h(evaluation["rounds_per_level"])} 轮。无性能通过阈值，负结果原样展示。</p>'
            '<h4>身份隔离与回访</h4>'
            '<div class="table-scroll" role="region" tabindex="0" '
            'aria-label="多身份回访实测表，可横向滚动"><table><thead><tr>'
            '<th>Guest boot</th><th>正确访问</th><th>污染记录</th>'
            '<th>返回访问</th><th>回退</th><th>证据</th></tr></thead>'
            f'<tbody>{"".join(visit_rows)}</tbody></table></div>'
            '<h4>并发 IPC 数据</h4>'
            '<div class="table-scroll" role="region" tabindex="0" '
            'aria-label="多身份并发实测表，可横向滚动"><table><thead><tr>'
            f'{concurrency_head}'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            '</article>'
        )
    return (
        '<section aria-labelledby="supplementary-evaluations-title">'
        '<h3 id="supplementary-evaluations-title" class="subsection-title">'
        '多身份回访与并发</h3>' + "".join(blocks) + "</section>"
    )


def _kernel_cost_html(detail: dict[str, Any] | None) -> str:
    if detail is None:
        return (
            '<div class="unavailable-block"><strong>系统成本 unavailable</strong>'
            '<span>本轮没有完整的可信 kernel-cost sidecar；未填补静态成本数值。</span></div>'
        )
    rows: list[str] = []
    for metric in detail["metrics"]:
        if metric["status"] == "measured":
            baseline = f'{int(metric["baseline"]):,} B'
            agentos = f'{int(metric["agentos"]):,} B'
            delta_value = int(metric["delta"])
            delta = f'{delta_value:+,} B'
        else:
            baseline = agentos = delta = "unavailable"
        rows.append(
            '<tr>'
            f'<th scope="row">{_h(metric["label"])}</th>'
            f'<td>{_h(baseline)}</td><td>{_h(agentos)}</td><td>{_h(delta)}</td>'
            f'<td>{_status(metric["status"])}</td></tr>'
        )
    guardrail_rows = "".join(
        '<tr>'
        f'<th scope="row">{_h(guardrail["label"])}</th>'
        f'<td>{int(guardrail["value"]):,} B</td>'
        f'<td>{int(guardrail["limit"]):,} B</td>'
        f'<td>{int(guardrail["headroom"]):,} B</td>'
        f'<td><code>{_h(guardrail["source"])}</code></td>'
        f'<td>{_status(guardrail["status"])}</td></tr>'
        for guardrail in detail["guardrails"]
    )
    return (
        '<dl class="summary-strip" aria-label="内核成本运行回执">'
        f'<div><dt>运行</dt><dd><code>{_h(detail["run_id"])}</code></dd></div>'
        f'<div><dt>Commit</dt><dd><code>{_h(detail["commit"])}</code></dd></div>'
        f'<div><dt>Sidecar 文件</dt><dd>{_h(detail["evidence_file_count"])}</dd></div>'
        f'<div><dt>运行状态</dt><dd>{_status(detail["status"])}</dd></div></dl>'
        '<div class="table-scroll" role="region" tabindex="0" aria-label="内核静态成本表，可横向滚动"><table><thead><tr><th>静态成本项</th>'
        f'<th>{_h(detail["baseline"]["label"])}</th>'
        f'<th>{_h(detail["agentos"]["label"])}</th>'
        '<th>Delta（AgentOS - baseline）</th><th>状态</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<h3 class="subsection-title">AgentOS 结构与用户栈预算</h3>'
        '<div class="table-scroll" role="region" tabindex="0" aria-label="AgentOS 结构与用户栈预算表，可横向滚动"><table><thead><tr>'
        '<th>预算项</th><th>实际</th><th>上限</th><th>余量</th><th>可信检查器</th><th>状态</th>'
        f'</tr></thead><tbody>{guardrail_rows}</tbody></table></div>'
        '<p class="diagnostic-note">ELF、.text、.data、.bss、struct proc 与用户栈是成本护栏，'
        '不是 CPU 性能证据，也不会生成 performance claim。struct proc 与用户栈只使用 '
        'AgentOS 的 canonical checker actual/limit，不冒充 baseline delta。</p>'
    )


def _memory_label(value: int) -> str:
    gibibytes = value / (1024**3)
    return f"{gibibytes:.2f} GiB · {value:,} B"


def _campaign_environment_html(detail: dict[str, Any] | None) -> str:
    heading = (
        '<div class="environment-heading"><div><p class="eyebrow">Reproducibility Receipt</p>'
        '<h2 id="environment-title">实验环境</h2></div>'
    )
    if detail is None:
        return (
            '<section class="environment-band" aria-labelledby="environment-title">'
            + heading
            + f'{_status("unavailable")}</div>'
            '<div class="unavailable-block"><strong>实验环境 unavailable</strong>'
            '<span>本轮没有与 run plan 绑定的已核验 campaign 平台证明。</span></div></section>'
        )

    values = (
        ("Source commit", f'<code>{_h(detail["source_commit"])}</code>', "environment-cell--wide"),
        ("执行域", _h(detail["execution_domain"]), ""),
        ("CPU", _h(detail["cpu_model"]), "environment-cell--wide"),
        ("逻辑核", _h(detail["logical_cpu_count"]), ""),
        ("总内存", _h(_memory_label(int(detail["memory_total_bytes"]))), ""),
        ("RISC-V GCC", _h(" ".join(str(detail["gcc_version"]).split())), ""),
        ("QEMU", _h(" ".join(str(detail["qemu_version"]).split())), ""),
    )
    cells = "".join(
        f'<div class="environment-cell {_h(css_class)}"><dt>{_h(label)}</dt><dd>{value}</dd></div>'
        for label, value, css_class in values
    )
    return (
        '<section class="environment-band" aria-labelledby="environment-title">'
        + heading
        + f'{_status(detail["status"])}</div>'
        f'<dl class="environment-grid">{cells}</dl>'
        '<footer><span>硬件来源 <code>'
        + _h(detail["hardware_source"])
        + '</code></span><a href="../campaign.json">campaign.json</a></footer></section>'
    )


def _page(
    summary: dict[str, Any],
    verification: dict[str, Any],
    scenario_details: list[dict[str, Any]],
    kernel_cost: dict[str, Any] | None,
    campaign_environment: dict[str, Any] | None,
) -> str:
    run = summary["run"]
    targets = {item["id"]: item for item in summary["targets"]}
    evidence = {item["id"]: item for item in summary["evidence"]}
    benchmarks = {item["id"]: item for item in summary["benchmarks"]}
    measured = [item for item in summary["benchmarks"] if item["status"] == "measured"]
    mechanism_load_count = sum(len(item["loads"]) for item in measured)
    sample_counts = [int(estimate["n"]) for item in measured for estimate in item["estimates"]]
    n_display = str(min(sample_counts)) if sample_counts and len(set(sample_counts)) == 1 else (
        f"{min(sample_counts)}-{max(sample_counts)}" if sample_counts else "暂无"
    )
    commit = run.get("commit", "unavailable")
    short_commit = commit[:12] if commit != "unavailable" else commit
    scenario_sample_counts = [
        int(item["performance"]["n"])
        for item in summary["scenarios"]
        if isinstance(item.get("performance"), dict)
    ]
    paired_scenario_count = max(scenario_sample_counts, default=0)
    scenario_boundary = summary["methodology"]["interpretation_boundaries"]
    scenario_design = {
        "full-stack": "完整链路",
    }.get(scenario_boundary["scenario_design"], scenario_boundary["scenario_design"])
    scenario_attribution = {
        "non-single-mechanism": "多机制共同影响",
    }.get(
        scenario_boundary["scenario_attribution"],
        scenario_boundary["scenario_attribution"],
    )
    host_cache = {
        "uncontrolled": "未控制",
    }.get(scenario_boundary["host_page_cache"], scenario_boundary["host_page_cache"])
    scenario_boundary_note = (
        f"{scenario_design}；{scenario_attribution}；宿主页缓存{host_cache}。"
        "功能结果与性能数据分别呈现。"
    )
    overview_claim_slots = _overview_claim_slots(summary, benchmarks, targets)
    compatibility_workload = _compatibility_workload_html(verification)
    overview_task6_slot = _overview_task6_slot(
        summary["scenarios"], scenario_details
    )
    overview_extensions = _overview_extension_slots(
        verification, scenario_details, kernel_cost
    )

    benchmark_sections = []
    for index, benchmark in enumerate(summary["benchmarks"]):
        measured_pairs = [pair for pair in benchmark["paired"] if pair["status"] == "measured"]
        pair_ns = sorted({int(pair["n"]) for pair in measured_pairs})
        pair_n = (
            str(pair_ns[0])
            if len(pair_ns) == 1
            else f"{pair_ns[0]}-{pair_ns[-1]}"
            if pair_ns
            else "0"
        )
        measurement_label = f"{len(measured_pairs)} 个负载 · n={pair_n}"
        comparison_note = (
            '<p class="diagnostic-note">Task 4 竞赛主对照：同一 N-file corpus 上的'
            '逐路径 open/read/fstat/close 属性检查与 ready index；负结果仍完整展示。</p>'
            if benchmark["id"] == "file_query_path_index"
            else '<p class="diagnostic-note">机制消融：固定容量 metadata table scan 与索引；'
            '它解释索引内部效果，但不能替代题面要求的逐文件对照。</p>'
            if benchmark["id"] == "file_query_table_ablation"
            else ""
        )
        benchmark_sections.append(
            '<article class="benchmark-block">'
            f'<header><div><p class="eyebrow">{_h(benchmark["task"])}</p><h3>{_h(benchmark["label"])}</h3></div>'
            f'<span class="measurement-count">{_h(measurement_label)}</span></header>'
            f'{comparison_note}'
            f'{_chart(benchmark, targets, evidence, suffix=f"benchmark-{index}")}'
            f'{_work_receipts_table(benchmark, targets)}'
            f'{_diagnostics_table(benchmark)}'
            '</article>'
        )

    scenario_rows = []
    scenario_inference = []
    for scenario in summary["scenarios"]:
        refs = _evidence_entry_link(scenario.get("evidence_ids", []))
        performance = scenario.get("performance")
        if isinstance(performance, dict):
            sample_count = int(performance["n"])
            sample_kind = "独立启动"
            scenario_inference.append(
                '<details class="evidence-item scenario-inference">'
                f'<summary>{_h(scenario["label"])} 的统计明细</summary>'
                f'<p>{_h(_scenario_metric(scenario))}</p></details>'
            )
        else:
            sample_count, sample_kind = _scenario_sample_count(scenario, evidence)
        reproduction = (
            f"{sample_count}/{sample_count} {sample_kind}结果一致"
            if scenario["functional_status"] == "pass"
            else "功能测量未提供"
            if scenario["functional_status"] == "unavailable"
            else f"{sample_count} 组{sample_kind}出现功能差异"
        )
        task_id = _task_id(scenario["task"], "scenario.task")
        scenario_rows.append(
            '<tr>'
            f'<td>任务 {_h(task_id.removeprefix("task"))}</td>'
            f'<th scope="row">{_h(OVERVIEW_TASK_LABELS[task_id])}</th>'
            f'<td>{_h(reproduction)}</td>'
            f'<td>{_h(_scenario_overview_metric(scenario))}</td>'
            f'<td>{refs}</td></tr>'
        )
    if not scenario_rows:
        scenario_rows.append('<tr><td colspan="5" class="empty-cell">尚未提供科研场景测量。</td></tr>')

    claim_items = []
    for claim in summary["claims"]:
        claim_title, claim_effect = _claim_text(claim, benchmarks, targets)
        refs = _evidence_entry_link(claim["evidence_ids"])
        claim_benchmark = benchmarks[claim["benchmark_id"]]
        measured_pairs = [
            pair for pair in claim_benchmark["paired"] if pair["status"] == "measured"
        ]
        sample_ns = sorted({int(pair["n"]) for pair in measured_pairs})
        sample_text = (
            f"{len(measured_pairs)} 负载 · n={sample_ns[0]}"
            if len(sample_ns) == 1
            else f"{len(measured_pairs)} 负载 · n={sample_ns[0]}-{sample_ns[-1]}"
            if sample_ns
            else "0 负载 · n=0"
        )
        claim_items.append(
            '<article class="claim-item">'
            f'<header><code>{_h(claim["benchmark_id"])}</code><span>{_h(sample_text)}</span></header>'
            f'<h3>{_h(claim_title)}</h3><p>{_h(claim_effect)}</p>'
            f'<footer><strong>绑定证据</strong> {refs}</footer></article>'
        )
    if not claim_items:
        claim_items.append('<div class="unavailable-block"><strong>结论 unavailable</strong><span>未声明 headline claim。</span></div>')

    evidence_items = []
    for item in summary["evidence"]:
        rows = []
        for key in ("kind", "path", "sha256"):
            if key in item:
                rows.append(f'<dt>{_h(key)}</dt><dd><code>{_h(item[key])}</code></dd>')
        source = item.get("source")
        if isinstance(source, str):
            safe_source = item["path"] if "\\" in source or WINDOWS_DRIVE.match(source) else source
            rows.append(f'<dt>source</dt><dd><code>{_h(safe_source)}</code></dd>')
        receipt = item.get("receipt")
        if isinstance(receipt, dict):
            receipt_fields = (
                ("source_lines", receipt.get("line_numbers")),
                ("command", receipt.get("command_argv")),
                ("environment_sha256", receipt.get("environment_sha256")),
                ("commit", receipt.get("commit")),
                ("boot_id", receipt.get("boot_id")),
                ("capture_status", receipt.get("capture_status")),
            )
            for label, value in receipt_fields:
                if value is None:
                    continue
                if isinstance(value, list):
                    value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                rows.append(f'<dt>{_h(label)}</dt><dd><code>{_h(value)}</code></dd>')
        rows.append(
            '<dt>local_file</dt><dd>'
            f'<a class="evidence-file-link" href="{_h(_evidence_href(item["path"]))}">'
            '打开原始文件</a></dd>'
        )
        evidence_items.append(
            f'<details class="evidence-item" id="evidence-{_h(item["id"])}" data-evidence-id="{_h(item["id"])}">'
            f'<summary><span><code>{_h(item["id"])}</code> {_h(item.get("label", item["id"]))}</span>{_status(item["status"])}</summary>'
            f'<dl class="raw-fields">{"".join(rows) or "<dt>raw</dt><dd>unavailable</dd>"}</dl></details>'
        )
    if not evidence_items:
        evidence_items.append('<div class="unavailable-block"><strong>原始证据 unavailable</strong></div>')

    methodology_rows = "".join(_methodology_rows({
        key: value for key, value in summary["methodology"].items()
        if key != "supplementary_evaluations"
    }))
    interpretation_boundaries = _interpretation_boundaries_html(summary["methodology"])
    methodology_diagnostics = "".join(
        _diagnostics_table(benchmark, heading_level=3)
        for benchmark in summary["benchmarks"]
        if benchmark.get("diagnostics")
    )
    target_text = f"机制样本/负载 n={n_display} · RISC-V / QEMU"
    evidence_count = int(verification["verified_evidence_count"])
    declared_evidence_count = len(verification["evidence"])
    marker_count = int(verification["verified_marker_count"])
    evidence_set_sha256 = str(verification["evidence_set_sha256"])
    scenario_detail_blocks = _scenario_details_html(scenario_details)
    supplementary_evaluations = _supplementary_evaluations_html(summary["methodology"])
    kernel_cost_block = _kernel_cost_html(kernel_cost)
    campaign_environment_block = _campaign_environment_html(campaign_environment)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>AgentOS-uCore 实测数据</title>
  <link rel="stylesheet" href="assets/evaluation-dashboard.css">
</head>
<body>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <header class="app-header">
    <div class="header-inner">
      <div><p class="product-name">AgentOS-uCore 实测数据</p><p class="run-name">同一提交 · 原始日志可查</p></div>
      <div class="header-actions"><span>{_h(target_text)}</span><a href="evaluation-summary.json" download>下载 JSON</a><a href="dashboard-verification.json" download>下载核验回执</a><a href="metrics.csv" download>下载 CSV</a></div>
    </div>
  </header>
  <nav class="tabs" aria-label="评价视图">
    <div class="tabs-inner" role="tablist">
      <button role="tab" id="tab-overview" aria-controls="panel-overview" aria-selected="true">总览</button>
      <button role="tab" id="tab-performance" aria-controls="panel-performance" aria-selected="false" tabindex="-1">性能</button>
      <button role="tab" id="tab-cost" aria-controls="panel-cost" aria-selected="false" tabindex="-1">系统成本</button>
      <button role="tab" id="tab-scenarios" aria-controls="panel-scenarios" aria-selected="false" tabindex="-1">负载与流程</button>
      <button role="tab" id="tab-evidence" aria-controls="panel-evidence" aria-selected="false" tabindex="-1">可信证据</button>
      <button role="tab" id="tab-methodology" aria-controls="panel-methodology" aria-selected="false" tabindex="-1">方法学</button>
    </div>
  </nav>
  <main id="main">
    <section role="tabpanel" id="panel-overview" aria-labelledby="tab-overview" class="tab-panel">
      <div class="section-heading"><div><p class="eyebrow">评测运行 {_h(run["id"])}</p><h1>AgentOS-uCore 实测数据</h1></div><p>比较口径、p50/p95、样本量与输出一致性同屏展示。</p></div>
      <dl class="summary-strip">
        <div><dt>机制样本/负载</dt><dd>n={_h(n_display)}</dd></div>
        <div><dt>机制负载</dt><dd>{_h(mechanism_load_count)}</dd></div>
        <div><dt>完整流程配对</dt><dd>{_h(paired_scenario_count)}</dd></div>
        <div><dt>Commit</dt><dd><code title="{_h(commit)}">{_h(short_commit)}</code></dd></div>
      </dl>
      <section class="judge-overview" aria-labelledby="judge-overview-title">
        <div class="subheading"><h2 id="judge-overview-title">评委速览</h2><span>先看可比口径，再看数值</span></div>
        <div class="evaluation-track-list">{compatibility_workload}{overview_task6_slot}</div>
      </section>
      <section class="overview-results" aria-labelledby="overview-results-title">
        <div class="subheading"><h2 id="overview-results-title">机制微基准</h2><span>最高预注册负载的 p50、p95、相对效果与 n</span></div>
        <div class="headline-result-grid">{overview_claim_slots}</div>
      </section>
      <section class="overview-resources" aria-labelledby="overview-resources-title">
        <div class="subheading"><h2 id="overview-resources-title">资源与系统成本</h2><span>回收压力、兼容成本范围和静态体积</span></div>
        <div class="extension-result-grid">{overview_extensions}</div>
      </section>
    </section>
    <section role="tabpanel" id="panel-performance" aria-labelledby="tab-performance" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">可复现测量</p><h2>性能</h2></div><p>区间、单位、样本量与来源同时呈现。</p></div>
      <div class="benchmark-list">{"".join(benchmark_sections) or '<div class="unavailable-block"><strong>unavailable</strong><span>没有 benchmark 记录。</span></div>'}</div>
    </section>
    <section role="tabpanel" id="panel-cost" aria-labelledby="tab-cost" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">同提交构建</p><h2>系统成本</h2></div><p>同 commit、构建清单绑定的内核静态体积对照。</p></div>
      {kernel_cost_block}
    </section>
    <section role="tabpanel" id="panel-scenarios" aria-labelledby="tab-scenarios" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">同源负载 / 完整工作流</p><h2>负载与流程</h2></div><p>{_h(scenario_boundary_note)}</p></div>
      <section aria-labelledby="functional-receipts-title"><h3 id="functional-receipts-title" class="subsection-title">任务功能回执</h3>{_task_matrix(summary["scenarios"], evidence)}</section>
      <div class="table-scroll" role="region" tabindex="0" aria-label="科研场景实测表，可横向滚动"><table><thead><tr><th>赛题任务</th><th>能力</th><th>动态复现</th><th>耗时对照</th><th>原始证据</th></tr></thead><tbody>{"".join(scenario_rows)}</tbody></table></div>
      {"".join(scenario_inference)}
      {supplementary_evaluations}
      <section aria-labelledby="scenario-details-title"><h3 id="scenario-details-title" class="subsection-title">场景明细</h3>{scenario_detail_blocks}</section>
    </section>
    <section role="tabpanel" id="panel-evidence" aria-labelledby="tab-evidence" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">结论与原始数据</p><h2>可信证据</h2></div><p>每项性能结论都可下钻到对应的原始文件。</p></div>
      <dl class="summary-strip" aria-label="本地证据核验回执">
        <div><dt>本地文件核验</dt><dd>{_h(evidence_count)} / {_h(declared_evidence_count)}</dd></div>
        <div><dt>SHA-256</dt><dd>全部匹配</dd></div>
        <div><dt>逐行回执</dt><dd>{_h(marker_count)} 条</dd></div>
        <div><dt>证据集摘要</dt><dd><code>{_h(evidence_set_sha256)}</code></dd></div>
      </dl>
      <section aria-labelledby="claims-title"><h3 id="claims-title" class="subsection-title">结论账本</h3><div class="claim-list">{"".join(claim_items)}</div></section>
      <section aria-labelledby="raw-title"><h3 id="raw-title" class="subsection-title">原始证据下钻</h3><div class="evidence-list">{"".join(evidence_items)}</div></section>
    </section>
    <section role="tabpanel" id="panel-methodology" aria-labelledby="tab-methodology" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">边界与可重复性</p><h2>方法学</h2></div><p>比较设计、缓存策略和局限必须随结果交付。</p></div>
      <dl class="summary-strip" aria-label="运行身份">
        <div><dt>Commit</dt><dd><code>{_h(commit)}</code></dd></div>
        <div><dt>证据等级</dt><dd>{_h(run.get("evidence_grade", "暂无"))}</dd></div>
        <div><dt>Cache 策略</dt><dd>{_h(run.get("cache_policy", "暂无"))}</dd></div>
        <div><dt>Suite</dt><dd><code>{_h(run.get("suite_id", "暂无"))}</code></dd></div>
      </dl>
      {campaign_environment_block}
      {interpretation_boundaries}
      <dl class="methodology-list">{methodology_rows or '<dt>methodology</dt><dd>unavailable</dd>'}</dl>
      {methodology_diagnostics}
    </section>
  </main>
  <footer class="app-footer"><span>Schema evaluation-summary-v{_h(summary["schema_version"])}</span><span>生成时间 {_h(run.get("generated_at", "unavailable"))}</span></footer>
  <script src="assets/evaluation-dashboard.js" defer></script>
</body>
</html>
'''


def _csv_rows(summary: dict[str, Any]) -> Iterable[dict[str, Any]]:
    targets = {item["id"]: item for item in summary["targets"]}
    evidence = {item["id"]: item for item in summary["evidence"]}
    cache_default = summary["run"].get("cache_policy") or summary["methodology"].get("cache_policy", "unavailable")
    for benchmark in summary["benchmarks"]:
        for estimate in benchmark["estimates"]:
            target_id = estimate["target_id"]
            yield {
                "benchmark_id": benchmark["id"],
                "benchmark_label": benchmark["label"],
                "task": benchmark["task"],
                "status": benchmark["status"],
                "target_id": target_id,
                "target_label": targets[target_id]["label"],
                "load": estimate["load"],
                "estimate": estimate["value"],
                "lower": estimate["lower"],
                "upper": estimate["upper"],
                "unit": benchmark["unit"],
                "n": estimate["n"],
                "cache_policy": benchmark.get("cache_policy", cache_default),
                "evidence_ids": ";".join(benchmark["evidence_ids"]),
                "sources": _evidence_sources(benchmark["evidence_ids"], evidence),
            }
    for evaluation in summary["methodology"].get("supplementary_evaluations", []):
        for boot in evaluation["boots"]:
            evidence_ids = [boot["evidence_id"]]
            for sample in boot["concurrency"]:
                load = f'boot={boot["boot_id"]};concurrency={sample["concurrency"]}'
                load += f';digest={sample["workload_digest"]}'
                metrics = (
                    ("throughput", sample["throughput_milli_rps"] / 1000, "requests/s"),
                    ("goodput", sample["goodput_milli_rps"] / 1000, "requests/s"),
                    ("p90_turnaround", sample["p90_us"], "us"),
                    ("p90_wait", sample["wait_p90_us"], "us"),
                    ("jain_fairness", sample["fairness_jain_ppm"] / 1_000_000, "ratio"),
                    ("max_min_fairness", sample["max_min_fairness_ppm"] / 1_000_000, "ratio"),
                    ("isolation_rate", sample["isolated"] / sample["requests"], "ratio"),
                )
                for metric, value, unit in metrics:
                    yield {
                        "benchmark_id": f'{evaluation["id"]}.{metric}',
                        "benchmark_label": evaluation["label"],
                        "task": evaluation["task"],
                        "status": evaluation["status"],
                        "target_id": "agentos",
                        "target_label": "AgentOS-uCore",
                        "load": load,
                        "estimate": value,
                        "lower": "",
                        "upper": "",
                        "unit": unit,
                        "n": sample["requests"],
                        "cache_policy": "not_applicable",
                        "evidence_ids": boot["evidence_id"],
                        "sources": _evidence_sources(evidence_ids, evidence),
                    }


def _csv_bytes(summary: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(_csv_rows(summary))
    return output.getvalue().encode("utf-8")


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _safe_output_directory(path: Path) -> tuple[Path, Path]:
    output = absolute_lexical_path(path)
    if output == output.parent:
        _fail("Dashboard output cannot replace a filesystem root")
    try:
        parent = ensure_safe_directory(output.parent)
        reject_link_components(output)
    except (OSError, ValueError) as error:
        _fail(
            "Dashboard output path is missing, unsafe, or traverses a symlink "
            f"or junction: {error}"
        )
    try:
        info = output.lstat()
    except FileNotFoundError:
        return output, parent
    except OSError as error:
        _fail(f"Dashboard output cannot be inspected safely: {error}")
    if path_is_link(output, info.st_mode, file_info=info):
        _fail("Dashboard output must not be a symlink or junction")
    if not stat.S_ISDIR(info.st_mode):
        _fail("Dashboard output path must be a directory")
    return output, parent


def _verify_directory_tree(path: Path, label: str) -> None:
    try:
        walk_regular_files_no_links(path)
    except (OSError, ValueError) as error:
        _fail(f"{label} is not a bounded link-free directory tree: {error}")


def _require_existing_dashboard_site(path: Path) -> None:
    _verify_directory_tree(path, "existing Dashboard output")
    required = (
        "index.html",
        "evaluation-summary.json",
        "dashboard-verification.json",
        "metrics.csv",
        "assets/evaluation-dashboard.css",
        "assets/evaluation-dashboard.js",
    )
    try:
        payloads: dict[str, bytes] = {}
        for relative in required:
            payloads[relative] = read_regular_file(
                path.joinpath(*relative.split("/")),
                nonempty=True,
                maximum_bytes=MAX_PORTABLE_EVIDENCE_FILE_BYTES,
            )
        summary = strict_json_loads(payloads["evaluation-summary.json"])
        verification = strict_json_loads(
            payloads["dashboard-verification.json"]
        )
    except (OSError, UnicodeError, ValueError) as error:
        _fail(f"existing output is not an AgentOS Dashboard site: {error}")
    if (
        not isinstance(summary, dict)
        or summary.get("kind") != "agentos-evaluation-summary"
        or not isinstance(verification, dict)
        or verification.get("kind") != "agentos-dashboard-evidence-verification"
    ):
        _fail("existing output is not an AgentOS Dashboard site")


def _directory_is_empty(path: Path) -> bool:
    try:
        require_safe_directory(path)
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except (OSError, ValueError) as error:
        _fail(f"existing Dashboard output cannot be inspected safely: {error}")


def _new_private_staging_directory(parent: Path, output_name: str) -> Path:
    try:
        staging = Path(
            tempfile.mkdtemp(prefix=f".{output_name}.staging-", dir=parent)
        )
        os.chmod(staging, 0o700)
        return require_private_directory(staging)
    except (OSError, ValueError) as error:
        _fail(f"Dashboard staging directory cannot be created safely: {error}")


def _reject_output_containing_input(
    output: Path, protected: Path, label: str
) -> None:
    if _path_entry_exists(output):
        comparison_root = output.resolve(strict=True)
    else:
        comparison_root = output.parent.resolve(strict=True) / output.name
    try:
        if _path_entry_exists(protected):
            reject_link_components(protected)
            protected = protected.resolve(strict=True)
    except (OSError, ValueError) as error:
        _fail(f"{label} cannot be inspected before Dashboard publication: {error}")
    try:
        protected.relative_to(comparison_root)
    except ValueError:
        return
    _fail(f"Dashboard output must not contain the {label}")


def _reject_output_containing_sources(
    output: Path,
    summary_path: Path,
    summary: dict[str, Any],
    contract_root: Path,
    measurement_source_tree: Path,
) -> None:
    evidence_root = summary_path.parent
    protected: list[tuple[Path, str]] = [
        (summary_path, "summary input"),
        (contract_root / "ci" / "evaluation-suite.json", "evaluation contract"),
        (
            contract_root / "host_tools" / "assets" / "evaluation-dashboard.css",
            "Dashboard stylesheet",
        ),
        (
            contract_root / "host_tools" / "assets" / "evaluation-dashboard.js",
            "Dashboard script",
        ),
    ]
    for index, item in enumerate(summary["evidence"]):
        parts = _canonical_evidence_reference(
            item["path"], f"evidence[{index}].path"
        )
        protected.append(
            (evidence_root.joinpath(*parts), f"evidence[{index}] input")
        )
    for relative in (
        "campaign.json",
        "measurement-source-receipt.json",
        "suite.json",
        "run-plan.json",
        "metrics.jsonl",
        "raw",
        "scenario/report.json",
        "scenario/scenario-plan.json",
        "compatibility",
        *KERNEL_COST_FILES,
    ):
        protected.append(
            (
                evidence_root.joinpath(*relative.split("/")),
                f"evaluation sidecar {relative}",
            )
        )
    policy = measurement_source_policy_inventory()
    for entry in policy["entries"]:
        relative = entry["path"]
        protected.append(
            (
                measurement_source_tree.joinpath(*relative.split("/")),
                f"measurement source {relative}",
            )
        )
    for path, label in protected:
        _reject_output_containing_input(output, path, label)


def _remove_site_tree(path: Path) -> None:
    if not _path_entry_exists(path):
        return
    require_safe_directory(path)
    walk_regular_files_no_links(path)
    shutil.rmtree(path)


def _prepare_staged_site_permissions(staging: Path) -> None:
    if os.name == "nt":
        return
    try:
        directories, files = walk_directory_tree_no_links(staging)
        for path in files:
            os.chmod(path, 0o644)
        for path in directories:
            if path != staging:
                os.chmod(path, 0o755)
    except (OSError, ValueError) as error:
        _fail(f"staged Dashboard permissions cannot be prepared safely: {error}")


def _replace_site_directory(source: Path, destination: Path) -> None:
    """Rename one verified sibling directory without copying partial contents."""

    os.replace(source, destination)


def _publish_staged_site(staging: Path, output: Path, parent: Path) -> None:
    """Publish a complete sibling tree, restoring the old tree on failure."""

    backup = staging.with_name(f"{staging.name}.previous")
    had_previous = False
    try:
        require_private_directory(staging)
        require_safe_directory(parent)
        reject_link_components(output)
        if _path_entry_exists(backup):
            _fail("Dashboard publication backup path is unexpectedly occupied")
        if _path_entry_exists(output):
            had_previous = True
            require_safe_directory(output)
            if not _directory_is_empty(output):
                _require_existing_dashboard_site(output)
        if os.name != "nt":
            os.chmod(staging, 0o755)
        if had_previous:
            _replace_site_directory(output, backup)
        _replace_site_directory(staging, output)
    except BaseException as error:
        rollback_error: BaseException | None = None
        previous_moved = had_previous and _path_entry_exists(backup)
        if previous_moved:
            try:
                if _path_entry_exists(output):
                    if _path_entry_exists(staging):
                        raise OSError(
                            "publication destination changed while staging still exists"
                        )
                    _replace_site_directory(output, staging)
                _replace_site_directory(backup, output)
            except BaseException as restore_error:
                rollback_error = restore_error
        if isinstance(error, (OSError, ValueError, DashboardError)):
            if rollback_error is not None:
                _fail(
                    "Dashboard publication failed and the previous site could not be "
                    f"restored: publish={error}; rollback={rollback_error}"
                )
            suffix = "; previous site restored" if previous_moved else ""
            _fail(f"Dashboard publication failed{suffix}: {error}")
        raise
    if had_previous:
        try:
            _remove_site_tree(backup)
        except (OSError, ValueError) as error:
            _fail(f"published Dashboard is complete but old-site cleanup failed: {error}")


def _verify_staged_site(
    staging: Path, expected: dict[str, tuple[str, int]]
) -> None:
    try:
        files = walk_regular_files_no_links(
            staging,
            max_files=len(expected),
            max_directories=MAX_PORTABLE_EVIDENCE_FILES * 64 + 8,
            max_total_bytes=sum(size for _digest, size in expected.values()),
            max_depth=64,
        )
    except (OSError, ValueError) as error:
        _fail(f"staged Dashboard cannot be verified safely: {error}")
    actual = {path.relative_to(staging).as_posix(): path for path in files}
    if set(actual) != set(expected):
        _fail("staged Dashboard inventory is incomplete or contains extra files")
    for relative, (digest, size) in expected.items():
        try:
            data = read_regular_file(actual[relative], maximum_bytes=size)
        except (OSError, ValueError) as error:
            _fail(f"staged Dashboard file cannot be re-read safely: {relative}: {error}")
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            _fail(f"staged Dashboard file changed before publication: {relative}")


def render(
    summary_path: Path,
    output_dir: Path,
    *,
    contract_root: Path,
    measurement_source_tree: Path | None = None,
) -> None:
    output_dir, output_parent = _safe_output_directory(output_dir)
    try:
        contract_root = require_safe_directory(contract_root).resolve(strict=True)
    except (OSError, ValueError) as error:
        _fail(f"trusted contract root is missing or unsafe: {error}")
    try:
        measurement_source_tree = require_safe_directory(
            measurement_source_tree or contract_root
        ).resolve(strict=True)
    except (OSError, ValueError) as error:
        _fail(f"measurement source tree is missing or unsafe: {error}")
    summary_path = summary_path.resolve(strict=True)
    if not summary_path.is_file():
        _fail("summary input must be a regular file")
    _reject_output_containing_input(output_dir, contract_root, "trusted contract root")
    _reject_output_containing_input(
        output_dir, measurement_source_tree, "measurement source tree"
    )
    source_summary = summary_path.read_bytes()
    validated = validate_summary(read_strict_json(summary_path))
    _reject_output_containing_sources(
        output_dir,
        summary_path,
        validated,
        contract_root,
        measurement_source_tree,
    )
    if summary_path.read_bytes() != source_summary:
        _fail("summary input changed while it was being validated")
    verification, scenario_details, kernel_cost, campaign_environment = _verify_evidence_files(
        summary_path.parent,
        validated,
        source_summary,
        contract_root=contract_root,
        measurement_source_tree=measurement_source_tree,
    )
    _replay_scientific_contract(
        summary_path.parent,
        summary_path,
        validated,
        contract_root=contract_root,
        measurement_source_tree=measurement_source_tree,
    )
    summary = _canonical_dashboard_summary(validated, kernel_cost)
    staging = _new_private_staging_directory(output_parent, output_dir.name)
    try:
        expected: dict[str, tuple[str, int]] = {}

        def write_expected(relative: str, data: bytes) -> None:
            _atomic_write_bytes(staging.joinpath(*relative.split("/")), data)
            expected[relative] = (hashlib.sha256(data).hexdigest(), len(data))

        assets_source = contract_root / "host_tools" / "assets"
        ensure_safe_directory(staging / "assets")
        for name in ("evaluation-dashboard.css", "evaluation-dashboard.js"):
            source = assets_source / name
            try:
                data = read_regular_file(
                    source,
                    nonempty=True,
                    maximum_bytes=MAX_PORTABLE_EVIDENCE_FILE_BYTES,
                )
            except (OSError, ValueError) as error:
                _fail(f"dashboard asset missing or unsafe: {source}: {error}")
            write_expected(f"assets/{name}", data)

        expected.update(
            _write_portable_evidence(summary_path.parent, staging, validated)
        )
        write_expected(
            "index.html",
            _page(
                summary,
                verification,
                scenario_details,
                kernel_cost,
                campaign_environment,
            ).encode("utf-8"),
        )
        write_expected(
            "evaluation-summary.json",
            (
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8"),
        )
        write_expected(
            "dashboard-verification.json",
            (
                json.dumps(
                    verification, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n"
            ).encode("utf-8"),
        )
        write_expected("metrics.csv", _csv_bytes(summary))
        _prepare_staged_site_permissions(staging)
        _verify_staged_site(staging, expected)
        _publish_staged_site(staging, output_dir, output_parent)
    except BaseException:
        try:
            _remove_site_tree(staging)
        except (OSError, ValueError):
            pass
        raise
    if _path_entry_exists(staging):
        _remove_site_tree(staging)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="versioned evaluation-summary JSON")
    parser.add_argument("output", type=Path, help="dashboard output directory")
    parser.add_argument(
        "--contract-root",
        type=Path,
        required=True,
        help="trusted repository root containing the evaluation contract and assets",
    )
    args = parser.parse_args(argv)
    try:
        render(args.summary, args.output, contract_root=args.contract_root)
    except (DashboardError, OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
