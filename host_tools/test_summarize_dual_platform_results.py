#!/usr/bin/env python3
"""Unit checks for dual-platform result summaries."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import summarize_dual_platform_results as summary


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir = Path(work_tmp)
        out_dir = Path(out_tmp)
        write_json(
            work_dir / "state-compare-summary.json",
            {
                "plain_files": 258,
                "agentos_files": 271,
                "common_files": 258,
                "agentos_extra_files": 13,
                "checked_success_records": 1244,
                "preserved_plain_costs": 7,
                "embedded_action_records": 44,
                "agentos_evidence_checks": 32,
                "agentos_mainflow_stages": 11,
                "agentos_mainflow_facts": 12,
                "run_result_match": 1,
                "scenario_evidence": [
                    {
                        "scenario": "Context Path",
                        "label": "上下文可信记录",
                        "expected": 3,
                        "matched": 3,
                        "sources": ["rp_agentos_mainflow", "rp_agentos_recovery", "rp_agentos_real_task"],
                        "status": "ready",
                    },
                    {
                        "scenario": "File Metadata",
                        "label": "文件对象查询",
                        "expected": 3,
                        "matched": 3,
                        "sources": ["rp_agentos_mainflow", "rp_agentos_query", "rp_agentos_workbench"],
                        "status": "ready",
                    },
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
                "plain_state_files": 260,
                "agentos_state_files": 273,
                "agentos_extra_state_files": 13,
                "plain_api_json": 267,
                "agentos_api_json": 280,
                "agentos_extra_api_json": 13,
                "checked_pages": 40,
                "checked_api_json": 267,
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
            "qemu_elapsed_seconds=12.5\nqemu_idle_notices=0\nqemu_timed_out=0\n",
            encoding="utf-8",
        )
        (work_dir / "agentos-state" / "rp_host_run_result").write_text(
            "qemu_elapsed_seconds=15.25\nqemu_idle_notices=1\nqemu_timed_out=0\n",
            encoding="utf-8",
        )
        (work_dir / "stage-timings.csv").write_text(
            "stage,start_epoch,end_epoch,duration_seconds,status\n"
            "structure-check,1,2,1,ready\n"
            "seeded-dual-run,2,18,16,ready\n"
            "reader-render-check,18,20,2,ready\n",
            encoding="utf-8",
        )

        result = summary.summarize(work_dir, out_dir)
        assert result["status"] == "ready", result
        assert str(result["monitor"]).endswith("monitor.html"), result
        report = (out_dir / "report.md").read_text(encoding="utf-8")
        assert "普通 uCore 提取状态文件 258 个" in report
        assert "AgentOS-uCore 提取 271 个" in report
        assert "阶段耗时" in report
        assert "预置请求双目标运行" in report
        assert "自动判读" in report
        assert "两个目标运行结果可对照" in report
        assert "多场景机制证据" in report
        assert "上下文可信记录" in report
        csv_text = (out_dir / "summary.csv").read_text(encoding="utf-8")
        assert "提取到的 rp_* 状态文件" in csv_text
        assert "QEMU 无输出提示次数" in csv_text
        index_html = (out_dir / "index.html").read_text(encoding="utf-8")
        assert "AgentOS 双目标测试结果" in index_html
        assert "charts/dual-target-state-reader.svg" in index_html
        assert "monitor.html" in index_html
        assert "charts/runtime-observation.svg" in index_html
        assert "charts/scenario-evidence.svg" in index_html
        assert "make demo-reader" in index_html
        assert "阶段耗时明细" in index_html
        assert "预置请求双目标运行" in index_html
        assert "自动判读" in index_html
        monitor_html = (out_dir / "monitor.html").read_text(encoding="utf-8")
        assert "AgentOS 运行观测面板" in monitor_html
        assert "一张图看本次运行" in monitor_html
        assert "make demo-reader" in monitor_html
        assert "charts/scenario-evidence.svg" in monitor_html
        for name in [
            "dual-target-state-reader.svg",
            "launch-model.svg",
            "agentos-evidence.svg",
            "stage-timings.svg",
            "runtime-observation.svg",
            "scenario-evidence.svg",
        ]:
            svg = (out_dir / "charts" / name).read_text(encoding="utf-8")
            assert "<svg" in svg
            assert "<text" in svg

    print("test_summarize_dual_platform_results: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
