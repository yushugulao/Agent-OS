#!/usr/bin/env python3
"""Check that chart types are backed by concrete result data."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import summarize_dual_platform_results as summary


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_fixture(work_dir: Path) -> None:
    write_json(
        work_dir / "state-compare-summary.json",
        {
            "plain_files": 64,
            "agentos_files": 78,
            "common_files": 64,
            "agentos_extra_files": 14,
            "checked_success_records": 300,
            "preserved_plain_costs": 7,
            "embedded_action_records": 44,
            "agentos_evidence_checks": 32,
            "agentos_mainflow_stages": 11,
            "agentos_mainflow_facts": 12,
            "run_result_match": 1,
            "scenario_evidence": [
                {"scenario": "Context Path", "label": "上下文可信记录", "expected": 3, "matched": 3, "sources": ["rp_agentos_mainflow"], "status": "ready"},
                {"scenario": "Event Loop", "label": "事件通知与等待", "expected": 3, "matched": 3, "sources": ["rp_agentos_timeline"], "status": "ready"},
            ],
            "cost_replacements": [
                {"case": "agentos-context", "plain_cost": "rebuild_steps_6", "agentos_replace": "kernel_context_path", "risk": "untrusted_context", "preserved_from_plain": 1, "status": "passed"},
                {"case": "agentos-event", "plain_cost": "file_polling", "agentos_replace": "kernel_event_queue", "risk": "lost_handoff", "preserved_from_plain": 1, "status": "passed"},
            ],
            "runner_tick_comparison": [
                {"label": "基础执行计划", "plain_case": "plain-ucore", "agentos_case": "plain-ucore", "plain_ticks": 3, "agentos_ticks": 3, "saved_ticks": 0, "speedup_x100": 100},
                {"label": "上下文路径", "plain_case": "user-context", "agentos_case": "agentos-context", "plain_ticks": 6, "agentos_ticks": 1, "saved_ticks": 5, "speedup_x100": 600},
                {"label": "文件对象查询", "plain_case": "user-fsmeta", "agentos_case": "agentos-fsmeta", "plain_ticks": 7, "agentos_ticks": 1, "saved_ticks": 6, "speedup_x100": 700},
                {"label": "事件交接", "plain_case": "user-event", "agentos_case": "agentos-event", "plain_ticks": 6, "agentos_ticks": 1, "saved_ticks": 5, "speedup_x100": 600},
            ],
            "plain_timing_records": 70,
            "plain_agent_launches": 0,
            "plain_fork_launches": 70,
            "agentos_timing_records": 70,
            "agentos_agent_launches": 9,
            "agentos_fork_launches": 61,
            "status": "ready",
        },
    )
    write_json(
        work_dir / "reader-compare-summary.json",
        {
            "plain_pages": 40,
            "agentos_pages": 40,
            "plain_api_json": 267,
            "agentos_api_json": 280,
            "agentos_extra_api_json": 13,
            "status": "ready",
        },
    )
    write_json(work_dir / "seeded-action-state.json", {"action_count": 44, "status": "ready"})
    write_json(work_dir / "host-platform-alignment.json", {"status": "ready"})
    write_json(work_dir / "host-test-alignment.json", {"status": "ready"})
    write_json(work_dir / "host-surface-alignment.json", {"status": "ready"})
    (work_dir / "plain-state").mkdir()
    (work_dir / "agentos-state").mkdir()
    (work_dir / "plain-state" / "rp_host_run_result").write_text(
        "qemu_elapsed_seconds=12\nqemu_idle_notices=0\nqemu_timed_out=0\n",
        encoding="utf-8",
    )
    (work_dir / "agentos-state" / "rp_host_run_result").write_text(
        "qemu_elapsed_seconds=15\nqemu_idle_notices=0\nqemu_timed_out=0\n",
        encoding="utf-8",
    )
    (work_dir / "stage-timings.csv").write_text(
        "stage,start_epoch,end_epoch,duration_seconds,status\n"
        "structure-check,1,2,1,ready\n"
        "seeded-dual-run,2,18,16,ready\n"
        "state-compare,18,21,3,ready\n"
        "reader-render-check,21,24,3,ready\n"
        "result-report-chart,24,25,1,ready\n",
        encoding="utf-8",
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir = Path(work_tmp)
        out_dir = Path(out_tmp)
        write_fixture(work_dir)
        result = summary.summarize(work_dir, out_dir)

        coverage_rows = read_csv(out_dir / "chart-type-coverage.csv")
        assert len(coverage_rows) >= 6, coverage_rows
        assert all(row["agentos_artifacts"] != "待补充" for row in coverage_rows), coverage_rows
        assert all("Python 标准库 SVG 生成" in row["data_source"] for row in coverage_rows), coverage_rows

        for row in coverage_rows:
            for artifact in row["agentos_artifacts"].split(";"):
                artifact = artifact.strip()
                if not artifact.endswith(".svg"):
                    continue
                path = out_dir / "charts" / artifact
                assert path.is_file(), artifact
                text = path.read_text(encoding="utf-8")
                assert "<svg" in text and "<text" in text, artifact

        sweep_rows = read_csv(out_dir / "runner-sweep.csv")
        assert len(sweep_rows) == 4, sweep_rows
        assert any(row["scene"] == "上下文路径" and row["speedup_x"] == "6" for row in sweep_rows), sweep_rows
        assert any(float(row["speedup_x"]) > 1 for row in sweep_rows), sweep_rows

        chart_expectations = {
            "runner-cumulative-line.svg": ("<polyline", "Runner 累计 Tick 曲线", "上下文"),
            "runner-tick-box.svg": ("Runner Tick 分布箱形图", "min", "mid"),
            "runner-cost-heatmap.svg": ("Runner 成本热力图", "普通 tick", "节省 tick"),
            "stage-monitor-area.svg": ("<polygon", "双目标运行阶段监控面积图", "预置请求"),
            "runner-surface-composite.svg": ("等距曲面", "二维投影", "热力图"),
        }
        for chart_name, tokens in chart_expectations.items():
            text = (out_dir / "charts" / chart_name).read_text(encoding="utf-8")
            for token in tokens:
                assert token in text, f"{chart_name} missing {token}"

        assert str(result["chart_type_coverage_csv"]).endswith("chart-type-coverage.csv"), result

    print("test_chart_type_data_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
