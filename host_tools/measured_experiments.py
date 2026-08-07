#!/usr/bin/env python3
"""从真实 Guest 日志标记抽取绑定来源的基准数据行。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from safe_host_paths import atomic_write_bytes, read_regular_file
from strict_json import strict_json_loads

from benchmark_source_contract import (
    BENCHMARK_SOURCE,
    validate_benchmark_source as _validate_benchmark_source,
)


SCHEMA_VERSION = 2
MARKER_PREFIX = "agentbench_ucore: file_query_benchmark "
PASS_MARKER = "agentbench_ucore: parent passed"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_GUEST_LOG_BYTES = 128 << 20
MAX_SMALL_ARTIFACT_BYTES = 16 << 20
EXPECTED_KEYS = {
    "schema",
    "unit",
    "load",
    "traversal_ops",
    "traversal_records",
    "traversal_duration_us",
    "cold_index_ops",
    "cold_index_records",
    "cold_index_duration_us",
    "cold_rebuild_records",
    "cold_rebuild_included",
    "warm_index_ops",
    "warm_index_records",
    "warm_index_duration_us",
    "status",
}
CSV_FIELDS = [
    "experiment",
    "load",
    "trial",
    "path",
    "operations",
    "primary_metric",
    "primary_value",
    "duration_unit",
    "duration_value",
    "rebuild_records",
    "measurement_kind",
    "source_log",
    "source_line",
    "source_log_sha256",
    "source_marker_sha256",
    "source_command_json",
    "commit",
    "run_id",
]


class MeasurementError(RuntimeError):
    pass


def validate_benchmark_source(path: Path = BENCHMARK_SOURCE) -> None:
    try:
        _validate_benchmark_source(path)
    except ValueError as error:
        raise MeasurementError(str(error)) from error


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(read_regular_file(path, maximum_bytes=MAX_GUEST_LOG_BYTES))

def _read_artifact(path: Path, maximum: int, message: str) -> bytes:
    try:
        return read_regular_file(path, nonempty=True, maximum_bytes=maximum)
    except (OSError, ValueError) as error:
        raise MeasurementError(message) from error

def _parse_tokens(line: str, line_number: int) -> dict[str, str]:
    if not line.startswith(MARKER_PREFIX):
        raise MeasurementError(f"line {line_number} is not a benchmark marker")
    fields: dict[str, str] = {}
    for token in line[len(MARKER_PREFIX) :].split():
        if token.count("=") != 1:
            raise MeasurementError(f"invalid benchmark token at line {line_number}")
        key, value = token.split("=", 1)
        if not key or not value or key in fields:
            raise MeasurementError(f"duplicate or empty benchmark token at line {line_number}")
        fields[key] = value
    if set(fields) != EXPECTED_KEYS:
        missing = sorted(EXPECTED_KEYS - set(fields))
        extra = sorted(set(fields) - EXPECTED_KEYS)
        raise MeasurementError(
            f"benchmark marker schema mismatch at line {line_number}: missing={missing} extra={extra}"
        )
    return fields


def _positive_int(fields: dict[str, str], key: str, line_number: int) -> int:
    try:
        value = int(fields[key], 10)
    except ValueError as error:
        raise MeasurementError(f"{key} is not an integer at line {line_number}") from error
    if value <= 0 or value > (1 << 31) - 1:
        raise MeasurementError(f"{key} is outside the accepted range at line {line_number}")
    return value


def _nonnegative_int(fields: dict[str, str], key: str, line_number: int) -> int:
    try:
        value = int(fields[key], 10)
    except ValueError as error:
        raise MeasurementError(f"{key} is not an integer at line {line_number}") from error
    if value < 0 or value > (1 << 31) - 1:
        raise MeasurementError(f"{key} is outside the accepted range at line {line_number}")
    return value


def _rows_for_marker(
    fields: dict[str, str],
    trial: int,
    source_ref: str,
    source_line: int,
    source_hash: str,
    marker_hash: str,
    command: list[str],
    commit: str,
    run_id: str,
) -> list[dict[str, object]]:
    if (
        fields["schema"] != "2"
        or fields["unit"] != "us"
        or fields["status"] != "measured"
    ):
        raise MeasurementError(f"benchmark marker is not measured at line {source_line}")
    if fields["cold_rebuild_included"] != "1":
        raise MeasurementError(f"cold index does not include rebuild work at line {source_line}")
    load = _positive_int(fields, "load", source_line)
    values = {
        key: _positive_int(fields, key, source_line)
        for key in EXPECTED_KEYS
        if key.endswith(("_ops", "_records")) and key != "cold_rebuild_records"
    }
    values["cold_rebuild_records"] = _nonnegative_int(fields, "cold_rebuild_records", source_line)
    durations = {
        path: _nonnegative_int(fields, f"{path}_duration_us", source_line)
        for path in ("traversal", "cold_index", "warm_index")
    }
    if load > values["traversal_records"]:
        raise MeasurementError(f"load exceeds traversal work at line {source_line}")
    if values["cold_index_records"] > values["traversal_records"]:
        raise MeasurementError(f"cold index work exceeds traversal work at line {source_line}")
    if values["warm_index_records"] > values["traversal_records"]:
        raise MeasurementError(f"warm index work exceeds traversal work at line {source_line}")
    if values["warm_index_records"] != values["cold_index_records"]:
        raise MeasurementError(f"cold and warm index candidates differ at line {source_line}")
    command_json = json.dumps(command, separators=(",", ":"), ensure_ascii=True)
    specs = (
        ("traversal", "records_touched", 0, "guest-syscall"),
        ("cold_index", "metadata_candidates", values["cold_rebuild_records"], "guest-syscall"),
        ("warm_index", "metadata_candidates", 0, "guest-syscall"),
    )
    rows: list[dict[str, object]] = []
    for path, metric, rebuild_records, kind in specs:
        rows.append(
            {
                "experiment": "file_metadata",
                "load": load,
                "trial": trial,
                "path": path,
                "operations": values[f"{path}_ops"],
                "primary_metric": metric,
                "primary_value": values[f"{path}_records"],
                "duration_unit": "us",
                "duration_value": durations[path],
                "rebuild_records": rebuild_records,
                "measurement_kind": kind,
                "source_log": source_ref,
                "source_line": source_line,
                "source_log_sha256": source_hash,
                "source_marker_sha256": marker_hash,
                "source_command_json": command_json,
                "commit": commit,
                "run_id": run_id,
            }
        )
    return rows


def extract_file_query_measurements(
    source_log: Path,
    source_ref: str,
    command: list[str],
    commit: str,
    run_id: str,
    benchmark_source: Path = BENCHMARK_SOURCE,
) -> dict[str, object]:
    validate_benchmark_source(benchmark_source)
    if not COMMIT.fullmatch(commit):
        raise MeasurementError("measurement commit is invalid")
    if not RUN_ID.fullmatch(run_id):
        raise MeasurementError("measurement run id is invalid")
    if not command or not all(isinstance(item, str) and item for item in command):
        raise MeasurementError("measurement command is invalid")
    if not source_ref or Path(source_ref).is_absolute() or ".." in Path(source_ref).parts:
        raise MeasurementError("measurement source reference is invalid")

    raw = _read_artifact(source_log, MAX_GUEST_LOG_BYTES,
                         "Guest source log is missing or unsafe")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise MeasurementError("Guest source log is not UTF-8") from error
    source_hash = sha256_bytes(raw)
    markers: list[tuple[int, str, dict[str, str]]] = []
    pending: tuple[int, str, dict[str, str]] | None = None
    for line_number, line in enumerate(lines, 1):
        if line.startswith(MARKER_PREFIX):
            if pending is not None:
                raise MeasurementError("benchmark marker was not followed by a pass marker")
            pending = (line_number, line, _parse_tokens(line, line_number))
        elif line == PASS_MARKER and pending is not None:
            markers.append(pending)
            pending = None
    if pending is not None:
        raise MeasurementError("benchmark marker was not followed by a pass marker")
    if not markers:
        raise MeasurementError("Guest log has no measured file-query benchmark")

    rows: list[dict[str, object]] = []
    for trial, (line_number, line, fields) in enumerate(markers, 1):
        rows.extend(
            _rows_for_marker(
                fields,
                trial,
                source_ref,
                line_number,
                source_hash,
                sha256_bytes(line.encode("utf-8")),
                command,
                commit,
                run_id,
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "agentos-measured-experiments",
        "status": "measured",
        "commit": commit,
        "run_id": run_id,
        "command": command,
        "source": {
            "path": source_ref,
            "bytes": len(raw),
            "sha256": source_hash,
        },
        "rows": rows,
    }

def write_manifest(path: Path, value: dict[str, object], *, replace: bool = True) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True,
                          ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write_bytes(path, payload, replace=replace)

def write_csv(path: Path, rows: list[dict[str, object]], *, replace: bool = True) -> None:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    atomic_write_bytes(path, output.getvalue().encode("utf-8"), replace=replace)

def verify_manifest(path: Path, source_root: Path) -> dict[str, object]:
    raw = _read_artifact(path, MAX_SMALL_ARTIFACT_BYTES,
                         "measurement manifest is invalid")
    try:
        value = strict_json_loads(raw)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise MeasurementError(f"measurement manifest is invalid: {error}") from error
    expected_top = {
        "schema_version",
        "kind",
        "status",
        "commit",
        "run_id",
        "command",
        "source",
        "rows",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_top
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("kind") != "agentos-measured-experiments"
        or value.get("status") != "measured"
    ):
        raise MeasurementError("measurement manifest contract is invalid")
    source = value.get("source")
    if not isinstance(source, dict) or set(source) != {"path", "bytes", "sha256"}:
        raise MeasurementError("measurement source contract is invalid")
    relative = Path(str(source.get("path", "")))
    if relative.is_absolute() or ".." in relative.parts:
        raise MeasurementError("measurement source path is unsafe")
    source_log = source_root / relative
    source_bytes = _read_artifact(source_log, MAX_GUEST_LOG_BYTES,
                                  "measurement source log differs from manifest")
    if (len(source_bytes) != source.get("bytes") or
            sha256_bytes(source_bytes) != source.get("sha256")):
        raise MeasurementError("measurement source log differs from manifest")
    rebuilt = extract_file_query_measurements(
        source_log,
        relative.as_posix(),
        value.get("command"),
        str(value.get("commit", "")),
        str(value.get("run_id", "")),
    )
    if rebuilt != value:
        raise MeasurementError("measurement rows do not match the Guest log")
    return value

def capture_bundle_artifacts(
    stage: Path,
    raw_records: list[dict[str, object]],
    command: list[str],
    commit: str,
) -> dict[str, dict[str, object]]:
    guests = [item for item in raw_records if item.get("name") == "agent-suite-guest.log"]
    if len(guests) != 1:
        raise MeasurementError("summary lacks a unique Agent Guest benchmark artifact")
    guest = guests[0]
    guest_path = stage / str(guest.get("path", ""))
    guest_hash = str(guest.get("sha256", ""))
    run_id = f"local-{commit[:12]}-{guest_hash[:12]}"
    value = extract_file_query_measurements(
        guest_path, str(guest["path"]), command, commit, run_id
    )
    manifest_path = stage / "metrics" / "file-query-benchmark.json"
    csv_path = stage / "metrics" / "file-query-benchmark.csv"
    write_manifest(manifest_path, value)
    write_csv(csv_path, value["rows"])
    return {
        "file_query_benchmark_csv": {
            "path": "metrics/file-query-benchmark.csv",
            "sha256": sha256_file(csv_path),
        },
        "file_query_benchmark_manifest": {
            "path": "metrics/file-query-benchmark.json",
            "sha256": sha256_file(manifest_path),
        },
    }

def _require_bundle_ref(
    root: Path, reference: object, expected_path: str
) -> Path:
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise MeasurementError(f"measurement artifact reference is invalid: {expected_path}")
    path = root / expected_path
    if (
        reference.get("path") != expected_path
        or not path.is_file()
        or path.is_symlink()
        or reference.get("sha256") != sha256_file(path)
    ):
        raise MeasurementError(f"measurement artifact differs: {expected_path}")
    return path

def verify_measurement_artifact_set(
    manifest_path: Path,
    csv_path: Path,
    source_root: Path,
    commit: str,
    source_ref: str,
) -> dict[str, object]:
    value = verify_manifest(manifest_path, source_root)
    csv_bytes = _read_artifact(csv_path, MAX_SMALL_ARTIFACT_BYTES,
                               "file-query benchmark CSV is invalid")
    try:
        csv_text = csv_bytes.decode("utf-8")
        reader = csv.DictReader(io.StringIO(csv_text, newline=""))
        rows = list(reader)
    except (OSError, UnicodeDecodeError, ValueError, csv.Error) as error:
        raise MeasurementError(f"file-query benchmark CSV is invalid: {error}") from error
    expected_rows = [
        {field: str(row.get(field, "")) for field in CSV_FIELDS}
        for row in value["rows"]
    ]
    source_log = source_root / source_ref
    source_bytes = _read_artifact(source_log, MAX_GUEST_LOG_BYTES,
                                  "file-query benchmark source is unsafe")
    if rows != expected_rows or reader.fieldnames != CSV_FIELDS:
        raise MeasurementError("file-query benchmark CSV differs from its manifest")
    if (
        value["commit"] != commit
        or value["source"] != {
            "path": source_ref,
            "bytes": len(source_bytes),
            "sha256": sha256_bytes(source_bytes),
        }
        or any(
            row["source_log"] != source_ref
            or row["source_log_sha256"] != value["source"]["sha256"]
            or row["commit"] != commit
            or row["run_id"] != value["run_id"]
            for row in value["rows"]
        )
    ):
        raise MeasurementError("file-query benchmark is not bound to this Guest run")
    return value

def verify_bundle_artifacts(
    root: Path,
    metrics: object,
    commit: str,
    guest_record: object,
    expected_command: list[str],
) -> dict[str, object]:
    if not isinstance(metrics, dict) or not isinstance(guest_record, dict):
        raise MeasurementError("measurement bundle references are invalid")
    csv_path = _require_bundle_ref(
        root, metrics.get("file_query_benchmark_csv"),
        "metrics/file-query-benchmark.csv"
    )
    manifest_path = _require_bundle_ref(
        root, metrics.get("file_query_benchmark_manifest"),
        "metrics/file-query-benchmark.json"
    )
    source_ref = "logs/raw/agent-suite-guest.log"
    guest_path = root / source_ref
    if guest_record.get("sha256") != sha256_file(guest_path):
        raise MeasurementError("Agent Guest benchmark reference differs")
    value = verify_measurement_artifact_set(
        manifest_path, csv_path, root, commit, source_ref
    )
    if value["command"] != expected_command:
        raise MeasurementError("file-query benchmark command differs from full-verify command")
    return value
