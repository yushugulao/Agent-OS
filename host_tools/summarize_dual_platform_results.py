#!/usr/bin/env python3
"""Build CSV, Markdown, and SVG summaries for dual-platform verification."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from measured_experiments import (
    MeasurementError,
    verify_manifest,
    write_csv as write_measurement_csv,
    write_manifest,
)


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
    "reader-render-check": "本地页面渲染与检查",
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
            "本地阅读器输出",
            "HTML 页面",
            as_number(reader.get("plain_pages")),
            as_number(reader.get("agentos_pages")),
            as_number(reader.get("agentos_pages")) - as_number(reader.get("plain_pages")),
            "页面集合应保持一致，避免增强目标缺少流程页面。",
        ),
        MetricRow(
            "本地阅读器输出",
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
            "状态兼容性",
            "非证据状态记录",
            as_number(state.get("checked_compatibility_records")),
            as_number(state.get("checked_compatibility_records")),
            0,
            "只检查两个目标的普通状态兼容性；demo/reference 与 runtime evidence 均被排除。",
        ),
        MetricRow(
            "参考目录",
            "参考产品与记录",
            as_number(state.get("plain_reference_products"))
            + as_number(state.get("plain_reference_records")),
            as_number(state.get("agentos_reference_products"))
            + as_number(state.get("agentos_reference_records")),
            as_number(state.get("agentos_reference_products"))
            + as_number(state.get("agentos_reference_records"))
            - as_number(state.get("plain_reference_products"))
            - as_number(state.get("plain_reference_records")),
            "这些记录仅描述 demo_expected 目录，不能作为动态通过或性能证据。",
        ),
        MetricRow(
            "运行证据",
            "来源绑定运行记录",
            None,
            as_number(state.get("source_bound_runtime_records")),
            as_number(state.get("source_bound_runtime_records")),
            "仅统计同时绑定 runtime generation、源文件字节数、哈希与实际断言的记录。",
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
            "非 Agent 启动记录",
            as_number(state.get("plain_fork_launches")),
            as_number(state.get("agentos_worker_launches")),
            as_number(state.get("agentos_worker_launches"))
            - as_number(state.get("plain_fork_launches")),
            "普通目标使用 fork；增强目标使用完成 exec 后身份校验的 delegated worker。",
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
    tests = meta.get("tests", {}) if isinstance(meta.get("tests"), dict) else {}
    items: list[tuple[str, str]] = []

    if state.get("status") == "ready" and as_number(state.get("run_result_match")) == 1:
        items.append(("通过", "两个目标运行结果可对照，普通状态兼容记录已核对；该结论不包含 demo/reference 目录。"))
    else:
        items.append(("关注", "双目标状态对照没有给出 ready/match 结果，需要查看 state-compare-summary.json。"))
    if (
        as_number(state.get("source_bound_runtime_records")) > 0
        and tests.get("runtime_evidence_verified") is True
    ):
        items.append(("通过", "来源绑定运行记录与 Host runtime assertion sets 均已独立校验。"))
    else:
        items.append(("关注", "缺少来源绑定运行记录或 Host runtime assertion 校验，不能把 reference 目录提升为动态通过证据。"))
    if as_number(state.get("agentos_extra_files")) > 0 and as_number(state.get("agentos_evidence_checks")) >= 20:
        items.append(("通过", "AgentOS target 额外输出内核证据文件，并通过多项内核事实检查。"))
    else:
        items.append(("关注", "AgentOS 额外内核证据不足，需要检查 rp_agentos_* 状态文件。"))
    if as_number(state.get("agentos_mainflow_stages")) >= 10:
        items.append(("通过", "科研主流程中记录到足够多的 AgentOS 内核参与阶段。"))
    else:
        items.append(("关注", "AgentOS 主流程阶段数量偏少，需要检查 rp_agentos_mainflow。"))
    if reader.get("status") == "ready" and as_number(reader.get("plain_pages")) == as_number(reader.get("agentos_pages")):
        items.append(("通过", "本地结果阅读器为两个目标生成同一套页面，增强目标保留完整页面集合。"))
    else:
        items.append(("关注", "本地结果阅读器页面集合或状态异常，需要查看 reader-summary.json。"))
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
        if len(current) >= max_chars and (char in "，；。,. /:-_" or len(current) >= max_chars + 8):
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
    lines.append('<text x="34" y="58" class="subtitle">用于定位构建、QEMU 运行、镜像提取、本地页面渲染或结果对照中的异常停顿。</text>')
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
    lines.append('<text x="34" y="58" class="subtitle">把运行阶段、产物数量、AgentOS 内核证据和 QEMU 健康状态放在同一张图中，便于快速判断本次测试是否可信。</text>')

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
            ("非证据状态兼容记录", fmt_number(as_number(state.get("checked_compatibility_records")))),
            ("来源绑定运行记录", fmt_number(as_number(state.get("source_bound_runtime_records")))),
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
            ("本地页面", fmt_number(as_number(reader.get("agentos_pages")))),
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
            ("增强目标委派worker", fmt_number(as_number(by_metric["非 Agent 启动记录"].agentos))),
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
        "复查读图顺序",
        [
            ("第一步", "看运行结果"),
            ("第二步", "看内核证据"),
            ("第三步", "打开本地页面"),
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


EXPERIMENT_SPECS = {
    "file_metadata": {
        "title": "文件查询真实路径对照",
        "plain_path": "Guest 强制遍历 metadata catalog，记录每次实际触达的可见记录。",
        "agentos_path": "Guest 分别测量包含全索引重建的冷索引和索引就绪后的热索引。",
        "mechanism": "每一行都绑定 Agent suite Guest 日志的 SHA256、行号、提交和运行标识；冷索引单列内核实际报告的重建记录数。",
        "raw_file": "file-query-benchmark.csv",
    },
}


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * ratio
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def experiment_rows(meta: dict[str, object]) -> list[dict[str, object]]:
    rows = meta.get("measured_experiment_rows", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("measured experiment rows are invalid")
    return [dict(row) for row in rows]


def write_experiment_raw_csvs(rows: list[dict[str, object]], out_dir: Path) -> None:
    raw_dir = out_dir / "experiments" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for experiment, spec in EXPERIMENT_SPECS.items():
        out_path = raw_dir / str(spec["raw_file"])
        write_measurement_csv(
            out_path,
            [row for row in rows if row.get("experiment") == experiment],
        )


def experiment_stats_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    keys = sorted({(str(row["experiment"]), int(as_number(row["load"])), str(row["path"])) for row in rows})
    for experiment, load, path in keys:
        subset = [
            row
            for row in rows
            if str(row.get("experiment")) == experiment and int(as_number(row.get("load"))) == load and str(row.get("path")) == path
        ]
        values = [as_number(row.get("primary_value")) for row in subset]
        durations = [as_number(row.get("duration_value")) for row in subset]
        duration_units = {str(row.get("duration_unit", "")) for row in subset}
        if duration_units != {"us"}:
            raise ValueError("measured file-query durations must use raw microseconds")
        result.append(
            {
                "experiment": experiment,
                "load": load,
                "path": path,
                "metric": subset[0].get("primary_metric", "") if subset else "",
                "unit": "count",
                "runs": len(subset),
                "min": min(values) if values else 0.0,
                "avg": statistics.fmean(values) if values else 0.0,
                "max": max(values) if values else 0.0,
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
                "duration_unit": "us",
                "duration_min": min(durations) if durations else 0.0,
                "duration_avg": statistics.fmean(durations) if durations else 0.0,
                "duration_max": max(durations) if durations else 0.0,
                "plain_path": EXPERIMENT_SPECS[experiment]["plain_path"],
                "agentos_path": EXPERIMENT_SPECS[experiment]["agentos_path"],
                "mechanism": EXPERIMENT_SPECS[experiment]["mechanism"],
            }
        )
    return result


def write_experiment_stats_csv(rows: list[dict[str, object]], out_path: Path) -> None:
    stats_rows = experiment_stats_rows(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "experiment",
        "load",
        "path",
        "metric",
        "unit",
        "runs",
        "min",
        "avg",
        "max",
        "p50",
        "p95",
        "duration_unit",
        "duration_min",
        "duration_avg",
        "duration_max",
        "plain_path",
        "agentos_path",
        "mechanism",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in stats_rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_experiment_mechanism_csv(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["experiment", "title", "plain_path", "agentos_path", "mechanism", "raw_csv"])
        for experiment, spec in EXPERIMENT_SPECS.items():
            writer.writerow(
                [
                    experiment,
                    spec["title"],
                    spec["plain_path"],
                    spec["agentos_path"],
                    spec["mechanism"],
                    f"experiments/raw/{spec['raw_file']}",
                ]
            )


def write_experiment_outputs(meta: dict[str, object], out_dir: Path) -> list[dict[str, object]]:
    rows = experiment_rows(meta)
    if not rows:
        return []
    write_experiment_raw_csvs(rows, out_dir)
    write_experiment_stats_csv(rows, out_dir / "experiments" / "experiment-stats.csv")
    write_experiment_mechanism_csv(out_dir / "experiments" / "mechanism-notes.csv")
    return rows


def write_experiment_status(meta: dict[str, object], out_dir: Path) -> dict[str, object]:
    rows = experiment_rows(meta)
    manifest = meta.get("measured_experiment_manifest")
    status: dict[str, object] = {
        "schema_version": 1,
        "status": "measured" if rows else "unavailable",
        "rows": len(rows),
        "reason": "provenance-bound Guest marker verified" if rows else "measured-experiments.json is missing",
    }
    if rows and isinstance(manifest, dict):
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        status["source_log"] = source.get("path")
        status["source_log_sha256"] = source.get("sha256")
        status["commit"] = manifest.get("commit")
        status["run_id"] = manifest.get("run_id")
    path = out_dir / "experiments" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return status


def median_experiment_value(rows: list[dict[str, object]], experiment: str, path: str, load: int) -> float:
    values = [
        as_number(row.get("primary_value"))
        for row in rows
        if row.get("experiment") == experiment and row.get("path") == path and int(as_number(row.get("load"))) == load
    ]
    return statistics.median(values) if values else 0.0


def experiment_file_bar_svg(rows: list[dict[str, object]], out_path: Path) -> None:
    loads = sorted(
        {int(as_number(row.get("load"))) for row in rows if row.get("experiment") == "file_metadata"}
    )
    if not loads:
        raise ValueError("file-query chart requires measured Guest rows")
    load = loads[-1]
    traversal = median_experiment_value(rows, "file_metadata", "traversal", load)
    cold_candidates = median_experiment_value(rows, "file_metadata", "cold_index", load)
    warm_candidates = median_experiment_value(rows, "file_metadata", "warm_index", load)
    cold_rebuild = statistics.median(
        [
            as_number(row.get("rebuild_records"))
            for row in rows
            if row.get("experiment") == "file_metadata"
            and row.get("path") == "cold_index"
            and int(as_number(row.get("load"))) == load
        ]
    )
    grouped_bar_svg(
        "真实 Guest 文件查询：遍历、冷索引与热索引",
        f"本轮可见记录 {load} 条；冷索引柱包含 {fmt_number(cold_rebuild)} 条内核实际报告的重建记录，热索引不含缓存命中。",
        ["冷索引（含重建）", "热索引"],
        [traversal, traversal],
        [cold_candidates + cold_rebuild, warm_candidates],
        out_path,
        y_label="实际检查记录数",
    )


def experiment_design_rows(meta: dict[str, object]) -> list[dict[str, str]]:
    measured = experiment_rows(meta)
    trials = len({int(as_number(row.get("trial"))) for row in measured})
    loads = sorted({int(as_number(row.get("load"))) for row in measured})
    return [
        {
            "scenario": "文件对象查询实测",
            "workload": "同一次 Guest 运行分别执行强制遍历、冷索引和热索引；不使用内核查询结果缓存。",
            "plain_path": EXPERIMENT_SPECS["file_metadata"]["plain_path"],
            "agentos_path": EXPERIMENT_SPECS["file_metadata"]["agentos_path"],
            "parameter": f"observed_loads={loads or 'none'}; measured_trials={trials}。",
            "metric": "每次查询实际触达记录、操作次数、Guest 原始微秒差值；冷索引另列内核实际报告的重建记录数。",
            "source": "experiments/raw/file-query-benchmark.csv, measured-experiments.json",
            "artifact": "charts/experiment-file-query-bar.svg" if measured else "not-measured",
        }
    ]


def write_experiment_design_csv(meta: dict[str, object], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scenario", "workload", "plain_path", "agentos_path", "parameter", "metric", "source", "artifact"])
        for row in experiment_design_rows(meta):
            writer.writerow(
                [
                    row["scenario"],
                    row["workload"],
                    row["plain_path"],
                    row["agentos_path"],
                    row["parameter"],
                    row["metric"],
                    row["source"],
                    row["artifact"],
                ]
            )


def write_experiment_design_page(meta: dict[str, object], out_path: Path) -> None:
    rows_html = []
    for row in experiment_design_rows(meta):
        artifact = row["artifact"]
        artifact_html = f'<a href="{escape(artifact)}">{escape(artifact)}</a>'
        rows_html.append(
            "<tr><td>{scenario}</td><td>{workload}</td><td>{plain_path}</td><td>{agentos_path}</td><td>{parameter}</td><td>{metric}</td><td>{source}</td><td>{artifact}</td></tr>".format(
                scenario=escape(row["scenario"]),
                workload=escape(row["workload"]),
                plain_path=escape(row["plain_path"]),
                agentos_path=escape(row["agentos_path"]),
                parameter=escape(row["parameter"]),
                metric=escape(row["metric"]),
                source=escape(row["source"]),
                artifact=artifact_html,
            )
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 实验场景说明</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1240px; margin:0 auto; padding:24px 42px 42px; }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.75; color:var(--muted); }}
    .links {{ display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }}
    .links a {{ color:#075985; text-decoration:none; background:#fff; border:1px solid var(--line); padding:8px 12px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; font-size:14px; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    a {{ color:#075985; text-decoration:none; }}
    @media (max-width:900px) {{ main,header {{ padding-left:18px; padding-right:18px; }} table {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 实验场景说明</h1>
    <p>本页说明本次双目标测试中每类场景的负载、对照路径、参数、指标和数据来源。它用于解释图表数字从何而来，以及为什么这些测试能说明 AgentOS 主流程效果。</p>
  </header>
  <main>
    <div class="links">
      <a href="experiment-design.csv">下载 CSV</a>
      <a href="reader-guide.html">运行导览页</a>
      <a href="reader-checklist.html">结果核验表</a>
      <a href="index.html">图表索引页</a>
    </div>
    <table>
      <thead><tr><th>场景</th><th>负载设计</th><th>普通目标路径</th><th>AgentOS 目标路径</th><th>参数</th><th>指标</th><th>数据来源</th><th>关联产物</th></tr></thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


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
        "横向条来自 runner-sweep.csv，表示 AgentOS 相对普通路径的 tick 倍数和节省量。",
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


def value_stats(values: list[float]) -> tuple[int, float, float, float]:
    if not values:
        return (0, 0.0, 0.0, 0.0)
    return (len(values), min(values), sum(values) / len(values), max(values))


def runner_statistics_rows(meta: dict[str, object]) -> list[dict[str, object]]:
    rows = runner_tick_rows(meta)
    values = {
        "普通路径 tick": [as_number(row.get("plain_ticks")) for row in rows],
        "AgentOS 路径 tick": [as_number(row.get("agentos_ticks")) for row in rows],
        "节省 tick": [as_number(row.get("saved_ticks")) for row in rows],
        "相对倍数": [as_number(row.get("speedup_x100")) / 100.0 for row in rows],
    }
    units = {
        "普通路径 tick": "tick",
        "AgentOS 路径 tick": "tick",
        "节省 tick": "tick",
        "相对倍数": "x",
    }
    reading = {
        "普通路径 tick": "普通用户态路径完成同一 runner 场景的 tick 分布。",
        "AgentOS 路径 tick": "AgentOS 路径完成同一 runner 场景的 tick 分布。",
        "节省 tick": "普通路径 tick 减去 AgentOS 路径 tick。",
        "相对倍数": "普通路径 tick 除以 AgentOS 路径 tick；大于 1 表示 AgentOS 使用更少 tick。",
    }
    result: list[dict[str, object]] = []
    for metric, metric_values in values.items():
        count, min_value, avg_value, max_value = value_stats(metric_values)
        result.append(
            {
                "metric": metric,
                "count": count,
                "min": min_value,
                "avg": avg_value,
                "max": max_value,
                "unit": units[metric],
                "source": "runner-sweep.csv",
                "reading": reading[metric],
            }
        )
    return result


def write_runner_statistics_csv(meta: dict[str, object], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "count", "min", "avg", "max", "unit", "source", "reading"])
        for row in runner_statistics_rows(meta):
            writer.writerow(
                [
                    row["metric"],
                    row["count"],
                    fmt_number(as_number(row["min"])),
                    fmt_number(as_number(row["avg"])),
                    fmt_number(as_number(row["max"])),
                    row["unit"],
                    row["source"],
                    row["reading"],
                ]
            )


def test_suite_rows() -> list[dict[str, str]]:
    return [
        {
            "level": "主运行路径",
            "command": "make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-",
            "qemu": "是",
            "purpose": "运行 plain uCore 与 AgentOS-uCore 两个目标，生成状态文件、CSV、HTML 和 SVG 图表。",
            "when_to_use": "完整复查前必须运行。",
            "main_output": "results/latest/",
        },
        {
            "level": "主运行路径",
            "command": "make reader",
            "qemu": "否",
            "purpose": "启动本地结果阅读器，把科研平台页面、双目标结果页和运行导览页放在一个浏览器服务里。",
            "when_to_use": "双目标结果生成后立即运行。",
            "main_output": "http://127.0.0.1:8767/",
        },
        {
            "level": "日常快速检查",
            "command": "make target-readiness",
            "qemu": "否",
            "purpose": "检查目录职责、双目标结构、Host 工具契约、本地阅读器输出和图表生成逻辑。",
            "when_to_use": "修改文档、Host 工具、结果页或脚本后运行。",
            "main_output": "终端通过标记",
        },
        {
            "level": "完整验证",
            "command": "make full-verify TOOLPREFIX=riscv64-linux-gnu-",
            "qemu": "是",
            "purpose": "串联结构检查、Host 工具检查、双目标 QEMU、本地页面渲染和 AgentOS 内核专项测试。",
            "when_to_use": "最终审查前运行。",
            "main_output": "终端输出和 results/latest/",
        },
        {
            "level": "AgentOS 内核专项",
            "command": "TOOLPREFIX=riscv64-linux-gnu- QEMU=qemu-system-riscv64 CASE_TIMEOUT=240s bash scripts/run-agent-tests.sh",
            "qemu": "是",
            "purpose": "在 AgentOS-uCore 目标中运行 Agent Context、文件对象、事件队列、权限、LLM relay 等专项程序。",
            "when_to_use": "内核、用户程序或 syscall ABI 改动后运行。",
            "main_output": "AgentOS 专项测试通过标记",
        },
        {
            "level": "LLM 模式契约",
            "command": "bash -lc 'python3 host_tools/test_llm_relay_mode_contract.py'",
            "qemu": "否",
            "purpose": "验证无密钥 default 模式和外部 DeepSeek key 文件模式，不把密钥写入仓库或结果文件。",
            "when_to_use": "修改 LLM Relay、环境变量或密钥读取逻辑后运行。",
            "main_output": "test_llm_relay_mode_contract: passed",
        },
        {
            "level": "图表可读性检查",
            "command": "bash -lc 'python3 host_tools/test_chart_svg_layout_contract.py'",
            "qemu": "否",
            "purpose": "解析生成后的 SVG 和文档示例图，检查文字是否在画布内以及是否明显互相压住。",
            "when_to_use": "修改图表生成逻辑或文档示例图后运行。",
            "main_output": "test_chart_svg_layout_contract: passed",
        },
    ]


def write_test_suite_csv(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["level", "command", "qemu", "purpose", "when_to_use", "main_output"])
        for row in test_suite_rows():
            writer.writerow([row["level"], row["command"], row["qemu"], row["purpose"], row["when_to_use"], row["main_output"]])


def write_test_suite_page(out_path: Path) -> None:
    rows_html = []
    for row in test_suite_rows():
        rows_html.append(
            "<tr><td>{level}</td><td><code>{command}</code></td><td>{qemu}</td><td>{purpose}</td><td>{when_to_use}</td><td>{main_output}</td></tr>".format(
                level=escape(row["level"]),
                command=escape(row["command"]),
                qemu=escape(row["qemu"]),
                purpose=escape(row["purpose"]),
                when_to_use=escape(row["when_to_use"]),
                main_output=escape(row["main_output"]),
            )
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 测试入口说明</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1240px; margin:0 auto; padding:24px 42px 42px; }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.75; color:var(--muted); }}
    .links {{ display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }}
    .links a {{ color:#075985; text-decoration:none; background:#fff; border:1px solid var(--line); padding:8px 12px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; font-size:14px; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    code {{ white-space:normal; overflow-wrap:anywhere; }}
    @media (max-width:900px) {{ main,header {{ padding-left:18px; padding-right:18px; }} table {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 测试入口说明</h1>
    <p>本页把主运行路径、快速检查、完整验证和专项测试分开说明。日常复查优先使用前两条命令；深入检查时再运行完整验证和专项测试。</p>
  </header>
  <main>
    <div class="links">
      <a href="test-suite.csv">下载 CSV</a>
      <a href="reader-guide.html">运行导览页</a>
      <a href="reader-checklist.html">结果核验表</a>
      <a href="experiment-design.html">实验场景说明</a>
    </div>
    <table>
      <thead><tr><th>用途层级</th><th>命令</th><th>启动 QEMU</th><th>检查内容</th><th>使用时机</th><th>主要输出</th></tr></thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def delivery_readiness_rows() -> list[dict[str, str]]:
    return [
        {
            "requirement": "带数据的测试结果图表化",
            "status": "已覆盖",
            "evidence": "index.html; charts/*.svg; experiments/status.json",
            "verification": "test_chart_type_data_contract.py",
            "note": "实验数据仅从可追溯 Guest marker 提取；缺失时明确标记 unavailable，不生成填充数据。",
        },
        {
            "requirement": "图表文字不互相遮挡",
            "status": "已覆盖",
            "evidence": "charts/*.svg; docs/assets/verification-charts/*.svg",
            "verification": "test_chart_svg_layout_contract.py",
            "note": "测试解析 SVG 文本框，检查画布范围和明显相交问题。",
        },
        {
            "requirement": "DeepSeek v4 pro 优先且默认不访问云端",
            "status": "已覆盖",
            "evidence": "test-suite.html; README.md",
            "verification": "test_llm_relay_mode_contract.py",
            "note": "无外部密钥时走 template 模式；外部 key 文件只由宿主机 Relay 读取。",
        },
        {
            "requirement": "常用运行只需要少量命令",
            "status": "已覆盖",
            "evidence": "reader-guide.html; reader-url-list.txt; dual-results.html",
            "verification": "test_plain_ucore_reader.py",
            "note": "推荐路径仍是 make dual-platform-run 与 make reader。",
        },
        {
            "requirement": "测试入口清晰，不堆叠旧测试",
            "status": "已覆盖",
            "evidence": "test-suite.html; test-suite.csv",
            "verification": "test_summarize_dual_platform_results.py",
            "note": "主运行路径、快速检查、完整验证和专项测试分开说明。",
        },
        {
            "requirement": "实验场景、负载、对照和指标清楚",
            "status": "已覆盖",
            "evidence": "experiment-design.html; experiment-design.csv; experiments/mechanism-notes.csv",
            "verification": "test_summarize_dual_platform_results.py",
            "note": "每组实验都列出普通路径、AgentOS 路径、参数、指标、原始数据和机制解释。",
        },
        {
            "requirement": "结果能追溯到原始数据",
            "status": "已覆盖",
            "evidence": "evidence-map.html; evidence-manifest.csv",
            "verification": "test_summarize_dual_platform_results.py",
            "note": "图表、CSV、报告和复查用途都有索引。",
        },
        {
            "requirement": "同时覆盖功能状态和性能观测",
            "status": "已覆盖",
            "evidence": "runtime-observation.svg; cost-replacement.svg; runner-speedup.svg; experiments/status.json",
            "verification": "test_chart_type_data_contract.py",
            "note": "运行观测覆盖功能状态；文件查询性能仅在真实 Guest 测量可用时生成。",
        },
        {
            "requirement": "文档避免空泛待办描述",
            "status": "已覆盖",
            "evidence": "README.md; docs/; docs/agentos/",
            "verification": "verify-dual-target-structure.sh",
            "note": "结构检查包含文档措辞扫描，不把开发过程写入仓库文档。",
        },
    ]


def write_delivery_readiness_csv(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["requirement", "status", "evidence", "verification", "note"])
        for row in delivery_readiness_rows():
            writer.writerow([row["requirement"], row["status"], row["evidence"], row["verification"], row["note"]])


def write_delivery_readiness_page(out_path: Path) -> None:
    rows_html = []
    for row in delivery_readiness_rows():
        rows_html.append(
            "<tr><td>{requirement}</td><td>{status}</td><td>{evidence}</td><td>{verification}</td><td>{note}</td></tr>".format(
                requirement=escape(row["requirement"]),
                status=escape(row["status"]),
                evidence=escape(row["evidence"]),
                verification=escape(row["verification"]),
                note=escape(row["note"]),
            )
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 结果材料核对</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; --ok:#166534; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1240px; margin:0 auto; padding:24px 42px 42px; }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.75; color:var(--muted); }}
    .links {{ display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }}
    .links a {{ color:#075985; text-decoration:none; background:#fff; border:1px solid var(--line); padding:8px 12px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; font-size:14px; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    td:nth-child(2) {{ color:var(--ok); font-weight:700; white-space:nowrap; }}
    @media (max-width:900px) {{ main,header {{ padding-left:18px; padding-right:18px; }} table {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 结果材料核对</h1>
    <p>本页把测试、图表、LLM Relay、文档和结果材料的关键要求对应到当前结果产物和验证脚本。它用于运行结束后快速确认结果是否齐全。</p>
  </header>
  <main>
    <div class="links">
      <a href="delivery-readiness.csv">下载 CSV</a>
      <a href="reader-guide.html">运行导览页</a>
      <a href="test-suite.html">测试入口说明</a>
      <a href="evidence-map.html">证据索引页</a>
    </div>
    <table>
      <thead><tr><th>要求</th><th>状态</th><th>证据产物</th><th>验证脚本</th><th>说明</th></tr></thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def chart_evidence_description(chart_name: str) -> tuple[str, str]:
    descriptions = {
        "runtime-observation.svg": ("summary.csv, stage-timings.csv", "运行健康、状态产物、内核事实和 QEMU 诊断"),
        "cost-replacement.svg": ("state-compare-summary.json:cost_replacements", "普通用户态成本项与 AgentOS 替代机制的配对"),
        "runner-ticks.svg": ("state-compare-summary.json:runner_tick_comparison", "同一 runner 场景下两条路径的 tick 对照"),
        "runner-speedup.svg": ("runner-sweep.csv", "runner 场景的相对倍数和节省 tick"),
        "experiment-file-query-bar.svg": ("experiments/raw/file-query-benchmark.csv", "同一真实 Guest 运行中的强制遍历、冷索引重建和热索引查询"),
    }
    return descriptions.get(chart_name, ("summary.csv", "双目标运行派生图表"))


def evidence_manifest_rows(charts: list[Path]) -> list[dict[str, str]]:
    rows = [
        {
            "artifact": "reader-guide.html",
            "kind": "运行页面",
            "source": "summary.json, charts/*.svg",
            "proves": "两条命令和建议查看顺序已经整理为一个可打开页面",
            "reader_use": "复查时从这里开始",
        },
        {
            "artifact": "test-suite.html",
            "kind": "说明页面",
            "source": "Makefile, scripts, host_tools tests",
            "proves": "主运行路径、快速检查、完整验证和专项测试入口已经区分清楚",
            "reader_use": "说明应该运行哪些命令",
        },
        {
            "artifact": "delivery-readiness.html",
            "kind": "核对页面",
            "source": "delivery_readiness_rows",
            "proves": "结果材料要求已经对应到证据产物和验证脚本",
            "reader_use": "运行结束后快速核对结果完整性",
        },
        {
            "artifact": "delivery-readiness.csv",
            "kind": "数据表",
            "source": "delivery_readiness_rows",
            "proves": "结果材料核对结果可以复制和脚本复查",
            "reader_use": "结果材料核对表",
        },
        {
            "artifact": "test-suite.csv",
            "kind": "数据表",
            "source": "test_suite_rows",
            "proves": "测试入口说明可以被脚本复查和复制",
            "reader_use": "测试入口表",
        },
        {
            "artifact": "experiment-design.html",
            "kind": "说明页面",
            "source": "state-compare-summary.json, reader-compare-summary.json, runner-sweep.csv, stage-timings.csv",
            "proves": "每类测试场景都有负载、对照路径、参数、指标和数据来源",
            "reader_use": "解释测试为什么这样设计",
        },
        {
            "artifact": "experiment-design.csv",
            "kind": "数据表",
            "source": "experiment_design_rows",
            "proves": "实验场景说明可以被脚本复查和复制",
            "reader_use": "测试设计表",
        },
        {
            "artifact": "monitor.html",
            "kind": "观测页面",
            "source": "summary.csv, stage-timings.csv, rp_host_run_result",
            "proves": "本次运行是否健康、AgentOS 是否有额外内核事实",
            "reader_use": "先确认运行可信度",
        },
        {
            "artifact": "index.html",
            "kind": "图表页面",
            "source": "summary.csv, runner-sweep.csv, experiments/status.json",
            "proves": "测试数据已生成图表并可集中查看",
            "reader_use": "查看图表总览",
        },
        {
            "artifact": "report.md",
            "kind": "文字报告",
            "source": "summary.csv, state-compare-summary.json, reader-compare-summary.json",
            "proves": "关键结论、明细表和机制说明可文字复查",
            "reader_use": "复查材料",
        },
        {
            "artifact": "summary.csv",
            "kind": "数据表",
            "source": "QEMU 状态文件、本地页面摘要、状态对照摘要",
            "proves": "双目标状态、页面、API、QEMU 诊断等基础指标",
            "reader_use": "复查图表基础数字",
        },
        {
            "artifact": "runner-sweep.csv",
            "kind": "数据表",
            "source": "state-compare-summary.json:runner_tick_comparison",
            "proves": "runner 场景、tick、节省 tick 和相对倍数",
            "reader_use": "复查性能趋势图",
        },
        {
            "artifact": "experiments/status.json",
            "kind": "测量状态",
            "source": "measured-experiments.json",
            "proves": "真实 Guest 测量是否可用；缺 marker 时不会生成填充数据",
            "reader_use": "先确认性能数据是否可用于结论",
        },
        {
            "artifact": "summary.json",
            "kind": "机器摘要",
            "source": "summarize_dual_platform_results.py",
            "proves": "生成器返回的核心产物路径和状态",
            "reader_use": "脚本复查入口",
        },
    ]
    if any(chart.name == "experiment-file-query-bar.svg" for chart in charts):
        rows.extend([
            {
                "artifact": "experiments/experiment-stats.csv",
                "kind": "数据表",
                "source": "experiments/raw/file-query-benchmark.csv",
                "proves": "真实文件查询测量的 min、avg、max、P50、P95 和 tick 统计",
                "reader_use": "复查统计聚合",
            },
            {
                "artifact": "experiments/mechanism-notes.csv",
                "kind": "数据表",
                "source": "EXPERIMENT_SPECS",
                "proves": "强制遍历、冷索引和热索引的测量边界",
                "reader_use": "解释对照路径",
            },
            {
                "artifact": "experiments/raw/file-query-benchmark.csv",
                "kind": "原始数据表",
                "source": "measured-experiments.json + Guest log SHA256",
                "proves": "逐行绑定 Guest 日志、提交、命令和运行标识的原始测量",
                "reader_use": "复查图表输入和来源",
            },
        ])
    for chart in charts:
        source, proves = chart_evidence_description(chart.name)
        rows.append(
            {
                "artifact": f"charts/{chart.name}",
                "kind": "SVG 图表",
                "source": source,
                "proves": proves,
                "reader_use": "图表页和导览页查看",
            }
        )
    return rows


def write_evidence_manifest_csv(charts: list[Path], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["artifact", "kind", "source", "proves", "reader_use"])
        for row in evidence_manifest_rows(charts):
            writer.writerow([row["artifact"], row["kind"], row["source"], row["proves"], row["reader_use"]])


def write_evidence_map_page(charts: list[Path], out_path: Path) -> None:
    display_names = {
        "reader-guide.html": "运行导览页",
        "reader-checklist.html": "结果核验表",
        "reader-checklist.csv": "结果核验 CSV",
        "delivery-readiness.html": "结果材料核对",
        "delivery-readiness.csv": "结果核对 CSV",
        "test-suite.html": "测试入口说明",
        "experiment-design.html": "实验场景说明",
        "evidence-map.html": "证据索引页",
        "index.html": "图表索引页",
        "monitor.html": "运行观测面板",
    }
    rows_html = []
    for row in evidence_manifest_rows(charts):
        artifact = row["artifact"]
        link = artifact if artifact.endswith((".html", ".md", ".csv", ".json", ".svg")) else ""
        display_name = display_names.get(artifact, artifact)
        artifact_html = f'<a href="{escape(link)}">{escape(display_name)}</a>' if link else escape(display_name)
        rows_html.append(
            "<tr><td>{artifact}</td><td>{kind}</td><td>{source}</td><td>{proves}</td><td>{reader_use}</td></tr>".format(
                artifact=artifact_html,
                kind=escape(row["kind"]),
                source=escape(row["source"]),
                proves=escape(row["proves"]),
                reader_use=escape(row["reader_use"]),
            )
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 证据索引</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1180px; margin:0 auto; padding:24px 42px 42px; }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.75; color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    a {{ color:#075985; text-decoration:none; }}
    @media (max-width:860px) {{ main,header {{ padding-left:18px; padding-right:18px; }} table {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 证据索引</h1>
    <p>本页列出本次双目标结果目录中的主要产物、数据来源、说明内容和复查用途。每个可打开的文件都保留为相对链接。</p>
  </header>
  <main>
    <table>
      <thead><tr><th>产物</th><th>类型</th><th>数据来源</th><th>说明内容</th><th>复查用途</th></tr></thead>
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def reader_checklist_rows(meta: dict[str, object], charts: list[Path], work_dir: Path) -> list[dict[str, str]]:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    chart_names = {chart.name for chart in charts}
    required_charts = {
        "runtime-observation.svg",
        "cost-replacement.svg",
        "runner-ticks.svg",
        "runner-speedup.svg",
    }
    if experiment_rows(meta):
        required_charts.add("experiment-file-query-bar.svg")

    def row(item: str, status: bool, evidence: str, action: str) -> dict[str, str]:
        return {
            "item": item,
            "status": "通过" if status else "关注",
            "evidence": evidence,
            "action": action,
        }

    return [
        row(
            "双目标结果",
            state.get("status") == "ready" and as_number(state.get("run_result_match")) == 1,
            f"state_status={state.get('status', '')}; run_result_match={fmt_number(as_number(state.get('run_result_match')))}",
            "先确认两个目标使用同一批输入，且普通目标成功记录在 AgentOS 目标中保留。",
        ),
        row(
            "本地阅读器页面与 API",
            reader.get("status") == "ready"
            and as_number(reader.get("plain_pages")) == as_number(reader.get("agentos_pages"))
            and as_number(reader.get("agentos_api_json")) >= as_number(reader.get("plain_api_json")),
            "plain_pages={}; agentos_pages={}; plain_api={}; agentos_api={}".format(
                fmt_number(as_number(reader.get("plain_pages"))),
                fmt_number(as_number(reader.get("agentos_pages"))),
                fmt_number(as_number(reader.get("plain_api_json"))),
                fmt_number(as_number(reader.get("agentos_api_json"))),
            ),
            "打开 本地阅读器页面时先看两个目标页面集合是否一致，再看 AgentOS 额外 API 证据。",
        ),
        row(
            "QEMU 运行状态",
            as_number(plain_result.get("qemu_timed_out")) == 0
            and as_number(agentos_result.get("qemu_timed_out")) == 0
            and as_number(agentos_result.get("qemu_idle_notices")) <= 1,
            "plain_timeout={}; agentos_timeout={}; agentos_idle_notices={}".format(
                fmt_number(as_number(plain_result.get("qemu_timed_out"))),
                fmt_number(as_number(agentos_result.get("qemu_timed_out"))),
                fmt_number(as_number(agentos_result.get("qemu_idle_notices"))),
            ),
            "如果这里需要关注，先查看两个 ucore-run.log 和 stage-timings.csv。",
        ),
        row(
            "核心图表",
            required_charts.issubset(chart_names),
            f"required={len(required_charts)}; generated={len(required_charts & chart_names)}; total_charts={len(chart_names)}",
            "复查时至少查看运行观测、成本替代和 runner 对照；有真实 Guest 数据时再查看文件查询图。",
        ),
        row(
            "证据索引",
            True,
            "evidence-map.html; evidence-manifest.csv",
            "从证据索引页返回每个图表和 CSV 的数据来源。",
        ),
        row(
            "运行入口",
            True,
            "reader-guide.html; monitor.html; index.html",
            "复查建议先打开运行导览页，再进入观测面板和图表页。",
        ),
        row(
            "原始运行状态",
            (work_dir / "plain-state" / "rp_host_run_result").is_file()
            and (work_dir / "agentos-state" / "rp_host_run_result").is_file()
            and (work_dir / "stage-timings.csv").is_file(),
            "plain-state/rp_host_run_result; agentos-state/rp_host_run_result; stage-timings.csv",
            "出现争议时直接回到原始状态文件和阶段耗时表。",
        ),
        row(
            "AgentOS 主流程证据",
            as_number(state.get("agentos_evidence_checks")) >= 20 and as_number(state.get("agentos_mainflow_stages")) >= 10,
            "agentos_evidence_checks={}; agentos_mainflow_stages={}".format(
                fmt_number(as_number(state.get("agentos_evidence_checks"))),
                fmt_number(as_number(state.get("agentos_mainflow_stages"))),
            ),
            "科研流程不是旁路测试，它在主流程中使用 AgentOS 机制。",
        ),
    ]


def write_reader_checklist_csv(meta: dict[str, object], charts: list[Path], work_dir: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["item", "status", "evidence", "action"])
        for row in reader_checklist_rows(meta, charts, work_dir):
            writer.writerow([row["item"], row["status"], row["evidence"], row["action"]])


def write_reader_checklist_page(meta: dict[str, object], charts: list[Path], work_dir: Path, out_path: Path) -> None:
    rows = reader_checklist_rows(meta, charts, work_dir)
    ready_count = sum(1 for row in rows if row["status"] == "通过")
    row_html = []
    for row in rows:
        class_name = "ok" if row["status"] == "通过" else "warn"
        row_html.append(
            "<tr class=\"{class_name}\"><td>{item}</td><td>{status}</td><td>{evidence}</td><td>{action}</td></tr>".format(
                class_name=class_name,
                item=escape(row["item"]),
                status=escape(row["status"]),
                evidence=escape(row["evidence"]),
                action=escape(row["action"]),
            )
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 结果核验表</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; --ok:#166534; --warn:#9a3412; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1180px; margin:0 auto; padding:24px 42px 42px; }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ line-height:1.75; color:var(--muted); }}
    .summary {{ display:flex; gap:12px; flex-wrap:wrap; margin:18px 0; }}
    .pill {{ border:1px solid var(--line); background:#fff; padding:10px 14px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    tr.ok td:nth-child(2) {{ color:var(--ok); font-weight:700; }}
    tr.warn td:nth-child(2) {{ color:var(--warn); font-weight:700; }}
    a {{ color:#075985; text-decoration:none; }}
    @media (max-width:860px) {{ main,header {{ padding-left:18px; padding-right:18px; }} table {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 结果核验表</h1>
    <p>本页用于确认本次双目标结果是否完整可复查。它只读取本次运行生成的数据，不替代原始日志和测试脚本。</p>
  </header>
  <main>
    <div class="summary">
      <div class="pill">通过项：{ready_count} / {len(rows)}</div>
      <div class="pill"><a href="reader-guide.html">运行导览页</a></div>
      <div class="pill"><a href="evidence-map.html">证据索引页</a></div>
      <div class="pill"><a href="reader-checklist.csv">下载 CSV</a></div>
    </div>
    <table>
      <thead><tr><th>检查项</th><th>状态</th><th>证据</th><th>查看动作</th></tr></thead>
      <tbody>{"".join(row_html)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def write_charts(rows: list[MetricRow], meta: dict[str, object], charts_dir: Path) -> list[Path]:
    charts_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    observation_chart = charts_dir / "runtime-observation.svg"
    runtime_observation_svg(rows, meta, observation_chart)
    paths.append(observation_chart)

    cost_chart = charts_dir / "cost-replacement.svg"
    cost_replacement_svg(meta, cost_chart)
    paths.append(cost_chart)

    runner_tick_chart = charts_dir / "runner-ticks.svg"
    runner_tick_svg(meta, runner_tick_chart)
    paths.append(runner_tick_chart)

    runner_speedup_chart = charts_dir / "runner-speedup.svg"
    runner_speedup_svg(meta, runner_speedup_chart)
    paths.append(runner_speedup_chart)

    experiment_data = experiment_rows(meta)
    if experiment_data:
        file_chart = charts_dir / "experiment-file-query-bar.svg"
        experiment_file_bar_svg(experiment_data, file_chart)
        paths.append(file_chart)

    return paths


def write_report(rows: list[MetricRow], meta: dict[str, object], charts: list[Path], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}

    lines = [
        "# 双目标运行结果摘要",
        "",
        "本报告由双目标运行脚本在验证结束后生成，数据来自 QEMU 运行日志、文件系统镜像提取结果、本地页面渲染摘要和状态文件对照结果。",
        "",
        "## 关键结论",
        "",
        f"- 普通 uCore 提取状态文件 {fmt_number(as_number(state.get('plain_files')))} 个，AgentOS-uCore 提取 {fmt_number(as_number(state.get('agentos_files')))} 个，其中 AgentOS 额外状态文件 {fmt_number(as_number(state.get('agentos_extra_files')))} 个。",
        f"- 本地结果阅读器生成页面 {fmt_number(as_number(reader.get('plain_pages')))} 个，两个目标页面集合一致；AgentOS API JSON 比普通目标多 {fmt_number(as_number(reader.get('agentos_extra_api_json')))} 个。",
        f"- 同一批预置 action 请求数为 {fmt_number(as_number(state.get('embedded_action_records')))}；另核对 {fmt_number(as_number(state.get('checked_compatibility_records')))} 条非证据状态兼容记录，并识别 {fmt_number(as_number(state.get('source_bound_runtime_records')))} 条来源绑定运行记录。",
        f"- AgentOS 主流程中记录到 {fmt_number(as_number(state.get('agentos_mainflow_stages')))} 个内核参与阶段和 {fmt_number(as_number(state.get('agentos_mainflow_facts')))} 条主流程事实。",
        f"- QEMU 诊断：普通目标耗时 {plain_result.get('qemu_elapsed_seconds', '0')} 秒、无输出提示 {plain_result.get('qemu_idle_notices', '0')} 次；AgentOS 目标耗时 {agentos_result.get('qemu_elapsed_seconds', '0')} 秒、无输出提示 {agentos_result.get('qemu_idle_notices', '0')} 次。",
        "",
        "## 图表",
        "",
    ]
    for chart in charts:
        lines.append(f"- `{chart.relative_to(out_path.parent.parent).as_posix()}`")
    lines.append("- `runner-sweep.csv`")
    lines.append("- `experiments/status.json`")
    if experiment_rows(meta):
        lines.append("- `experiments/experiment-stats.csv`")
        lines.append("- `experiments/mechanism-notes.csv`")
        for spec in EXPERIMENT_SPECS.values():
            lines.append(f"- `experiments/raw/{spec['raw_file']}`")
    lines.append("- `delivery-readiness.csv`")
    lines.append("- `delivery-readiness.html`")
    lines.append("- `test-suite.csv`")
    lines.append("- `test-suite.html`")
    lines.append("- `evidence-manifest.csv`")
    lines.append("- `evidence-map.html`")
    lines.append("- `reader-checklist.csv`")
    lines.append("- `reader-checklist.html`")
    lines.append("- `experiment-design.csv`")
    lines.append("- `experiment-design.html`")
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
    exp_stats = experiment_stats_rows(experiment_rows(meta))
    if exp_stats:
        lines.extend(
            [
                "",
                "## 真实 Guest 文件查询统计",
                "",
                "| 实验 | 负载 | 路径 | 指标 | min | avg | max | P50 | P95 |",
                "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in exp_stats:
            lines.append(
                "| {experiment} | {load} | {path} | {metric} | {min_value} | {avg_value} | {max_value} | {p50} | {p95} |".format(
                    experiment=row.get("experiment", ""),
                    load=fmt_number(as_number(row.get("load"))),
                    path=row.get("path", ""),
                    metric=row.get("metric", ""),
                    min_value=fmt_number(as_number(row.get("min"))),
                    avg_value=fmt_number(as_number(row.get("avg"))),
                    max_value=fmt_number(as_number(row.get("max"))),
                    p50=fmt_number(as_number(row.get("p50"))),
                    p95=fmt_number(as_number(row.get("p95"))),
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
        "runtime-observation.svg": "双目标运行观测面板",
        "cost-replacement.svg": "用户态成本项与 AgentOS 替代机制",
        "runner-ticks.svg": "Runner Tick 对照",
        "runner-speedup.svg": "Runner 成组场景相对倍数",
        "experiment-file-query-bar.svg": "文件对象查询实验",
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
    experiment_links = (
        '<a href="experiments/experiment-stats.csv">下载真实实验统计</a>'
        '<a href="experiments/mechanism-notes.csv">下载测量边界说明</a>'
        '<a href="charts/experiment-file-query-bar.svg">打开文件查询实测图</a>'
        if experiment_rows(meta)
        else '<a href="experiments/status.json">实验测量 unavailable</a>'
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
    <p>本页由双目标运行结果自动生成。它把 QEMU 运行、状态文件对照、本地阅读器输出和阶段耗时整理成便于复查的图表页面。</p>
    <div class="summary">
      <div class="metric"><strong>{fmt_number(as_number(state.get("plain_files")))}</strong><span>普通 uCore 状态文件</span></div>
      <div class="metric"><strong>{fmt_number(as_number(state.get("agentos_files")))}</strong><span>AgentOS 状态文件</span></div>
      <div class="metric"><strong>{fmt_number(as_number(reader.get("agentos_extra_api_json")))}</strong><span>AgentOS 额外 API JSON</span></div>
      <div class="metric"><strong>{agentos_result.get("qemu_idle_notices", "0")}</strong><span>AgentOS QEMU 无输出提示</span></div>
    </div>
  </header>
  <main>
    <div class="links">
      <a href="reader-guide.html">打开运行导览页</a>
      <a href="monitor.html">打开运行观测面板</a>
      <a href="report.md">查看 Markdown 报告</a>
      <a href="summary.csv">下载 CSV 明细</a>
      <a href="runner-sweep.csv">下载 runner 成组数据</a>
      <a href="delivery-readiness.html">打开结果材料核对</a>
      <a href="delivery-readiness.csv">下载结果材料核对</a>
      <a href="test-suite.html">打开测试入口说明</a>
      <a href="test-suite.csv">下载测试入口说明</a>
      <a href="evidence-map.html">打开证据索引页</a>
      <a href="evidence-manifest.csv">下载证据索引表</a>
      <a href="reader-checklist.html">打开结果核验表</a>
      <a href="reader-checklist.csv">下载结果核验表</a>
      <a href="experiment-design.html">打开实验场景说明</a>
      <a href="experiment-design.csv">下载实验场景说明</a>
      <a href="charts/runtime-observation.svg">打开观测图</a>
      <a href="charts/cost-replacement.svg">打开成本替代图</a>
      <a href="charts/runner-ticks.svg">打开 tick 对照图</a>
      <a href="charts/runner-speedup.svg">打开相对倍数图</a>
      {experiment_links}
    </div>
    <p>建议先运行 <code>make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-</code>，再运行 <code>make reader</code> 打开交互页面。需要按顺序查看时，先打开 <a href="reader-guide.html">运行导览页</a>；本页用于快速查看测试数据图表，本地阅读器页面用于查看完整运行对象和 AgentOS 主流程细节。</p>
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
    experiment_links = (
        '、<a href="experiments/experiment-stats.csv">真实实验统计</a>'
        '、<a href="charts/experiment-file-query-bar.svg">文件查询实测图</a>'
        if experiment_rows(meta)
        else '、<a href="experiments/status.json">实验测量 unavailable</a>'
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
    <p>这个页面面向运行复查：它不替代完整本地页面，而是先把本次双目标运行是否健康、增强目标是否真的产生内核证据、页面/API 是否保留完整输出讲清楚。</p>
  </header>
  <main>
    <div class="grid">
      <div class="card"><strong>{fmt_number(as_number(state.get("checked_compatibility_records")))}</strong><span>非证据状态兼容记录</span></div>
      <div class="card"><strong>{fmt_number(as_number(state.get("source_bound_runtime_records")))}</strong><span>来源绑定运行记录</span></div>
      <div class="card"><strong>{fmt_number(as_number(state.get("agentos_extra_files")))}</strong><span>AgentOS 额外状态文件</span></div>
      <div class="card"><strong>{fmt_number(as_number(state.get("agentos_evidence_checks")))}</strong><span>内核证据检查项</span></div>
      <div class="card"><strong>{fmt_number(as_number(reader.get("agentos_extra_api_json")))}</strong><span>AgentOS 额外 API JSON</span></div>
    </div>
    <section class="panel">
      <h2>一张图看本次运行</h2>
      <img src="{escape(observation_rel)}" alt="双目标运行观测面板">
    </section>
    <section class="panel">
      <h2>复查建议</h2>
      <p>先运行 <code>make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-</code> 生成结果，再运行 <code>make reader</code> 打开完整交互页面。复查时可以先查看本页的运行观测图，再切到 本地页面中的 AgentOS Compare、LLM Relay、运行详情和证据页面。</p>
      <p>如果双目标运行异常，本页会优先提示状态文件、QEMU 超时、无输出提示和阶段耗时。普通目标耗时 {escape(str(plain_result.get("qemu_elapsed_seconds", "0")))} 秒，AgentOS 目标耗时 {escape(str(agentos_result.get("qemu_elapsed_seconds", "0")))} 秒。</p>
    </section>
    <section class="panel">
      <h2>自动判读</h2>
      <table><thead><tr><th>状态</th><th>说明</th></tr></thead><tbody>{eval_rows}</tbody></table>
    </section>
    <section class="panel">
      <h2>相关结果</h2>
      <p><a href="reader-guide.html">运行导览页</a>、<a href="reader-checklist.html">结果核验表</a>、<a href="evidence-map.html">证据索引页</a>、<a href="index.html">图表索引页</a>、<a href="report.md">Markdown 报告</a>、<a href="summary.csv">CSV 明细</a>、<a href="runner-sweep.csv">runner 成组数据</a>、<a href="charts/runtime-observation.svg">运行观测图</a>、<a href="charts/cost-replacement.svg">成本替代图</a>、<a href="charts/runner-ticks.svg">tick 对照图</a>、<a href="charts/runner-speedup.svg">相对倍数图</a>{experiment_links}</p>
    </section>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def write_reader_guide_page(rows: list[MetricRow], meta: dict[str, object], charts: list[Path], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    reader = meta.get("reader", {}) if isinstance(meta.get("reader"), dict) else {}
    seeded = meta.get("seeded", {}) if isinstance(meta.get("seeded"), dict) else {}
    scenario_count = len(state.get("scenario_evidence", [])) if isinstance(state.get("scenario_evidence"), list) else 0
    cost_count = as_number(state.get("cost_replacement_count"))
    runner_pairs = as_number(state.get("runner_tick_pairs"))
    chart_links = {
        chart.name: chart.relative_to(out_path.parent).as_posix()
        for chart in charts
    }
    if experiment_rows(meta):
        experiment_guide_row = (
            '<tr><td>3</td><td><a href="charts/experiment-file-query-bar.svg">文件查询实测图</a></td>'
            '<td>对照同一 Guest 运行中的强制遍历、含重建冷索引和无缓存热索引。</td></tr>'
        )
        experiment_file_links = (
            '<a href="experiments/experiment-stats.csv">真实实验统计 CSV</a>'
            '<a href="experiments/mechanism-notes.csv">测量边界说明</a>'
        )
        experiment_summary = "真实文件查询数据已通过 Guest 日志 SHA256 和 marker 行号校验。"
    else:
        experiment_guide_row = (
            '<tr><td>3</td><td><a href="experiments/status.json">实验测量状态</a></td>'
            '<td>当前没有可验证 Guest marker，状态明确为 unavailable，未生成替代数据。</td></tr>'
        )
        experiment_file_links = '<a href="experiments/status.json">实验测量 unavailable</a>'
        experiment_summary = "本次没有可验证的 Guest 性能 marker，不提供性能结论。"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentOS 运行导览</title>
  <style>
    :root {{ --ink:#1f2937; --muted:#52616f; --line:#d8dee6; --bg:#f7f9fb; --panel:#fff; --accent:#f58518; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial,"Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:30px 42px 20px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1180px; margin:0 auto; padding:24px 42px 42px; }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    h2 {{ margin:0 0 12px; font-size:20px; }}
    p, li {{ line-height:1.75; }}
    code {{ background:#eef3f8; padding:2px 5px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:16px; }}
    .metric,.panel {{ background:var(--panel); border:1px solid var(--line); padding:16px; }}
    .metric strong {{ display:block; font-size:23px; margin-bottom:6px; }}
    .metric span {{ color:var(--muted); font-size:13px; }}
    .panel {{ margin-top:18px; }}
    .steps {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .step {{ background:#fff; border:1px solid var(--line); padding:14px; }}
    .links a {{ display:inline-block; margin:6px 8px 6px 0; color:#075985; text-decoration:none; border:1px solid var(--line); padding:7px 10px; background:#fff; }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; background:#fff; }}
    th,td {{ border:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ background:#eef3f8; }}
    @media (max-width:860px) {{ .grid,.steps {{ grid-template-columns:1fr; }} main,header {{ padding-left:18px; padding-right:18px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentOS 运行导览</h1>
    <p>这个页面把双目标运行结果、本地阅读器页面和关键图表组织成一条复查路径。它只读取本次运行产物，不重新执行 QEMU。</p>
    <div class="grid">
      <div class="metric"><strong>{fmt_number(as_number(seeded.get("action_count")))}</strong><span>预置请求</span></div>
      <div class="metric"><strong>{fmt_number(as_number(state.get("agentos_evidence_checks")))}</strong><span>内核证据检查项</span></div>
      <div class="metric"><strong>{fmt_number(scenario_count)}</strong><span>机制场景</span></div>
      <div class="metric"><strong>{fmt_number(as_number(reader.get("agentos_extra_api_json")))}</strong><span>AgentOS 额外 API JSON</span></div>
    </div>
  </header>
  <main>
    <section class="panel">
      <h2>常用运行只需要两条命令</h2>
      <div class="steps">
        <div class="step"><strong>第一条：生成双目标结果</strong><p><code>make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-</code></p><p>它会运行普通 uCore 和 AgentOS-uCore，提取状态文件，生成 CSV、报告和图表。</p></div>
        <div class="step"><strong>第二条：打开本地页面</strong><p><code>make reader</code></p><p>它会启动 本地结果阅读器，并把本页、观测面板和完整科研平台页面放在同一个本地服务里。</p></div>
      </div>
    </section>
    <section class="panel">
      <h2>建议查看顺序</h2>
      <table>
        <thead><tr><th>顺序</th><th>打开内容</th><th>要讲清楚的点</th></tr></thead>
        <tbody>
          <tr><td>1</td><td><a href="monitor.html">运行观测面板</a></td><td>先确认本次运行健康：QEMU 状态、状态产物、API 输出、AgentOS 额外证据都来自本次运行。</td></tr>
          <tr><td>2</td><td><a href="index.html">图表索引页</a></td><td>查看运行观测、成本替代、runner tick 和 runner 相对倍数。</td></tr>
          {experiment_guide_row}
          <tr><td>4</td><td><a href="charts/runner-speedup.svg">相对倍数图</a></td><td>打开 <a href="runner-sweep.csv">runner-sweep.csv</a> 复查对应输入。</td></tr>
          <tr><td>5</td><td><a href="../index.html">本地结果阅读器首页</a></td><td>进入完整科研平台页面查看运行对象和内核证据。</td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel">
      <h2>关键数据</h2>
      <p>本次结果包含 {fmt_number(cost_count)} 项 reference 成本替代目录、{fmt_number(runner_pairs)} 组来源绑定 runner 观测、{fmt_number(as_number(state.get("checked_compatibility_records")))} 条非证据状态兼容记录，以及 {fmt_number(as_number(state.get("source_bound_runtime_records")))} 条来源绑定运行记录。{experiment_summary}</p>
      <div class="links">
        <a href="delivery-readiness.html">结果材料核对</a>
        <a href="delivery-readiness.csv">结果核对 CSV</a>
        <a href="test-suite.html">测试入口说明</a>
        <a href="test-suite.csv">测试入口 CSV</a>
        <a href="experiment-design.html">实验场景说明</a>
        <a href="experiment-design.csv">实验说明 CSV</a>
        {experiment_file_links}
        <a href="reader-checklist.html">结果核验表</a>
        <a href="reader-checklist.csv">检查表 CSV</a>
        <a href="evidence-manifest.csv">证据索引 CSV</a>
        <a href="report.md">Markdown 报告</a>
        <a href="summary.json">JSON 摘要</a>
        <a href="{escape(chart_links.get("runtime-observation.svg", "charts/runtime-observation.svg"))}">运行观测图</a>
      </div>
    </section>
  </main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def copy_docs_assets(charts: list[Path], docs_assets_dir: Path) -> None:
    docs_assets_dir.mkdir(parents=True, exist_ok=True)
    for old_chart in docs_assets_dir.glob("*.svg"):
        old_chart.unlink()
    for chart in charts:
        shutil.copy2(chart, docs_assets_dir / chart.name)


def reset_generated_experiment_surfaces(out_dir: Path) -> None:
    """Remove generated experiment surfaces before rebuilding a result in-place."""
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve(strict=True)
    experiments = root / "experiments"
    if experiments.is_symlink():
        raise ValueError("experiment output directory is a symlink")
    if experiments.exists():
        resolved = experiments.resolve(strict=True)
        if resolved.parent != root or not resolved.is_dir():
            raise ValueError("experiment output directory is unsafe")
        shutil.rmtree(resolved)

    charts = root / "charts"
    if charts.is_symlink():
        raise ValueError("chart output directory is a symlink")
    if charts.is_dir():
        for old_chart in charts.glob("experiment-*.svg"):
            if old_chart.is_dir():
                raise ValueError(f"experiment chart output is unsafe: {old_chart.name}")
            old_chart.unlink()


def persist_verified_measurement(
    measured: dict[str, object], work_dir: Path, out_dir: Path
) -> dict[str, object]:
    """Copy the verified manifest and its Guest log into the served result."""
    source = measured.get("source")
    rows = measured.get("rows")
    if not isinstance(source, dict) or not isinstance(rows, list):
        raise ValueError("measured experiment evidence has an invalid bundle shape")
    relative = Path(str(source.get("path", "")))
    if (
        not relative.name
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.name in {".", ".."}
    ):
        raise ValueError("measured experiment source path is unsafe")
    work_root = work_dir.resolve(strict=True)
    lexical_source = work_root / relative
    if lexical_source.is_symlink():
        raise ValueError("measured experiment source log is a symlink")
    source_path = lexical_source.resolve(strict=True)
    if work_root not in source_path.parents or not source_path.is_file():
        raise ValueError("measured experiment source log escapes the work directory")

    experiments = out_dir / "experiments"
    experiments.mkdir(parents=True, exist_ok=True)
    bundled_name = relative.name
    bundled_log = experiments / bundled_name
    shutil.copy2(source_path, bundled_log)

    bundled = json.loads(json.dumps(measured))
    bundled_source = bundled["source"]
    bundled_rows = bundled["rows"]
    bundled_source["path"] = bundled_name
    for row in bundled_rows:
        if not isinstance(row, dict):
            raise ValueError("measured experiment row is invalid")
        row["source_log"] = bundled_name
    manifest_path = experiments / "measured-experiments.json"
    write_manifest(manifest_path, bundled)
    try:
        return verify_manifest(manifest_path, experiments)
    except MeasurementError as error:
        raise ValueError(f"persisted measurement bundle is invalid: {error}") from error


def summarize(
    work_dir: Path,
    out_dir: Path,
    docs_assets_dir: Path | None = None,
    require_measured_experiments: bool = False,
) -> dict[str, object]:
    rows, meta = collect_rows(work_dir)
    measurement_manifest = work_dir / "measured-experiments.json"
    if measurement_manifest.is_file():
        try:
            measured = verify_manifest(measurement_manifest, work_dir)
        except MeasurementError as error:
            raise ValueError(f"measured experiment evidence is invalid: {error}") from error
        meta["measured_experiment_rows"] = measured["rows"]
        meta["measured_experiment_manifest"] = measured
    else:
        meta["measured_experiment_rows"] = []
        if require_measured_experiments:
            raise ValueError("measured experiment evidence is unavailable: measured-experiments.json is missing")
    reset_generated_experiment_surfaces(out_dir)
    if measurement_manifest.is_file():
        bundled = persist_verified_measurement(measured, work_dir, out_dir)
        meta["measured_experiment_manifest"] = bundled
        meta["measured_experiment_rows"] = bundled["rows"]
    write_csv(rows, out_dir / "summary.csv")
    write_runner_sweep_csv(meta, out_dir / "runner-sweep.csv")
    experiment_data = write_experiment_outputs(meta, out_dir)
    experiment_status = write_experiment_status(meta, out_dir)
    write_delivery_readiness_csv(out_dir / "delivery-readiness.csv")
    write_delivery_readiness_page(out_dir / "delivery-readiness.html")
    write_test_suite_csv(out_dir / "test-suite.csv")
    write_test_suite_page(out_dir / "test-suite.html")
    write_experiment_design_csv(meta, out_dir / "experiment-design.csv")
    write_experiment_design_page(meta, out_dir / "experiment-design.html")
    charts = write_charts(rows, meta, out_dir / "charts")
    write_evidence_manifest_csv(charts, out_dir / "evidence-manifest.csv")
    write_evidence_map_page(charts, out_dir / "evidence-map.html")
    write_reader_checklist_csv(meta, charts, work_dir, out_dir / "reader-checklist.csv")
    write_reader_checklist_page(meta, charts, work_dir, out_dir / "reader-checklist.html")
    write_report(rows, meta, charts, out_dir / "report.md")
    write_index(rows, meta, charts, out_dir / "index.html")
    write_monitor_page(rows, meta, charts, out_dir / "monitor.html")
    write_reader_guide_page(rows, meta, charts, out_dir / "reader-guide.html")
    if docs_assets_dir is not None:
        copy_docs_assets(charts, docs_assets_dir)
    summary = {
        "status": "ready",
        "rows": len(rows),
        "charts": [str(path) for path in charts],
        "report": str(out_dir / "report.md"),
        "index": str(out_dir / "index.html"),
        "monitor": str(out_dir / "monitor.html"),
        "reader_guide": str(out_dir / "reader-guide.html"),
        "csv": str(out_dir / "summary.csv"),
        "runner_sweep_csv": str(out_dir / "runner-sweep.csv"),
        "experiment_status": experiment_status["status"],
        "experiment_status_json": str(out_dir / "experiments" / "status.json"),
        "experiment_stats_csv": str(out_dir / "experiments" / "experiment-stats.csv") if experiment_data else None,
        "experiment_mechanism_csv": str(out_dir / "experiments" / "mechanism-notes.csv") if experiment_data else None,
        "experiment_raw_csvs": [
            str(out_dir / "experiments" / "raw" / str(spec["raw_file"]))
            for spec in EXPERIMENT_SPECS.values() if experiment_data
        ],
        "delivery_readiness_csv": str(out_dir / "delivery-readiness.csv"),
        "delivery_readiness": str(out_dir / "delivery-readiness.html"),
        "test_suite_csv": str(out_dir / "test-suite.csv"),
        "test_suite": str(out_dir / "test-suite.html"),
        "experiment_design_csv": str(out_dir / "experiment-design.csv"),
        "experiment_design": str(out_dir / "experiment-design.html"),
        "evidence_manifest_csv": str(out_dir / "evidence-manifest.csv"),
        "evidence_map": str(out_dir / "evidence-map.html"),
        "reader_checklist_csv": str(out_dir / "reader-checklist.csv"),
        "reader_checklist": str(out_dir / "reader-checklist.html"),
        "experiment_rows": len(experiment_data),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Create chart and report artifacts from dual-platform verification outputs.")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--docs-assets-dir", type=Path, default=None)
    parser.add_argument("--require-measured-experiments", action="store_true")
    args = parser.parse_args()

    summary = summarize(
        args.work_dir,
        args.out_dir,
        args.docs_assets_dir,
        require_measured_experiments=args.require_measured_experiments,
    )
    print(
        "dual_platform_result_summary: rows={rows} charts={charts} report={report} index={index} monitor={monitor} reader_guide={reader_guide} status={status}".format(
            rows=summary["rows"],
            charts=len(summary["charts"]),
            report=summary["report"],
            index=summary["index"],
            monitor=summary["monitor"],
            reader_guide=summary["reader_guide"],
            status=summary["status"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
