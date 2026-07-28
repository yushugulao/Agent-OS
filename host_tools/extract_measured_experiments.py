#!/usr/bin/env python3
"""Create measured experiment artifacts from an actual AgentOS Guest log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from measured_experiments import (
    MeasurementError,
    extract_file_query_measurements,
    write_csv,
    write_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest-log", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--command-json", required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--csv-out", type=Path, required=True)
    args = parser.parse_args()
    try:
        command = json.loads(args.command_json)
        if not isinstance(command, list):
            raise MeasurementError("command JSON must be an array")
        manifest = extract_file_query_measurements(
            args.guest_log,
            args.source_ref,
            command,
            args.commit,
            args.run_id,
        )
        write_manifest(args.manifest_out, manifest)
        write_csv(args.csv_out, manifest["rows"])
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
