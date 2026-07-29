#!/usr/bin/env python3
"""Fail-closed validation for result bundles served by the local Reader."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from measured_experiments import CSV_FIELDS, MeasurementError, verify_manifest


LEGACY_RAW_FILES = {
    "agent-concurrency.csv",
    "context-timeline.csv",
    "event-loop.csv",
    "file-metadata.csv",
    "llm-relay.csv",
    "recovery-flow.csv",
}
LEGACY_EXPERIMENT_CHARTS = {
    "experiment-concurrency-heatmap.svg",
    "experiment-context-line.svg",
    "experiment-event-box.svg",
    "experiment-llm-relay-bar.svg",
    "experiment-monitor-area.svg",
    "experiment-recovery-line.svg",
}
CURRENT_RAW_FILES = {"file-query-benchmark.csv"}
CURRENT_CHART_FILES = {
    "cost-replacement.svg",
    "experiment-file-query-bar.svg",
    "runtime-observation.svg",
}
SUMMARY_ARTIFACT_FIELDS = {
    "report": Path("report.md"),
    "index": Path("index.html"),
    "monitor": Path("monitor.html"),
    "reader_guide": Path("reader-guide.html"),
    "csv": Path("summary.csv"),
    "runner_sweep_csv": Path("runner-sweep.csv"),
    "experiment_status_json": Path("experiments") / "status.json",
    "experiment_stats_csv": Path("experiments") / "experiment-stats.csv",
    "experiment_mechanism_csv": Path("experiments") / "mechanism-notes.csv",
    "delivery_readiness_csv": Path("delivery-readiness.csv"),
    "delivery_readiness": Path("delivery-readiness.html"),
    "test_suite_csv": Path("test-suite.csv"),
    "test_suite": Path("test-suite.html"),
    "experiment_design_csv": Path("experiment-design.csv"),
    "experiment_design": Path("experiment-design.html"),
    "evidence_manifest_csv": Path("evidence-manifest.csv"),
    "evidence_map": Path("evidence-map.html"),
    "reader_checklist_csv": Path("reader-checklist.csv"),
    "reader_checklist": Path("reader-checklist.html"),
}
SUMMARY_CHART_PATHS = (
    Path("charts") / "runtime-observation.svg",
    Path("charts") / "cost-replacement.svg",
    Path("charts") / "experiment-file-query-bar.svg",
)
SUMMARY_RAW_PATHS = (Path("experiments") / "raw" / "file-query-benchmark.csv",)
SUMMARY_FIELDS = frozenset(
    {
        "status",
        "rows",
        "charts",
        "runner_tick_status",
        "runner_tick_reason",
        "experiment_status",
        "experiment_raw_csvs",
        "experiment_rows",
        *SUMMARY_ARTIFACT_FIELDS,
    }
)
RUNNER_SWEEP_FIELDS = ["evidence_status", "evidence_reason"]
REMOVED_RUNNER_SUMMARY_FIELDS = {
    "runner_tick_comparison",
    "runner_tick_pairs",
    "runner_tick_expected_pairs",
}
STATUS_FIELDS = {
    "schema_version",
    "status",
    "rows",
    "reason",
    "source_log",
    "source_log_sha256",
    "commit",
    "run_id",
}
SOURCE_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ResultBundleError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ResultBundleError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ResultBundleError(f"non-finite JSON number: {value}")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ResultBundleError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResultBundleError(f"{label} is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ResultBundleError(f"{label} must be a JSON object")
    return value


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(junction) and junction())


def _safe_regular_file(root: Path, relative: Path, label: str) -> Path:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ResultBundleError(f"{label} has an unsafe path")
    current = root
    for part in relative.parts:
        current = current / part
        if _is_link(current):
            raise ResultBundleError(f"{label} must not be a symlink")
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise ResultBundleError(f"{label} is missing: {error}") from error
    if not _is_within(resolved, root) or not resolved.is_file():
        raise ResultBundleError(f"{label} is not a contained regular file")
    return resolved


def _single_file_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultBundleError(f"{label} must be a non-empty file name")
    # Check both separator styles so bundles remain safe across host platforms.
    if "/" in value or "\\" in value or ":" in value or Path(value).is_absolute():
        raise ResultBundleError(f"{label} must be a single relative file name")
    if Path(value).name != value:
        raise ResultBundleError(f"{label} must be a single relative file name")
    if not SOURCE_FILE_NAME.fullmatch(value):
        raise ResultBundleError(f"{label} contains unsafe file-name characters")
    return value


def _reject_symlinks(root: Path) -> None:
    for directory, directories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in directories + files:
            if _is_link(base / name):
                raise ResultBundleError(f"result bundle contains a symlink: {(base / name).relative_to(root)}")


def _reject_legacy_products(root: Path) -> None:
    for candidate in root.rglob("*"):
        if candidate.name in LEGACY_RAW_FILES:
            raise ResultBundleError(
                f"obsolete formula-generated raw file is present: {candidate.name}"
            )
        if candidate.name in LEGACY_EXPERIMENT_CHARTS:
            raise ResultBundleError(
                f"obsolete formula-generated chart is present: {candidate.name}"
            )


def _enforce_generated_product_allowlists(root: Path) -> None:
    for relative, allowed, label in (
        (Path("experiments") / "raw", CURRENT_RAW_FILES, "experiment raw artifact"),
        (Path("charts"), CURRENT_CHART_FILES, "chart artifact"),
    ):
        directory = root / relative
        if not directory.exists():
            continue
        if _is_link(directory) or not directory.is_dir():
            raise ResultBundleError(f"{label} directory is unsafe")
        for candidate in directory.iterdir():
            if not candidate.is_file() or candidate.name not in allowed:
                raise ResultBundleError(f"unexpected {label}: {candidate.name}")


def _validate_measurement_csv(path: Path, rows: list[object]) -> None:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CSV_FIELDS:
                raise ResultBundleError("measurement raw CSV fields do not match the manifest")
            actual = list(reader)
    except ResultBundleError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise ResultBundleError(f"measurement raw CSV is invalid: {error}") from error
    expected: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ResultBundleError("measurement manifest rows are invalid")
        expected.append({field: str(row.get(field, "")) for field in CSV_FIELDS})
    if actual != expected:
        raise ResultBundleError("measurement raw CSV rows do not match the manifest")


def _validate_runner_evidence(root: Path, summary: dict[str, Any]) -> None:
    sweep_path = _safe_regular_file(root, Path("runner-sweep.csv"), "runner sweep CSV")
    try:
        with sweep_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != RUNNER_SWEEP_FIELDS:
                raise ResultBundleError("runner sweep CSV fields differ")
            rows = list(reader)
    except ResultBundleError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise ResultBundleError(f"runner sweep CSV is invalid: {error}") from error

    status = summary.get("runner_tick_status")
    reason = summary.get("runner_tick_reason")
    if status != "unavailable" or reason != "plain_runtime_cases_zero":
        raise ResultBundleError("runner availability summary differs")
    if rows != [
        {
            "evidence_status": "unavailable",
            "evidence_reason": "plain_runtime_cases_zero",
        }
    ]:
        raise ResultBundleError("runner availability sweep differs")


def _validate_summary_artifact_path(
    root: Path,
    published_root: Path,
    value: object,
    relative: Path,
    label: str,
) -> None:
    _safe_regular_file(root, relative, label)
    if not isinstance(value, str) or not value:
        raise ResultBundleError(f"{label} path is missing")
    candidate = Path(value)
    if candidate.is_absolute():
        expected = (published_root / relative).resolve(strict=False)
        try:
            actual = candidate.resolve(strict=False)
        except OSError as error:
            raise ResultBundleError(f"{label} path is unavailable: {error}") from error
        if actual != expected:
            raise ResultBundleError(f"{label} path is not bound to the published result")
        return
    if candidate != relative or ".." in candidate.parts:
        raise ResultBundleError(f"{label} path is not a canonical bundle-relative path")


def _validate_summary_artifacts(
    root: Path, published_root: Path, summary: dict[str, Any]
) -> None:
    for field, relative in SUMMARY_ARTIFACT_FIELDS.items():
        _validate_summary_artifact_path(
            root, published_root, summary.get(field), relative, f"result summary {field}"
        )
    for field, relatives in (
        ("charts", SUMMARY_CHART_PATHS),
        ("experiment_raw_csvs", SUMMARY_RAW_PATHS),
    ):
        values = summary.get(field)
        if not isinstance(values, list) or len(values) != len(relatives):
            raise ResultBundleError(f"result summary {field} inventory differs")
        for index, (value, relative) in enumerate(zip(values, relatives)):
            _validate_summary_artifact_path(
                root,
                published_root,
                value,
                relative,
                f"result summary {field}[{index}]",
            )


def _validate_bundle_inventory(root: Path, source_name: str) -> None:
    expected_files = {
        Path("summary.json"),
        Path("experiments") / "measured-experiments.json",
        Path("experiments") / source_name,
        *SUMMARY_ARTIFACT_FIELDS.values(),
        *SUMMARY_CHART_PATHS,
        *SUMMARY_RAW_PATHS,
    }
    actual_files: set[Path] = set()
    actual_directories: set[Path] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        base = Path(directory)
        relative_base = base.relative_to(root)
        for name in directories:
            actual_directories.add(relative_base / name)
        for name in files:
            actual_files.add(relative_base / name)
    expected_directories = {
        parent
        for path in expected_files
        for parent in path.parents
        if parent != Path(".")
    }
    if actual_files != expected_files:
        raise ResultBundleError(
            "result bundle file inventory differs: "
            f"missing={sorted(str(path) for path in expected_files - actual_files)} "
            f"extra={sorted(str(path) for path in actual_files - expected_files)}"
        )
    if actual_directories != expected_directories:
        raise ResultBundleError(
            "result bundle directory inventory differs: "
            f"missing={sorted(str(path) for path in expected_directories - actual_directories)} "
            f"extra={sorted(str(path) for path in actual_directories - expected_directories)}"
        )


def validate_result_bundle(
    result_dir: Path, published_dir: Path | None = None
) -> dict[str, Any]:
    if _is_link(result_dir) or not result_dir.is_dir():
        raise ResultBundleError("result directory is missing or is a symlink")
    try:
        root = result_dir.resolve(strict=True)
    except OSError as error:
        raise ResultBundleError(f"result directory is unavailable: {error}") from error
    published_root = (
        root
        if published_dir is None
        else Path(os.path.abspath(published_dir)).resolve(strict=False)
    )

    _reject_symlinks(root)
    _reject_legacy_products(root)
    _enforce_generated_product_allowlists(root)
    _safe_regular_file(root, Path("monitor.html"), "monitor page")
    summary_path = _safe_regular_file(root, Path("summary.json"), "result summary")
    status_path = _safe_regular_file(
        root, Path("experiments") / "status.json", "experiment status"
    )
    manifest_path = _safe_regular_file(
        root,
        Path("experiments") / "measured-experiments.json",
        "measurement manifest",
    )

    summary = _read_json(summary_path, "result summary")
    status = _read_json(status_path, "experiment status")
    manifest_json = _read_json(manifest_path, "measurement manifest")
    if REMOVED_RUNNER_SUMMARY_FIELDS & set(summary):
        raise ResultBundleError("removed runner measurement fields are present")
    if set(summary) != SUMMARY_FIELDS:
        raise ResultBundleError("result summary fields do not match the closed contract")
    _validate_runner_evidence(root, summary)
    _validate_summary_artifacts(root, published_root, summary)

    source = manifest_json.get("source")
    if not isinstance(source, dict):
        raise ResultBundleError("measurement manifest source is invalid")
    source_name = _single_file_name(source.get("path"), "measurement source path")
    source_path = _safe_regular_file(
        root,
        Path("experiments") / source_name,
        "measurement source log",
    )
    experiments_root = (root / "experiments").resolve(strict=True)
    if source_path.parent != experiments_root:
        raise ResultBundleError("measurement source log escaped the experiments directory")
    _validate_bundle_inventory(root, source_name)

    try:
        manifest = verify_manifest(manifest_path, experiments_root)
    except MeasurementError as error:
        raise ResultBundleError(f"measurement manifest verification failed: {error}") from error

    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise ResultBundleError("measurement manifest rows are invalid")
    raw_csv = _safe_regular_file(
        root,
        Path("experiments") / "raw" / "file-query-benchmark.csv",
        "measurement raw CSV",
    )
    _validate_measurement_csv(raw_csv, rows)
    if set(status) != STATUS_FIELDS:
        raise ResultBundleError("experiment status fields do not match the measured contract")
    expected_status = {
        "schema_version": 1,
        "status": "measured",
        "rows": len(rows),
        "reason": "provenance-bound Guest marker verified",
        "source_log": manifest["source"]["path"],
        "source_log_sha256": manifest["source"]["sha256"],
        "commit": manifest["commit"],
        "run_id": manifest["run_id"],
    }
    if status != expected_status:
        raise ResultBundleError("experiment status does not match the verified manifest")

    if (
        summary.get("status") != "ready"
        or summary.get("experiment_status") != "measured"
        or summary.get("experiment_rows") != len(rows)
    ):
        raise ResultBundleError("result summary does not describe the verified measurement")
    return {
        "status": "valid",
        "rows": len(rows),
        "commit": manifest["commit"],
        "run_id": manifest["run_id"],
        "source_log": source_name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a provenance-bound Reader result bundle before serving it."
    )
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--published-dir", type=Path, default=None)
    args = parser.parse_args()
    try:
        result = validate_result_bundle(args.result_dir, args.published_dir)
    except ResultBundleError as error:
        print(f"result_bundle_contract: invalid: {error}", file=sys.stderr)
        return 1
    print(
        "result_bundle_contract: valid rows={rows} commit={commit} run_id={run_id}".format(
            **result
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
