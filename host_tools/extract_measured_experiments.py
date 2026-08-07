#!/usr/bin/env python3
"""从真实 AgentOS Guest 日志创建实测实验工件。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from measured_experiments import (
    MeasurementError,
    extract_file_query_measurements,
    sha256_bytes,
    verify_measurement_artifact_set,
    write_csv,
    write_manifest,
)
from safe_host_paths import (
    absolute_lexical_path,
    atomic_write_bytes,
    path_is_link,
    read_regular_file,
    require_private_directory,
)


GENERATION = re.compile(r"^g-[0-9a-f]{24}$")


def _artifact_record(path: Path, relative: str, role: str) -> dict[str, object]:
    raw = read_regular_file(path, nonempty=True)
    return {
        "role": role,
        "path": relative,
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
    }


def _remove_created(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            info = path.lstat()
            if not path_is_link(path, info.st_mode, file_info=info):
                path.unlink()
        except (FileNotFoundError, OSError):
            pass


def publish_measurement_set(args: argparse.Namespace) -> dict[str, object]:
    if not GENERATION.fullmatch(args.generation):
        raise MeasurementError("measurement generation is invalid")
    if not args.run_id.endswith("-" + args.generation):
        raise MeasurementError("measurement run id does not bind its generation")

    guest_log = absolute_lexical_path(args.guest_log)
    manifest_out = absolute_lexical_path(args.manifest_out)
    csv_out = absolute_lexical_path(args.csv_out)
    receipt_out = absolute_lexical_path(args.receipt_out)
    output_dir = require_private_directory(manifest_out.parent)
    paths = (guest_log, manifest_out, csv_out, receipt_out)
    if any(path.parent != output_dir for path in paths):
        raise MeasurementError("measurement artifacts must share one private run directory")
    expected_guest = absolute_lexical_path(output_dir / args.source_ref)
    if guest_log != expected_guest:
        raise MeasurementError("measurement source reference leaves its run directory")
    if len(set(paths)) != len(paths):
        raise MeasurementError("measurement artifact paths must be distinct")
    for output in (manifest_out, csv_out, receipt_out):
        if output.exists() or path_is_link(output):
            raise MeasurementError("measurement output already exists or is unsafe")

    created: list[Path] = []
    try:
        command = json.loads(args.command_json)
        if not isinstance(command, list):
            raise MeasurementError("command JSON must be an array")
        manifest = extract_file_query_measurements(
            guest_log,
            args.source_ref,
            command,
            args.commit,
            args.run_id,
        )
        write_manifest(manifest_out, manifest, replace=False)
        created.append(manifest_out)
        write_csv(csv_out, manifest["rows"], replace=False)
        created.append(csv_out)
        verify_measurement_artifact_set(
            manifest_out, csv_out, output_dir, args.commit, args.source_ref
        )
        receipt = {
            "schema_version": 1,
            "kind": "agentos-dual-measurement-set",
            "status": "complete",
            "generation": args.generation,
            "run_id": args.run_id,
            "commit": args.commit,
            "files": [
                _artifact_record(guest_log, args.source_ref, "guest_log"),
                _artifact_record(
                    manifest_out, manifest_out.name, "measurement_manifest"
                ),
                _artifact_record(csv_out, csv_out.name, "measurement_csv"),
            ],
        }
        receipt_bytes = (
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        atomic_write_bytes(receipt_out, receipt_bytes, replace=False)
        created.append(receipt_out)
        if read_regular_file(receipt_out, nonempty=True) != receipt_bytes:
            raise MeasurementError("measurement set receipt changed after publication")
        return manifest
    except Exception:
        _remove_created(created)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest-log", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--command-json", required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--csv-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = publish_measurement_set(args)
    except (MeasurementError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"extract_measured_experiments: failed: {error}")
        return 1
    print(
        "extract_measured_experiments: trials={} rows={} status=measured".format(
            len(manifest["rows"]) // 3, len(manifest["rows"])
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
