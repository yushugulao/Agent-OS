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
    ) -> bool:
        status = "pass" if passed else ("warn" if incomplete and allow_incomplete else "error")
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

    contest_ready = len(contest) >= min_contest_pairs and contest_positive and contest_balanced
    chart(
        "traversal_indexed_dumbbell",
        contest_ready,
        "paired traversal/indexed boot measurements",
        paired_boots=len(contest),
        minimum=min_contest_pairs,
    )
    chart(
        "core_latency_violin",
        contest_ready,
        "raw core latency distribution for both paths",
        samples_per_path=len(contest),
        minimum=min_contest_pairs,
    )
    chart(
        "paired_difference_ecdf",
        contest_ready,
        "within-boot indexed-minus-traversal differences",
        differences=len(contest),
        minimum=min_contest_pairs,
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
    complete_cells = len(grid_counts) == len(loads) * len(operations)
    powered_cells = bool(grid_counts) and min(grid_counts.values()) >= min_cell_pairs
    grid_ready = (
        len(loads) >= 3
        and len(operations) >= 3
        and complete_cells
        and powered_cells
        and pair_equivalent
        and pair_positive
        and pair_balance
    )
    grid_evidence = {
        "catalog_sizes": [int(value) for value in loads if value],
        "hit_counts": [int(value) for value in operations if value],
        "cells": len(grid_counts),
        "minimum_pairs_per_cell": min(grid_counts.values()) if grid_counts else 0,
        "required_pairs_per_cell": min_cell_pairs,
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

    diagnostics = tables["agenteval_diagnostics"]
    samples = tables["agenteval_samples"]
    cold = [
        row
        for row in diagnostics
        if row.get("experiment") == "file_query_table_ablation"
        and row.get("cache") == "cold-rebuild"
    ]
    warm_scan = [
        row
        for row in samples
        if row.get("experiment") == "file_query_table_ablation"
        and row.get("variant") == "scan"
    ]
    warm_index = [
        row
        for row in samples
        if row.get("experiment") == "file_query_table_ablation"
        and row.get("variant") == "index"
    ]
    cold_loads = {row.get("load") for row in cold}
    grouped_ready = (
        len(cold_loads) >= 3
        and len(cold) >= 3
        and len(warm_scan) >= min_cell_pairs * 3
        and len(warm_index) >= min_cell_pairs * 3
    )
    chart(
        "cold_warm_indexed_grouped",
        grouped_ready,
        "cold rebuild, warm scan, and ready-index observations share at least three loads",
        cold_samples=len(cold),
        warm_scan_samples=len(warm_scan),
        warm_index_samples=len(warm_index),
        loads=len(cold_loads),
    )

    # Task sequences must retain every interval marker, not just p50/p99.
    sequences = tables["task_sequences"]
    operations_rows = tables["task_operations"]
    expected_paths = {"batch", "scalar_v3", "sq_cq"}
    sequence_counts = Counter(row.get("path", "") for row in sequences)
    sequence_positive = all((_integer(row, "duration_us") or 0) > 0 for row in sequences)
    if sequences:
        check("task_positive_latency", sequence_positive, "all one-shot sequence durations are positive")
    op_groups: Counter[tuple[str, str, str]] = Counter(
        (_source_key(row), row.get("boot_round", ""), row.get("path", ""))
        for row in operations_rows
    )
    operation_complete = True
    missing_operation_groups: list[str] = []
    for row in sequences:
        key = (_source_key(row), row.get("boot_round", ""), row.get("path", ""))
        expected = _integer(row, "operations")
        if expected is None or op_groups[key] != expected:
            operation_complete = False
            missing_operation_groups.append(f"{key}:{op_groups[key]}/{expected}")
    if sequences:
        check(
            "task_raw_operation_completeness",
            operation_complete,
            "each sequence retains exactly one raw interval row per operation",
            observed=missing_operation_groups[:10] or "complete",
            required="raw rows == operations",
        )
    task_ready = (
        expected_paths.issubset(sequence_counts)
        and min((sequence_counts[path] for path in expected_paths), default=0)
        >= min_task_repeats
        and sequence_positive
        and operation_complete
    )
    chart(
        "task_latency_distribution",
        task_ready,
        "batch/scalar_v3/SQ-CQ raw sequence and per-operation distributions",
        sequence_counts=dict(sequence_counts),
        raw_operation_rows=len(operations_rows),
        minimum_repeats_per_path=min_task_repeats,
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
    exact_ready = len(wakeups) >= 12 and len(exact_scenarios) >= 2 and exact_positive
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
    concurrency = tables["agenteval_concurrency"]
    fallback_levels = {
        _integer(row, "concurrency")
        for row in concurrency
        if _integer(row, "concurrency") is not None
    }
    eevdf_jain_ready = {1, 2, 3, 4}.issubset(jain_concurrency) and jain_valid
    fallback_jain_ready = len(fallback_levels) >= 3 and all(
        0 < (_integer(row, "fairness_jain_ppm") or 0) <= 1_000_000
        for row in concurrency
    )
    fairness_ready = eevdf_jain_ready or fallback_jain_ready
    chart(
        "jain_fairness_concurrency",
        fairness_ready,
        "Jain fairness from raw EEVDF service-cycle sums at concurrency 1-4",
        method="eevdf_service_cycles" if eevdf_jain_ready else "agenteval_completion_fairness",
        eevdf_concurrency=sorted(jain_concurrency),
        agenteval_concurrency=sorted(level for level in fallback_levels if level is not None),
    )

    contest_io = tables["contest_io_normalized"]
    task_io = tables["task_perf_normalized"]
    contest_io_paths = {row.get("path", "") for row in contest_io}
    task_io_paths = {row.get("path", "") for row in task_io}
    io_ready = (
        {"traversal", "indexed"}.issubset(contest_io_paths)
        and expected_paths.issubset(task_io_paths)
        and any(row.get("per_workload_syscall", "") != "" for row in contest_io)
        and any(row.get("per_operation", "") != "" for row in task_io)
    )
    chart(
        "kernel_io_normalized_heatmap",
        io_ready,
        "contest kernel counters per workload syscall plus Task ABI work per operation",
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
