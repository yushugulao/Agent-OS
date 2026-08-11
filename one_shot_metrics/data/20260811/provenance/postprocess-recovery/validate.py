#!/usr/bin/env python3
"""Validate one-shot tables and report whether all ten figures are defensible."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
KIND = "agentos-one-shot-chart-readiness"

REQUIRED_FIELDS = {
    "contest_paired": {
        "sample_id",
        "order",
        "traversal_core_duration_us",
        "indexed_core_duration_us",
    },
    "contest_io_normalized": {
        "path",
        "metric",
        "metric_origin",
        "counter_scope",
        "counter_window",
        "counter_representation",
        "counter_owner",
        "raw_value",
        "denominator_metric",
        "denominator_scope",
        "denominator_window",
        "denominator_owner",
        "normalization_cross_scope",
        "normalization_cross_window",
        "per_workload_syscall",
        "raw_counter_evidence",
    },
    "agenteval_samples": {
        "schema",
        "experiment",
        "load",
        "pair",
        "variant",
        "order",
        "operations",
        "duration_us",
        "workload_fingerprint",
        "result_fingerprint",
        "status",
    },
    "agenteval_pairs": {
        "experiment",
        "load",
        "operations",
        "pair",
        "baseline_duration_us",
        "treatment_duration_us",
        "speedup_baseline_over_treatment",
        "result_fingerprint_equal",
        "workload_fingerprint_equal",
    },
    "task_sequences": {
        "schema",
        "boot_round",
        "path",
        "operations",
        "start_us",
        "end_us",
        "duration_us",
        "syscalls",
    },
    "task_operations": {
        "schema",
        "boot_round",
        "path",
        "operation_index",
        "service_start_interval_tick",
    },
    "eevdf_wakeups": {
        "schema",
        "scenario",
        "index",
        "probe",
        "wakeup_latency_ticks",
        "histogram_bucket",
    },
    "eevdf_jain": {"scenario", "n", "sum", "sum_sq"},
}

FIGURE_NAMES = (
    "traversal_indexed_dumbbell",
    "core_latency_violin",
    "paired_difference_ecdf",
    "catalog_hit_speedup_heatmap",
    "performance_surface_3d",
    "cold_warm_indexed_grouped",
    "task_latency_distribution",
    "eevdf_wakeup_ecdf",
    "jain_fairness_concurrency",
    "kernel_io_normalized_heatmap",
)

EXPECTED_CONTEST_BOOTS = 16
EXPECTED_GRID_LOADS = {24, 64, 96}
EXPECTED_GRID_OPERATIONS = {1, 2, 4, 8}
EXPECTED_GRID_PAIRS = 15
EXPECTED_TASK_BOOTS = 4
EXPECTED_TASK_ROUNDS = set(range(1, 9))
EXPECTED_TASK_OPERATION_INDICES = set(range(16))
EXPECTED_EEVDF_BOOTS = 6
EXPECTED_EEVDF_JAIN_N = {1: 1, 2: 2, 3: 3, 4: 4, 16: 16, 44: 4}
EXPECTED_EEVDF_WAKEUPS = {2: 4, 3: 8, 4: 12, 16: 48, 44: 12}
EXPECTED_CONTEST_LANE_IO = {"bytes_read"}
EXPECTED_CONTEST_GLOBAL_IO = {
    "directory_block_probes",
    "directory_entries_examined",
    "physical_reads",
    "physical_writes",
    "durable_flushes",
    "virtio_notifications",
    "virtio_submitted_requests",
    "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
}


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.exists() or path.stat().st_size == 0:
        return [], set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        return list(reader), fields


def load_tables(root: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, set[str]]]:
    names = {
        *REQUIRED_FIELDS,
        "contest_paths",
        "contest_io_normalized",
        "agenteval_diagnostics",
        "agenteval_concurrency_samples",
        "agenteval_concurrency",
        "task_perf",
        "task_perf_normalized",
        "eevdf_samples",
        "eevdf_cohorts",
        "eevdf_wake_histogram",
    }
    tables: dict[str, list[dict[str, str]]] = {}
    fields: dict[str, set[str]] = {}
    for name in sorted(names):
        tables[name], fields[name] = _read_csv(root / f"{name}.csv")
    return tables, fields


def _integer(row: dict[str, str], key: str) -> int | None:
    try:
        value = row.get(key, "")
        return int(value) if value != "" else None
    except (TypeError, ValueError):
        return None


def _number(row: dict[str, str], key: str) -> float | None:
    try:
        value = row.get(key, "")
        result = float(value) if value != "" else None
        return result if result is None or math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _source_key(row: dict[str, str]) -> str:
    return row.get("source_file", "unknown")


def validate_tables(
    root: Path,
    *,
    allow_incomplete: bool = False,
    min_contest_pairs: int = 8,
    min_cell_pairs: int = 7,
    min_task_repeats: int = 8,
) -> dict[str, Any]:
    tables, fields = load_tables(root)
    checks: list[dict[str, Any]] = []
    charts: dict[str, dict[str, Any]] = {}

    def check(
        name: str,
        passed: bool,
        detail: str,
        *,
        observed: Any = None,
        required: Any = None,
        incomplete: bool = False,
        advisory: bool = False,
    ) -> bool:
        status = "pass" if passed else (
            "warn" if advisory or (incomplete and allow_incomplete) else "error"
        )
        item: dict[str, Any] = {"name": name, "status": status, "detail": detail}
        if observed is not None:
            item["observed"] = observed
        if required is not None:
            item["required"] = required
        checks.append(item)
        return passed

    def chart(name: str, ready: bool, detail: str, **evidence: Any) -> None:
        charts[name] = {"ready": ready, "detail": detail, **evidence}
        check(
            f"chart:{name}",
            ready,
            detail,
            observed=evidence or None,
            required="chart contract",
            incomplete=True,
        )

    # Field and marker schema contracts.
    for table_name, required in REQUIRED_FIELDS.items():
        rows = tables[table_name]
        if not rows:
            continue
        missing = sorted(required - fields[table_name])
        check(
            f"schema:{table_name}",
            not missing,
            "required columns are present" if not missing else f"missing columns: {', '.join(missing)}",
            observed=sorted(fields[table_name]),
            required=sorted(required),
        )

    schema_expectations = {
        "agenteval_samples": 2,
        "agenteval_diagnostics": 2,
        "agenteval_concurrency_samples": 2,
        "agenteval_concurrency": 2,
        "task_sequences": 1,
        "task_operations": 1,
        "eevdf_wakeups": 1,
    }
    for table_name, expected in schema_expectations.items():
        rows = tables[table_name]
        if not rows:
            continue
        versions = sorted({row.get("schema", "") for row in rows})
        check(
            f"schema_version:{table_name}",
            versions == [str(expected)],
            f"marker schema must be exactly {expected}",
            observed=versions,
            required=[str(expected)],
        )

    # Contest pairing, positive timings, and balanced execution order.
    contest = tables["contest_paired"]
    contest_ids = [(row.get("source_file", ""), row.get("sample_id", "")) for row in contest]
    check(
        "contest_unique_samples",
        len(contest_ids) == len(set(contest_ids)),
        "contest sample IDs are unique within each source",
        observed=len(contest_ids),
        required=len(set(contest_ids)),
    )
    contest_positive = all(
        (_integer(row, "traversal_core_duration_us") or 0) > 0
        and (_integer(row, "indexed_core_duration_us") or 0) > 0
        for row in contest
    )
    if contest:
        check("contest_positive_latency", contest_positive, "both paired core latencies are positive")
    order_counts = Counter(row.get("order", "") for row in contest)
    contest_balanced = (
        set(order_counts).issubset({"traversal_then_indexed", "indexed_then_traversal"})
        and abs(
            order_counts["traversal_then_indexed"]
            - order_counts["indexed_then_traversal"]
        )
        <= 1
    )
    if contest:
        check(
            "contest_ab_ba_balance",
            contest_balanced,
            "AB/BA lane order differs by at most one sample",
            observed=dict(order_counts),
            required="difference <= 1",
        )

    contest_ready = (
        len(contest) == EXPECTED_CONTEST_BOOTS
        and contest_positive
        and contest_balanced
    )
    chart(
        "traversal_indexed_dumbbell",
        contest_ready,
        "paired traversal/indexed boot measurements",
        paired_boots=len(contest),
        required_boots=EXPECTED_CONTEST_BOOTS,
    )
    chart(
        "core_latency_violin",
        contest_ready,
        "raw core latency distribution for both paths",
        samples_per_path=len(contest),
        required_boots=EXPECTED_CONTEST_BOOTS,
    )
    chart(
        "paired_difference_ecdf",
        contest_ready,
        "within-boot indexed-minus-traversal differences",
        differences=len(contest),
        required_boots=EXPECTED_CONTEST_BOOTS,
    )

    # AgentEval result equivalence and balanced inner pairs.
    pairs = tables["agenteval_pairs"]
    pair_equivalent = all(
        row.get("result_fingerprint_equal") == "1"
        and row.get("workload_fingerprint_equal") == "1"
        for row in pairs
    )
    if pairs:
        check(
            "agenteval_pair_equivalence",
            pair_equivalent,
            "every treatment has the same workload and result fingerprint as its baseline",
            observed=sum(
                row.get("result_fingerprint_equal") == "1"
                and row.get("workload_fingerprint_equal") == "1"
                for row in pairs
            ),
            required=len(pairs),
        )
    pair_positive = all(
        (_integer(row, "baseline_duration_us") or 0) > 0
        and (_integer(row, "treatment_duration_us") or 0) > 0
        and (_number(row, "speedup_baseline_over_treatment") or 0) > 0
        for row in pairs
    )
    if pairs:
        check("agenteval_positive_latency", pair_positive, "all paired durations and speedups are positive")

    order_groups: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    for row in pairs:
        order_groups[
            (
                _source_key(row),
                row.get("experiment", ""),
                row.get("load", ""),
                row.get("operations", ""),
            )
        ][row.get("order", "")] += 1
    pair_balance = all(
        set(counts).issubset({"AB", "BA"})
        and abs(counts["AB"] - counts["BA"]) <= 1
        for counts in order_groups.values()
    )
    if order_groups:
        check(
            "agenteval_ab_ba_balance",
            pair_balance,
            "each parameter cell uses balanced alternating AB/BA order",
            observed=len(order_groups),
            required="all cells balanced",
        )

    grid_pairs = [
        row for row in pairs if row.get("experiment") == "file_query_table_ablation"
    ]
    grid_counts: Counter[tuple[str, str]] = Counter(
        (row.get("load", ""), row.get("operations", "")) for row in grid_pairs
    )
    loads = sorted({key[0] for key in grid_counts}, key=lambda value: int(value or 0))
    operations = sorted({key[1] for key in grid_counts}, key=lambda value: int(value or 0))
    numeric_loads = {int(value) for value in loads if value}
    numeric_operations = {int(value) for value in operations if value}
    complete_cells = (
        numeric_loads == EXPECTED_GRID_LOADS
        and numeric_operations == EXPECTED_GRID_OPERATIONS
        and len(grid_counts) == len(EXPECTED_GRID_LOADS) * len(EXPECTED_GRID_OPERATIONS)
    )
    powered_cells = bool(grid_counts) and all(
        count == EXPECTED_GRID_PAIRS for count in grid_counts.values()
    )
    grid_ready = (
        complete_cells
        and powered_cells
        and pair_equivalent
        and pair_positive
        and pair_balance
    )
    grid_evidence = {
        "catalog_sizes": sorted(numeric_loads),
        "hit_counts": sorted(numeric_operations),
        "cells": len(grid_counts),
        "minimum_pairs_per_cell": min(grid_counts.values()) if grid_counts else 0,
        "required_pairs_per_cell": EXPECTED_GRID_PAIRS,
    }
    chart(
        "catalog_hit_speedup_heatmap",
        grid_ready,
        "complete catalog-size by hit-count grid of paired speedups",
        **grid_evidence,
    )
    chart(
        "performance_surface_3d",
        grid_ready,
        "same measured grid rendered as a non-interpolated median speedup surface",
        **grid_evidence,
    )

    samples = tables["agenteval_samples"]
    first_scan = [
        row
        for row in samples
        if row.get("experiment") == "file_query_table_ablation"
        and row.get("variant") == "scan"
        and _integer(row, "pair") == 1
    ]
    repeat_scan = [
        row
        for row in samples
        if row.get("experiment") == "file_query_table_ablation"
        and row.get("variant") == "scan"
        and (_integer(row, "pair") or 0) >= 2
    ]
    ready_index = [
        row
        for row in samples
        if row.get("experiment") == "file_query_table_ablation"
        and row.get("variant") == "index"
    ]
    grouped_loads = {row.get("load") for row in first_scan}
    grouped_ready = (
        len(grouped_loads) >= 3
        and len(first_scan) >= 3
        and len(repeat_scan) >= min_cell_pairs * 3
        and len(ready_index) >= min_cell_pairs * 3
    )
    chart(
        "cold_warm_indexed_grouped",
        grouped_ready,
        "first measured scan, repeated scan, and ready-index observations share at least three loads",
        first_scan_samples=len(first_scan),
        repeat_scan_samples=len(repeat_scan),
        ready_index_samples=len(ready_index),
        loads=len(grouped_loads),
    )

    # Task sequences must retain every interval marker, not just p50/p99.
    sequences = tables["task_sequences"]
    operations_rows = tables["task_operations"]
    expected_paths = {"batch", "scalar_v3", "sq_cq"}
    sequence_counts = Counter(row.get("path", "") for row in sequences)
    sequence_positive = all((_integer(row, "duration_us") or 0) > 0 for row in sequences)
    if sequences:
        check("task_positive_latency", sequence_positive, "all one-shot sequence durations are positive")
    op_groups: Counter[tuple[str, str, str]] = Counter()
    op_indices: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for row in operations_rows:
        key = (_source_key(row), row.get("boot_round", ""), row.get("path", ""))
        op_groups[key] += 1
        index = _integer(row, "operation_index")
        if index is not None:
            op_indices[key].append(index)
    operation_complete = True
    missing_operation_groups: list[str] = []
    for row in sequences:
        key = (_source_key(row), row.get("boot_round", ""), row.get("path", ""))
        expected = _integer(row, "operations")
        unique_indices = set(op_indices[key])
        if (
            expected != len(EXPECTED_TASK_OPERATION_INDICES)
            or op_groups[key] != expected
            or unique_indices != EXPECTED_TASK_OPERATION_INDICES
            or len(op_indices[key]) != len(unique_indices)
        ):
            operation_complete = False
            missing_operation_groups.append(
                f"{key}:rows={op_groups[key]} expected={expected} "
                f"indices={sorted(unique_indices)}"
            )
    sequence_group_keys = {
        (_source_key(row), row.get("boot_round", ""), row.get("path", ""))
        for row in sequences
    }
    if set(op_groups) != sequence_group_keys:
        operation_complete = False
        missing_operation_groups.append(
            "operation groups differ from sequence groups"
        )
    if sequences:
        check(
            "task_raw_operation_completeness",
            operation_complete,
            "each sequence retains exactly one raw interval row per operation",
            observed=missing_operation_groups[:10] or "complete",
            required="raw rows == operations",
        )
    task_sources = {_source_key(row) for row in sequences}
    task_rounds_by_source_path: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in sequences:
        round_number = _integer(row, "boot_round")
        if round_number is not None:
            task_rounds_by_source_path[(_source_key(row), row.get("path", ""))].add(
                round_number
            )
    task_shape_exact = (
        len(task_sources) == EXPECTED_TASK_BOOTS
        and all(sequence_counts[path] == EXPECTED_TASK_BOOTS * len(EXPECTED_TASK_ROUNDS) for path in expected_paths)
        and all(
            task_rounds_by_source_path[(source, path)] == EXPECTED_TASK_ROUNDS
            for source in task_sources
            for path in expected_paths
        )
    )
    task_ready = (
        expected_paths.issubset(sequence_counts)
        and task_shape_exact
        and sequence_positive
        and operation_complete
    )
    chart(
        "task_latency_distribution",
        task_ready,
        "batch/scalar_v3/SQ-CQ raw sequence and per-operation distributions",
        sequence_counts=dict(sequence_counts),
        raw_operation_rows=len(operations_rows),
        required_boots=EXPECTED_TASK_BOOTS,
        required_rounds_per_path_per_boot=len(EXPECTED_TASK_ROUNDS),
    )

    # Prefer exact one-shot wake probes. Histogram data remains an honest fallback.
    wakeups = tables["eevdf_wakeups"]
    exact_positive = all(
        (_integer(row, "wakeup_latency_ticks") is not None)
        and (_integer(row, "wakeup_latency_ticks") or 0) >= 0
        for row in wakeups
    )
    exact_scenarios = {
        row.get("scenario", "") for row in wakeups if row.get("scenario", "")
    }
    histogram = tables["eevdf_wake_histogram"]
    workflow_hist = [row for row in histogram if row.get("histogram_scope") == "workflow"]
    hist_rows = workflow_hist or [
        row for row in histogram if row.get("histogram_scope") == "cohort"
    ]
    hist_samples = sum(_integer(row, "count") or 0 for row in hist_rows)
    hist_scenarios = {row.get("scenario", "") for row in hist_rows}
    wake_counts_by_source_scenario: Counter[tuple[str, int]] = Counter()
    wake_keys: list[tuple[str, int, int, int]] = []
    for row in wakeups:
        scenario = _integer(row, "scenario")
        workflow_index = _integer(row, "index")
        probe = _integer(row, "probe")
        if scenario is not None and workflow_index is not None and probe is not None:
            wake_counts_by_source_scenario[(_source_key(row), scenario)] += 1
            wake_keys.append((_source_key(row), scenario, workflow_index, probe))
    wake_sources = {_source_key(row) for row in wakeups}
    exact_shape = (
        len(wake_sources) == EXPECTED_EEVDF_BOOTS
        and len(wake_keys) == len(wakeups)
        and len(set(wake_keys)) == len(wake_keys)
        and all(
            wake_counts_by_source_scenario[(source, scenario)] == expected
            for source in wake_sources
            for scenario, expected in EXPECTED_EEVDF_WAKEUPS.items()
        )
    )
    exact_ready = (
        len(wakeups) == EXPECTED_EEVDF_BOOTS * sum(EXPECTED_EEVDF_WAKEUPS.values())
        and exact_shape
        and exact_positive
    )
    histogram_ready = hist_samples >= 12 and len(hist_scenarios) >= 2
    wake_ready = exact_ready or histogram_ready
    wake_method = "exact_probe_ecdf" if exact_ready else "histogram_derived_step_cdf"
    chart(
        "eevdf_wakeup_ecdf",
        wake_ready,
        "exact per-probe ECDF preferred; four-bin step CDF is explicitly labeled when used",
        method=wake_method,
        exact_probe_samples=len(wakeups),
        histogram_samples=hist_samples,
        scenarios=len(exact_scenarios if exact_ready else hist_scenarios),
    )

    sample_rows = tables["eevdf_samples"]
    incomplete_samples = [
        row for row in sample_rows if row.get("serial_record_complete") == "0"
    ]
    check(
        "eevdf_serial_sample_integrity",
        not incomplete_samples,
        "all EEVDF sample summary markers are complete"
        if not incomplete_samples
        else (
            f"{len(incomplete_samples)} sample summaries were serial-spliced; "
            "they remain marked in the raw table and are excluded from histogram derivation"
        ),
        observed=len(incomplete_samples),
        required=0,
        advisory=True,
    )

    jain = tables["eevdf_jain"]
    jain_rows: list[tuple[int, float]] = []
    jain_valid = True
    for row in jain:
        scenario = _integer(row, "scenario")
        n = _integer(row, "n")
        total = _integer(row, "sum")
        total_sq = _integer(row, "sum_sq")
        if not scenario or scenario not in {1, 2, 3, 4}:
            continue
        if not n or total is None or not total_sq:
            jain_valid = False
            continue
        fairness = total * total / (n * total_sq)
        if not (0 < fairness <= 1.000000001):
            jain_valid = False
        jain_rows.append((scenario, fairness))
    jain_concurrency = {item[0] for item in jain_rows}
    jain_sources = {_source_key(row) for row in jain}
    jain_keys = [
        (_source_key(row), _integer(row, "scenario")) for row in jain
    ]
    jain_shape = (
        len(jain_sources) == EXPECTED_EEVDF_BOOTS
        and len(set(jain_keys)) == len(jain_keys)
    )
    for source in jain_sources:
        source_rows = {
            _integer(row, "scenario"): _integer(row, "n")
            for row in jain
            if _source_key(row) == source
        }
        if source_rows != EXPECTED_EEVDF_JAIN_N:
            jain_shape = False

    raw_service_rows = [
        row
        for row in sample_rows
        if (_integer(row, "scenario") or 0) in {1, 2, 3, 4}
    ]
    raw_service_keys = [
        (_source_key(row), _integer(row, "scenario"), _integer(row, "index"))
        for row in raw_service_rows
    ]
    raw_service_shape = (
        len({_source_key(row) for row in raw_service_rows}) == EXPECTED_EEVDF_BOOTS
        and len(set(raw_service_keys)) == len(raw_service_keys)
        and all((_integer(row, "service") or 0) > 0 for row in raw_service_rows)
    )
    raw_service_counts: Counter[tuple[str, int]] = Counter(
        (_source_key(row), _integer(row, "scenario") or 0)
        for row in raw_service_rows
    )
    for source in {_source_key(row) for row in raw_service_rows}:
        for scenario in (1, 2, 3, 4):
            if raw_service_counts[(source, scenario)] != scenario:
                raw_service_shape = False
    eevdf_jain_ready = (
        {1, 2, 3, 4}.issubset(jain_concurrency)
        and jain_valid
        and jain_shape
        and raw_service_shape
    )
    fairness_ready = eevdf_jain_ready
    chart(
        "jain_fairness_concurrency",
        fairness_ready,
        "Jain fairness recomputed from each workflow's unscaled service_cycles at concurrency 1-4",
        method="raw_eevdf_service_cycles",
        eevdf_concurrency=sorted(jain_concurrency),
        eevdf_boots=len(jain_sources),
    )

    contest_io = tables["contest_io_normalized"]
    task_io = tables["task_perf_normalized"]
    contest_io_paths = {row.get("path", "") for row in contest_io}
    task_io_paths = {row.get("path", "") for row in task_io}
    contest_io_counts = Counter(row.get("metric", "") for row in contest_io)
    expected_io_counts = {
        metric: EXPECTED_CONTEST_BOOTS * 2
        for metric in EXPECTED_CONTEST_LANE_IO | EXPECTED_CONTEST_GLOBAL_IO
    }
    contest_io_shape = contest_io_counts == expected_io_counts
    lane_io = [row for row in contest_io if row.get("metric") in EXPECTED_CONTEST_LANE_IO]
    global_io = [row for row in contest_io if row.get("metric") in EXPECTED_CONTEST_GLOBAL_IO]
    lane_io_contract = bool(lane_io) and all(
        row.get("metric_origin") == "lane_metric"
        and row.get("counter_scope") == "workflow_lane"
        and row.get("counter_window") == "core"
        and row.get("counter_representation") == "lane_reported_total"
        and row.get("counter_owner") == "workflow_actor"
        and row.get("normalization_cross_scope") == "0"
        and row.get("normalization_cross_window") == "0"
        for row in lane_io
    )
    global_io_contract = bool(global_io) and all(
        row.get("metric_origin") == "mechanism_end_to_end"
        and row.get("counter_scope") == "global_kernel"
        and row.get("counter_window") == "end_to_end"
        and row.get("counter_representation") == "delta"
        and row.get("counter_owner") == "shared_kernel_not_process_attributed"
        and row.get("normalization_cross_scope") == "1"
        and row.get("normalization_cross_window") == "1"
        for row in global_io
    )
    contest_denominator_contract = bool(contest_io) and all(
        row.get("denominator_metric") == "observer_workload_syscalls"
        and row.get("denominator_scope") == "observer_process"
        and row.get("denominator_window") == "core"
        and row.get("denominator_owner") == "workflow_actor"
        for row in contest_io
    )
    contest_io_evidence = bool(contest_io) and all(
        row.get("raw_counter_evidence", "").startswith("raw/contest/sample-")
        and row.get("raw_counter_evidence", "").endswith("-qemu.serial.txt")
        for row in contest_io
    )
    if contest_io:
        check(
            "contest_io_metric_shape",
            contest_io_shape,
            "each frozen contest I/O metric has one row per path and boot",
            observed=dict(sorted(contest_io_counts.items())),
            required=expected_io_counts,
        )
        check(
            "contest_io_lane_scope",
            lane_io_contract,
            "bytes_read is workflow-actor lane work reported over the core window",
        )
        check(
            "contest_io_global_scope",
            global_io_contract,
            "block, directory, and virtio values are shared global end-to-end deltas",
        )
        check(
            "contest_io_denominator_scope",
            contest_denominator_contract,
            "normalization denominator is the workflow actor's core-window observer syscall counter",
        )
        check(
            "contest_io_evidence_paths",
            contest_io_evidence,
            "raw counter evidence paths name retained contest serial logs",
        )
    io_ready = (
        {"traversal", "indexed"}.issubset(contest_io_paths)
        and expected_paths.issubset(task_io_paths)
        and any(row.get("per_workload_syscall", "") != "" for row in contest_io)
        and any(row.get("per_operation", "") != "" for row in task_io)
        and contest_io_shape
        and lane_io_contract
        and global_io_contract
        and contest_denominator_contract
        and contest_io_evidence
        and all(
            row.get("metric_scope") == "task_path_sequence"
            for row in task_io
        )
    )
    chart(
        "kernel_io_normalized_heatmap",
        io_ready,
        "mixed-scope lane work and global end-to-end deltas per observer syscall, plus Task-path work per operation",
        contest_rows=len(contest_io),
        task_rows=len(task_io),
        contest_paths=sorted(contest_io_paths),
        task_paths=sorted(task_io_paths),
    )

    errors = [item for item in checks if item["status"] == "error"]
    warnings = [item for item in checks if item["status"] == "warn"]
    ready = all(charts.get(name, {}).get("ready", False) for name in FIGURE_NAMES)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        # Keep the published report movable; the canonical campaign directory is
        # atomically renamed after validation.
        "tables_dir": root.name,
        "valid": not errors,
        "ready": ready,
        "table_rows": {name: len(rows) for name, rows in sorted(tables.items())},
        "checks": checks,
        "chart_readiness": charts,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def _write(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # A deliberately small but internally valid pair exercises schema and
        # equivalence checks; allow_incomplete converts absent chart data to warnings.
        (root / "agenteval_pairs.csv").write_text(
            "source_file,experiment,load,operations,pair,order,baseline_duration_us,"
            "treatment_duration_us,speedup_baseline_over_treatment,"
            "result_fingerprint_equal,workload_fingerprint_equal\n"
            "x.log,file_query_table_ablation,24,4,1,AB,400,100,4,1,1\n",
            encoding="utf-8",
        )
        (root / "agenteval_samples.csv").write_text(
            "schema,experiment,load,pair,variant,order,operations,duration_us,"
            "workload_fingerprint,result_fingerprint,status\n"
            "2,file_query_table_ablation,24,1,scan,AB,4,400,w,r,measured\n"
            "2,file_query_table_ablation,24,1,index,AB,4,100,w,r,measured\n",
            encoding="utf-8",
        )
        report = validate_tables(root, allow_incomplete=True)
        assert report["error_count"] == 0
        assert not report["ready"]
        assert any(
            item["name"] == "agenteval_pair_equivalence"
            and item["status"] == "pass"
            for item in report["checks"]
        )
    print("validate.py self-test: passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, help="directory written by extract.py")
    parser.add_argument("--output", type=Path, help="JSON readiness report")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--min-contest-pairs", type=int, default=8)
    parser.add_argument("--min-cell-pairs", type=int, default=7)
    parser.add_argument("--min-task-repeats", type=int, default=8)
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if args.tables is None or args.output is None:
        raise SystemExit("--tables and --output are required")
    report = validate_tables(
        args.tables,
        allow_incomplete=args.allow_incomplete,
        min_contest_pairs=args.min_contest_pairs,
        min_cell_pairs=args.min_cell_pairs,
        min_task_repeats=args.min_task_repeats,
    )
    _write(args.output, report)
    state = "ready" if report["ready"] else "not ready"
    print(
        f"validate.py: {state}; {report['error_count']} errors, "
        f"{report['warning_count']} warnings; report={args.output}"
    )
    if report["error_count"]:
        for item in report["checks"]:
            if item["status"] == "error":
                print(f"validate.py: {item['name']}: {item['detail']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
