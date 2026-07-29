#!/usr/bin/env python3
"""Check that generated charts are backed by concrete, provenance-bound data."""

from __future__ import annotations

import tempfile
from pathlib import Path

import summarize_dual_platform_results as summary
from test_summarize_dual_platform_results import fixture, read_csv


def main() -> int:
    with tempfile.TemporaryDirectory() as work_tmp, tempfile.TemporaryDirectory() as out_tmp:
        work_dir, out_dir = Path(work_tmp), Path(out_tmp)
        fixture(work_dir, measured=True)
        result = summary.summarize(work_dir, out_dir, require_measured_experiments=True)

        sweep_rows = read_csv(out_dir / "runner-sweep.csv")
        assert sweep_rows == [{
            "evidence_status": "unavailable",
            "evidence_reason": "plain_runtime_cases_zero",
        }], sweep_rows

        raw_rows = read_csv(out_dir / "experiments" / "raw" / "file-query-benchmark.csv")
        assert len(raw_rows) == 6, raw_rows
        assert all(row["source_log"] == "agent-suite-guest.log" for row in raw_rows), raw_rows
        assert all(len(row["source_log_sha256"]) == 64 for row in raw_rows), raw_rows
        assert not any(row["measurement_kind"].startswith("formula") for row in raw_rows), raw_rows

        chart_expectations = {
            "runtime-observation.svg": ("双目标运行观测", "AgentOS"),
            "cost-replacement.svg": ("用户态成本项", "AgentOS 替代机制"),
            "experiment-file-query-bar.svg": ("真实 Guest 文件查询", "冷索引（含重建）", "热索引"),
        }
        assert {Path(path).name for path in result["charts"]} == set(chart_expectations), result
        for chart_name, tokens in chart_expectations.items():
            text = (out_dir / "charts" / chart_name).read_text(encoding="utf-8")
            for token in tokens:
                assert token in text, f"{chart_name} missing {token}"

        evidence_rows = read_csv(out_dir / "evidence-manifest.csv")
        assert any(
            row["artifact"] == "charts/experiment-file-query-bar.svg"
            and "file-query-benchmark.csv" in row["source"]
            for row in evidence_rows
        ), evidence_rows

    print("test_chart_type_data_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
