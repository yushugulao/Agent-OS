#!/usr/bin/env python3
"""Extract one-shot AgentOS serial logs into chart-ready long-form CSV tables.

The collector deliberately has no dependency beyond the Python standard library.
Every output row retains its source file, source digest, and serial line number so a
point in a figure can be traced back to the immutable campaign evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shlex
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
KIND = "agentos-one-shot-chart-tables"

MARKERS: tuple[tuple[str, str], ...] = (
    ("agenteval_ucore: concurrency_sample ", "agenteval_concurrency_samples"),
    ("agenteval_ucore: concurrency ", "agenteval_concurrency"),
    ("agenteval_ucore: diagnostic ", "agenteval_diagnostics"),
    ("agenteval_ucore: sample ", "agenteval_samples"),
    ("agenttask_ucore: one_shot_sequence ", "task_sequences"),
    ("agenttask_ucore: one_shot_op ", "task_operations"),
    ("agenttask_ucore: perf_fp ", "task_fingerprints"),
    ("agenttask_ucore: perf ", "task_perf"),
    ("agent_eevdf_ucore: one_shot_wakeup ", "eevdf_wakeups"),
    ("agent_eevdf_ucore: amplification_inputs ", "eevdf_amplification"),
    ("agent_eevdf_ucore: jain_inputs ", "eevdf_jain"),
    ("agent_eevdf_ucore: cohort ", "eevdf_cohorts"),
    ("agent_eevdf_ucore: sample ", "eevdf_samples"),
    ("agent_eevdf_ucore: wake ", "eevdf_wake"),
)

MARKER_COMPLETE_FIELDS = {
    "eevdf_samples": {
        "scenario", "index", "source", "threads", "wake_probes", "mode",
        "flags", "latency_class", "weight", "request_ticks", "lifecycle",
        "work", "service", "dispatch", "fallback", "deadline_miss",
        "wake_samples", "wake_max", "wake_bucket_0", "wake_bucket_1",
        "wake_bucket_2", "wake_bucket_3",
    },
}

TABLE_ORDER = (
    "contest_paired",
    "contest_paths",
    "contest_io_normalized",
    "agenteval_samples",
    "agenteval_pairs",
    "agenteval_diagnostics",
    "agenteval_concurrency_samples",
    "agenteval_concurrency",
    "task_sequences",
    "task_operations",
    "task_perf",
    "task_perf_normalized",
    "task_fingerprints",
    "eevdf_samples",
    "eevdf_cohorts",
    "eevdf_jain",
    "eevdf_wakeups",
    "eevdf_wake_histogram",
    "eevdf_amplification",
)

BASELINE_VARIANTS = {
    "file_query_path_index": "path_walk",
    "file_query_table_ablation": "scan",
    "tool_batch": "scalar",
    "context_access": "syscall",
}
TREATMENT_VARIANTS = {
    "file_query_path_index": "index",
    "file_query_table_ablation": "index",
    "tool_batch": "batch",
    "context_access": "direct",
}

CONTEST_PATH_FIELDS = (
    "core_duration_us",
    "end_to_end_duration_us",
    "workload_syscalls",
    "records_examined",
    "bytes_read",
    "directory_block_probes",
    "directory_entries_examined",
    "physical_reads",
    "physical_writes",
    "durable_flushes",
    "virtio_notifications",
    "virtio_submitted_requests",
    "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
)
CONTEST_IO_FIELDS = (
    "bytes_read",
    "directory_block_probes",
    "directory_entries_examined",
    "physical_reads",
    "physical_writes",
    "durable_flushes",
    "virtio_notifications",
    "virtio_submitted_requests",
    "virtio_batched_read_requests",
    "overwrite_prereads_skipped",
)
CONTEST_LANE_IO_FIELDS = {"bytes_read"}
TASK_NORMALIZED_FIELDS = (
    "syscalls",
    "abi_descriptor_bytes",
    "copied_descriptor_bytes",
    "dispatch_header_bytes",
    "control_abi_bytes",
    "control_copied_bytes",
    "service_start_span_ticks",
    "sequence_elapsed_ticks",
    "sched_dispatch_delta",
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
FLOAT = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]+$")
SOURCE_COLUMNS = ("campaign_id", "source_file", "source_sha256", "source_ordinal")


class ExtractionError(RuntimeError):
    """Raised when an explicitly supplied campaign input cannot be consumed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _display_path(path: Path, source_root: Path | None = None) -> str:
    resolved = path.resolve()
    if source_root is not None:
        try:
            relative = resolved.relative_to(source_root.resolve())
        except ValueError as error:
            raise ExtractionError(
                f"input {path} is outside --source-root {source_root}"
            ) from error
        if not relative.parts:
            raise ExtractionError("--source-root must be a directory containing the inputs")
        return relative.as_posix()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name


def _collect_paths(inputs: Sequence[str], suffixes: set[str]) -> list[Path]:
    found: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if not path.exists():
            raise ExtractionError(f"input does not exist: {path}")
        if path.is_file():
            found.append(path)
            continue
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and candidate.suffix.lower() in suffixes:
                found.append(candidate)
    unique: dict[Path, None] = {}
    for path in found:
        unique[path.resolve()] = None
    return sorted(unique, key=lambda item: item.as_posix())


def _parse_value(value: str) -> int | float | str:
    # Fingerprints and digests intentionally remain strings, including leading zeroes.
    if INTEGER.fullmatch(value):
        try:
            return int(value)
        except ValueError:
            return value
    if FLOAT.fullmatch(value):
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) else value
        except ValueError:
            return value
    return value


def _parse_key_values(payload: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for token in shlex.split(payload, comments=False, posix=True):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if not key or key in row:
            raise ExtractionError(f"malformed or duplicate key in marker: {token!r}")
        # Preserve fixed-width identities even when they happen to be decimal-only.
        if key.endswith(("fingerprint", "digest", "hash")) or key in {
            "request_id",
            "identity",
            "lifecycle",
        }:
            row[key] = value
        else:
            row[key] = _parse_value(value)
    return row


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _ratio(numerator: Any, denominator: Any) -> float | str:
    top = _as_int(numerator)
    bottom = _as_int(denominator)
    if top is None or bottom is None or bottom == 0:
        return ""
    return round(top / bottom, 9)


def _source_meta(
    path: Path,
    ordinal: int,
    campaign_id: str,
    digest: str | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "source_file": _display_path(path, source_root),
        "source_sha256": digest or _sha256(path),
        "source_ordinal": ordinal,
    }


def parse_serial_files(
    paths: Sequence[Path], campaign_id: str, source_root: Path | None = None
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources: list[dict[str, Any]] = []
    for ordinal, path in enumerate(paths, 1):
        digest = _sha256(path)
        meta = _source_meta(path, ordinal, campaign_id, digest, source_root)
        marker_count = 0
        fragmented_count = 0
        incomplete_count = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                line = ANSI_ESCAPE.sub("", raw_line).strip("\x00\r\n ")
                matches: list[tuple[int, str, str]] = []
                for prefix, table_name in MARKERS:
                    offset = 0
                    while (position := line.find(prefix, offset)) >= 0:
                        matches.append((position, prefix, table_name))
                        offset = position + len(prefix)
                matches.sort(key=lambda item: item[0])
                for segment_index, (position, prefix, table_name) in enumerate(
                    matches
                ):
                    end = (
                        matches[segment_index + 1][0]
                        if segment_index + 1 < len(matches)
                        else len(line)
                    )
                    row = {
                        **meta,
                        "line_number": line_number,
                        **_parse_key_values(line[position + len(prefix) : end]),
                    }
                    if len(matches) > 1:
                        # Concurrent Guest writers can splice complete marker
                        # prefixes into one serial line. Preserve that fact while
                        # recovering each independently identifiable record.
                        row["serial_fragmented_line"] = 1
                        row["serial_segment_index"] = segment_index
                        row["serial_segment_count"] = len(matches)
                        fragmented_count += 1
                    required = MARKER_COMPLETE_FIELDS.get(table_name)
                    if required is not None:
                        complete = required.issubset(row)
                        row["serial_record_complete"] = int(complete)
                        if not complete:
                            incomplete_count += 1
                    tables[table_name].append(row)
                    marker_count += 1
        sources.append(
            {
                **meta,
                "kind": "serial",
                "bytes": path.stat().st_size,
                "marker_rows": marker_count,
                "fragmented_marker_rows": fragmented_count,
                "incomplete_marker_rows": incomplete_count,
            }
        )
    return tables, sources


def parse_contest_files(
    paths: Sequence[Path],
    campaign_id: str,
    source_ordinal_start: int,
    source_root: Path | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources: list[dict[str, Any]] = []
    for offset, path in enumerate(paths, source_ordinal_start):
        digest = _sha256(path)
        meta = _source_meta(path, offset, campaign_id, digest, source_root)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ExtractionError(f"contest CSV has no header: {path}")
            required = {
                "sample_id",
                "order",
                "traversal_core_duration_us",
                "indexed_core_duration_us",
            }
            missing = required - set(reader.fieldnames)
            if missing:
                raise ExtractionError(
                    f"contest CSV {path} is missing: {', '.join(sorted(missing))}"
                )
            row_count = 0
            for csv_line, raw_row in enumerate(reader, 2):
                row_count += 1
                paired = {**meta, "csv_line": csv_line}
                paired.update(
                    {
                        key: _parse_value(value) if value is not None else ""
                        for key, value in raw_row.items()
                    }
                )
                traversal_us = paired.get("traversal_core_duration_us")
                indexed_us = paired.get("indexed_core_duration_us")
                paired.setdefault(
                    "indexed_minus_traversal_core_us",
                    (_as_int(indexed_us, 0) or 0) - (_as_int(traversal_us, 0) or 0),
                )
                paired.setdefault(
                    "traversal_over_indexed_core_ratio",
                    _ratio(traversal_us, indexed_us),
                )
                tables["contest_paired"].append(paired)

                order = str(paired.get("order", ""))
                positions = (
                    {"traversal": 1, "indexed": 2}
                    if order == "traversal_then_indexed"
                    else {"indexed": 1, "traversal": 2}
                )
                for path_name in ("traversal", "indexed"):
                    lane: dict[str, Any] = {
                        **meta,
                        "csv_line": csv_line,
                        "sample_id": paired.get("sample_id", ""),
                        "order": order,
                        "path": path_name,
                        "execution_position": positions[path_name],
                    }
                    for field in CONTEST_PATH_FIELDS:
                        key = f"{path_name}_{field}"
                        if key in paired:
                            lane[field] = paired[key]
                    tables["contest_paths"].append(lane)

                    workload = _as_int(lane.get("workload_syscalls"))
                    records = _as_int(lane.get("records_examined"))
                    for metric in CONTEST_IO_FIELDS:
                        if metric not in lane:
                            continue
                        raw_value = _as_int(lane[metric])
                        if raw_value is None:
                            continue
                        # bytes_read comes from the lane marker; the remaining
                        # CSV fields are end-to-end performance-snapshot deltas.
                        lane_reported = metric in CONTEST_LANE_IO_FIELDS
                        tables["contest_io_normalized"].append(
                            {
                                **meta,
                                "sample_id": lane["sample_id"],
                                "order": order,
                                "path": path_name,
                                "metric": metric,
                                "metric_origin": (
                                    "lane_metric" if lane_reported
                                    else "mechanism_end_to_end"
                                ),
                                "counter_scope": (
                                    "workflow_lane" if lane_reported
                                    else "global_kernel"
                                ),
                                "counter_window": (
                                    "core" if lane_reported else "end_to_end"
                                ),
                                "counter_representation": (
                                    "lane_reported_total" if lane_reported else "delta"
                                ),
                                "counter_owner": (
                                    "workflow_actor" if lane_reported
                                    else "shared_kernel_not_process_attributed"
                                ),
                                "raw_value": raw_value,
                                "workload_syscalls": workload if workload is not None else "",
                                "denominator_metric": "observer_workload_syscalls",
                                "denominator_scope": "observer_process",
                                "denominator_window": "core",
                                "denominator_owner": "workflow_actor",
                                "normalization_cross_scope": 0 if lane_reported else 1,
                                "normalization_cross_window": 0 if lane_reported else 1,
                                "raw_counter_evidence": (
                                    f"raw/contest/sample-{int(lane['sample_id']):02d}-qemu.serial.txt"
                                    if _as_int(lane.get("sample_id")) is not None
                                    else "raw/contest/sample-*-qemu.serial.txt"
                                ),
                                "records_examined": records if records is not None else "",
                                "per_workload_syscall": _ratio(raw_value, workload),
                                "per_1000_workload_syscalls": (
                                    round(raw_value * 1000 / workload, 9)
                                    if workload
                                    else ""
                                ),
                                "per_record_examined": _ratio(raw_value, records),
                            }
                        )
        sources.append(
            {
                **meta,
                "kind": "contest_measurements_csv",
                "bytes": path.stat().st_size,
                "marker_rows": row_count,
            }
        )
    return tables, sources


def derive_agenteval_pairs(
    samples: Sequence[dict[str, Any]], warnings: list[str]
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        key = (
            row.get("source_file"),
            row.get("experiment"),
            row.get("load"),
            row.get("operations"),
            row.get("pair"),
        )
        groups[key].append(row)

    pairs: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        source_file, experiment, load, operations, pair = key
        baseline_id = BASELINE_VARIANTS.get(str(experiment))
        treatment_id = TREATMENT_VARIANTS.get(str(experiment))
        variants = {str(row.get("variant")): row for row in rows}
        if baseline_id is None or treatment_id is None:
            if len(variants) != 2:
                warnings.append(f"cannot pair unknown experiment {experiment!r} at {key}")
                continue
            baseline_id, treatment_id = sorted(variants)
        baseline = variants.get(baseline_id)
        treatment = variants.get(treatment_id)
        if baseline is None or treatment is None:
            warnings.append(
                f"incomplete pair in {source_file}: {experiment}/load={load}/"
                f"operations={operations}/pair={pair}"
            )
            continue
        baseline_us = _as_int(baseline.get("duration_us"))
        treatment_us = _as_int(treatment.get("duration_us"))
        if baseline_us is None or treatment_us is None:
            warnings.append(f"non-numeric pair duration at {key}")
            continue
        result_equal = (
            baseline.get("result_fingerprint") == treatment.get("result_fingerprint")
        )
        workload_equal = (
            baseline.get("workload_fingerprint")
            == treatment.get("workload_fingerprint")
        )
        operation_count = _as_int(operations)
        pairs.append(
            {
                **{column: baseline.get(column, "") for column in SOURCE_COLUMNS},
                "experiment": experiment,
                "load": load,
                "dataset_size": baseline.get("dataset_size", load),
                "operations": operations,
                "pair": pair,
                "order": baseline.get("order", treatment.get("order", "")),
                "baseline_variant": baseline_id,
                "treatment_variant": treatment_id,
                "baseline_cache": baseline.get("cache", ""),
                "treatment_cache": treatment.get("cache", ""),
                "baseline_duration_us": baseline_us,
                "treatment_duration_us": treatment_us,
                "treatment_minus_baseline_us": treatment_us - baseline_us,
                "speedup_baseline_over_treatment": _ratio(
                    baseline_us, treatment_us
                ),
                "baseline_us_per_operation": _ratio(baseline_us, operation_count),
                "treatment_us_per_operation": _ratio(treatment_us, operation_count),
                "baseline_work_units": baseline.get("work_units", ""),
                "treatment_work_units": treatment.get("work_units", ""),
                "baseline_records_examined": baseline.get("records_examined", ""),
                "treatment_records_examined": treatment.get(
                    "records_examined", ""
                ),
                "baseline_result_items": baseline.get("result_items", ""),
                "treatment_result_items": treatment.get("result_items", ""),
                "result_fingerprint_equal": int(result_equal),
                "workload_fingerprint_equal": int(workload_equal),
                "result_fingerprint": baseline.get("result_fingerprint", ""),
                "workload_fingerprint": baseline.get("workload_fingerprint", ""),
            }
        )
    return pairs


def derive_task_normalized(perf_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in perf_rows:
        operations = _as_int(row.get("operations"))
        for metric in TASK_NORMALIZED_FIELDS:
            value = _as_int(row.get(metric))
            if value is None:
                continue
            output.append(
                {
                    **{column: row.get(column, "") for column in SOURCE_COLUMNS},
                    "line_number": row.get("line_number", ""),
                    "path": row.get("path", ""),
                    "metric": metric,
                    "metric_scope": "task_path_sequence",
                    "raw_value": value,
                    "operations": operations if operations is not None else "",
                    "denominator_metric": "completed_operations",
                    "denominator_scope": "task_path_sequence",
                    "per_operation": _ratio(value, operations),
                }
            )
    return output


WAKE_LABELS = {0: "<=1", 1: "<=2", 2: "<=8", 3: ">8"}
WAKE_UPPER_TICKS: dict[int, int | str] = {0: 1, 1: 2, 2: 8, 3: ""}


def derive_wake_histograms(
    sample_rows: Sequence[dict[str, Any]], wake_rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in sample_rows:
        if _as_int(row.get("serial_record_complete"), 1) == 0:
            continue
        buckets: list[int] | None = None
        if "wake_buckets" in row:
            try:
                buckets = [int(value) for value in str(row["wake_buckets"]).split(",")]
            except ValueError:
                buckets = None
        elif all(f"wake_bucket_{index}" in row for index in range(4)):
            buckets = [_as_int(row.get(f"wake_bucket_{index}"), 0) or 0 for index in range(4)]
        if buckets is None or len(buckets) != 4:
            continue
        for bucket, count in enumerate(buckets):
            output.append(
                {
                    **{column: row.get(column, "") for column in SOURCE_COLUMNS},
                    "line_number": row.get("line_number", ""),
                    "histogram_scope": "workflow",
                    "scenario": row.get("scenario", ""),
                    "workflow_index": row.get("index", ""),
                    "workflow_source": row.get("source", ""),
                    "bucket_index": bucket,
                    "bucket_label": WAKE_LABELS[bucket],
                    "bucket_upper_ticks": WAKE_UPPER_TICKS[bucket],
                    "count": count,
                }
            )
    for row in wake_rows:
        raw_buckets = str(row.get("buckets", ""))
        try:
            buckets = [int(value) for value in raw_buckets.split(",")]
        except ValueError:
            continue
        if len(buckets) != 4:
            continue
        for bucket, count in enumerate(buckets):
            output.append(
                {
                    **{column: row.get(column, "") for column in SOURCE_COLUMNS},
                    "line_number": row.get("line_number", ""),
                    "histogram_scope": "cohort",
                    "scenario": row.get("scenario", ""),
                    "workflow_index": "",
                    "workflow_source": row.get("scope", "fresh_agents_only"),
                    "bucket_index": bucket,
                    "bucket_label": WAKE_LABELS[bucket],
                    "bucket_upper_ticks": WAKE_UPPER_TICKS[bucket],
                    "count": count,
                }
            )
    return output


def _ordered_fields(rows: Sequence[dict[str, Any]]) -> list[str]:
    preferred = [*SOURCE_COLUMNS, "line_number", "csv_line"]
    seen: set[str] = set()
    fields: list[str] = []
    for field in preferred:
        if any(field in row for row in rows):
            fields.append(field)
            seen.add(field)
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    return fields


def write_table(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = _ordered_fields(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not fields:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def extract(
    serial_paths: Sequence[Path],
    contest_paths: Sequence[Path],
    output_dir: Path,
    campaign_id: str,
    source_root: Path | None = None,
) -> dict[str, Any]:
    if source_root is not None:
        source_root = source_root.resolve()
        if not source_root.is_dir():
            raise ExtractionError(f"--source-root is not a directory: {source_root}")
        # Validate every input before writing even the first table.
        for path in [*serial_paths, *contest_paths]:
            _display_path(path, source_root)
    tables, sources = parse_serial_files(serial_paths, campaign_id, source_root)
    contest_tables, contest_sources = parse_contest_files(
        contest_paths, campaign_id, len(sources) + 1, source_root
    )
    for name, rows in contest_tables.items():
        tables[name].extend(rows)
    sources.extend(contest_sources)
    warnings: list[str] = []
    for source in sources:
        if source.get("incomplete_marker_rows", 0):
            warnings.append(
                f"{source['source_file']}: {source['incomplete_marker_rows']} "
                "serial marker row(s) are explicitly marked incomplete"
            )
    tables["agenteval_pairs"] = derive_agenteval_pairs(
        tables.get("agenteval_samples", []), warnings
    )
    tables["task_perf_normalized"] = derive_task_normalized(
        tables.get("task_perf", [])
    )
    for row in tables.get("eevdf_wakeups", []):
        latency = _as_int(row.get("wakeup_latency_ticks"))
        row["right_censored"] = int(latency is not None and latency >= 200)
        row["censor_limit_ticks"] = 200
    tables["eevdf_wake_histogram"] = derive_wake_histograms(
        tables.get("eevdf_samples", []), tables.get("eevdf_wake", [])
    )
    # eevdf_wake is an intermediate wide marker table; the public chart contract
    # is the explicit long-form histogram table.
    tables.pop("eevdf_wake", None)

    output_dir.mkdir(parents=True, exist_ok=True)
    row_counts: dict[str, int] = {}
    for table_name in TABLE_ORDER:
        rows = tables.get(table_name, [])
        write_table(output_dir / f"{table_name}.csv", rows)
        row_counts[table_name] = len(rows)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": campaign_id,
        "source_path_policy": (
            "relative_to_explicit_source_root"
            if source_root is not None
            else "relative_to_working_directory_when_contained"
        ),
        "sources": sources,
        "tables": row_counts,
        "warnings": warnings,
        "units": {
            "duration_us": "microseconds",
            "service_start_interval_tick": "uCore scheduler ticks",
            "wakeup_latency_ticks": "uCore scheduler ticks",
            "fairness_jain_ppm": "parts per million",
            "normalized_io": "counter units per measured workload syscall",
        },
        "notes": [
            "No aggregate replaces a raw sample; paired and normalized rows are derived views.",
            "EEVDF ready_age values at the 200-tick reporting cap are marked right-censored.",
            "EEVDF histogram bucket 3 is open-ended (>8 ticks) and has no invented upper bound.",
        ],
    }
    (output_dir / "extraction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        serial = root / "serial.txt"
        serial.write_text(
            "\n".join(
                [
                    "agenteval_ucore: sample schema=2 experiment=file_query_table_ablation load=24 pair=1 variant=scan order=AB cache=forced-scan operations=4 dataset_size=24 work_units=96 records_examined=96 result_items=4 duration_us=400 index_rebuild_records=0 result_cache_hits=0 workload_fingerprint=0000000000000001 result_fingerprint=0000000000000002 status=measured",
                    "agenteval_ucore: sample schema=2 experiment=file_query_table_ablation load=24 pair=1 variant=index order=AB cache=ready-index operations=4 dataset_size=24 work_units=4 records_examined=4 result_items=4 duration_us=100 index_rebuild_records=0 result_cache_hits=4 workload_fingerprint=0000000000000001 result_fingerprint=0000000000000002 status=measured",
                    "agenttask_ucore: one_shot_sequence schema=1 boot_round=1 order=0 path=batch operations=16 start_us=10 end_us=30 duration_us=20 syscalls=1",
                    "agenttask_ucore: one_shot_op schema=1 boot_round=1 path=batch operation_index=0 service_start_interval_tick=0",
                    "agenttask_ucore: perf path=batch operations=16 syscalls=1 abi_descriptor_bytes=64 copied_descriptor_bytes=64 dispatch_header_bytes=0 control_abi_bytes=0 control_copied_bytes=0 service_start_interval_tick_p50=0 service_start_interval_tick_p99=1 service_start_span_ticks=4 sequence_elapsed_ticks=5 sched_dispatch_delta=1",
                    "agent_eevdf_ucore: sample scenario=4 index=1 source=fresh threads=1 wake_probes=4 mode=1 flags=1 latency_class=1 weight=1024 request_ticks=12 lifecycle=1:1 work=9 service=8 dispatch=4 fallback=0 deadline_miss=0 wake_samples=4 wake_max=2 wake_bucket_0=2 wake_bucket_1=2 wake_bucket_2=0 wake_bucket_3=0",
                    "agent_eevdf_ucore: one_shot_wakeup schema=1 scenario=4 index=1 probe=0 wakeup_latency_ticks=1 dispatch_tick=9 reason_flags=1 histogram_bucket=0",
                    "agent_eevdf_ucore: jain_inputs scenario=4 n=4 sum=400 sum_sq=40000 basis=service_cycles_div_1024",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        contest = root / "measurements.csv"
        contest.write_text(
            "sample_id,order,traversal_core_duration_us,indexed_core_duration_us,"
            "traversal_end_to_end_duration_us,indexed_end_to_end_duration_us,"
            "traversal_workload_syscalls,indexed_workload_syscalls,"
            "traversal_records_examined,indexed_records_examined,"
            "traversal_bytes_read,indexed_bytes_read\n"
            "1,traversal_then_indexed,500,100,550,120,10,8,97,2,4096,512\n",
            encoding="utf-8",
        )
        output = root / "tables"
        manifest = extract(
            [serial], [contest], output, "self-test", source_root=root
        )
        assert manifest["tables"]["agenteval_pairs"] == 1
        assert manifest["tables"]["contest_paths"] == 2
        assert manifest["tables"]["task_perf_normalized"] == 9
        assert manifest["tables"]["eevdf_wakeups"] == 1
        assert manifest["tables"]["eevdf_wake_histogram"] == 4
        pair = next(csv.DictReader((output / "agenteval_pairs.csv").open()))
        assert pair["source_file"] == "serial.txt"
        assert pair["speedup_baseline_over_treatment"] == "4.0"
        assert pair["result_fingerprint_equal"] == "1"
    print("extract.py self-test: passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serial",
        action="append",
        default=[],
        metavar="PATH",
        help="serial log file or directory; may be repeated",
    )
    parser.add_argument(
        "--contest-csv",
        action="append",
        default=[],
        metavar="PATH",
        help="contest measurements CSV file or directory; may be repeated",
    )
    parser.add_argument("--output-dir", type=Path, help="destination for CSV tables")
    parser.add_argument("--campaign-id", default="one-shot-advanced-figures")
    parser.add_argument(
        "--source-root",
        type=Path,
        help="stable root containing every input; source_file is recorded relative to it",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if args.output_dir is None:
        raise SystemExit("--output-dir is required")
    if not args.serial and not args.contest_csv:
        raise SystemExit("at least one --serial or --contest-csv input is required")
    try:
        serial = _collect_paths(args.serial, {".txt", ".log", ".serial", ".out"})
        contest = _collect_paths(args.contest_csv, {".csv"})
        manifest = extract(
            serial,
            contest,
            args.output_dir,
            args.campaign_id,
            source_root=args.source_root,
        )
    except ExtractionError as error:
        print(f"extract.py: {error}", file=sys.stderr)
        return 2
    print(
        f"extract.py: wrote {sum(manifest['tables'].values())} rows across "
        f"{len(manifest['tables'])} tables to {args.output_dir}"
    )
    if manifest["warnings"]:
        for warning in manifest["warnings"]:
            print(f"extract.py: warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
