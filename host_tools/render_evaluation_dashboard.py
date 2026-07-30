#!/usr/bin/env python3
"""Render a self-contained, evidence-bound AgentOS evaluation dashboard."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
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
from evaluation_contract import (
    EvaluationError as EvaluationContractError,
    verify as verify_evaluation_contract,
)
from strict_json import read_strict_json, strict_json_loads


SCHEMA_VERSION = 1
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
    "samples",
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
SCENARIO_STATUSES = {"pass", "partial", "fail", "unavailable"}
SCENARIO_PERFORMANCE_STATUSES = {"supported", "inconclusive", "failed", "unavailable"}
CLAIM_STATUSES = {"supported", "not_supported", "unavailable"}
EVIDENCE_STATUSES = {"verified", "unverified", "unavailable", "invalid"}
RUN_STATUSES = {"measured", "unavailable", "failed"}
DIRECTIONS = {"lower_is_better", "higher_is_better", "neutral"}
TASK_IDS = tuple(f"task{index}" for index in range(1, 7))
SHA256 = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
BOOTSTRAP_REPETITIONS = 2_000
MINIMUM_SCENARIO_BOOTS = 7
MINIMUM_BENCHMARK_BOOTS = 7
MINIMUM_INNER_PAIRS = 7
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
MAX_SCENARIO_REPORT_BYTES = 32 << 20
MAX_SCENARIO_SAMPLES = 128
MAX_SCENARIO_PROGRAMS = 128
MAX_SCENARIO_MODULES = 64
MAX_SCENARIO_DURATION_MS = 3_600_000
SCENARIO_KEY_OUTCOMES = (
    "research_rerun",
    "workflow_stage",
    "artifact_derivation",
    "llm_response",
)
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
    "claim_gate",
    "n",
    "median",
    "ci_low",
    "ci_high",
    "relative_median_percent",
    "relative_ci_low",
    "relative_ci_high",
    "sign_test",
    "bootstrap",
    "samples",
}

STATUS_ZH = {
    "measured": "已测量",
    "pass": "通过",
    "partial": "部分通过",
    "supported": "证据支持",
    "not_supported": "暂不支持",
    "inconclusive": "证据不足",
    "unverified": "未核验",
    "verified": "已核验",
    "unavailable": "unavailable",
    "failed": "失败",
    "fail": "失败",
    "invalid": "无效",
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
            relative_samples = [paired_by_trial[trial][1] for trial in sorted(paired_by_trial)]
            if any(value is None for value in relative_samples):
                _fail(f"{pair_path} numeric relative summary requires every paired relative sample")
            relative_samples = [float(value) for value in relative_samples]
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
        p_value = _validate_sign_test(pair["sign_test"], f"{pair_path}.sign_test", n, improvements)
        gates.append(
            claim_gate is not None
            and direction != "neutral"
            and n >= MINIMUM_BENCHMARK_BOOTS
            and median > 0
            and ci_low >= claim_gate["minimum_absolute_improvement_us"]
            and relative_low is not None
            and relative_low >= claim_gate["minimum_relative_improvement_percent"]
            and min(baseline_samples.values())
            >= claim_gate["minimum_baseline_duration_us"]
            and p_value <= headline_alpha
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
    if isinstance(hypothesis_count, bool) or not isinstance(hypothesis_count, int) or hypothesis_count != 3:
        _fail("methodology.multiple_testing.hypothesis_count must be the registered three")
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


def _binding_sha256(value: Any, domain: str) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _validate_scenario_performance(
    raw: Any,
    path: str,
    functional_status: str,
) -> bool:
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
    relatives: list[float] = []
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
        else:
            relatives.append(expected_relative)

    expected_seed = _binding_sha256(samples, "scenario-paired-bootstrap-seed-v1")
    bootstrap = _require_object(value["bootstrap"], f"{path}.bootstrap")
    if set(bootstrap) != {"method", "confidence", "repetitions", "seed_sha256"}:
        _fail(f"{path}.bootstrap fields do not match the scenario bootstrap schema")
    if (
        bootstrap["method"] != "deterministic_percentile_median"
        or _require_number(bootstrap["confidence"], f"{path}.bootstrap.confidence") != 0.95
        or _nonnegative_int(bootstrap["repetitions"], f"{path}.bootstrap.repetitions")
        != BOOTSTRAP_REPETITIONS
        or bootstrap["seed_sha256"] != expected_seed
    ):
        _fail(f"{path}.bootstrap does not match the bound scenario samples")

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
        expected_relative = float(statistics.median(relatives))
        expected_relative_low, expected_relative_high = _bootstrap_interval(
            relatives, f"{expected_seed}:relative"
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
    p_value = _validate_sign_test(
        value["sign_test"],
        f"{path}.sign_test",
        n,
        improvements,
        alternative="agentos_lower_makespan",
    )
    return (
        functional_status == "pass"
        and n >= MINIMUM_SCENARIO_BOOTS
        and abs(orders["AB"] - orders["BA"]) <= 1
        and ci_low >= thresholds["minimum_absolute_improvement_ms"]
        and relative_low is not None
        and float(relative_low) >= thresholds["minimum_relative_improvement_percent"]
        and min(plain_values) >= thresholds["minimum_baseline_makespan_ms"]
        and p_value <= 0.05
    )


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
        missing = DIAGNOSTIC_FIELDS - set(item)
        if missing:
            _fail(f"{item_path} missing required fields {sorted(missing)}")
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
                for field in ("duration_us", "work_units", "index_rebuild_records")
            ):
                _fail(f"{item_path} unavailable/failed diagnostic must not contain measurements")
            continue
        if not cache_states or len(cache_states) != len(set(cache_states)) or not samples_raw:
            _fail(f"{item_path} measured diagnostic requires cache states and samples")
        values_by_field: dict[str, list[float]] = {
            "duration_us": [],
            "work_units": [],
            "index_rebuild_records": [],
        }
        observed_states: set[str] = set()
        boot_ids: set[str] = set()
        for sample_index, raw_sample in enumerate(samples_raw):
            sample_path = f"{item_path}.samples[{sample_index}]"
            sample = _require_object(raw_sample, sample_path)
            expected_fields = {
                "boot_id", "cache", "duration_us", "work_units",
                "index_rebuild_records", "evidence_id", "source_line",
            }
            if set(sample) != expected_fields:
                _fail(f"{sample_path} fields do not match diagnostic sample schema")
            boot_id = _require_string(sample["boot_id"], f"{sample_path}.boot_id")
            if boot_id in boot_ids:
                _fail(f"{sample_path} duplicates boot_id")
            boot_ids.add(boot_id)
            cache = _require_string(sample["cache"], f"{sample_path}.cache")
            if cache not in {"ready", "cold-rebuild"}:
                _fail(f"{sample_path}.cache has unknown readiness state")
            observed_states.add(cache)
            for field in values_by_field:
                number = _require_number(sample[field], f"{sample_path}.{field}")
                if number < 0:
                    _fail(f"{sample_path}.{field} must be non-negative")
                values_by_field[field].append(number)
            rebuild = sample["index_rebuild_records"]
            if (cache == "ready" and rebuild != 0) or (cache == "cold-rebuild" and rebuild <= 0):
                _fail(f"{sample_path} cache state conflicts with rebuild records")
            evidence_id = _require_string(sample["evidence_id"], f"{sample_path}.evidence_id")
            if evidence_id not in benchmark_evidence or evidence[evidence_id]["status"] != "verified":
                _fail(f"{sample_path} requires benchmark-bound verified evidence")
            source_line = sample["source_line"]
            if isinstance(source_line, bool) or not isinstance(source_line, int) or source_line <= 0:
                _fail(f"{sample_path}.source_line must be a positive integer")
        if cache_states != sorted(observed_states):
            _fail(f"{item_path}.cache_states do not match diagnostic samples")
        for field, samples in values_by_field.items():
            _validate_diagnostic_stat(item[field], f"{item_path}.{field}", samples)
    if diagnostics and seen_loads != set(loads):
        _fail(f"{path} must cover every benchmark load when diagnostics are present")


def validate_summary(raw: Any) -> dict[str, Any]:
    """Validate summary schema v1 and every evidence-bearing relation."""

    root = _require_object(raw, "summary")
    fields = set(root)
    if fields != TOP_LEVEL_FIELDS:
        _fail(
            "summary fields mismatch: "
            f"missing={sorted(TOP_LEVEL_FIELDS - fields)} extra={sorted(fields - TOP_LEVEL_FIELDS)}"
        )
    if root["schema_version"] != SCHEMA_VERSION:
        _fail(f"schema_version must be {SCHEMA_VERSION}")
    if root["kind"] != "agentos-evaluation-summary":
        _fail("kind must be 'agentos-evaluation-summary'")

    run = _require_object(root["run"], "run")
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
        _require_string(item["path"], f"{path}.path")
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
    raw_claims = _require_list(root["claims"], "claims")
    claim_benchmark_ids = {
        _require_string(claim.get("benchmark_id"), f"claims[{index}].benchmark_id")
        for index, claim in enumerate(raw_claims)
        if isinstance(claim, dict)
    }
    if len(claim_benchmark_ids) != len(raw_claims):
        _fail("every claim must be an object with a unique benchmark_id")
    headline_alpha = _validate_multiple_testing(methodology, claim_benchmark_ids)

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
        for sample_index, raw_sample in enumerate(samples):
            sample_path = f"{path}.samples[{sample_index}]"
            sample = _require_object(raw_sample, sample_path)
            key = _sample_key(sample, sample_path, set(targets), set(loads))
            value = _require_number(sample.get("value"), f"{sample_path}.value")
            trial_raw = sample.get("trial")
            if isinstance(trial_raw, bool) or not isinstance(trial_raw, (str, int)) or str(trial_raw) == "":
                _fail(f"{sample_path}.trial must be a non-empty string or integer")
            trial = str(trial_raw)
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
        if functional_status not in SCENARIO_STATUSES:
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
        if functional_status in {"pass", "partial"} and not verified:
            _fail(f"{path} functional status requires verified evidence")
        if performance_status in {"supported", "inconclusive"}:
            if not verified:
                _fail(f"{path} performance status requires verified evidence")
            if not any(
                evidence[item_id]["kind"] == "research-platform-scenario"
                for item_id in scenario_evidence
            ):
                _fail(f"{path} performance statistics require bound scenario-report evidence")
            supports = _validate_scenario_performance(
                scenario["performance"], f"{path}.performance", functional_status
            )
            expected_performance_status = "supported" if supports else "inconclusive"
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

    has_supported_result = any(
        claim["status"] == "supported" for claim in claims_list
    ) or any(
        scenario["performance_status"] == "supported" for scenario in scenarios_list
    )
    if has_supported_result:
        commit = run.get("commit")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            _fail("supported results require run.commit as a full lowercase commit")
        if run.get("evidence_grade") != "E2-local-raw":
            _fail("supported results require contract-derived run.evidence_grade")

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
    return "../" + "/".join(quote(part, safe="-._~") for part in parts)


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


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


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _read_evidence_file(root: Path, parts: tuple[str, ...], path: str) -> bytes:
    candidate = root
    for part in parts:
        candidate /= part
        try:
            if _is_link_like(candidate):
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
        data = resolved.read_bytes()
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


def _scenario_percentile(values: list[int], quantile: float) -> float:
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


def _extract_scenario_detail(
    data: bytes,
    item: dict[str, Any],
    item_path: str,
    summary_document: dict[str, Any],
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
    if report["schema_version"] != 1:
        _fail(f"{item_path} scenario report schema_version must be 1")
    scenario_id = _scenario_string(report["scenario_id"], f"{item_path}.report.scenario_id")
    source_commit = _scenario_string(
        report["source_commit"], f"{item_path}.report.source_commit", maximum=40
    )
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        _fail(f"{item_path} scenario report source_commit must be a full lowercase commit")
    run_id = _scenario_string(report["run_id"], f"{item_path}.report.run_id", maximum=128)
    report_status = _scenario_string(report["status"], f"{item_path}.report.status")
    if report_status not in {"supported", "inconclusive"}:
        _fail(f"{item_path} verified scenario report must be supported or inconclusive")
    supplied_report_sha256 = _scenario_sha256(
        report["report_sha256"], f"{item_path}.report.report_sha256"
    )
    unsigned_report = dict(report)
    unsigned_report.pop("report_sha256")
    if supplied_report_sha256 != _binding_sha256(unsigned_report, "scenario-report-v1"):
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
    if bound_scenario["performance_status"] in {"supported", "inconclusive"}:
        if bound_scenario["performance_status"] != report_status:
            _fail(f"{item_path} scenario report status differs from the summary scenario")

    samples = _require_list(report["samples"], f"{item_path}.report.samples")
    if not 0 < len(samples) <= MAX_SCENARIO_SAMPLES:
        _fail(
            f"{item_path} scenario report must contain 1 through {MAX_SCENARIO_SAMPLES} samples"
        )
    sample_ids: set[str] = set()
    program_order: list[str] | None = None
    timings: dict[str, dict[str, list[int]]] = {
        "plain": {},
        "agentos": {},
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
        raw_order = _require_list(binding.get("program_order"), f"{sample_path}.binding.program_order")
        current_order = [
            _scenario_string(value, f"{sample_path}.binding.program_order[{index}]", maximum=64)
            for index, value in enumerate(raw_order)
        ]
        if not current_order or len(current_order) > MAX_SCENARIO_PROGRAMS or len(current_order) != len(set(current_order)):
            _fail(f"{sample_path}.binding.program_order is empty, duplicated, or over the bound")
        if program_order is None:
            program_order = current_order
            timings = {
                target: {program: [] for program in program_order}
                for target in ("plain", "agentos")
            }
        elif current_order != program_order:
            _fail(f"{sample_path}.binding.program_order differs between samples")

        source_receipts = _require_object(
            binding.get("source_receipts"), f"{sample_path}.binding.source_receipts"
        )
        targets = _require_object(sample["targets"], f"{sample_path}.targets")
        if set(targets) != {"plain", "agentos"} or set(source_receipts) != {"plain", "agentos"}:
            _fail(f"{sample_path} must bind plain and agentos targets exactly")
        for target in ("plain", "agentos"):
            target_path = f"{sample_path}.targets.{target}"
            measurement = _require_object(targets[target], target_path)
            makespan = _scenario_uint(
                measurement.get("makespan_ms"), f"{target_path}.makespan_ms",
                maximum=MAX_SCENARIO_DURATION_MS,
            )
            makespans[target].append(makespan)
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

    assert program_order is not None
    report_summary = _require_object(report["summary"], f"{item_path}.report.summary")
    independent_boots = _scenario_uint(
        report_summary.get("independent_boots"),
        f"{item_path}.report.summary.independent_boots",
        maximum=MAX_SCENARIO_SAMPLES,
    )
    if independent_boots != len(samples):
        _fail(f"{item_path} scenario summary boot count differs from its samples")
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
    if bound_scenario["functional_status"] in {"pass", "partial"} and functional_status != "passed":
        _fail(f"{item_path} scenario functional status lacks passed module evidence")

    paired_improvement = report_summary.get("paired_improvement")
    if bound_scenario["performance"] is not None and paired_improvement != bound_scenario["performance"]:
        _fail(f"{item_path} scenario report performance differs from the summary scenario")
    return {
        "evidence_id": item["id"],
        "scenario_id": scenario_id,
        "status": report_status,
        "independent_boots": independent_boots,
        "programs": display_programs,
        "functional": {
            "status": functional_status,
            "required_modules": modules,
            "verified_boots": verified_boots,
        },
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
        if path.exists() or path.is_symlink() or _is_link_like(path)
    }
    directory_present = (
        kernel_directory.exists()
        or kernel_directory.is_symlink()
        or _is_link_like(kernel_directory)
    )
    if not present and not directory_present:
        return None, []
    if present != set(KERNEL_COST_FILES):
        missing = sorted(set(KERNEL_COST_FILES) - present)
        _fail(f"kernel-cost sidecar is incomplete; missing={missing}")
    if _is_link_like(kernel_directory) or not kernel_directory.is_dir():
        _fail("kernel-build must be a regular, non-link directory")

    expected_files = set(KERNEL_COST_FILES)
    expected_directories = {"kernel-build", "kernel-build/raw"}
    try:
        descendants = list(kernel_directory.rglob("*"))
    except OSError as error:
        _fail(f"cannot inventory kernel-build sidecar: {error}")
    for descendant in descendants:
        relative = descendant.relative_to(evidence_root).as_posix()
        if _is_link_like(descendant):
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
    return {
        "status": fragment_run["status"],
        "run_id": fragment_run["id"],
        "commit": fragment_run["commit"],
        "baseline": {"id": baseline_id, "label": baseline_target["label"]},
        "agentos": {"id": treatment_id, "label": treatment_target["label"]},
        "metrics": metric_rows,
        "evidence_file_count": len(records),
    }, records


def _verify_evidence_files(
    evidence_root: Path,
    summary: dict[str, Any],
    source_summary: bytes,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    records: list[dict[str, Any]] = []
    scenario_details: list[dict[str, Any]] = []
    marker_count = 0
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
        if item["kind"] == "research-platform-scenario" and item["status"] == "verified":
            scenario_details.append(
                _extract_scenario_detail(data, item, item_path, summary)
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

    kernel_cost, kernel_cost_records = _verify_kernel_cost_sidecar(
        evidence_root, summary
    )
    records.extend(kernel_cost_records)
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
        "kernel_cost": {
            "status": (
                "unavailable" if kernel_cost is None
                else "verified" if kernel_cost["status"] == "measured"
                else kernel_cost["status"]
            ),
            "evidence_file_count": len(kernel_cost_records),
        },
        "evidence_set_sha256": evidence_set_sha256,
        "evidence": records,
    }
    scenario_details.sort(key=lambda detail: detail["scenario_id"])
    scenario_ids = [detail["scenario_id"] for detail in scenario_details]
    if len(scenario_ids) != len(set(scenario_ids)):
        _fail("verified scenario reports repeat a scenario_id")
    return verification, scenario_details, kernel_cost


def _has_supported_result(summary: dict[str, Any]) -> bool:
    return any(
        claim["status"] == "supported" for claim in summary["claims"]
    ) or any(
        scenario["performance_status"] == "supported"
        for scenario in summary["scenarios"]
    )


def _replay_supported_contract(
    evidence_root: Path,
    summary_path: Path,
    summary: dict[str, Any],
) -> None:
    """Rebuild every supported result from raw Guest markers before rendering."""

    if not _has_supported_result(summary):
        return
    repository_suite = Path(__file__).resolve().parents[1] / "ci" / "evaluation-suite.json"
    suite_path = evidence_root / "suite.json"
    if not suite_path.exists():
        suite_path = repository_suite
    required = {
        "suite": suite_path,
        "run plan": evidence_root / "run-plan.json",
        "metrics": evidence_root / "metrics.jsonl",
    }
    for label, path in required.items():
        if not path.is_file() or _is_link_like(path):
            _fail(f"supported results require a regular {label} file for contract replay")
    raw_root = evidence_root / "raw"
    if not raw_root.is_dir() or _is_link_like(raw_root):
        _fail("supported results require a regular raw Guest evidence directory")
    scenario_report = evidence_root / "scenario" / "report.json"
    scenario_plan = evidence_root / "scenario" / "scenario-plan.json"
    scenario_args: tuple[Path | None, Path | None]
    if scenario_report.exists() or scenario_plan.exists():
        if (
            not scenario_report.is_file()
            or not scenario_plan.is_file()
            or _is_link_like(scenario_report)
            or _is_link_like(scenario_plan)
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


def _chart(benchmark: dict[str, Any], targets: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]], *, suffix: str) -> str:
    if benchmark["status"] != "measured":
        return (
            '<section class="unavailable-block" aria-label="无可用图表">'
            '<strong>unavailable</strong><span>当前没有满足证据合同的测量，不绘制推断图。</span></section>'
        )
    estimates = _estimate_map(benchmark)
    baseline = benchmark["baseline"]
    treatment = benchmark["treatment"]
    loads = [str(value) for value in benchmark["loads"]]
    values = [float(item[key]) for item in estimates.values() for key in ("lower", "upper")]
    low, high = min(values), max(values)
    if low == high:
        pad = abs(low) * 0.1 or 1.0
        low -= pad
        high += pad
    else:
        pad = (high - low) * 0.08
        low -= pad
        high += pad
    plot_left, plot_right = 176.0, 748.0
    row_height = 68
    height = 72 + len(loads) * row_height

    def x(value: float) -> float:
        return plot_left + (value - low) / (high - low) * (plot_right - plot_left)

    svg: list[str] = [
        f'<svg viewBox="0 0 800 {height}" role="img" aria-labelledby="chart-title-{_h(suffix)} chart-desc-{_h(suffix)}">',
        f'<title id="chart-title-{_h(suffix)}">{_h(benchmark["label"])}配对区间图</title>',
        f'<desc id="chart-desc-{_h(suffix)}">单位 {_h(benchmark["unit"])}，展示基线与 AgentOS 的估计值及区间。</desc>',
        f'<line class="axis" x1="{plot_left}" y1="36" x2="{plot_right}" y2="36" />',
        f'<text class="axis-label" x="{plot_left}" y="22">{_h(_value(low))}</text>',
        f'<text class="axis-label" x="{plot_right}" y="22" text-anchor="end">{_h(_value(high))} {_h(benchmark["unit"])}</text>',
    ]
    ns: list[int] = []
    for row, load in enumerate(loads):
        y_base = 67 + row * row_height
        svg.append(f'<text class="load-label" x="8" y="{y_base + 12}">负载 {_h(load)}</text>')
        first_x = x(float(estimates[(baseline, load)]["value"]))
        second_x = x(float(estimates[(treatment, load)]["value"]))
        svg.append(f'<line class="pair-link" x1="{first_x:.2f}" y1="{y_base}" x2="{second_x:.2f}" y2="{y_base + 24}" />')
        for offset, target_id, css_class in ((0, baseline, "baseline"), (24, treatment, "treatment")):
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
    n_text = str(ns[0]) if ns and len(set(ns)) == 1 else f"{min(ns)}-{max(ns)}"
    source = _evidence_sources(benchmark["evidence_ids"], evidence)
    evidence_buttons = " ".join(
        f'<button class="evidence-link" type="button" data-evidence-ref="{_h(item_id)}">{_h(item_id)}</button>'
        for item_id in benchmark["evidence_ids"]
    )
    return (
        f'<figure class="interval-chart" data-chart-unit="{_h(benchmark["unit"])}" '
        f'data-chart-n="{_h(n_text)}" data-chart-source="{_h(source)}">'
        f'<div class="chart-scroll" role="region" tabindex="0" aria-label="{_h(benchmark["label"])}图表，可横向滚动">'
        + "".join(svg)
        + "</div>"
        + '<figcaption><span><strong>单位</strong> '
        + _h(benchmark["unit"])
        + '</span><span><strong>n</strong> '
        + _h(n_text)
        + '</span><span><strong>来源</strong> '
        + evidence_buttons
        + '</span></figcaption></figure>'
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
        evidence_links = " ".join(
            f'<button class="evidence-link" type="button" data-evidence-ref="{_h(evidence_id)}">{_h(evidence_id)}</button>'
            for evidence_id in evidence_ids
        ) or "unavailable"
        cache = " / ".join(item["cache_states"]) if item["cache_states"] else "unavailable"
        rows.append(
            '<tr>'
            f'<th scope="row">{_h(item["load"])}</th><td>{_status(item["status"])}</td>'
            f'<td><code>{_h(cache)}</code></td>'
            f'<td>{stat(item["duration_us"], item["unit"])}</td>'
            f'<td>{stat(item["work_units"], "work units")}</td>'
            f'<td>{stat(item["index_rebuild_records"], "records")}</td>'
            f'<td>{evidence_links}</td></tr>'
        )
    heading = min(max(heading_level, 2), 6)
    return (
        '<section class="diagnostic-block">'
        f'<h{heading}>索引准备成本与缓存状态</h{heading}>'
        '<p class="diagnostic-note">准备阶段独立计量；ready 查询优势不包含未披露的索引建立成本。</p>'
        '<div class="table-scroll" role="region" tabindex="0" '
        'aria-label="索引准备成本与缓存状态表，可横向滚动"><table class="diagnostic-table"><thead><tr>'
        '<th>负载</th><th>状态</th><th>Cache</th><th>准备耗时</th><th>工作量</th><th>重建记录</th><th>原始证据</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )
def _task_matrix(scenarios: list[dict[str, Any]]) -> str:
    rank = {"pass": 0, "partial": 1, "unavailable": 2, "fail": 3}
    cells: list[str] = []
    for task in TASK_IDS:
        task_items = [item for item in scenarios if _task_id(item["task"], "scenario.task") == task]
        if not task_items:
            status = "unavailable"
            detail = "未提供动态场景"
        else:
            status = max((item["functional_status"] for item in task_items), key=rank.__getitem__)
            detail = "、".join(item["label"] for item in task_items)
        number = task.removeprefix("task")
        cells.append(
            f'<div class="task-cell task-cell--{_h(status)}"><span class="task-number">任务 {number}</span>'
            f'{_status(status)}<span class="task-detail">{_h(detail)}</span></div>'
        )
    return '<div class="task-matrix" aria-label="赛题任务一至六覆盖矩阵">' + "".join(cells) + "</div>"


def _claim_text(
    claim: dict[str, Any],
    benchmarks: dict[str, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    benchmark = benchmarks[claim["benchmark_id"]]
    if claim["status"] == "supported":
        title = (
            f"{targets[benchmark['treatment']]['label']} 的 {benchmark['label']}"
            "通过预注册性能支持门"
        )
    elif claim["status"] == "not_supported":
        title = f"{benchmark['label']}未通过预注册性能支持门"
    else:
        title = f"{benchmark['label']}没有可用的合同测量"
    details: list[str] = []
    for pair in benchmark["paired"]:
        if pair["status"] != "measured":
            continue
        relative = (
            f"，相对 95% CI {_value(float(pair['relative_ci_low']))}%.."
            f"{_value(float(pair['relative_ci_high']))}%"
            if pair["relative_ci_low"] is not None
            else "，相对改善 unavailable"
        )
        details.append(
            f"负载 {pair['load']}：改善中位数 {_value(float(pair['median']))} {benchmark['unit']}，"
            f"95% CI {_value(float(pair['ci_low']))}..{_value(float(pair['ci_high']))}{relative}，"
            f"sign-test p={_value(float(pair['sign_test']['p_value']))}"
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
    return (
        f"n={performance['n']}；配对 makespan 改善中位数 "
        f"{_value(float(performance['median']))} {performance['unit']}；95% CI "
        f"{_value(float(performance['ci_low']))}..{_value(float(performance['ci_high']))} "
        f"{performance['unit']}；sign-test p={_value(float(performance['sign_test']['p_value']))}"
    )


def _canonical_dashboard_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Remove narrative authority from caller-supplied presentation fields."""

    canonical = copy.deepcopy(summary)
    canonical["run"].pop("conclusion", None)
    benchmarks = {item["id"]: item for item in canonical["benchmarks"]}
    targets = {item["id"]: item for item in canonical["targets"]}
    for claim in canonical["claims"]:
        claim["title"], claim["effect"] = _claim_text(claim, benchmarks, targets)
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


def _scenario_details_html(details: list[dict[str, Any]]) -> str:
    if not details:
        return (
            '<div class="unavailable-block"><strong>场景明细 unavailable</strong>'
            '<span>没有已核验的 research-platform-scenario 报告。</span></div>'
        )
    blocks: list[str] = []
    for detail in details:
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
        module_status = "verified" if detail["functional"]["status"] == "passed" else "unavailable"
        module_rows = "".join(
            '<tr>'
            f'<th scope="row"><code>{_h(module)}</code></th>'
            f'<td>{_status(module_status)}</td>'
            f'<td>{_h(detail["functional"]["verified_boots"])} / {_h(detail["independent_boots"])}</td>'
            '</tr>'
            for module in detail["functional"]["required_modules"]
        )
        outcome_rows = "".join(
            '<tr>'
            f'<th scope="row"><code>{_h(outcome["key"])}</code></th>'
            f'<td>{_status(outcome["status"])}</td>'
            f'<td>{_h(outcome["verified_pairs"])} / {_h(detail["independent_boots"])}</td>'
            '</tr>'
            for outcome in detail["outcomes"]
        )
        blocks.append(
            '<article class="benchmark-block scenario-detail">'
            '<header><div>'
            f'<p class="eyebrow">{_h(detail["independent_boots"])} 个独立 Guest boot</p>'
            f'<h3>{_h(detail["scenario_id"])}</h3></div>{_status(detail["status"])}</header>'
            '<section class="diagnostic-block" aria-label="逐程序耗时">'
            '<h4>逐程序耗时</h4><p class="diagnostic-note">来自已核验场景报告；单位 ms，不提升为新的 headline claim。</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="逐程序耗时表，可横向滚动"><table><thead><tr><th>程序</th><th>Plain p50</th><th>Plain p95</th>'
            f'<th>{_h("AgentOS")} p50</th><th>{_h("AgentOS")} p95</th></tr></thead><tbody>{program_rows}</tbody></table></div>'
            '</section>'
            '<section class="diagnostic-block" aria-label="AgentOS 功能模块">'
            '<h4>AgentOS 功能模块</h4><p class="diagnostic-note">required_modules 的逐 boot 验收回执。</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="AgentOS 功能模块表，可横向滚动"><table><thead><tr><th>模块</th><th>状态</th><th>核验 boot</th>'
            f'</tr></thead><tbody>{module_rows}</tbody></table></div></section>'
            '<section class="diagnostic-block" aria-label="关键 outcome 一致性">'
            '<h4>预注册 key outcome 一致性</h4>'
            '<p class="diagnostic-note">仅覆盖预注册关键 outcome 的配对回执，不代表完整最终状态相同。</p>'
            '<div class="table-scroll" role="region" tabindex="0" aria-label="关键 outcome 一致性表，可横向滚动"><table><thead><tr><th>Key outcome</th><th>状态</th><th>一致配对</th>'
            f'</tr></thead><tbody>{outcome_rows}</tbody></table></div></section>'
            f'<footer><strong>绑定证据</strong> <button class="evidence-link" type="button" data-evidence-ref="{_h(detail["evidence_id"])}">{_h(detail["evidence_id"])}</button></footer>'
            '</article>'
        )
    return "".join(blocks)


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
        '<p class="diagnostic-note">ELF、.text、.data 与 .bss 是体积和静态成本护栏，'
        '不是 CPU 性能证据，也不会生成 performance claim。</p>'
    )


def _page(
    summary: dict[str, Any],
    verification: dict[str, Any],
    scenario_details: list[dict[str, Any]],
    kernel_cost: dict[str, Any] | None,
) -> str:
    run = summary["run"]
    targets = {item["id"]: item for item in summary["targets"]}
    evidence = {item["id"]: item for item in summary["evidence"]}
    benchmarks = {item["id"]: item for item in summary["benchmarks"]}
    measured = [item for item in summary["benchmarks"] if item["status"] == "measured"]
    headline = next((claim for claim in summary["claims"] if claim["status"] == "supported"), None)
    conclusion = (
        _claim_text(headline, benchmarks, targets)[0]
        if headline
        else "本轮没有达到支持门的性能结论"
    )
    sample_counts = [int(estimate["n"]) for item in measured for estimate in item["estimates"]]
    n_display = str(min(sample_counts)) if sample_counts and len(set(sample_counts)) == 1 else (
        f"{min(sample_counts)}-{max(sample_counts)}" if sample_counts else "unavailable"
    )
    cache_policy = run.get("cache_policy") or summary["methodology"].get("cache_policy", "unavailable")
    commit = run.get("commit", "unavailable")
    grade = run.get("evidence_grade", "unavailable")
    overview_benchmark = benchmarks[headline["benchmark_id"]] if headline else None
    overview_chart = _chart(overview_benchmark, targets, evidence, suffix="overview") if overview_benchmark else (
        '<section class="unavailable-block"><strong>性能图 unavailable</strong>'
        '<span>没有与 headline 结论绑定且通过支持门的配对测量。</span></section>'
    )

    benchmark_sections = []
    for index, benchmark in enumerate(summary["benchmarks"]):
        benchmark_sections.append(
            '<article class="benchmark-block">'
            f'<header><div><p class="eyebrow">{_h(benchmark["task"])}</p><h3>{_h(benchmark["label"])}</h3></div>'
            f'{_status(benchmark["status"])}</header>'
            f'{_chart(benchmark, targets, evidence, suffix=f"benchmark-{index}")}'
            f'{_diagnostics_table(benchmark)}'
            '</article>'
        )

    scenario_rows = []
    for scenario in summary["scenarios"]:
        refs = " ".join(
            f'<button class="evidence-link" type="button" data-evidence-ref="{_h(item_id)}">{_h(item_id)}</button>'
            for item_id in scenario.get("evidence_ids", [])
        ) or "unavailable"
        scenario_rows.append(
            '<tr>'
            f'<td>{_h(scenario["task"])}</td><th scope="row">{_h(scenario["label"])}</th>'
            f'<td>{_status(scenario["functional_status"])}</td>'
            f'<td>{_status(scenario["performance_status"])}</td>'
            f'<td>{_h(_scenario_metric(scenario))}</td>'
            f'<td>{refs}</td></tr>'
        )
    if not scenario_rows:
        scenario_rows.append('<tr><td colspan="6" class="empty-cell">unavailable：未提供科研场景测量。</td></tr>')

    claim_items = []
    for claim in summary["claims"]:
        claim_title, claim_effect = _claim_text(claim, benchmarks, targets)
        refs = " ".join(
            f'<button class="evidence-link" type="button" data-evidence-ref="{_h(item_id)}">{_h(item_id)}</button>'
            for item_id in claim["evidence_ids"]
        )
        claim_items.append(
            '<article class="claim-item">'
            f'<header>{_status(claim["status"])}<code>{_h(claim["benchmark_id"])}</code></header>'
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

    methodology_rows = "".join(_methodology_rows(summary["methodology"]))
    methodology_diagnostics = "".join(
        _diagnostics_table(benchmark, heading_level=3)
        for benchmark in summary["benchmarks"]
        if benchmark.get("diagnostics")
    )
    target_text = " 对 ".join(str(target["label"]) for target in summary["targets"][:2])
    evidence_count = int(verification["verified_evidence_count"])
    declared_evidence_count = len(verification["evidence"])
    marker_count = int(verification["verified_marker_count"])
    evidence_set_sha256 = str(verification["evidence_set_sha256"])
    scenario_detail_blocks = _scenario_details_html(scenario_details)
    kernel_cost_block = _kernel_cost_html(kernel_cost)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>AgentOS 竞赛评价仪表板</title>
  <link rel="stylesheet" href="assets/evaluation-dashboard.css">
</head>
<body>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <header class="app-header">
    <div class="header-inner">
      <div><p class="product-name">AgentOS Evaluation</p><p class="run-name">{_h(run.get("label", run["id"]))}</p></div>
      <div class="header-actions"><span>{_h(target_text)}</span><a href="evaluation-summary.json" download>下载 JSON</a><a href="dashboard-verification.json" download>下载核验回执</a><a href="metrics.csv" download>下载 CSV</a></div>
    </div>
  </header>
  <nav class="tabs" aria-label="评价视图">
    <div class="tabs-inner" role="tablist">
      <button role="tab" id="tab-overview" aria-controls="panel-overview" aria-selected="true">总览</button>
      <button role="tab" id="tab-performance" aria-controls="panel-performance" aria-selected="false" tabindex="-1">性能</button>
      <button role="tab" id="tab-cost" aria-controls="panel-cost" aria-selected="false" tabindex="-1">系统成本</button>
      <button role="tab" id="tab-scenarios" aria-controls="panel-scenarios" aria-selected="false" tabindex="-1">科研场景</button>
      <button role="tab" id="tab-evidence" aria-controls="panel-evidence" aria-selected="false" tabindex="-1">可信证据</button>
      <button role="tab" id="tab-methodology" aria-controls="panel-methodology" aria-selected="false" tabindex="-1">方法学</button>
    </div>
  </nav>
  <main id="main">
    <section role="tabpanel" id="panel-overview" aria-labelledby="tab-overview" class="tab-panel">
      <div class="section-heading"><div><p class="eyebrow">评测运行 {_h(run["id"])}</p><h1>AgentOS 竞赛评价</h1></div><p>只展示可回溯到原始证据的测量；缺项保持 unavailable。</p></div>
      <dl class="summary-strip">
        <div><dt>Commit</dt><dd><code>{_h(commit)}</code></dd></div>
        <div><dt>证据等级</dt><dd>{_h(grade)}</dd></div>
        <div><dt>样本 n</dt><dd>{_h(n_display)}</dd></div>
        <div><dt>Cache</dt><dd>{_h(cache_policy)}</dd></div>
      </dl>
      <section class="conclusion-band" aria-labelledby="conclusion-title"><p class="eyebrow">证据约束结论</p><h2 id="conclusion-title">{_h(conclusion)}</h2></section>
      <section class="overview-grid">
        <div><div class="subheading"><h2>核心性能对照</h2><span>配对估计与区间</span></div>{overview_chart}</div>
        <div><div class="subheading"><h2>赛题任务覆盖</h2><span>任务 1-6 证据状态</span></div>{_task_matrix(summary["scenarios"])}</div>
      </section>
    </section>
    <section role="tabpanel" id="panel-performance" aria-labelledby="tab-performance" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">可复现测量</p><h2>性能</h2></div><p>区间、单位、样本量与来源同时呈现。</p></div>
      <div class="benchmark-list">{"".join(benchmark_sections) or '<div class="unavailable-block"><strong>unavailable</strong><span>没有 benchmark 记录。</span></div>'}</div>
    </section>
    <section role="tabpanel" id="panel-cost" aria-labelledby="tab-cost" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">Artifact Guardrails</p><h2>系统成本</h2></div><p>同 commit、构建清单绑定的内核静态体积对照。</p></div>
      {kernel_cost_block}
    </section>
    <section role="tabpanel" id="panel-scenarios" aria-labelledby="tab-scenarios" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">端到端工作负载</p><h2>科研场景</h2></div><p>功能完成不代替性能测量，两类证据分别标记。</p></div>
      <div class="table-scroll" role="region" tabindex="0" aria-label="科研场景验收表，可横向滚动"><table><thead><tr><th>赛题任务</th><th>场景</th><th>功能状态</th><th>性能状态</th><th>结构化测量</th><th>原始证据</th></tr></thead><tbody>{"".join(scenario_rows)}</tbody></table></div>
      <section aria-labelledby="scenario-details-title"><h3 id="scenario-details-title" class="subsection-title">场景明细</h3>{scenario_detail_blocks}</section>
    </section>
    <section role="tabpanel" id="panel-evidence" aria-labelledby="tab-evidence" class="tab-panel" hidden>
      <div class="section-heading"><div><p class="eyebrow">Claim → Evidence</p><h2>可信证据</h2></div><p>每条 headline claim 必须绑定已声明的 evidence ID。</p></div>
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
      <dl class="methodology-list">{methodology_rows or '<dt>methodology</dt><dd>unavailable</dd>'}</dl>
      {methodology_diagnostics}
    </section>
  </main>
  <footer class="app-footer"><span>Schema evaluation-summary-v1</span><span>生成时间 {_h(run.get("generated_at", "unavailable"))}</span></footer>
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


def _write_csv(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(_csv_rows(summary))
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def render(summary_path: Path, output_dir: Path) -> None:
    summary_path = summary_path.resolve(strict=True)
    if not summary_path.is_file():
        _fail("summary input must be a regular file")
    source_summary = summary_path.read_bytes()
    validated = validate_summary(read_strict_json(summary_path))
    if summary_path.read_bytes() != source_summary:
        _fail("summary input changed while it was being validated")
    verification, scenario_details, kernel_cost = _verify_evidence_files(
        summary_path.parent, validated, source_summary
    )
    _replay_supported_contract(summary_path.parent, summary_path, validated)
    summary = _canonical_dashboard_summary(validated)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        _fail("output path must be a directory")

    assets_source = Path(__file__).resolve().parent / "assets"
    assets_output = output_dir / "assets"
    assets_output.mkdir(exist_ok=True)
    for name in ("evaluation-dashboard.css", "evaluation-dashboard.js"):
        source = assets_source / name
        if not source.is_file():
            _fail(f"dashboard asset missing: {source}")
        shutil.copyfile(source, assets_output / name)

    _atomic_write(
        output_dir / "index.html",
        _page(summary, verification, scenario_details, kernel_cost),
    )
    _atomic_write(
        output_dir / "evaluation-summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(
        output_dir / "dashboard-verification.json",
        json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write_csv(output_dir / "metrics.csv", summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="evaluation-summary-v1 JSON")
    parser.add_argument("output", type=Path, help="dashboard output directory")
    args = parser.parse_args(argv)
    try:
        render(args.summary, args.output)
    except (DashboardError, OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
