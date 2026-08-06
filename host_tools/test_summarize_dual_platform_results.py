#!/usr/bin/env python3
"""Regression checks for real-data dual-platform summaries."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import summarize_dual_platform_results as summary
from dual_state_evidence_contract import RUN_RESULT_WORK_FILES
from measured_experiments import extract_file_query_measurements, write_manifest
from result_bundle_contract import validate_result_bundle


MARKER = (
    "agentbench_ucore: file_query_benchmark schema=2 unit=us load=143 "
    "traversal_ops=64 traversal_records=143 traversal_duration_us=36 "
    "cold_index_ops=1 cold_index_records=6 cold_index_duration_us=2 "
    "cold_rebuild_records=512 cold_rebuild_included=1 "
    "warm_index_ops=64 warm_index_records=6 warm_index_duration_us=20 status=measured"
)
def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def fixture(work_dir: Path, measured: bool) -> None:
    write_json(
        work_dir / "state-compare-summary.json",
        {
            "plain_files": 258,
            "agentos_files": 271,
            "common_files": 258,
            "agentos_extra_files": 13,
            "checked_compatibility_records": 1244,
            "plain_reference_products": 2,
            "agentos_reference_products": 2,
            "plain_reference_records": 4,
            "agentos_reference_records": 4,
            "guest_source_bound_runtime_records": 0,
            "embedded_action_records": 44,
            "agentos_evidence_checks": 32,
            "host_derived_mainflow_stages": 11,
            "agentos_mainflow_verification_origin": "host_inventory",
            "agentos_mainflow_facts": 12,
            "run_result_match": 1,
            "cost_replacement_count": 1,
            "cost_replacements": [
                {
                    "case": "agentos-fsmeta",
                    "plain_cost": "scan_records_128",
                    "agentos_replace": "metadata_index",
                    "risk": "scan_growth",
                    "preserved_from_plain": 1,
                    "status": "passed",
                }
            ],
            "runner_tick_status": "unavailable",
            "runner_tick_reason": "plain_runtime_cases_zero",
            "status": "ready",
        },
    )
    write_json(
        work_dir / "reader-compare-summary.json",
        {
            "plain_pages": 40,
            "agentos_pages": 40,
            "plain_state_files": 260,
            "agentos_state_files": 273,
            "plain_api_json": 267,
            "agentos_api_json": 280,
            "agentos_extra_api_json": 13,
            "status": "ready",
        },
    )
    for name in ("seeded-action-state", "host-surface-alignment"):
        write_json(work_dir / f"{name}.json", {"action_count": 44, "status": "ready"})
    write_json(
        work_dir / "host-platform-alignment.json",
        {
            "status": "ready",
            "mainflow_host_verified": True,
            "mainflow_verification_origin": "host_inventory",
            "mainflow_host_stages": 11,
        },
    )
    write_json(
        work_dir / "host-test-alignment.json",
        {"status": "ready", "runtime_evidence_verified": True},
    )
    for target, elapsed in (("plain", "12.5"), ("agentos", "15.25")):
        path = work_dir / RUN_RESULT_WORK_FILES[target]
        path.write_text(
            f"qemu_elapsed_seconds={elapsed}\nqemu_idle_notices=0\nqemu_timed_out=0\n",
            encoding="utf-8",
        )
    (work_dir / "stage-timings.csv").write_text(
        "stage,start_epoch,end_epoch,duration_seconds,status\n"
        "structure-check,1,2,1,ready\nseeded-dual-run,2,18,16,ready\n",
        encoding="utf-8",
    )
    if measured:
        log = work_dir / "agent-suite-guest.log"
        log.write_text(
            MARKER + "\nagentbench_ucore: parent passed\n" + MARKER
            + "\nagentbench_ucore: parent passed\n",
            encoding="utf-8",
        )
        manifest = extract_file_query_measurements(
            log,
            "agent-suite-guest.log",
            ["make", "run-agent-tests"],
            "a" * 40,
            "fixture-run",
        )
        write_manifest(work_dir / "measured-experiments.json", manifest)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir, out_dir = Path(work_tmp), Path(out_tmp)
        fixture(work_dir, measured=True)
        result = summary.summarize(work_dir, out_dir, require_measured_experiments=True)
        assert result["status"] == "ready", result
        assert result["experiment_status"] == "measured", result
        assert result["experiment_rows"] == 6, result
        assert result["runner_tick_status"] == "unavailable", result
        assert result["runner_tick_reason"] == "plain_runtime_cases_zero", result
        assert "runner_tick_pairs" not in result, result
        assert "runner_tick_expected_pairs" not in result, result
        assert len(result["experiment_raw_csvs"]) == 1, result
        assert len(result["charts"]) == 2, result
        served = validate_result_bundle(out_dir)
        assert served["status"] == "valid" and served["rows"] == 6, served
        assert {Path(path).name for path in result["charts"]} == {
            "runtime-observation.svg",
            "experiment-file-query-bar.svg",
        }, result
        for artifact in (
            "runner-sweep.csv",
            "experiments/mechanism-notes.csv",
            "monitor.html",
            "reader-guide.html",
            "evidence-manifest.csv",
            "evidence-map.html",
            "reader-checklist.csv",
            "reader-checklist.html",
            "delivery-readiness.csv",
            "delivery-readiness.html",
            "test-suite.csv",
            "test-suite.html",
            "experiment-design.csv",
            "experiment-design.html",
        ):
            assert (out_dir / artifact).is_file(), artifact

        raw = read_csv(out_dir / "experiments" / "raw" / "file-query-benchmark.csv")
        assert len(raw) == 6, raw
        assert {row["path"] for row in raw} == {"traversal", "cold_index", "warm_index"}, raw
        assert all(row["source_log_sha256"] and row["source_marker_sha256"] for row in raw), raw
        assert all(row["source_command_json"] == '["make","run-agent-tests"]' for row in raw), raw
        assert {row["measurement_kind"] for row in raw} == {"guest-syscall"}, raw
        sweep_rows = read_csv(out_dir / "runner-sweep.csv")
        assert sweep_rows == [{
            "evidence_status": "unavailable",
            "evidence_reason": "plain_runtime_cases_zero",
        }], sweep_rows

        status = json.loads((out_dir / "experiments" / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == "measured" and status["rows"] == 6, status
        assert status["source_log"] == "agent-suite-guest.log", status
        assert len(status["source_log_sha256"]) == 64, status
        bundled_manifest = out_dir / "experiments" / "measured-experiments.json"
        bundled_log = out_dir / "experiments" / "agent-suite-guest.log"
        assert bundled_manifest.is_file() and bundled_log.is_file()
        bundled = json.loads(bundled_manifest.read_text(encoding="utf-8"))
        assert bundled["source"]["path"] == "agent-suite-guest.log", bundled
        assert all(row["source_log"] == "agent-suite-guest.log" for row in bundled["rows"]), bundled
        stats = read_csv(out_dir / "experiments" / "experiment-stats.csv")
        assert {row["path"] for row in stats} == {"traversal", "cold_index", "warm_index"}, stats
        chart = (out_dir / "charts" / "experiment-file-query-bar.svg").read_text(encoding="utf-8")
        assert "真实 Guest 文件查询" in chart and "冷索引（含重建）" in chart, chart
        report = (out_dir / "report.md").read_text(encoding="utf-8")
        assert "真实 Guest 文件查询统计" in report
        assert "非证据状态兼容记录" in report
        evidence = (out_dir / "evidence-manifest.csv").read_text(encoding="utf-8")
        assert "file-query-benchmark.csv" in evidence and "Guest log SHA256" in evidence
        generated = "\n".join(path.read_text(encoding="utf-8") for path in out_dir.glob("*.html"))
        for obsolete in (
            "cost-replacement.svg",
            "用户态成本项与 AgentOS 替代机制",
            "experiment-context-line.svg",
            "experiment-monitor-area.svg",
            "file-metadata.csv",
        ):
            assert obsolete not in generated, obsolete

    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir, out_dir = Path(work_tmp), Path(out_tmp)
        fixture(work_dir, measured=False)
        legacy_raw = out_dir / "experiments" / "raw"
        legacy_raw.mkdir(parents=True)
        for name in (
            "agent-concurrency.csv",
            "context-timeline.csv",
            "event-loop.csv",
            "file-metadata.csv",
            "llm-relay.csv",
            "recovery-flow.csv",
        ):
            (legacy_raw / name).write_text("formula,generated\n", encoding="utf-8")
        charts = out_dir / "charts"
        charts.mkdir()
        (charts / "cost-replacement.svg").write_text("<svg>stale costs</svg>\n", encoding="utf-8")
        (charts / "experiment-context-line.svg").write_text("<svg/>\n", encoding="utf-8")
        (charts / "runner-ticks.svg").write_text("<svg>stale ticks</svg>\n", encoding="utf-8")
        (charts / "runner-speedup.svg").write_text("<svg>stale speedup</svg>\n", encoding="utf-8")
        result = summary.summarize(work_dir, out_dir)
        assert result["experiment_status"] == "unavailable", result
        assert result["experiment_rows"] == 0 and result["experiment_raw_csvs"] == [], result
        assert result["runner_tick_status"] == "unavailable", result
        assert result["runner_tick_reason"] == "plain_runtime_cases_zero", result
        assert "runner_tick_pairs" not in result, result
        assert "runner_tick_expected_pairs" not in result, result
        assert len(result["charts"]) == 1, result
        assert {Path(path).name for path in result["charts"]} == {
            "runtime-observation.svg",
        }, result
        assert not (out_dir / "experiments" / "raw").exists()
        assert not (out_dir / "experiments" / "experiment-stats.csv").exists()
        assert not (out_dir / "charts" / "experiment-context-line.svg").exists()
        assert not (out_dir / "charts" / "cost-replacement.svg").exists()
        assert not (out_dir / "charts" / "runner-ticks.svg").exists()
        assert not (out_dir / "charts" / "runner-speedup.svg").exists()
        sweep_rows = read_csv(out_dir / "runner-sweep.csv")
        assert sweep_rows == [{
            "evidence_status": "unavailable",
            "evidence_reason": "plain_runtime_cases_zero",
        }], sweep_rows
        status = json.loads((out_dir / "experiments" / "status.json").read_text(encoding="utf-8"))
        assert status == {
            "schema_version": 1,
            "status": "unavailable",
            "rows": 0,
            "reason": "measured-experiments.json is missing",
        }, status
        for artifact in ("report.md", "index.html", "monitor.html", "reader-guide.html"):
            rendered = (out_dir / artifact).read_text(encoding="utf-8")
            assert "unavailable" in rendered and "plain_runtime_cases_zero" in rendered, artifact
            assert "charts/runner-" not in rendered, artifact
        manifest_rows = read_csv(out_dir / "evidence-manifest.csv")
        assert any(row["artifact"] == "runner-sweep.csv" for row in manifest_rows), manifest_rows
        assert not any(row["artifact"].startswith("charts/runner-") for row in manifest_rows), manifest_rows
        try:
            summary.summarize(work_dir, out_dir, require_measured_experiments=True)
        except ValueError as error:
            assert "unavailable" in str(error), error
        else:
            raise AssertionError("required measured evidence was silently accepted")

    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir, out_dir = Path(work_tmp), Path(out_tmp)
        fixture(work_dir, measured=True)
        result = summary.summarize(work_dir, out_dir, require_measured_experiments=True)
        assert result["experiment_status"] == "measured", result
        assert result["runner_tick_status"] == "unavailable", result
        assert {Path(path).name for path in result["charts"]} == {
            "runtime-observation.svg",
            "experiment-file-query-bar.svg",
        }, result

    def bad_unavailable_reason(state: dict[str, object]) -> None:
        state["runner_tick_reason"] = "unknown"

    def measured_status(state: dict[str, object]) -> None:
        state["runner_tick_status"] = "measured"

    def removed_measurement_field(state: dict[str, object]) -> None:
        state["runner_tick_pairs"] = 0

    for mutate in (bad_unavailable_reason, measured_status, removed_measurement_field):
        with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
            work_dir, out_dir = Path(work_tmp), Path(out_tmp)
            fixture(work_dir, measured=True)
            state_path = work_dir / "state-compare-summary.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            mutate(state)
            write_json(state_path, state)
            try:
                summary.summarize(work_dir, out_dir)
            except ValueError as error:
                assert "runner tick" in str(error), error
            else:
                raise AssertionError(f"invalid runner evidence was silently accepted: {mutate.__name__}")

    def legacy_mainflow_field(state: dict[str, object]) -> None:
        state["agentos_mainflow_stages"] = 11

    def guest_origin(state: dict[str, object]) -> None:
        state["agentos_mainflow_verification_origin"] = "guest_claim"

    def negative_guest_count(state: dict[str, object]) -> None:
        state["guest_source_bound_runtime_records"] = -1

    for mutate in (legacy_mainflow_field, guest_origin, negative_guest_count):
        with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
            work_dir, out_dir = Path(work_tmp), Path(out_tmp)
            fixture(work_dir, measured=True)
            state_path = work_dir / "state-compare-summary.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            mutate(state)
            write_json(state_path, state)
            try:
                summary.summarize(work_dir, out_dir)
            except ValueError as error:
                assert "Mainflow" in str(error), error
            else:
                raise AssertionError(
                    f"invalid Mainflow evidence was silently accepted: {mutate.__name__}"
                )

    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir, out_dir = Path(work_tmp), Path(out_tmp)
        fixture(work_dir, measured=True)
        state_path = work_dir / "state-compare-summary.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["guest_source_bound_runtime_records"] = 3
        write_json(state_path, state)
        result = summary.summarize(work_dir, out_dir)
        assert result["status"] == "ready", result
        report = (out_dir / "report.md").read_text(encoding="utf-8")
        assert "Guest 来源绑定运行记录为 3，仅作观测计数" in report, report

    print("test_summarize_dual_platform_results: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
