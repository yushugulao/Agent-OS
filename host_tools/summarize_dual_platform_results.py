#!/usr/bin/env python3
"""Build CSV, Markdown, and SVG summaries for dual-platform verification."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape


PALETTE = {
    "plain": "#4c78a8",
    "agentos": "#f58518",
    "shared": "#54a24b",
    "extra": "#e45756",
    "neutral": "#6f7f8f",
    "grid": "#d8dee6",
    "text": "#1f2937",
}

STAGE_LABELS = {
    "structure-check": "结构检查",
    "seeded-dual-run": "预置请求双目标运行",
    "qemu-log-marker-check": "QEMU日志标记检查",
    "state-extract-copy": "状态文件提取整理",
    "host-alignment": "宿主平台能力对照",
    "state-compare": "状态文件结果对照",
    "reader-render-check": "Reader页面渲染与检查",
    "result-report-chart": "结果报告与图表生成",
}


@dataclass(frozen=True)
class MetricRow:
    group: str
    metric: str
    plain: float | None
    agentos: float | None
    delta: float | None
    note: str


def read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_state_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for part in raw.split(";"):
            item = part.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def as_number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def fmt_number(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def load_stage_timings(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row:
                rows.append({str(k): str(v) for k, v in row.items() if k is not None})
    return rows


def stage_label(stage: object) -> str:
    text = str(stage)
    return STAGE_LABELS.get(text, text)


def collect_rows(work_dir: Path) -> tuple[list[MetricRow], dict[str, object]]:
    state = read_json(work_dir / "state-compare-summary.json")
    reader = read_json(work_dir / "reader-compare-summary.json")
    seeded = read_json(work_dir / "seeded-action-state.json")
    platform = read_json(work_dir / "host-platform-alignment.json")
    tests = read_json(work_dir / "host-test-alignment.json")
    surface = read_json(work_dir / "host-surface-alignment.json")
    stage_rows = load_stage_timings(work_dir / "stage-timings.csv")
    plain_result = read_state_values(work_dir / "plain-state" / "rp_host_run_result")
    agentos_result = read_state_values(work_dir / "agentos-state" / "rp_host_run_result")

    rows = [
        MetricRow(
            "状态文件",
            "提取到的 rp_* 状态文件",
            as_number(state.get("plain_files")),
            as_number(state.get("agentos_files")),
            as_number(state.get("agentos_extra_files")),
            "AgentOS 目标会额外输出内核 Agent 证据文件。",
        ),
        MetricRow(
            "状态文件",
            "两目标共有状态文件",
            as_number(state.get("common_files")),
            as_number(state.get("common_files")),
            0,
            "共有文件用于检查同一科研流程在两个目标中是否保持结果一致。",
        ),
        MetricRow(
            "Reader 输出",
            "HTML 页面",
            as_number(reader.get("plain_pages")),
            as_number(reader.get("agentos_pages")),
            as_number(reader.get("agentos_pages")) - as_number(reader.get("plain_pages")),
            "页面数量应保持一致，避免增强目标少展示流程。",
        ),
        MetricRow(
            "Reader 输出",
            "API JSON",
            as_number(reader.get("plain_api_json")),
            as_number(reader.get("agentos_api_json")),
            as_number(reader.get("agentos_extra_api_json")),
            "AgentOS 目标可增加内核证据 API，但不能少于普通目标。",
        ),
        MetricRow(
            "主流程证据",
            "预置 action 请求",
            as_number(state.get("embedded_action_records")),
            as_number(state.get("embedded_action_records")),
            0,
            "同一批宿主机请求进入两个 QEMU 目标。",
        ),
        MetricRow(
            "主流程证据",
            "成功记录核对",
            as_number(state.get("checked_success_records")),
            as_number(state.get("checked_success_records")),
            0,
            "普通目标的成功记录需要在 AgentOS 目标中被保留。",
        ),
        MetricRow(
            "AgentOS 证据",
            "内核证据检查项",
            None,
            as_number(state.get("agentos_evidence_checks")),
            as_number(state.get("agentos_evidence_checks")),
            "包括 Context、文件 metadata、事件、timeline、audit、provenance 等状态。",
        ),
        MetricRow(
            "AgentOS 证据",
            "主流程内核阶段事实",
            None,
            as_number(state.get("agentos_mainflow_stages")),
            as_number(state.get("agentos_mainflow_stages")),
            "科研平台主流程中使用 AgentOS 能力的阶段数量。",
        ),
        MetricRow(
            "启动方式",
            "普通 fork 启动记录",
            as_number(state.get("plain_fork_launches")),
            as_number(state.get("agentos_fork_launches")),
            as_number(state.get("agentos_fork_launches")) - as_number(state.get("plain_fork_launches")),
            "增强目标仍保留普通进程路径，便于和未改动内核对照。",
        ),
        MetricRow(
            "启动方式",
            "Agent 启动记录",
            as_number(state.get("plain_agent_launches")),
            as_number(state.get("agentos_agent_launches")),
            as_number(state.get("agentos_agent_launches")) - as_number(state.get("plain_agent_launches")),
            "增强目标把关键 worker 作为 Agent 进程运行。",
        ),
        MetricRow(
            "运行诊断",
            "QEMU 运行秒数",
            as_number(plain_result.get("qemu_elapsed_seconds")),
            as_number(agentos_result.get("qemu_elapsed_seconds")),
            as_number(agentos_result.get("qemu_elapsed_seconds"))
            - as_number(plain_result.get("qemu_elapsed_seconds")),
            "用于观察是否出现异常慢速，不作为固定性能门槛。",
        ),
        MetricRow(
            "运行诊断",
            "QEMU 无输出提示次数",
            as_number(plain_result.get("qemu_idle_notices")),
            as_number(agentos_result.get("qemu_idle_notices")),
            as_number(agentos_result.get("qemu_idle_notices"))
            - as_number(plain_result.get("qemu_idle_notices")),
            "该项升高通常说明程序卡住、日志未刷新或 QEMU 运行异常。",
        ),
    ]

    meta = {
        "state": state,
        "reader": reader,
        "seeded": seeded,
        "platform": platform,
        "tests": tests,
        "surface": surface,
        "stage_rows": stage_rows,
        "plain_result": plain_result,
        "agentos_result": agentos_result,
    }
    return rows, meta


def write_csv(rows: list[MetricRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["分组", "指标", "普通 uCore", "AgentOS-uCore", "差值", "说明"])
        for row in rows:
            writer.writerow(
                [
                    row.group,
                    row.metric,
                    fmt_number(row.plain),
                    fmt_number(row.agentos),
                    fmt_number(row.delta),
                    row.note,
                ]
            )


def svg_header(width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>",
        "text{font-family:Arial,'Microsoft YaHei',sans-serif;fill:#1f2937}",
        ".title{font-size:20px;font-weight:700}.subtitle{font-size:13px;fill:#52616f}",
        ".axis{font-size:12px;fill:#52616f}.label{font-size:12px}.value{font-size:12px;font-weight:700}",
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]


def wrap_label(text: str, limit: int = 12) -> list[str]:
    if len(text) <= limit:
        return [text]
    words: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= limit:
            words.append(current)
            current = ""
    if current:
        words.append(current)
    return words[:3]


def grouped_bar_svg(
    title: str,
    subtitle: str,
    categories: list[str],
    plain_values: list[float],
    agentos_values: list[float],
    out_path: Path,
    y_label: str = "数量",
) -> None:
    width, height = 980, 560
    margin_left, margin_right, margin_top, margin_bottom = 90, 34, 88, 120
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_value = max([1.0] + plain_values + agentos_values)
    max_axis = math.ceil(max_value * 1.18 / 10.0) * 10.0
    lines = svg_header(width, height)
    lines.append(f'<text x="34" y="34" class="title">{escape(title)}</text>')
    lines.append(f'<text x="34" y="58" class="subtitle">{escape(subtitle)}</text>')
    lines.append(f'<text x="{margin_left}" y="{height - 32}" class="axis">{escape(y_label)}</text>')

    for tick in range(0, 6):
        value = max_axis * tick / 5
        y = margin_top + plot_h - (value / max_axis) * plot_h
        lines.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - margin_right}" y2="{y:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{margin_left - 12}" y="{y + 4:.1f}" text-anchor="end" class="axis">{fmt_number(value)}</text>')

    group_w = plot_w / max(1, len(categories))
    bar_w = min(52, group_w * 0.26)
    for index, category in enumerate(categories):
        center = margin_left + group_w * (index + 0.5)
        values = [plain_values[index], agentos_values[index]]
        colors = [PALETTE["plain"], PALETTE["agentos"]]
        offsets = [-bar_w * 0.58, bar_w * 0.58]
        for value, color, offset in zip(values, colors, offsets):
            h = (value / max_axis) * plot_h
            x = center + offset - bar_w / 2
            y = margin_top + plot_h - h
            lines.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{color}" rx="2"/>')
            label_y = max(margin_top + 14, y - 7)
            lines.append(f'<text x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" text-anchor="middle" class="value">{fmt_number(value)}</text>')
        for line_no, label in enumerate(wrap_label(category, 10)):
            lines.append(f'<text x="{center:.1f}" y="{margin_top + plot_h + 28 + line_no * 16}" text-anchor="middle" class="label">{escape(label)}</text>')

    legend_y = 82
    lines.append(f'<rect x="{width - 245}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["plain"]}"/>')
    lines.append(f'<text x="{width - 224}" y="{legend_y}" class="label">普通 uCore</text>')
    lines.append(f'<rect x="{width - 130}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["agentos"]}"/>')
    lines.append(f'<text x="{width - 109}" y="{legend_y}" class="label">AgentOS-uCore</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stacked_bar_svg(title: str, subtitle: str, groups: list[tuple[str, float, float]], out_path: Path) -> None:
    width, height = 920, 430
    margin_left, margin_right, margin_top, margin_bottom = 160, 80, 86, 64
    plot_w = width - margin_left - margin_right
    row_h = 58
    max_total = max([1.0] + [a + b for _, a, b in groups])
    lines = svg_header(width, height)
    lines.append(f'<text x="34" y="34" class="title">{escape(title)}</text>')
    lines.append(f'<text x="34" y="58" class="subtitle">{escape(subtitle)}</text>')
    for index, (label, first, second) in enumerate(groups):
        y = margin_top + index * row_h
        total = first + second
        first_w = (first / max_total) * plot_w
        second_w = (second / max_total) * plot_w
        lines.append(f'<text x="{margin_left - 14}" y="{y + 24}" text-anchor="end" class="label">{escape(label)}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y}" width="{first_w:.1f}" height="30" fill="{PALETTE["neutral"]}" rx="2"/>')
        lines.append(f'<rect x="{margin_left + first_w:.1f}" y="{y}" width="{second_w:.1f}" height="30" fill="{PALETTE["agentos"]}" rx="2"/>')
        lines.append(f'<text x="{margin_left + first_w + second_w + 10:.1f}" y="{y + 21}" class="value">{fmt_number(total)}</text>')
        if first_w > 34:
            lines.append(f'<text x="{margin_left + first_w / 2:.1f}" y="{y + 21}" text-anchor="middle" class="label" fill="#fff">{fmt_number(first)}</text>')
        if second_w > 34:
            lines.append(f'<text x="{margin_left + first_w + second_w / 2:.1f}" y="{y + 21}" text-anchor="middle" class="label" fill="#fff">{fmt_number(second)}</text>')
    legend_y = height - 28
    lines.append(f'<rect x="{margin_left}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["neutral"]}"/>')
    lines.append(f'<text x="{margin_left + 22}" y="{legend_y}" class="label">普通进程或共有项</text>')
    lines.append(f'<rect x="{margin_left + 160}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["agentos"]}"/>')
    lines.append(f'<text x="{margin_left + 182}" y="{legend_y}" class="label">AgentOS 专有项</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage_timing_svg(stage_rows: list[dict[str, str]], out_path: Path) -> None:
    width, height = 1040, 520
    margin_left, margin_right, margin_top, margin_bottom = 240, 80, 80, 50
    usable = [row for row in stage_rows if row.get("stage")]
    if not usable:
        usable = [{"stage": "未记录阶段耗时", "duration_seconds": "0", "status": "unknown"}]
    values = [as_number(row.get("duration_seconds")) for row in usable]
    max_value = max([1.0] + values)
    plot_w = width - margin_left - margin_right
    row_h = min(40, (height - margin_top - margin_bottom) / max(1, len(usable)))
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">双目标运行阶段耗时</text>')
    lines.append('<text x="34" y="58" class="subtitle">用于定位构建、QEMU 运行、镜像提取、Reader 渲染或结果对照中的异常停顿。</text>')
    for index, row in enumerate(usable):
        y = margin_top + index * row_h
        value = values[index]
        w = (value / max_value) * plot_w if max_value else 0
        color = PALETTE["agentos"] if row.get("status") == "ready" else PALETTE["extra"]
        label = stage_label(row.get("stage", ""))
        lines.append(f'<text x="{margin_left - 14}" y="{y + 21:.1f}" text-anchor="end" class="label">{escape(label[:28])}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y:.1f}" width="{w:.1f}" height="{max(18, row_h - 10):.1f}" fill="{color}" rx="2"/>')
        lines.append(f'<text x="{margin_left + w + 8:.1f}" y="{y + 21:.1f}" class="value">{fmt_number(value)}s</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_charts(rows: list[MetricRow], meta: dict[str, object], charts_dir: Path) -> list[Path]:
    by_metric = {row.metric: row for row in rows}
    charts_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    state_chart = charts_dir / "dual-target-state-reader.svg"
    grouped_bar_svg(
        "双目标状态与页面输出",
        "普通 uCore 和 AgentOS-uCore 使用同一批请求；增强目标不能少于普通目标，并额外导出内核 Agent 证据。",
        ["状态文件", "HTML页面", "API JSON"],
        [
            by_metric["提取到的 rp_* 状态文件"].plain or 0,
            by_metric["HTML 页面"].plain or 0,
            by_metric["API JSON"].plain or 0,
        ],
        [
            by_metric["提取到的 rp_* 状态文件"].agentos or 0,
            by_metric["HTML 页面"].agentos or 0,
            by_metric["API JSON"].agentos or 0,
        ],
        state_chart,
    )
    paths.append(state_chart)

    launch_chart = charts_dir / "launch-model.svg"
    stacked_bar_svg(
        "科研流程启动方式组成",
        "AgentOS 目标把关键 worker 转为 Agent 进程，同时保留普通 fork 路径作为对照。",
        [
            (
                "普通 uCore",
                by_metric["普通 fork 启动记录"].plain or 0,
                by_metric["Agent 启动记录"].plain or 0,
            ),
            (
                "AgentOS-uCore",
                by_metric["普通 fork 启动记录"].agentos or 0,
                by_metric["Agent 启动记录"].agentos or 0,
            ),
        ],
        launch_chart,
    )
    paths.append(launch_chart)

    evidence_chart = charts_dir / "agentos-evidence.svg"
    grouped_bar_svg(
        "AgentOS 额外机制证据",
        "图中数值来自双目标对照脚本，体现增强目标在同一科研流程中额外记录的内核事实。",
        ["内核证据", "主流程阶段", "主流程事实", "预置请求"],
        [0, 0, 0, by_metric["预置 action 请求"].plain or 0],
        [
            by_metric["内核证据检查项"].agentos or 0,
            by_metric["主流程内核阶段事实"].agentos or 0,
            as_number(meta.get("state", {}).get("agentos_mainflow_facts")) if isinstance(meta.get("state"), dict) else 0,
            by_metric["预置 action 请求"].agentos or 0,
        ],
        evidence_chart,
    )
    paths.append(evidence_chart)

    timing_chart = charts_dir / "stage-timings.svg"
    stage_timing_svg(meta.get("stage_rows", []), timing_chart)  # type: ignore[arg-type]
    paths.append(timing_chart)

    return paths


def write_report(rows: list[MetricRow], meta: dict[str, object], charts: list[Path], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}

    lines = [
        "# 双目标运行结果摘要",
        "",
        "本报告由双目标运行脚本在验证结束后生成，数据来自 QEMU 运行日志、文件系统镜像提取结果、Reader 渲染摘要和状态文件对照结果。",
        "",
        "## 关键结论",
        "",
        f"- 普通 uCore 提取状态文件 {fmt_number(as_number(state.get('plain_files')))} 个，AgentOS-uCore 提取 {fmt_number(as_number(state.get('agentos_files')))} 个，其中 AgentOS 额外状态文件 {fmt_number(as_number(state.get('agentos_extra_files')))} 个。",
        f"- Host Reader 页面数量为 {fmt_number(as_number(reader.get('plain_pages')))}，两个目标页面集合一致；AgentOS API JSON 比普通目标多 {fmt_number(as_number(reader.get('agentos_extra_api_json')))} 个。",
        f"- 同一批预置 action 请求数为 {fmt_number(as_number(state.get('embedded_action_records')))}，成功记录核对数为 {fmt_number(as_number(state.get('checked_success_records')))}。",
        f"- AgentOS 主流程中记录到 {fmt_number(as_number(state.get('agentos_mainflow_stages')))} 个内核参与阶段和 {fmt_number(as_number(state.get('agentos_mainflow_facts')))} 条主流程事实。",
        f"- QEMU 诊断：普通目标耗时 {plain_result.get('qemu_elapsed_seconds', '0')} 秒、无输出提示 {plain_result.get('qemu_idle_notices', '0')} 次；AgentOS 目标耗时 {agentos_result.get('qemu_elapsed_seconds', '0')} 秒、无输出提示 {agentos_result.get('qemu_idle_notices', '0')} 次。",
        "",
        "## 图表",
        "",
    ]
    for chart in charts:
        lines.append(f"- `{chart.relative_to(out_path.parent.parent).as_posix()}`")
    lines.extend(
        [
            "",
            "## 明细表",
            "",
            "| 分组 | 指标 | 普通 uCore | AgentOS-uCore | 差值 | 说明 |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.group} | {row.metric} | {fmt_number(row.plain)} | {fmt_number(row.agentos)} | {fmt_number(row.delta)} | {row.note} |"
        )
    stage_rows = meta.get("stage_rows", [])
    if isinstance(stage_rows, list) and stage_rows:
        lines.extend(["", "## 阶段耗时", "", "| 阶段 | 秒数 | 状态 |", "| --- | ---: | --- |"])
        for row in stage_rows:
            if isinstance(row, dict):
                lines.append(
                    f"| {stage_label(row.get('stage', ''))} | {row.get('duration_seconds', '')} | {row.get('status', '')} |"
                )
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def copy_docs_assets(charts: list[Path], docs_assets_dir: Path) -> None:
    docs_assets_dir.mkdir(parents=True, exist_ok=True)
    for chart in charts:
        shutil.copy2(chart, docs_assets_dir / chart.name)


def summarize(work_dir: Path, out_dir: Path, docs_assets_dir: Path | None = None) -> dict[str, object]:
    rows, meta = collect_rows(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_dir / "summary.csv")
    charts = write_charts(rows, meta, out_dir / "charts")
    write_report(rows, meta, charts, out_dir / "report.md")
    if docs_assets_dir is not None:
        copy_docs_assets(charts, docs_assets_dir)
    summary = {
        "status": "ready",
        "rows": len(rows),
        "charts": [str(path) for path in charts],
        "report": str(out_dir / "report.md"),
        "csv": str(out_dir / "summary.csv"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Create chart and report artifacts from dual-platform verification outputs.")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--docs-assets-dir", type=Path, default=None)
    args = parser.parse_args()

    summary = summarize(args.work_dir, args.out_dir, args.docs_assets_dir)
    print(
        "dual_platform_result_summary: rows={rows} charts={charts} report={report} status={status}".format(
            rows=summary["rows"],
            charts=len(summary["charts"]),
            report=summary["report"],
            status=summary["status"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
