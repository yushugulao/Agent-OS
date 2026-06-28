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
                "cost_replacements": [
                    {
                        "case": "agentos-context",
                        "plain_cost": "rebuild_steps_6",
                        "agentos_replace": "kernel_context_path",
                        "risk": "untrusted_context",
                        "preserved_from_plain": 1,
                        "status": "passed",
                    },
                    {
                        "case": "agentos-fsmeta",
                        "plain_cost": "scan_records_128",
                        "agentos_replace": "metadata_index",
                        "risk": "scan_growth",
                        "preserved_from_plain": 1,
                        "status": "passed",
                    },
                ],
                "cost_replacement_count": 2,
                "runner_tick_comparison": [
                    {
                        "label": "上下文路径",
                        "plain_case": "user-context",
                        "agentos_case": "agentos-context",
                        "plain_ticks": 6,
                        "agentos_ticks": 1,
                        "saved_ticks": 5,
                        "speedup_x100": 600,
                    },
                    {
                        "label": "文件对象查询",
                        "plain_case": "user-fsmeta",
                        "agentos_case": "agentos-fsmeta",
                        "plain_ticks": 7,
                        "agentos_ticks": 1,
                        "saved_ticks": 6,
                        "speedup_x100": 700,
                    },
                ],
                "runner_tick_pairs": 2,
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
        assert str(result["demo_guide"]).endswith("demo-guide.html"), result
        assert str(result["runner_sweep_csv"]).endswith("runner-sweep.csv"), result
        assert str(result["runner_statistics_csv"]).endswith("runner-statistics.csv"), result
        assert str(result["runner_statistics"]).endswith("runner-statistics.html"), result
        assert str(result["load_profile_csv"]).endswith("load-profile.csv"), result
        assert str(result["delivery_readiness_csv"]).endswith("delivery-readiness.csv"), result
        assert str(result["delivery_readiness"]).endswith("delivery-readiness.html"), result
        assert str(result["test_suite_csv"]).endswith("test-suite.csv"), result
        assert str(result["test_suite"]).endswith("test-suite.html"), result
        assert str(result["chart_type_coverage_csv"]).endswith("chart-type-coverage.csv"), result
        assert str(result["experiment_design_csv"]).endswith("experiment-design.csv"), result
        assert str(result["experiment_design"]).endswith("experiment-design.html"), result
        assert str(result["evidence_manifest_csv"]).endswith("evidence-manifest.csv"), result
        assert str(result["evidence_map"]).endswith("evidence-map.html"), result
        assert str(result["demo_checklist_csv"]).endswith("demo-checklist.csv"), result
        assert str(result["demo_checklist"]).endswith("demo-checklist.html"), result
        report = (out_dir / "report.md").read_text(encoding="utf-8")
        assert "普通 uCore 提取状态文件 258 个" in report
        assert "AgentOS-uCore 提取 271 个" in report
        assert "阶段耗时" in report
        assert "预置请求双目标运行" in report
        assert "自动判读" in report
        assert "两个目标运行结果可对照" in report
        assert "多场景机制证据" in report
        assert "上下文可信记录" in report
        assert "用户态成本项与 AgentOS 替代机制" in report
        assert "kernel_context_path" in report
        assert "Runner Tick 对照" in report
        assert "agentos-context" in report
        assert "runner-statistics.csv" in report
        assert "runner-statistics.html" in report
        assert "delivery-readiness.csv" in report
        assert "delivery-readiness.html" in report
        assert "test-suite.csv" in report
        assert "test-suite.html" in report
        assert "负载参数组" in report
        assert "预置请求" in report
        assert "evidence-manifest.csv" in report
        assert "evidence-map.html" in report
        assert "demo-checklist.csv" in report
        assert "demo-checklist.html" in report
        assert "experiment-design.csv" in report
        assert "experiment-design.html" in report
        csv_text = (out_dir / "summary.csv").read_text(encoding="utf-8")
        assert "提取到的 rp_* 状态文件" in csv_text
        assert "QEMU 无输出提示次数" in csv_text
        sweep_csv = (out_dir / "runner-sweep.csv").read_text(encoding="utf-8")
        assert "scene,plain_case,agentos_case,plain_ticks,agentos_ticks,saved_ticks,speedup_x" in sweep_csv
        assert "上下文路径,user-context,agentos-context,6,1,5,6" in sweep_csv
        stats_csv = (out_dir / "runner-statistics.csv").read_text(encoding="utf-8")
        assert "metric,count,min,avg,max,unit,source,reading" in stats_csv
        assert "普通路径 tick,2,6,6.5,7,tick" in stats_csv
        assert "AgentOS 路径 tick,2,1,1,1,tick" in stats_csv
        assert "相对倍数,2,6,6.5,7,x" in stats_csv
        suite_csv = (out_dir / "test-suite.csv").read_text(encoding="utf-8")
        assert "level,command,qemu,purpose,when_to_use,main_output" in suite_csv
        assert "最终演示主路径,make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" in suite_csv
        assert "最终演示主路径,make demo-reader" in suite_csv
        assert "日常快速检查,make target-readiness" in suite_csv
        assert "完整验证,make full-verify TOOLPREFIX=riscv64-linux-gnu-" in suite_csv
        readiness_csv = (out_dir / "delivery-readiness.csv").read_text(encoding="utf-8")
        assert "requirement,status,evidence,verification,note" in readiness_csv
        assert "带数据的测试结果用图表展示,已覆盖" in readiness_csv
        assert "DeepSeek v4 pro 优先且默认不访问云端,已覆盖" in readiness_csv
        assert "录屏演示只需要少量命令,已覆盖" in readiness_csv
        assert "同时展示功能完善和性能良好,已覆盖" in readiness_csv
        profile_csv = (out_dir / "load-profile.csv").read_text(encoding="utf-8")
        assert "load_dimension,source,plain_value,agentos_value,delta,note" in profile_csv
        assert "预置请求" in profile_csv
        assert "Agent 启动" in profile_csv
        evidence_csv = (out_dir / "evidence-manifest.csv").read_text(encoding="utf-8")
        assert "artifact,kind,source,proves,demo_use" in evidence_csv
        assert "experiment-design.html" in evidence_csv
        assert "experiment-design.csv" in evidence_csv
        assert "test-suite.html" in evidence_csv
        assert "test-suite.csv" in evidence_csv
        assert "delivery-readiness.html" in evidence_csv
        assert "delivery-readiness.csv" in evidence_csv
        assert "charts/runtime-observation.svg" in evidence_csv
        assert "runner-sweep.csv" in evidence_csv
        assert "runner-statistics.csv" in evidence_csv
        assert "runner-statistics.html" in evidence_csv
        assert "load-profile.csv" in evidence_csv
        experiment_csv = (out_dir / "experiment-design.csv").read_text(encoding="utf-8")
        assert "scenario,workload,plain_path,agentos_path,parameter,metric,source,artifact" in experiment_csv
        assert "科研主流程双目标对照" in experiment_csv
        assert "Reader 页面与 API 对照" in experiment_csv
        assert "Runner tick 成组对照" in experiment_csv
        assert "运行阶段耗时观测" in experiment_csv
        checklist_csv = (out_dir / "demo-checklist.csv").read_text(encoding="utf-8")
        assert "item,status,evidence,action" in checklist_csv
        assert "双目标结果,通过" in checklist_csv
        assert "Reader 页面与 API,通过" in checklist_csv
        assert "核心图表,通过" in checklist_csv
        assert "AgentOS 主流程证据,通过" in checklist_csv
        coverage_csv = (out_dir / "chart-type-coverage.csv").read_text(encoding="utf-8")
        assert "条形/柱状对比" in coverage_csv
        assert "曲线趋势" in coverage_csv
        assert "箱形图" in coverage_csv
        assert "热力图" in coverage_csv
        assert "监控面积图" in coverage_csv
        assert "三维曲面与投影组合" in coverage_csv
        index_html = (out_dir / "index.html").read_text(encoding="utf-8")
        assert "AgentOS 双目标测试结果" in index_html
        assert "demo-guide.html" in index_html
        assert "演示导览页" in index_html
        assert "charts/dual-target-state-reader.svg" in index_html
        assert "monitor.html" in index_html
        assert "charts/runtime-observation.svg" in index_html
        assert "charts/scenario-evidence.svg" in index_html
        assert "charts/cost-replacement.svg" in index_html
        assert "charts/runner-ticks.svg" in index_html
        assert "charts/runner-speedup.svg" in index_html
        assert "charts/runner-statistics.svg" in index_html
        assert "runner-sweep.csv" in index_html
        assert "runner-statistics.html" in index_html
        assert "runner-statistics.csv" in index_html
        assert "load-profile.csv" in index_html
        assert "charts/load-profile.svg" in index_html
        assert "evidence-map.html" in index_html
        assert "evidence-manifest.csv" in index_html
        assert "demo-checklist.html" in index_html
        assert "demo-checklist.csv" in index_html
        assert "delivery-readiness.html" in index_html
        assert "delivery-readiness.csv" in index_html
        assert "test-suite.html" in index_html
        assert "test-suite.csv" in index_html
        assert "experiment-design.html" in index_html
        assert "experiment-design.csv" in index_html
        assert "chart-type-coverage.csv" in index_html
        assert "charts/runner-cumulative-line.svg" in index_html
        assert "charts/runner-tick-box.svg" in index_html
        assert "charts/runner-cost-heatmap.svg" in index_html
        assert "charts/stage-monitor-area.svg" in index_html
        assert "charts/runner-surface-composite.svg" in index_html
        assert "make demo-reader" in index_html
        assert "阶段耗时明细" in index_html
        assert "预置请求双目标运行" in index_html
        assert "自动判读" in index_html
        monitor_html = (out_dir / "monitor.html").read_text(encoding="utf-8")
        assert "AgentOS 运行观测面板" in monitor_html
        assert "一张图看本次运行" in monitor_html
        assert "make demo-reader" in monitor_html
        assert "demo-guide.html" in monitor_html
        assert "charts/scenario-evidence.svg" in monitor_html
        assert "charts/cost-replacement.svg" in monitor_html
        assert "charts/runner-ticks.svg" in monitor_html
        assert "charts/runner-speedup.svg" in monitor_html
        assert "runner-sweep.csv" in monitor_html
        assert "runner-statistics.html" in monitor_html
        assert "runner-statistics.csv" in monitor_html
        assert "load-profile.csv" in monitor_html
        assert "charts/load-profile.svg" in monitor_html
        assert "evidence-map.html" in monitor_html
        assert "evidence-manifest.csv" in monitor_html
        assert "demo-checklist.html" in monitor_html
        assert "demo-checklist.csv" in monitor_html
        assert "delivery-readiness.html" in monitor_html
        assert "delivery-readiness.csv" in monitor_html
        assert "test-suite.html" in monitor_html
        assert "test-suite.csv" in monitor_html
        assert "experiment-design.html" in monitor_html
        assert "experiment-design.csv" in monitor_html
        assert "chart-type-coverage.csv" in monitor_html
        assert "charts/runner-surface-composite.svg" in monitor_html
        for name in [
            "dual-target-state-reader.svg",
            "launch-model.svg",
            "agentos-evidence.svg",
            "stage-timings.svg",
            "runtime-observation.svg",
            "scenario-evidence.svg",
            "cost-replacement.svg",
            "runner-ticks.svg",
            "runner-speedup.svg",
            "runner-statistics.svg",
            "load-profile.svg",
            "runner-cumulative-line.svg",
            "runner-tick-box.svg",
            "runner-cost-heatmap.svg",
            "stage-monitor-area.svg",
            "runner-surface-composite.svg",
        ]:
            svg = (out_dir / "charts" / name).read_text(encoding="utf-8")
            assert "<svg" in svg
            assert "<text" in svg
        demo_html = (out_dir / "demo-guide.html").read_text(encoding="utf-8")
        assert "AgentOS 演示导览" in demo_html
        assert "make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" in demo_html
        assert "make demo-reader" in demo_html
        assert "建议展示顺序" in demo_html
        assert "Host Reader 首页" in demo_html
        assert "runner-sweep.csv" in demo_html
        assert "runner-statistics.html" in demo_html
        assert "runner-statistics.csv" in demo_html
        assert "load-profile.csv" in demo_html
        assert "evidence-manifest.csv" in demo_html
        assert "demo-checklist.html" in demo_html
        assert "demo-checklist.csv" in demo_html
        assert "delivery-readiness.html" in demo_html
        assert "delivery-readiness.csv" in demo_html
        assert "test-suite.html" in demo_html
        assert "test-suite.csv" in demo_html
        assert "experiment-design.html" in demo_html
        assert "experiment-design.csv" in demo_html
        assert "charts/scenario-evidence.svg" in demo_html
        experiment_html = (out_dir / "experiment-design.html").read_text(encoding="utf-8")
        assert "AgentOS 实验场景说明" in experiment_html
        assert "负载设计" in experiment_html
        assert "普通目标路径" in experiment_html
        assert "AgentOS 目标路径" in experiment_html
        assert "charts/runner-speedup.svg" in experiment_html
        evidence_html = (out_dir / "evidence-map.html").read_text(encoding="utf-8")
        assert "AgentOS 证据索引" in evidence_html
        assert "charts/runtime-observation.svg" in evidence_html
        assert "summary.csv" in evidence_html
        assert "runner-sweep.csv" in evidence_html
        assert "runner-statistics.csv" in evidence_html
        assert "runner-statistics.html" in evidence_html
        stats_html = (out_dir / "runner-statistics.html").read_text(encoding="utf-8")
        assert "AgentOS Runner 统计摘要" in stats_html
        assert "count、min、avg、max" in stats_html
        assert "charts/runner-statistics.svg" in stats_html
        suite_html = (out_dir / "test-suite.html").read_text(encoding="utf-8")
        assert "AgentOS 测试入口说明" in suite_html
        assert "make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" in suite_html
        assert "make demo-reader" in suite_html
        assert "make target-readiness" in suite_html
        assert "make full-verify TOOLPREFIX=riscv64-linux-gnu-" in suite_html
        readiness_html = (out_dir / "delivery-readiness.html").read_text(encoding="utf-8")
        assert "AgentOS 交付材料核对" in readiness_html
        assert "图表文字不互相遮挡" in readiness_html
        assert "test_chart_svg_layout_contract.py" in readiness_html
        assert "DeepSeek v4 pro" in readiness_html
        checklist_html = (out_dir / "demo-checklist.html").read_text(encoding="utf-8")
        assert "AgentOS 演示检查表" in checklist_html
        assert "通过项：8 / 8" in checklist_html
        assert "双目标结果" in checklist_html
        assert "QEMU 运行状态" in checklist_html
        assert "demo-guide.html" in checklist_html
        assert "evidence-map.html" in checklist_html

    print("test_summarize_dual_platform_results: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
