#!/usr/bin/env python3
"""Regression checks for the fail-closed Reader result bundle contract."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from measured_experiments import extract_file_query_measurements, write_csv, write_manifest
from result_bundle_publication import (
    ResultPublicationError,
    abort_publication,
    begin_publication,
    publish_result,
)
from result_bundle_contract import (
    RUNNER_SWEEP_FIELDS,
    SUMMARY_ARTIFACT_FIELDS,
    SUMMARY_CHART_PATHS,
    SUMMARY_RAW_PATHS,
    ResultBundleError,
    validate_result_bundle,
)


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
    charts = root / "charts"
    charts.mkdir()
    for relative in SUMMARY_CHART_PATHS:
        (root / relative).write_text("<svg/>\n", encoding="utf-8")
    with (root / "runner-sweep.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RUNNER_SWEEP_FIELDS)
        writer.writerow(["unavailable", "plain_runtime_cases_zero"])
    for relative in SUMMARY_ARTIFACT_FIELDS.values():
        path = root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
    for relative in SUMMARY_RAW_PATHS:
        path = root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
    summary_paths = {
        field: str(root / relative)
        for field, relative in SUMMARY_ARTIFACT_FIELDS.items()
    }
    write_json(
        root / "summary.json",
        {
            "status": "ready",
            "rows": 1,
            "charts": [str(root / relative) for relative in SUMMARY_CHART_PATHS],
            "experiment_raw_csvs": [
                str(root / relative) for relative in SUMMARY_RAW_PATHS
            ],
            **summary_paths,
            "runner_tick_status": "unavailable",
            "runner_tick_reason": "plain_runtime_cases_zero",
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
        base = Path(temp)
        target = base / "untracked-results" / "latest"
        stage = begin_publication(target)
        assert target.parent.is_dir()
        assert not target.exists()
        abort_publication(target, stage)

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        target = base / "latest"
        target.mkdir()
        (target / "stale.txt").write_text("stale\n", encoding="utf-8")
        stage = begin_publication(target)
        assert not target.exists(), "begin did not invalidate the stale result"
        assert stage.parent == target.parent.resolve() and stage.is_dir()
        abort_publication(target, stage)
        assert not target.exists() and not stage.exists()

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        target = base / "latest"
        target.mkdir()
        (target / "stale.txt").write_text("stale\n", encoding="utf-8")
        real_mkdtemp = tempfile.mkdtemp

        def fail_result_stage(*args: object, **kwargs: object) -> str:
            prefix = str(kwargs.get("prefix", args[0] if args else ""))
            if prefix.startswith(".latest.staging-"):
                raise OSError("fixture stage allocation failure")
            return real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]

        with patch(
            "result_bundle_publication.tempfile.mkdtemp",
            side_effect=fail_result_stage,
        ):
            try:
                begin_publication(target)
            except OSError as error:
                assert "fixture stage allocation failure" in str(error)
            else:
                raise AssertionError("stage allocation failure was ignored")
        assert not target.exists(), "failed begin exposed the stale result again"

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        target = base / "latest"
        stage = begin_publication(target)
        (stage / "ready.txt").write_text("ready\n", encoding="utf-8")
        published = publish_result(target, stage)
        assert published == target.resolve()
        assert not stage.exists()
        assert (target / "ready.txt").read_text(encoding="utf-8") == "ready\n"

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        protected = base / "repository" / "tracked.txt"
        protected.parent.mkdir()
        protected.write_text("tracked\n", encoding="utf-8")
        try:
            begin_publication(base / "repository", (protected,))
        except ResultPublicationError as error:
            assert "protected path" in str(error)
        else:
            raise AssertionError("protected result ancestor was accepted")
        assert protected.read_text(encoding="utf-8") == "tracked\n"

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        target = base / "latest"
        stage = begin_publication(target)
        target.mkdir()
        try:
            publish_result(target, stage)
        except ResultPublicationError as error:
            assert "appeared during publication" in str(error)
        else:
            raise AssertionError("concurrent result replacement was accepted")
        target.rmdir()
        abort_publication(target, stage)

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
        stale.parent.mkdir(exist_ok=True)
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
        charts.mkdir(exist_ok=True)
        (charts / "renamed.svg").write_text("<svg/>\n", encoding="utf-8")
        expect_rejected(root, "unexpected chart artifact")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "unknown-summary-field"
        make_bundle(root)
        summary_path = root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["full_verify_status"] = "passed"
        write_json(summary_path, summary)
        expect_rejected(root, "closed contract")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "unknown-root-artifact"
        make_bundle(root)
        (root / "formula-pass.csv").write_text("status,passed\n", encoding="utf-8")
        expect_rejected(root, "file inventory differs")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "missing-manifest"
        make_bundle(root)
        (root / "experiments" / "measured-experiments.json").unlink()
        expect_rejected(root, "measurement manifest is missing")

    missing_cases = (
        ("monitor.html", "monitor page is missing"),
        ("summary.json", "result summary is missing"),
        ("runner-sweep.csv", "runner sweep CSV is missing"),
        ("experiments/status.json", "experiment status is missing"),
        ("experiments/agent-suite-guest.log", "measurement source log is missing"),
        (
            "experiments/raw/file-query-benchmark.csv",
            "result summary experiment_raw_csvs[0] is missing",
        ),
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
        root = Path(temp) / "removed-runner-chart"
        make_bundle(root)
        (root / "charts" / "runner-ticks.svg").write_text("<svg/>\n", encoding="utf-8")
        summary_path = root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["charts"].append(str(root / "charts" / "runner-ticks.svg"))
        write_json(summary_path, summary)
        expect_rejected(root, "unexpected chart artifact")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "removed-runner-fields"
        make_bundle(root)
        summary_path = root / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["runner_tick_pairs"] = 0
        write_json(summary_path, summary)
        expect_rejected(root, "removed runner measurement fields")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "tampered-runner-sweep"
        make_bundle(root)
        sweep = root / "runner-sweep.csv"
        sweep.write_text(
            "evidence_status,evidence_reason\nmeasured,source_bound_complete\n",
            encoding="utf-8",
        )
        expect_rejected(root, "runner availability sweep differs")

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
        real_symlink = False
        try:
            os.symlink(target, source)
            real_symlink = source.is_symlink()
        except OSError:
            pass
        if not real_symlink:
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
    assert 'LLM_RELAY_MODE="${LLM_RELAY_MODE:-template}"' in script, (
        "serve-reader must default to the deterministic offline relay"
    )
    if os.name != "nt":
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            state = base / "state"
            result = base / "result"
            output = base / "output"
            host_run_result = base / "agentos-host-run-result.state"
            state.mkdir()
            result.mkdir()
            (state / "rp_agentos_mainflow").write_text("status=ready\n", encoding="utf-8")
            (result / "monitor.html").write_text("stale\n", encoding="utf-8")
            host_run_result.write_text("status=ready\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "PYTHON_BIN": sys.executable,
                    "STATE_DIR": str(state),
                    "RESULT_DIR": str(result),
                    "OUT_DIR": str(output),
                    "HOST_RUN_RESULT": str(host_run_result),
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
