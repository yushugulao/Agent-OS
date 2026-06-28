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


def evaluation_items(meta: dict[str, object]) -> list[tuple[str, str]]:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    items: list[tuple[str, str]] = []

    if state.get("status") == "ready" and as_number(state.get("run_result_match")) == 1:
        items.append(("通过", "两个目标运行结果可对照，plain target 的成功记录已在 AgentOS target 中保留。"))
    else:
        items.append(("关注", "双目标状态对照没有给出 ready/match 结果，需要查看 state-compare-summary.json。"))
    if as_number(state.get("agentos_extra_files")) > 0 and as_number(state.get("agentos_evidence_checks")) >= 20:
        items.append(("通过", "AgentOS target 额外输出内核证据文件，并通过多项内核事实检查。"))
    else:
        items.append(("关注", "AgentOS 额外内核证据不足，需要检查 rp_agentos_* 状态文件。"))
    if as_number(state.get("agentos_mainflow_stages")) >= 10:
        items.append(("通过", "科研主流程中记录到足够多的 AgentOS 内核参与阶段。"))
    else:
        items.append(("关注", "AgentOS 主流程阶段数量偏少，需要检查 rp_agentos_mainflow。"))
    if reader.get("status") == "ready" and as_number(reader.get("plain_pages")) == as_number(reader.get("agentos_pages")):
        items.append(("通过", "Host Reader 为两个目标生成同一套页面，增强目标没有缩小展示面。"))
    else:
        items.append(("关注", "Host Reader 页面数量或状态异常，需要查看 reader-summary.json。"))
    if as_number(plain_result.get("qemu_timed_out")) == 0 and as_number(agentos_result.get("qemu_timed_out")) == 0:
        items.append(("通过", "两个 QEMU 目标均未报告超时。"))
    else:
        items.append(("关注", "至少一个 QEMU 目标报告超时，需要查看 ucore-run.log。"))
    if as_number(agentos_result.get("qemu_idle_notices")) <= 1:
        items.append(("通过", "AgentOS 目标没有出现明显的长时间无输出现象。"))
    else:
        items.append(("关注", "AgentOS 目标无输出提示次数偏高，需要查看最后输出片段。"))
    return items


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


def append_wrapped_text(
    lines: list[str],
    x: float,
    y: float,
    text: str,
    css_class: str = "subtitle",
    max_chars: int = 64,
    line_height: int = 22,
    anchor: str | None = None,
) -> None:
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= max_chars and char in "，；。,. ":
            chunks.append(current.strip())
            current = ""
    if current:
        chunks.append(current.strip())
    if not chunks:
        chunks = [text]
    anchor_attr = f' text-anchor="{anchor}"' if anchor else ""
    for index, chunk in enumerate(chunks):
        lines.append(f'<text x="{x:g}" y="{y + index * line_height:g}" class="{css_class}"{anchor_attr}>{escape(chunk)}</text>')


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
    margin_left, margin_right, margin_top, margin_bottom = 90, 34, 108, 120
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_value = max([1.0] + plain_values + agentos_values)
    max_axis = math.ceil(max_value * 1.18 / 10.0) * 10.0
    lines = svg_header(width, height)
    lines.append(f'<text x="34" y="34" class="title">{escape(title)}</text>')
    append_wrapped_text(lines, 34, 58, subtitle, max_chars=54)
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


def runtime_observation_svg(rows: list[MetricRow], meta: dict[str, object], out_path: Path) -> None:
    by_metric = {row.metric: row for row in rows}
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    stage_rows = meta.get("stage_rows", [])
    stages = [row for row in stage_rows if isinstance(row, dict) and row.get("stage")] if isinstance(stage_rows, list) else []
    if not stages:
        stages = [{"stage": "未记录阶段耗时", "duration_seconds": "0", "status": "unknown"}]
    stages = stages[:8]

    width, height = 1120, 650
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">双目标运行观测面板</text>')
    lines.append('<text x="34" y="58" class="subtitle">把运行阶段、产物数量、AgentOS 内核证据和 QEMU 健康状态放在同一张图中，便于录屏时快速说明本次测试是否可信。</text>')

    stage_x, stage_y, stage_w, stage_h = 42, 92, 1036, 76
    lines.append(f'<rect x="{stage_x}" y="{stage_y}" width="{stage_w}" height="{stage_h}" fill="#f8fafc" stroke="{PALETTE["grid"]}"/>')
    lines.append(f'<text x="{stage_x + 16}" y="{stage_y + 25}" class="label">运行阶段</text>')
    seg_w = (stage_w - 150) / max(1, len(stages))
    x0 = stage_x + 120
    for index, row in enumerate(stages):
        x = x0 + index * seg_w
        status = str(row.get("status", ""))
        color = PALETTE["shared"] if status == "ready" else PALETTE["extra"]
        label = stage_label(row.get("stage", ""))
        duration = fmt_number(as_number(row.get("duration_seconds")))
        lines.append(f'<rect x="{x:.1f}" y="{stage_y + 16}" width="{max(24, seg_w - 12):.1f}" height="22" fill="{color}" rx="2"/>')
        lines.append(f'<text x="{x + max(24, seg_w - 12) / 2:.1f}" y="{stage_y + 32}" text-anchor="middle" class="label" fill="#fff">{escape(status or "unknown")}</text>')
        short_label = label[:8] if len(label) > 8 else label
        lines.append(f'<text x="{x + max(24, seg_w - 12) / 2:.1f}" y="{stage_y + 54}" text-anchor="middle" class="axis">{escape(short_label)}</text>')
        lines.append(f'<text x="{x + max(24, seg_w - 12) / 2:.1f}" y="{stage_y + 72}" text-anchor="middle" class="axis">{escape(duration)}s</text>')

    def card(x: int, y: int, title: str, items: list[tuple[str, str]], accent: str) -> None:
        lines.append(f'<rect x="{x}" y="{y}" width="330" height="128" fill="#ffffff" stroke="{PALETTE["grid"]}"/>')
        lines.append(f'<rect x="{x}" y="{y}" width="6" height="128" fill="{accent}"/>')
        lines.append(f'<text x="{x + 18}" y="{y + 28}" class="value">{escape(title)}</text>')
        for index, (label, value) in enumerate(items[:4]):
            yy = y + 52 + index * 20
            lines.append(f'<text x="{x + 18}" y="{yy}" class="axis">{escape(label)}</text>')
            lines.append(f'<text x="{x + 252}" y="{yy}" text-anchor="end" class="value">{escape(value)}</text>')

    card(
        42,
        202,
        "运行结果",
        [
            ("结果可对照", "是" if as_number(state.get("run_result_match")) == 1 else "否"),
            ("成功记录核对", fmt_number(as_number(state.get("checked_success_records")))),
            ("预置请求", fmt_number(as_number(state.get("embedded_action_records")))),
        ],
        PALETTE["shared"],
    )
    card(
        395,
        202,
        "状态与页面",
        [
            ("普通状态文件", fmt_number(as_number(state.get("plain_files")))),
            ("AgentOS状态文件", fmt_number(as_number(state.get("agentos_files")))),
            ("Reader页面", fmt_number(as_number(reader.get("agentos_pages")))),
            ("额外API JSON", fmt_number(as_number(reader.get("agentos_extra_api_json")))),
        ],
        PALETTE["plain"],
    )
    card(
        748,
        202,
        "内核证据",
        [
            ("检查项", fmt_number(as_number(by_metric["内核证据检查项"].agentos))),
            ("主流程阶段", fmt_number(as_number(by_metric["主流程内核阶段事实"].agentos))),
            ("主流程事实", fmt_number(as_number(state.get("agentos_mainflow_facts")))),
        ],
        PALETTE["agentos"],
    )
    card(
        42,
        366,
        "启动方式",
        [
            ("普通目标Agent启动", fmt_number(as_number(by_metric["Agent 启动记录"].plain))),
            ("增强目标Agent启动", fmt_number(as_number(by_metric["Agent 启动记录"].agentos))),
            ("增强目标普通fork", fmt_number(as_number(by_metric["普通 fork 启动记录"].agentos))),
        ],
        PALETTE["agentos"],
    )
    card(
        395,
        366,
        "QEMU健康",
        [
            ("普通目标超时", str(plain_result.get("qemu_timed_out", "0"))),
            ("增强目标超时", str(agentos_result.get("qemu_timed_out", "0"))),
            ("普通无输出提示", str(plain_result.get("qemu_idle_notices", "0"))),
            ("增强无输出提示", str(agentos_result.get("qemu_idle_notices", "0"))),
        ],
        PALETTE["neutral"],
    )
    card(
        748,
        366,
        "录屏读图顺序",
        [
            ("第一步", "看运行结果"),
            ("第二步", "看内核证据"),
            ("第三步", "打开Reader页面"),
            ("第四步", "查看日志定位异常"),
        ],
        PALETTE["extra"],
    )
    append_wrapped_text(
        lines,
        42,
        552,
        "读图方法：先确认运行结果和 QEMU 健康，再看 AgentOS 多出的状态、API、Agent 启动与内核事实；异常时回到 stage-timings.csv 和 ucore-run.log。",
        max_chars=58,
    )
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scenario_evidence_svg(meta: dict[str, object], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    raw_rows = state.get("scenario_evidence", [])
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    if not rows:
        rows = [{"label": "未记录场景证据", "expected": 1, "matched": 0, "status": "partial"}]

    width, height = 1080, max(420, 118 + len(rows) * 44)
    margin_left, margin_right, margin_top = 210, 120, 88
    plot_w = width - margin_left - margin_right
    max_value = max([1.0] + [as_number(row.get("expected")) for row in rows] + [as_number(row.get("matched")) for row in rows])
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">AgentOS 多场景机制证据</text>')
    lines.append('<text x="34" y="58" class="subtitle">每一行对应一个 Agent 工作流场景；浅色条是应检查证据数，深色条是本次运行实际命中的证据数。</text>')
    for tick in range(0, int(max_value) + 1):
        if max_value <= 8 or tick % max(1, int(max_value // 5)) == 0:
            x = margin_left + (tick / max_value) * plot_w
            lines.append(f'<line x1="{x:.1f}" y1="{margin_top - 10}" x2="{x:.1f}" y2="{height - 42}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
            lines.append(f'<text x="{x:.1f}" y="{height - 22}" text-anchor="middle" class="axis">{tick}</text>')
    for index, row in enumerate(rows):
        y = margin_top + index * 44
        label = str(row.get("label") or row.get("scenario") or "")
        expected = as_number(row.get("expected"))
        matched = as_number(row.get("matched"))
        expected_w = (expected / max_value) * plot_w if max_value else 0
        matched_w = (matched / max_value) * plot_w if max_value else 0
        color = PALETTE["agentos"] if str(row.get("status")) == "ready" else PALETTE["extra"]
        lines.append(f'<text x="{margin_left - 14}" y="{y + 22}" text-anchor="end" class="label">{escape(label[:20])}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y + 4}" width="{expected_w:.1f}" height="28" fill="#eef3f8" stroke="{PALETTE["grid"]}" rx="2"/>')
        lines.append(f'<rect x="{margin_left}" y="{y + 9}" width="{matched_w:.1f}" height="18" fill="{color}" rx="2"/>')
        lines.append(f'<text x="{margin_left + max(expected_w, matched_w) + 10:.1f}" y="{y + 23}" class="value">{fmt_number(matched)}/{fmt_number(expected)}</text>')
    legend_y = height - 22
    lines.append(f'<rect x="{width - 360}" y="{legend_y - 12}" width="14" height="14" fill="#eef3f8" stroke="{PALETTE["grid"]}"/>')
    lines.append(f'<text x="{width - 338}" y="{legend_y}" class="axis">应检查证据</text>')
    lines.append(f'<rect x="{width - 230}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["agentos"]}"/>')
    lines.append(f'<text x="{width - 208}" y="{legend_y}" class="axis">已命中证据</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cost_replacement_svg(meta: dict[str, object], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    raw_rows = state.get("cost_replacements", [])
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    if not rows:
        rows = [{"plain_cost": "未记录用户态成本项", "agentos_replace": "未记录替代机制", "risk": "unknown", "preserved_from_plain": 0}]
    rows = rows[:10]

    width, height = 1180, max(452, 136 + len(rows) * 48)
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">用户态成本项与 AgentOS 替代机制</text>')
    append_wrapped_text(
        lines,
        34,
        58,
        "每行来自 runner_report：左侧是用户态成本，右侧是 AgentOS 内核替代机制。",
        max_chars=90,
    )
    x_cost, x_arrow, x_replace, x_risk = 52, 438, 520, 920
    lines.append(f'<text x="{x_cost}" y="88" class="value">普通用户态成本项</text>')
    lines.append(f'<text x="{x_replace}" y="88" class="value">AgentOS 替代机制</text>')
    lines.append(f'<text x="{x_risk}" y="88" class="value">对应风险</text>')
    for index, row in enumerate(rows):
        y = 112 + index * 48
        preserved = as_number(row.get("preserved_from_plain")) == 1
        color = PALETTE["agentos"] if preserved else PALETTE["extra"]
        bg = "#ffffff" if index % 2 == 0 else "#f8fafc"
        lines.append(f'<rect x="34" y="{y - 24}" width="{width - 68}" height="38" fill="{bg}" stroke="{PALETTE["grid"]}"/>')
        lines.append(f'<circle cx="{x_cost - 18}" cy="{y - 5}" r="5" fill="{color}"/>')
        lines.append(f'<text x="{x_cost}" y="{y}" class="label">{escape(str(row.get("plain_cost", ""))[:46])}</text>')
        lines.append(f'<line x1="{x_arrow}" y1="{y - 5}" x2="{x_replace - 20}" y2="{y - 5}" stroke="{color}" stroke-width="2"/>')
        lines.append(f'<polygon points="{x_replace - 20},{y - 10} {x_replace - 8},{y - 5} {x_replace - 20},{y}" fill="{color}"/>')
        lines.append(f'<text x="{x_replace}" y="{y}" class="label">{escape(str(row.get("agentos_replace", ""))[:44])}</text>')
        lines.append(f'<text x="{x_risk}" y="{y}" class="axis">{escape(str(row.get("risk", ""))[:32])}</text>')
    append_wrapped_text(
        lines,
        52,
        height - 46,
        "读图方法：同一行两端必须同时存在；左侧说明普通用户态成本，右侧说明同一负载下的内核机制替代。",
        max_chars=62,
    )
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def runner_tick_svg(meta: dict[str, object], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    raw_rows = state.get("runner_tick_comparison", [])
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    if not rows:
        rows = [{"label": "未记录 runner tick", "plain_ticks": 0, "agentos_ticks": 0, "saved_ticks": 0}]

    width, height = 1080, max(460, 160 + len(rows) * 54)
    margin_left, margin_right, margin_top = 190, 130, 92
    plot_w = width - margin_left - margin_right
    max_value = max([1.0] + [as_number(row.get("plain_ticks")) for row in rows] + [as_number(row.get("agentos_ticks")) for row in rows])
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">Runner Tick 对照</text>')
    lines.append('<text x="34" y="58" class="subtitle">同一科研负载中的 runner case 成对比较：蓝色为普通用户态路径，橙色为 AgentOS 内核辅助路径。</text>')
    tick_step = max(1, int(math.ceil(max_value / 5)))
    tick = 0
    while tick <= max_value:
        x = margin_left + (tick / max_value) * plot_w if max_value else margin_left
        lines.append(f'<line x1="{x:.1f}" y1="{margin_top - 12}" x2="{x:.1f}" y2="{height - 48}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{x:.1f}" y="{height - 26}" text-anchor="middle" class="axis">{tick}</text>')
        tick += tick_step
    for index, row in enumerate(rows):
        y = margin_top + index * 54
        label = str(row.get("label", ""))[:18]
        plain_ticks = as_number(row.get("plain_ticks"))
        agentos_ticks = as_number(row.get("agentos_ticks"))
        plain_w = (plain_ticks / max_value) * plot_w if max_value else 0
        agentos_w = (agentos_ticks / max_value) * plot_w if max_value else 0
        saved = as_number(row.get("saved_ticks"))
        lines.append(f'<text x="{margin_left - 14}" y="{y + 24}" text-anchor="end" class="label">{escape(label)}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y + 3}" width="{plain_w:.1f}" height="18" fill="{PALETTE["plain"]}" rx="2"/>')
        lines.append(f'<rect x="{margin_left}" y="{y + 27}" width="{agentos_w:.1f}" height="18" fill="{PALETTE["agentos"]}" rx="2"/>')
        lines.append(f'<text x="{margin_left + plain_w + 8:.1f}" y="{y + 17}" class="axis">{fmt_number(plain_ticks)}</text>')
        lines.append(f'<text x="{margin_left + agentos_w + 8:.1f}" y="{y + 41}" class="axis">{fmt_number(agentos_ticks)}</text>')
        lines.append(f'<text x="{width - 118}" y="{y + 31}" class="value">省 {fmt_number(saved)}</text>')
    legend_y = height - 26
    lines.append(f'<rect x="{width - 360}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["plain"]}"/>')
    lines.append(f'<text x="{width - 338}" y="{legend_y}" class="axis">普通用户态路径</text>')
    lines.append(f'<rect x="{width - 220}" y="{legend_y - 12}" width="14" height="14" fill="{PALETTE["agentos"]}"/>')
    lines.append(f'<text x="{width - 198}" y="{legend_y}" class="axis">AgentOS 路径</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def runner_tick_rows(meta: dict[str, object]) -> list[dict[str, object]]:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    raw_rows = state.get("runner_tick_comparison", [])
    return [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []


def write_runner_sweep_csv(meta: dict[str, object], out_path: Path) -> None:
    rows = runner_tick_rows(meta)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "scene",
                "plain_case",
                "agentos_case",
                "plain_ticks",
                "agentos_ticks",
                "saved_ticks",
                "speedup_x",
            ]
        )
        for row in rows:
            speedup = as_number(row.get("speedup_x100")) / 100.0
            writer.writerow(
                [
                    row.get("label", ""),
                    row.get("plain_case", ""),
                    row.get("agentos_case", ""),
                    fmt_number(as_number(row.get("plain_ticks"))),
                    fmt_number(as_number(row.get("agentos_ticks"))),
                    fmt_number(as_number(row.get("saved_ticks"))),
                    fmt_number(speedup),
                ]
            )


def runner_speedup_svg(meta: dict[str, object], out_path: Path) -> None:
    rows = runner_tick_rows(meta)
    if not rows:
        rows = [{"label": "未记录", "plain_ticks": 0, "agentos_ticks": 0, "saved_ticks": 0, "speedup_x100": 0}]
    rows = rows[:10]
    width, height = 1120, max(460, 190 + len(rows) * 44)
    margin_left, margin_right, margin_top = 210, 160, 92
    plot_w = width - margin_left - margin_right
    max_speed = max([1.0] + [as_number(row.get("speedup_x100")) / 100.0 for row in rows])
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">Runner 成组场景相对倍数</text>')
    append_wrapped_text(
        lines,
        34,
        58,
        "横向条来自 runner-sweep.csv，展示 AgentOS 相对普通路径的 tick 倍数和节省量。",
        max_chars=58,
    )
    tick_step = max(1, int(math.ceil(max_speed / 5)))
    tick = 0
    while tick <= max_speed:
        x = margin_left + (tick / max_speed) * plot_w if max_speed else margin_left
        lines.append(f'<line x1="{x:.1f}" y1="{margin_top - 12}" x2="{x:.1f}" y2="{height - 78}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{x:.1f}" y="{height - 58}" text-anchor="middle" class="axis">{tick}x</text>')
        tick += tick_step
    for index, row in enumerate(rows):
        y = margin_top + index * 44
        label = str(row.get("label", ""))[:18]
        speedup = as_number(row.get("speedup_x100")) / 100.0
        saved = as_number(row.get("saved_ticks"))
        bar_w = (speedup / max_speed) * plot_w if max_speed else 0
        color = PALETTE["agentos"] if saved > 0 else PALETTE["neutral"]
        value_x = margin_left + bar_w + 8
        value_anchor = ""
        if value_x > width - 260:
            value_x = margin_left + max(40, bar_w - 10)
            value_anchor = ' text-anchor="end"'
        lines.append(f'<text x="{margin_left - 14}" y="{y + 22}" text-anchor="end" class="label">{escape(label)}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y + 4}" width="{bar_w:.1f}" height="24" fill="{color}" rx="2"/>')
        lines.append(f'<text x="{value_x:.1f}" y="{y + 21}" class="value"{value_anchor}>{fmt_number(speedup)}x</text>')
        lines.append(f'<text x="{width - 130}" y="{y + 21}" class="axis">省 {fmt_number(saved)} tick</text>')
    lines.append(f'<text x="52" y="{height - 18}" class="subtitle">读图方法：1x 表示两条路径 tick 相同；超过 1x 表示 AgentOS 用更少 tick 完成。</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_chart_type_coverage_csv(out_path: Path) -> None:
    rows = [
        ("条形/柱状对比", "dual-target-state-reader.svg;launch-model.svg;runner-ticks.svg", "状态文件、页面、API、启动方式、runner tick；Python 标准库 SVG 生成"),
        ("曲线趋势", "runner-cumulative-line.svg", "runner tick 累计成本；Python 标准库 SVG 生成"),
        ("箱形图", "runner-tick-box.svg", "plain/AgentOS runner tick 分布；Python 标准库 SVG 生成"),
        ("热力图", "runner-cost-heatmap.svg", "runner 场景与指标强弱；Python 标准库 SVG 生成"),
        ("监控面积图", "stage-monitor-area.svg", "双目标运行阶段耗时；Python 标准库 SVG 生成"),
        ("三维曲面与投影组合", "runner-surface-composite.svg", "runner 场景、路径类型和 tick 数值；Python 标准库 SVG 生成"),
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["reference_chart_type", "agentos_artifacts", "data_source"])
        writer.writerows(rows)


def runner_cumulative_line_svg(meta: dict[str, object], out_path: Path) -> None:
    rows = runner_tick_rows(meta)
    if not rows:
        rows = [{"label": "未记录", "plain_ticks": 0, "agentos_ticks": 0, "saved_ticks": 0}]
    width, height = 1080, 440
    left, right, top, bottom = 88, 54, 76, 78
    plot_w, plot_h = width - left - right, height - top - bottom
    plain_sum = 0.0
    agentos_sum = 0.0
    plain_points: list[tuple[float, float]] = []
    agentos_points: list[tuple[float, float]] = []
    labels: list[str] = []
    for index, row in enumerate(rows):
        plain_sum += as_number(row.get("plain_ticks"))
        agentos_sum += as_number(row.get("agentos_ticks"))
        x = left + (index / max(1, len(rows) - 1)) * plot_w
        plain_points.append((x, plain_sum))
        agentos_points.append((x, agentos_sum))
        labels.append(str(row.get("label", ""))[:8])
    max_value = max([1.0] + [point[1] for point in plain_points + agentos_points])

    def y_pos(value: float) -> float:
        return top + plot_h - (value / max_value) * plot_h

    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">Runner 累计 Tick 曲线</text>')
    lines.append('<text x="34" y="58" class="subtitle">按场景顺序累计 runner tick。曲线分离越明显，说明增强目标在完整流程中积累的执行成本越低。</text>')
    for tick in range(0, int(max_value) + 1, max(1, int(math.ceil(max_value / 5)))):
        y = y_pos(tick)
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="axis">{tick}</text>')
    for index, label in enumerate(labels):
        x = left + (index / max(1, len(labels) - 1)) * plot_w
        lines.append(f'<text x="{x:.1f}" y="{height - 42}" text-anchor="middle" class="axis">{escape(label)}</text>')
    for points, color, name, y_legend in (
        (plain_points, PALETTE["plain"], "普通用户态累计 tick", height - 22),
        (agentos_points, PALETTE["agentos"], "AgentOS 累计 tick", height - 22),
    ):
        path = " ".join(f'{x:.1f},{y_pos(value):.1f}' for x, value in points)
        lines.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="3"/>')
        for x, value in points:
            lines.append(f'<circle cx="{x:.1f}" cy="{y_pos(value):.1f}" r="4" fill="{color}"/>')
    lines.append(f'<rect x="{width - 350}" y="{height - 30}" width="14" height="14" fill="{PALETTE["plain"]}"/>')
    lines.append(f'<text x="{width - 328}" y="{height - 18}" class="axis">普通用户态累计 tick</text>')
    lines.append(f'<rect x="{width - 180}" y="{height - 30}" width="14" height="14" fill="{PALETTE["agentos"]}"/>')
    lines.append(f'<text x="{width - 158}" y="{height - 18}" class="axis">AgentOS 累计 tick</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * ratio
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def runner_tick_box_svg(meta: dict[str, object], out_path: Path) -> None:
    rows = runner_tick_rows(meta)
    plain = [as_number(row.get("plain_ticks")) for row in rows]
    agentos = [as_number(row.get("agentos_ticks")) for row in rows]
    if not plain:
        plain = [0.0]
    if not agentos:
        agentos = [0.0]
    width, height = 860, 420
    left, top, plot_w, plot_h = 92, 76, 680, 250
    max_value = max([1.0] + plain + agentos)

    def x_pos(value: float) -> float:
        return left + (value / max_value) * plot_w

    def draw_box(y: int, values: list[float], color: str, label: str) -> list[str]:
        q0 = min(values)
        q1 = percentile(values, 0.25)
        q2 = percentile(values, 0.5)
        q3 = percentile(values, 0.75)
        q4 = max(values)
        box = [
            f'<text x="{left - 18}" y="{y + 6}" text-anchor="end" class="label">{escape(label)}</text>',
            f'<line x1="{x_pos(q0):.1f}" y1="{y}" x2="{x_pos(q4):.1f}" y2="{y}" stroke="{color}" stroke-width="3"/>',
            f'<rect x="{x_pos(q1):.1f}" y="{y - 18}" width="{max(2, x_pos(q3) - x_pos(q1)):.1f}" height="36" fill="#ffffff" stroke="{color}" stroke-width="3"/>',
            f'<line x1="{x_pos(q2):.1f}" y1="{y - 18}" x2="{x_pos(q2):.1f}" y2="{y + 18}" stroke="{color}" stroke-width="3"/>',
            f'<line x1="{x_pos(q0):.1f}" y1="{y - 12}" x2="{x_pos(q0):.1f}" y2="{y + 12}" stroke="{color}" stroke-width="3"/>',
            f'<line x1="{x_pos(q4):.1f}" y1="{y - 12}" x2="{x_pos(q4):.1f}" y2="{y + 12}" stroke="{color}" stroke-width="3"/>',
        ]
        stat_x = x_pos(q4) + 8
        stat_anchor = ""
        if stat_x > width - 170:
            stat_x = width - 8
            stat_anchor = ' text-anchor="end"'
        box.append(
            f'<text x="{stat_x:.1f}" y="{y + 5}" class="axis"{stat_anchor}>min {fmt_number(q0)} / mid {fmt_number(q2)} / max {fmt_number(q4)}</text>'
        )
        return box

    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">Runner Tick 分布箱形图</text>')
    lines.append('<text x="34" y="58" class="subtitle">箱体展示 P25-P75，中线为中位数，横线两端为最小和最大 tick。</text>')
    for tick in range(0, int(max_value) + 1, max(1, int(math.ceil(max_value / 5)))):
        x = x_pos(tick)
        lines.append(f'<line x1="{x:.1f}" y1="{top - 14}" x2="{x:.1f}" y2="{top + plot_h}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{x:.1f}" y="{top + plot_h + 24}" text-anchor="middle" class="axis">{tick}</text>')
    lines.extend(draw_box(150, plain, PALETTE["plain"], "普通路径"))
    lines.extend(draw_box(250, agentos, PALETTE["agentos"], "AgentOS"))
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def runner_cost_heatmap_svg(meta: dict[str, object], out_path: Path) -> None:
    rows = runner_tick_rows(meta)
    if not rows:
        rows = [{"label": "未记录", "plain_ticks": 0, "agentos_ticks": 0, "saved_ticks": 0, "speedup_x100": 0}]
    metrics = [
        ("plain_ticks", "普通 tick"),
        ("agentos_ticks", "AgentOS tick"),
        ("saved_ticks", "节省 tick"),
        ("speedup_x100", "相对倍数x100"),
    ]
    width, height = 1080, max(420, 128 + len(rows) * 38)
    left, top, cell_w, cell_h = 190, 94, 150, 30
    values = [as_number(row.get(key)) for row in rows for key, _ in metrics]
    max_value = max([1.0] + values)

    def color(value: float) -> str:
        t = max(0.0, min(1.0, value / max_value))
        r = int(238 + (245 - 238) * t)
        g = int(243 - (243 - 133) * t)
        b = int(248 - (248 - 24) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">Runner 成本热力图</text>')
    lines.append('<text x="34" y="58" class="subtitle">颜色越深表示数值越高。该图用于快速定位哪些场景成本最高、哪些场景收益最明显。</text>')
    for col, (_, label) in enumerate(metrics):
        x = left + col * cell_w
        lines.append(f'<text x="{x + cell_w / 2:.1f}" y="{top - 16}" text-anchor="middle" class="value">{escape(label)}</text>')
    for row_index, row in enumerate(rows):
        y = top + row_index * cell_h
        lines.append(f'<text x="{left - 12}" y="{y + 20}" text-anchor="end" class="label">{escape(str(row.get("label", ""))[:18])}</text>')
        for col, (key, _) in enumerate(metrics):
            value = as_number(row.get(key))
            if key == "speedup_x100":
                value = value / 100.0
            x = left + col * cell_w
            lines.append(f'<rect x="{x}" y="{y}" width="{cell_w - 4}" height="{cell_h - 4}" fill="{color(value)}" stroke="{PALETTE["grid"]}"/>')
            lines.append(f'<text x="{x + cell_w / 2:.1f}" y="{y + 19}" text-anchor="middle" class="axis">{fmt_number(value)}</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage_monitor_area_svg(meta: dict[str, object], out_path: Path) -> None:
    raw_rows = meta.get("stage_rows", [])
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    if not rows:
        rows = [{"stage": "未记录", "duration_seconds": "0", "status": "unknown"}]
    durations = [as_number(row.get("duration_seconds")) for row in rows]
    width, height = 1080, 420
    left, right, top, bottom = 84, 44, 74, 80
    plot_w, plot_h = width - left - right, height - top - bottom
    max_value = max([1.0] + durations)
    points: list[tuple[float, float]] = []
    for index, duration in enumerate(durations):
        x = left + (index / max(1, len(durations) - 1)) * plot_w
        y = top + plot_h - (duration / max_value) * plot_h
        points.append((x, y))
    area_points = [(left, top + plot_h), *points, (left + plot_w, top + plot_h)]
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">双目标运行阶段监控面积图</text>')
    lines.append('<text x="34" y="58" class="subtitle">按阶段展示耗时强弱，适合录屏时快速定位主要耗时是否集中在 QEMU 双目标运行阶段。</text>')
    for tick in range(0, int(max_value) + 1, max(1, int(math.ceil(max_value / 5)))):
        y = top + plot_h - (tick / max_value) * plot_h
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="axis">{tick}s</text>')
    lines.append(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in area_points)}" fill="#fce8d4" stroke="{PALETTE["agentos"]}" stroke-width="2"/>')
    lines.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="{PALETTE["agentos"]}" stroke-width="3"/>')
    for index, row in enumerate(rows):
        x, y = points[index]
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{PALETTE["agentos"]}"/>')
        lines.append(f'<text x="{x:.1f}" y="{height - 42}" text-anchor="middle" class="axis">{escape(stage_label(row.get("stage", ""))[:6])}</text>')
    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def runner_surface_composite_svg(meta: dict[str, object], out_path: Path) -> None:
    rows = runner_tick_rows(meta)
    if not rows:
        rows = [{"label": "未记录", "plain_ticks": 0, "agentos_ticks": 0, "saved_ticks": 0}]
    rows = rows[:8]
    series = [
        ("plain_ticks", "普通路径", PALETTE["plain"]),
        ("agentos_ticks", "AgentOS", PALETTE["agentos"]),
        ("saved_ticks", "节省", PALETTE["shared"]),
    ]
    values = [[as_number(row.get(key)) for key, _, _ in series] for row in rows]
    max_value = max([1.0] + [value for line in values for value in line])
    width, height = 1180, 760
    lines = svg_header(width, height)
    lines.append('<text x="34" y="34" class="title">Runner 曲面 / 投影 / 热力组合图</text>')
    lines.append('<text x="34" y="58" class="subtitle">上部用等距网格模拟三维曲面，中部给出二维投影，下部给出热力图。三者使用同一组 runner-sweep 数据。</text>')

    def iso_point(ix: int, iy: int, value: float) -> tuple[float, float]:
        base_x, base_y = 168, 268
        return (
            base_x + ix * 92 + iy * 48,
            base_y + iy * 32 - ix * 10 - (value / max_value) * 116,
        )

    for ix, row_values in enumerate(values):
        for iy, value in enumerate(row_values):
            x, y = iso_point(ix, iy, value)
            shade = "#f58518" if iy == 1 else ("#4c78a8" if iy == 0 else "#54a24b")
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{shade}"/>')
            lines.append(f'<text x="{x + 6:.1f}" y="{y - 5:.1f}" class="axis">{fmt_number(value)}</text>')
    for ix in range(len(values)):
        for iy in range(len(series) - 1):
            x1, y1 = iso_point(ix, iy, values[ix][iy])
            x2, y2 = iso_point(ix, iy + 1, values[ix][iy + 1])
            lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
    for ix in range(len(values) - 1):
        for iy in range(len(series)):
            x1, y1 = iso_point(ix, iy, values[ix][iy])
            x2, y2 = iso_point(ix + 1, iy, values[ix + 1][iy])
            lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
    lines.append('<text x="58" y="92" class="value">一、等距曲面视图</text>')

    projection_top, projection_left, projection_w, projection_h = 360, 88, 980, 150
    lines.append('<text x="58" y="342" class="value">二、二维投影曲线</text>')
    for tick in range(0, int(max_value) + 1, max(1, int(math.ceil(max_value / 4)))):
        y = projection_top + projection_h - (tick / max_value) * projection_h
        lines.append(f'<line x1="{projection_left}" y1="{y:.1f}" x2="{projection_left + projection_w}" y2="{y:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        lines.append(f'<text x="{projection_left - 10}" y="{y + 4:.1f}" text-anchor="end" class="axis">{tick}</text>')
    for iy, (key, label, color) in enumerate(series):
        points = []
        for ix, row in enumerate(rows):
            x = projection_left + (ix / max(1, len(rows) - 1)) * projection_w
            y = projection_top + projection_h - (as_number(row.get(key)) / max_value) * projection_h
            points.append((x, y))
        lines.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="none" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<rect x="{projection_left + 720 + iy * 82}" y="{projection_top - 30}" width="12" height="12" fill="{color}"/>')
        lines.append(f'<text x="{projection_left + 738 + iy * 82}" y="{projection_top - 19}" class="axis">{escape(label)}</text>')
    for ix, row in enumerate(rows):
        x = projection_left + (ix / max(1, len(rows) - 1)) * projection_w
        lines.append(f'<text x="{x:.1f}" y="{projection_top + projection_h + 24}" text-anchor="middle" class="axis">{escape(str(row.get("label", ""))[:7])}</text>')

    heat_top, heat_left, cell_w, cell_h = 590, 190, 120, 28
    lines.append('<text x="58" y="568" class="value">三、热力图</text>')
    for iy, (_, label, _) in enumerate(series):
        lines.append(f'<text x="{heat_left - 12}" y="{heat_top + iy * cell_h + 19}" text-anchor="end" class="label">{escape(label)}</text>')
    for ix, row in enumerate(rows):
        lines.append(f'<text x="{heat_left + ix * cell_w + cell_w / 2:.1f}" y="{heat_top - 12}" text-anchor="middle" class="axis">{escape(str(row.get("label", ""))[:6])}</text>')
    for ix, row_values in enumerate(values):
        for iy, value in enumerate(row_values):
            t = max(0.0, min(1.0, value / max_value))
            fill = f"#{int(238 + 7 * t):02x}{int(243 - 110 * t):02x}{int(248 - 220 * t):02x}"
            x = heat_left + ix * cell_w
            y = heat_top + iy * cell_h
            lines.append(f'<rect x="{x}" y="{y}" width="{cell_w - 4}" height="{cell_h - 4}" fill="{fill}" stroke="{PALETTE["grid"]}"/>')
            lines.append(f'<text x="{x + cell_w / 2:.1f}" y="{y + 18}" text-anchor="middle" class="axis">{fmt_number(value)}</text>')
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
        "同一批请求分别进入普通 uCore 和 AgentOS-uCore；增强目标保持页面规模，并额外导出内核 Agent 证据。",
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

    observation_chart = charts_dir / "runtime-observation.svg"
    runtime_observation_svg(rows, meta, observation_chart)
    paths.append(observation_chart)

    scenario_chart = charts_dir / "scenario-evidence.svg"
    scenario_evidence_svg(meta, scenario_chart)
    paths.append(scenario_chart)

    cost_chart = charts_dir / "cost-replacement.svg"
    cost_replacement_svg(meta, cost_chart)
    paths.append(cost_chart)

    runner_tick_chart = charts_dir / "runner-ticks.svg"
    runner_tick_svg(meta, runner_tick_chart)
    paths.append(runner_tick_chart)

    runner_speedup_chart = charts_dir / "runner-speedup.svg"
    runner_speedup_svg(meta, runner_speedup_chart)
    paths.append(runner_speedup_chart)

    runner_line_chart = charts_dir / "runner-cumulative-line.svg"
    runner_cumulative_line_svg(meta, runner_line_chart)
    paths.append(runner_line_chart)

    runner_box_chart = charts_dir / "runner-tick-box.svg"
    runner_tick_box_svg(meta, runner_box_chart)
    paths.append(runner_box_chart)

    runner_heatmap_chart = charts_dir / "runner-cost-heatmap.svg"
    runner_cost_heatmap_svg(meta, runner_heatmap_chart)
    paths.append(runner_heatmap_chart)

    stage_area_chart = charts_dir / "stage-monitor-area.svg"
    stage_monitor_area_svg(meta, stage_area_chart)
    paths.append(stage_area_chart)

    surface_chart = charts_dir / "runner-surface-composite.svg"
    runner_surface_composite_svg(meta, surface_chart)
    paths.append(surface_chart)

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
    lines.append("- `runner-sweep.csv`")
    lines.append("- `chart-type-coverage.csv`")
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
    lines.extend(["", "## 自动判读", ""])
    for status, text in evaluation_items(meta):
        lines.append(f"- {status}：{text}")
    scenario_rows = state.get("scenario_evidence", []) if isinstance(state, dict) else []
    if isinstance(scenario_rows, list) and scenario_rows:
        lines.extend(["", "## 多场景机制证据", "", "| 场景 | 命中/应检查 | 来源状态文件 | 状态 |", "| --- | ---: | --- | --- |"])
        for row in scenario_rows:
            if isinstance(row, dict):
                sources = row.get("sources", [])
                source_text = ",".join(str(item) for item in sources) if isinstance(sources, list) else str(sources)
                lines.append(
                    "| {label} | {matched}/{expected} | {sources} | {status} |".format(
                        label=row.get("label", row.get("scenario", "")),
                        matched=fmt_number(as_number(row.get("matched"))),
                        expected=fmt_number(as_number(row.get("expected"))),
                        sources=source_text,
                        status=row.get("status", ""),
                    )
                )
    replacement_rows = state.get("cost_replacements", []) if isinstance(state, dict) else []
    if isinstance(replacement_rows, list) and replacement_rows:
        lines.extend(
            [
                "",
                "## 用户态成本项与 AgentOS 替代机制",
                "",
                "| 用户态成本项 | AgentOS 替代机制 | 风险说明 | 普通目标中存在 |",
                "| --- | --- | --- | ---: |",
            ]
        )
        for row in replacement_rows:
            if isinstance(row, dict):
                preserved = "是" if as_number(row.get("preserved_from_plain")) == 1 else "否"
                lines.append(
                    "| {plain_cost} | {agentos_replace} | {risk} | {preserved} |".format(
                        plain_cost=row.get("plain_cost", ""),
                        agentos_replace=row.get("agentos_replace", ""),
                        risk=row.get("risk", ""),
                        preserved=preserved,
                    )
                )
    runner_rows = state.get("runner_tick_comparison", []) if isinstance(state, dict) else []
    if isinstance(runner_rows, list) and runner_rows:
        lines.extend(
            [
                "",
                "## Runner Tick 对照",
                "",
                "| 场景 | 普通用户态 case | AgentOS case | 普通 tick | AgentOS tick | 节省 tick |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            ]
        )
        for row in runner_rows:
            if isinstance(row, dict):
                lines.append(
                    "| {label} | {plain_case} | {agentos_case} | {plain_ticks} | {agentos_ticks} | {saved_ticks} |".format(
                        label=row.get("label", ""),
                        plain_case=row.get("plain_case", ""),
                        agentos_case=row.get("agentos_case", ""),
                        plain_ticks=fmt_number(as_number(row.get("plain_ticks"))),
                        agentos_ticks=fmt_number(as_number(row.get("agentos_ticks"))),
                        saved_ticks=fmt_number(as_number(row.get("saved_ticks"))),
                    )
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


def write_index(rows: list[MetricRow], meta: dict[str, object], charts: list[Path], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    chart_cards = []
    chart_titles = {
        "dual-target-state-reader.svg": "双目标状态与页面输出",
        "launch-model.svg": "科研流程启动方式组成",
        "agentos-evidence.svg": "AgentOS 额外机制证据",
        "stage-timings.svg": "双目标运行阶段耗时",
        "runtime-observation.svg": "双目标运行观测面板",
        "scenario-evidence.svg": "AgentOS 多场景机制证据",
        "cost-replacement.svg": "用户态成本项与 AgentOS 替代机制",
        "runner-ticks.svg": "Runner Tick 对照",
        "runner-speedup.svg": "Runner 成组场景相对倍数",
        "runner-cumulative-line.svg": "Runner 累计 Tick 曲线",
        "runner-tick-box.svg": "Runner Tick 分布箱形图",
        "runner-cost-heatmap.svg": "Runner 成本热力图",
        "stage-monitor-area.svg": "双目标运行阶段监控面积图",
        "runner-surface-composite.svg": "Runner 曲面 / 投影 / 热力组合图",
    }
    for chart in charts:
        rel = chart.relative_to(out_path.parent).as_posix()
        title = chart_titles.get(chart.name, chart.name)
        chart_cards.append(
            f'<section class="chart-card"><h2>{escape(title)}</h2><img src="{escape(rel)}" alt="{escape(title)}"></section>'
        )
    rows_html = []
    for row in rows:
        rows_html.append(
            "<tr>"
            f"<td>{escape(row.group)}</td>"
            f"<td>{escape(row.metric)}</td>"
            f"<td>{escape(fmt_number(row.plain))}</td>"
            f"<td>{escape(fmt_number(row.agentos))}</td>"
            f"<td>{escape(fmt_number(row.delta))}</td>"
            f"<td>{escape(row.note)}</td>"
            "</tr>"
        )
    stage_rows = meta.get("stage_rows", [])
    stage_rows_html = []
    if isinstance(stage_rows, list):
        for row in stage_rows:
            if isinstance(row, dict):
                stage_rows_html.append(
                    "<tr>"
                    f"<td>{escape(stage_label(row.get('stage', '')))}</td>"
                    f"<td>{escape(str(row.get('duration_seconds', '')))}</td>"
                    f"<td>{escape(str(row.get('status', '')))}</td>"
                    "</tr>"
                )
    eval_html = "".join(
        f'<li><strong class="eval-{escape(status)}">{escape(status)}</strong>{escape(text)}</li>'
        for status, text in evaluation_items(meta)
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 双目标测试结果</title>
  <style>
    :root {{ color-scheme: light; --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --plain:#4c78a8; --agent:#f58518; --bg:#f7f9fb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Arial, "Microsoft YaHei", sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:32px 40px 22px; background:#fff; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.7; }}
    .summary {{ display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:12px; margin-top:18px; }}
    .metric {{ background:#fff; border:1px solid var(--line); padding:14px; }}
    .metric strong {{ display:block; font-size:24px; margin-bottom:4px; }}
    .metric span {{ color:var(--muted); font-size:13px; }}
    main {{ padding:24px 40px 42px; max-width:1180px; margin:0 auto; }}
    .links {{ display:flex; flex-wrap:wrap; gap:10px; margin:8px 0 22px; }}
    .links a {{ color:#075985; text-decoration:none; background:#fff; border:1px solid var(--line); padding:8px 12px; }}
    .eval {{ background:#fff; border:1px solid var(--line); padding:16px 20px; margin:0 0 18px; }}
    .eval li {{ margin:8px 0; line-height:1.6; }}
    .eval-通过 {{ color:#15803d; margin-right:8px; }}
    .eval-关注 {{ color:#b45309; margin-right:8px; }}
    .chart-grid {{ display:grid; grid-template-columns:1fr; gap:18px; }}
    .chart-card {{ background:#fff; border:1px solid var(--line); padding:18px; }}
    .chart-card h2 {{ margin:0 0 12px; font-size:20px; }}
    .chart-card img {{ display:block; width:100%; height:auto; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; margin-top:22px; }}
    th, td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    td:nth-child(3), td:nth-child(4), td:nth-child(5) {{ text-align:right; white-space:nowrap; }}
    @media (max-width: 860px) {{ .summary {{ grid-template-columns:1fr 1fr; }} main, header {{ padding-left:18px; padding-right:18px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 双目标测试结果</h1>
    <p>本页由双目标运行结果自动生成。它把 QEMU 运行、状态文件对照、Host Reader 输出和阶段耗时整理成适合演示和复查的图表页面。</p>
    <div class="summary">
      <div class="metric"><strong>{fmt_number(as_number(state.get("plain_files")))}</strong><span>普通 uCore 状态文件</span></div>
      <div class="metric"><strong>{fmt_number(as_number(state.get("agentos_files")))}</strong><span>AgentOS 状态文件</span></div>
      <div class="metric"><strong>{fmt_number(as_number(reader.get("agentos_extra_api_json")))}</strong><span>AgentOS 额外 API JSON</span></div>
      <div class="metric"><strong>{agentos_result.get("qemu_idle_notices", "0")}</strong><span>AgentOS QEMU 无输出提示</span></div>
    </div>
  </header>
  <main>
    <div class="links">
      <a href="monitor.html">打开运行观测面板</a>
      <a href="report.md">查看 Markdown 报告</a>
      <a href="summary.csv">下载 CSV 明细</a>
      <a href="runner-sweep.csv">下载 runner 成组数据</a>
      <a href="chart-type-coverage.csv">下载图表类型覆盖表</a>
      <a href="charts/runtime-observation.svg">打开观测图</a>
      <a href="charts/scenario-evidence.svg">打开场景证据图</a>
      <a href="charts/cost-replacement.svg">打开成本替代图</a>
      <a href="charts/runner-ticks.svg">打开 tick 对照图</a>
      <a href="charts/runner-speedup.svg">打开相对倍数图</a>
      <a href="charts/runner-surface-composite.svg">打开组合图</a>
      <a href="charts/dual-target-state-reader.svg">打开状态图</a>
      <a href="charts/stage-timings.svg">打开阶段耗时图</a>
    </div>
    <p>录屏演示建议先运行 <code>make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-</code>，再运行 <code>make demo-reader</code> 打开交互页面。本页用于快速展示测试数据图表，Reader 页面用于查看完整运行对象和 AgentOS 主流程细节。</p>
    <section class="eval">
      <h2>自动判读</h2>
      <ul>{eval_html}</ul>
    </section>
    <div class="chart-grid">
      {"".join(chart_cards)}
    </div>
    <h2>阶段耗时明细</h2>
    <table>
      <thead><tr><th>阶段</th><th>秒数</th><th>状态</th></tr></thead>
      <tbody>{"".join(stage_rows_html) if stage_rows_html else '<tr><td colspan="3">本次结果没有记录阶段耗时。</td></tr>'}</tbody>
    </table>
    <p>阶段耗时用于定位运行问题。正常情况下，时间主要集中在预置请求双目标运行；如果其他阶段耗时异常，应优先查看对应脚本输出和日志文件。</p>
    <table>
      <thead><tr><th>分组</th><th>指标</th><th>普通 uCore</th><th>AgentOS-uCore</th><th>差值</th><th>说明</th></tr></thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
    <p>QEMU 运行诊断：普通目标耗时 {escape(str(plain_result.get("qemu_elapsed_seconds", "0")))} 秒，AgentOS 目标耗时 {escape(str(agentos_result.get("qemu_elapsed_seconds", "0")))} 秒。若结果异常，应先查看 <code>/tmp/agentos-dual-platform/stage-timings.csv</code> 和两个 <code>ucore-run.log</code>。</p>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def write_monitor_page(rows: list[MetricRow], meta: dict[str, object], charts: list[Path], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    observation = next((chart for chart in charts if chart.name == "runtime-observation.svg"), None)
    observation_rel = observation.relative_to(out_path.parent).as_posix() if observation is not None else ""
    eval_rows = "".join(
        f"<tr><td>{escape(status)}</td><td>{escape(text)}</td></tr>" for status, text in evaluation_items(meta)
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 运行观测面板</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; --accent:#f58518; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.7; }}
    main {{ max-width:1180px; margin:0 auto; padding:24px 42px 42px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); padding:14px; min-height:92px; }}
    .card strong {{ display:block; font-size:23px; margin-bottom:6px; }}
    .card span {{ color:var(--muted); font-size:13px; }}
    .panel {{ background:#fff; border:1px solid var(--line); padding:18px; margin-top:18px; }}
    .panel img {{ display:block; width:100%; height:auto; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; margin-top:12px; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    a {{ color:#075985; text-decoration:none; }}
    code {{ background:#eef3f8; padding:2px 5px; }}
    @media (max-width:860px) {{ .grid {{ grid-template-columns:1fr 1fr; }} main,header {{ padding-left:18px; padding-right:18px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 运行观测面板</h1>
    <p>这个页面面向演示和复查：它不替代完整 Reader，而是先把本次双目标运行是否健康、增强目标是否真的产生内核证据、页面/API 是否保留完整展示面讲清楚。</p>
  </header>
  <main>
    <div class="grid">
      <div class="card"><strong>{fmt_number(as_number(state.get("checked_success_records")))}</strong><span>成功记录核对数</span></div>
      <div class="card"><strong>{fmt_number(as_number(state.get("agentos_extra_files")))}</strong><span>AgentOS 额外状态文件</span></div>
      <div class="card"><strong>{fmt_number(as_number(state.get("agentos_evidence_checks")))}</strong><span>内核证据检查项</span></div>
      <div class="card"><strong>{fmt_number(as_number(reader.get("agentos_extra_api_json")))}</strong><span>AgentOS 额外 API JSON</span></div>
    </div>
    <section class="panel">
      <h2>一张图看本次运行</h2>
      <img src="{escape(observation_rel)}" alt="双目标运行观测面板">
    </section>
    <section class="panel">
      <h2>录屏建议</h2>
      <p>先运行 <code>make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-</code> 生成结果，再运行 <code>make demo-reader</code> 打开完整交互页面。录屏中可以先展示本页的运行观测图，再切到 Reader 的 AgentOS Compare、LLM Relay、运行详情和证据页面。</p>
      <p>如果双目标运行异常，本页会优先提示状态文件、QEMU 超时、无输出提示和阶段耗时。普通目标耗时 {escape(str(plain_result.get("qemu_elapsed_seconds", "0")))} 秒，AgentOS 目标耗时 {escape(str(agentos_result.get("qemu_elapsed_seconds", "0")))} 秒。</p>
    </section>
    <section class="panel">
      <h2>自动判读</h2>
      <table><thead><tr><th>状态</th><th>说明</th></tr></thead><tbody>{eval_rows}</tbody></table>
    </section>
    <section class="panel">
      <h2>相关结果</h2>
      <p><a href="index.html">图表索引页</a>、<a href="report.md">Markdown 报告</a>、<a href="summary.csv">CSV 明细</a>、<a href="runner-sweep.csv">runner 成组数据</a>、<a href="chart-type-coverage.csv">图表类型覆盖表</a>、<a href="charts/runtime-observation.svg">运行观测图</a>、<a href="charts/scenario-evidence.svg">场景证据图</a>、<a href="charts/cost-replacement.svg">成本替代图</a>、<a href="charts/runner-ticks.svg">tick 对照图</a>、<a href="charts/runner-speedup.svg">相对倍数图</a>、<a href="charts/runner-surface-composite.svg">组合图</a></p>
    </section>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def copy_docs_assets(charts: list[Path], docs_assets_dir: Path) -> None:
    docs_assets_dir.mkdir(parents=True, exist_ok=True)
    for chart in charts:
        shutil.copy2(chart, docs_assets_dir / chart.name)


def summarize(work_dir: Path, out_dir: Path, docs_assets_dir: Path | None = None) -> dict[str, object]:
    rows, meta = collect_rows(work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_dir / "summary.csv")
    write_runner_sweep_csv(meta, out_dir / "runner-sweep.csv")
    write_chart_type_coverage_csv(out_dir / "chart-type-coverage.csv")
    charts = write_charts(rows, meta, out_dir / "charts")
    write_report(rows, meta, charts, out_dir / "report.md")
    write_index(rows, meta, charts, out_dir / "index.html")
    write_monitor_page(rows, meta, charts, out_dir / "monitor.html")
    if docs_assets_dir is not None:
        copy_docs_assets(charts, docs_assets_dir)
    summary = {
        "status": "ready",
        "rows": len(rows),
        "charts": [str(path) for path in charts],
        "report": str(out_dir / "report.md"),
        "index": str(out_dir / "index.html"),
        "monitor": str(out_dir / "monitor.html"),
        "csv": str(out_dir / "summary.csv"),
        "runner_sweep_csv": str(out_dir / "runner-sweep.csv"),
        "chart_type_coverage_csv": str(out_dir / "chart-type-coverage.csv"),
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
        "dual_platform_result_summary: rows={rows} charts={charts} report={report} index={index} monitor={monitor} status={status}".format(
            rows=summary["rows"],
            charts=len(summary["charts"]),
            report=summary["report"],
            index=summary["index"],
            monitor=summary["monitor"],
            status=summary["status"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
