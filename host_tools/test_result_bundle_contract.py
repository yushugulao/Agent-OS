#!/usr/bin/env python3
"""Regression checks for the fail-closed Reader result bundle contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from measured_experiments import extract_file_query_measurements, write_csv, write_manifest
from result_bundle_contract import ResultBundleError, validate_result_bundle


MARKER = (
    "agentbench_ucore: file_query_benchmark schema=2 unit=us load=143 "
    "traversal_ops=64 traversal_records=143 traversal_duration_us=36 "
    "cold_index_ops=1 cold_index_records=6 cold_index_duration_us=2 "
    "cold_rebuild_records=512 cold_rebuild_included=1 "
    "warm_index_ops=64 warm_index_records=6 warm_index_duration_us=20 status=measured"
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_bundle(root: Path) -> dict[str, object]:
    experiments = root / "experiments"
    source = experiments / "agent-suite-guest.log"
    experiments.mkdir(parents=True)
    (root / "monitor.html").write_text("<!doctype html><title>results</title>\n", encoding="utf-8")
    source.write_text(MARKER + "\nagentbench_ucore: parent passed\n", encoding="utf-8")
    manifest = extract_file_query_measurements(
        source,
        source.name,
        ["make", "run-agent-tests"],
        "a" * 40,
        "bundle-test-run",
    )
    write_manifest(experiments / "measured-experiments.json", manifest)
    write_csv(experiments / "raw" / "file-query-benchmark.csv", manifest["rows"])
    status = {
        "schema_version": 1,
        "status": "measured",
        "rows": len(manifest["rows"]),
        "reason": "provenance-bound Guest marker verified",
        "source_log": source.name,
        "source_log_sha256": manifest["source"]["sha256"],
        "commit": manifest["commit"],
        "run_id": manifest["run_id"],
    }
    write_json(experiments / "status.json", status)
    write_json(
        root / "summary.json",
        {
            "status": "ready",
            "rows": 1,
            "experiment_status": "measured",
            "experiment_rows": len(manifest["rows"]),
        },
    )
    return manifest


def expect_rejected(root: Path, message: str) -> None:
    try:
        validate_result_bundle(root)
    except ResultBundleError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid result bundle: {message}")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "valid"
        manifest = make_bundle(root)
        result = validate_result_bundle(root)
        assert result == {
            "status": "valid",
            "rows": len(manifest["rows"]),
            "commit": manifest["commit"],
            "run_id": manifest["run_id"],
            "source_log": "agent-suite-guest.log",
        }, result

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "legacy-raw"
        make_bundle(root)
        stale = root / "experiments" / "raw" / "file-metadata.csv"
        stale.parent.mkdir(exist_ok=True)
        stale.write_text("formula,data\n", encoding="utf-8")
        expect_rejected(root, "obsolete formula-generated raw file")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "legacy-chart"
        make_bundle(root)
        stale = root / "charts" / "experiment-monitor-area.svg"
        stale.parent.mkdir()
        stale.write_text("<svg/>\n", encoding="utf-8")
        expect_rejected(root, "obsolete formula-generated chart")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "renamed-formula"
        make_bundle(root)
        (root / "experiments" / "raw" / "renamed.csv").write_text(
            "formula,data\n", encoding="utf-8"
        )
        expect_rejected(root, "unexpected experiment raw artifact")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "renamed-chart"
        make_bundle(root)
        charts = root / "charts"
        charts.mkdir()
        (charts / "renamed.svg").write_text("<svg/>\n", encoding="utf-8")
        expect_rejected(root, "unexpected chart artifact")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "missing-manifest"
        make_bundle(root)
        (root / "experiments" / "measured-experiments.json").unlink()
        expect_rejected(root, "measurement manifest is missing")

    missing_cases = (
        ("monitor.html", "monitor page is missing"),
        ("summary.json", "result summary is missing"),
        ("experiments/status.json", "experiment status is missing"),
        ("experiments/agent-suite-guest.log", "measurement source log is missing"),
        ("experiments/raw/file-query-benchmark.csv", "measurement raw CSV is missing"),
    )
    for relative, message in missing_cases:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "missing-artifact"
            make_bundle(root)
            (root / relative).unlink()
            expect_rejected(root, message)

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bad-hash"
        make_bundle(root)
        source = root / "experiments" / "agent-suite-guest.log"
        source.write_text(source.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        expect_rejected(root, "source log differs from manifest")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bad-raw-row"
        make_bundle(root)
        raw = root / "experiments" / "raw" / "file-query-benchmark.csv"
        raw.write_text(
            raw.read_text(encoding="utf-8").replace(",36,", ",35,", 1),
            encoding="utf-8",
        )
        expect_rejected(root, "raw CSV rows do not match")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bad-status"
        make_bundle(root)
        status_path = root / "experiments" / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["commit"] = "b" * 40
        write_json(status_path, status)
        expect_rejected(root, "status does not match")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "nonfinite-status"
        make_bundle(root)
        status_path = root / "experiments" / "status.json"
        status_path.write_text('{"rows":NaN}\n', encoding="utf-8")
        expect_rejected(root, "non-finite JSON number")

    for unsafe in (
        "../agent-suite-guest.log",
        "/absolute.log",
        "nested/agent-suite-guest.log",
        "nested\\agent-suite-guest.log",
        "C:\\absolute.log",
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "unsafe-path"
            make_bundle(root)
            manifest_path = root / "experiments" / "measured-experiments.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"]["path"] = unsafe
            write_json(manifest_path, manifest)
            expect_rejected(root, "single relative file name")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "symlink"
        make_bundle(root)
        source = root / "experiments" / "agent-suite-guest.log"
        target = root / "real-guest.log"
        target.write_bytes(source.read_bytes())
        source.unlink()
        try:
            os.symlink(target, source)
        except OSError:
            real_is_symlink = Path.is_symlink

            def report_source_symlink(path: Path) -> bool:
                return path == source or real_is_symlink(path)

            with patch.object(Path, "is_symlink", report_source_symlink):
                expect_rejected(root, "symlink")
        else:
            expect_rejected(root, "symlink")

    script = (Path(__file__).resolve().parents[1] / "scripts" / "serve-reader.sh").read_text(
        encoding="utf-8"
    )
    assert "result_bundle_contract.py" in script, "serve-reader does not validate result bundles"
    assert "exit 1" in script, "serve-reader does not fail closed"
    if os.name != "nt":
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            state = base / "state"
            result = base / "result"
            output = base / "output"
            state.mkdir()
            result.mkdir()
            (state / "rp_agentos_mainflow").write_text("status=ready\n", encoding="utf-8")
            (result / "monitor.html").write_text("stale\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "STATE_DIR": str(state),
                    "RESULT_DIR": str(result),
                    "OUT_DIR": str(output),
                }
            )
            completed = subprocess.run(
                ["bash", str(Path(__file__).resolve().parents[1] / "scripts" / "serve-reader.sh")],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
            assert completed.returncode != 0, completed.stdout + completed.stderr
            assert not (output / "dual-results").exists(), "invalid bundle was copied"
            assert "拒绝" in completed.stderr, completed.stderr
    print("result_bundle_contract_test: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
