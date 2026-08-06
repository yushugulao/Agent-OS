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
from dual_state_evidence_contract import MAIN_FLOW_SOURCE_SPECS, RUN_RESULT_WORK_FILES
from evidence_semantic_profiles import DUAL_STAGES


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
    "measured-file-query": "文件查询实测",
    "result-report-chart": "结果报告与图表生成",
}

RUNNER_TICK_STATUS_UNAVAILABLE = "unavailable"
RUNNER_TICK_REASON_PLAIN_ZERO = "plain_runtime_cases_zero"
MAIN_FLOW_VERIFICATION_ORIGIN = "host_inventory"
MAIN_FLOW_STAGE_COUNT = len(MAIN_FLOW_SOURCE_SPECS)
REMOVED_RESULT_ARTIFACTS = (
    "runner-sweep.csv",
    "monitor.html",
    "delivery-readiness.csv",
    "delivery-readiness.html",
    "test-suite.csv",
    "test-suite.html",
    "experiment-design.csv",
    "experiment-design.html",
    "evidence-manifest.csv",
    "evidence-map.html",
)


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
    seeded = read_json(work_dir / "seeded-action-state.json")
    platform = read_json(work_dir / "host-platform-alignment.json")
    tests = read_json(work_dir / "host-test-alignment.json")
    surface = read_json(work_dir / "host-surface-alignment.json")
    stage_rows = load_stage_timings(work_dir / "stage-timings.csv")
    plain_result = read_state_values(work_dir / RUN_RESULT_WORK_FILES["plain"])
    agentos_result = read_state_values(work_dir / RUN_RESULT_WORK_FILES["agentos"])

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
            "Guest 来源绑定运行记录",
            None,
            as_number(state.get("guest_source_bound_runtime_records")),
            as_number(state.get("guest_source_bound_runtime_records")),
            "仅作观测计数，不参与 Mainflow readiness；Mainflow 只能由 Host 从原始清单复验。",
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
            as_number(state.get("host_derived_mainflow_stages")),
            as_number(state.get("host_derived_mainflow_stages")),
            "Host 从安全状态清单独立复验的有序 AgentOS 主流程阶段数量。",
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
        "seeded": seeded,
        "platform": platform,
        "tests": tests,
        "surface": surface,
        "stage_rows": stage_rows,
        "plain_result": plain_result,
        "agentos_result": agentos_result,
    }
    return rows, meta


def runner_tick_evidence(
    meta: dict[str, object],
) -> tuple[str, str]:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    if {
        "runner_tick_comparison",
        "runner_tick_pairs",
        "runner_tick_expected_pairs",
    } & set(state):
        raise ValueError("removed runner tick measurement fields are present")
    status = state.get("runner_tick_status")
    reason = state.get("runner_tick_reason")
    if (
        status != RUNNER_TICK_STATUS_UNAVAILABLE
        or reason != RUNNER_TICK_REASON_PLAIN_ZERO
    ):
        raise ValueError("runner tick availability evidence is inconsistent")
    return status, reason


def runner_tick_summary_text(meta: dict[str, object]) -> str:
    _, reason = runner_tick_evidence(meta)
    return (
        "runner tick 状态为 unavailable：普通目标明确声明 runtime_cases=0；"
        f"原因码为 {reason}，未从参考目录补值，也不生成 tick 或 speedup 图。"
    )


def mainflow_evidence(meta: dict[str, object]) -> tuple[int, int]:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    platform = meta.get("platform", {}) if isinstance(meta.get("platform"), dict) else {}
    legacy = {"source_bound_runtime_records", "agentos_mainflow_stages"} & set(state)
    if legacy:
        raise ValueError(
            "removed Guest Mainflow evidence fields are present: "
            + ", ".join(sorted(legacy))
        )
    guest_records = state.get("guest_source_bound_runtime_records")
    host_stages = state.get("host_derived_mainflow_stages")
    platform_stages = platform.get("mainflow_host_stages")
    if (
        not isinstance(guest_records, int)
        or isinstance(guest_records, bool)
        or guest_records < 0
        or not isinstance(host_stages, int)
        or isinstance(host_stages, bool)
        or not isinstance(platform_stages, int)
        or isinstance(platform_stages, bool)
    ):
        raise ValueError("Mainflow evidence counters are not canonical integers")
    if (
        state.get("agentos_mainflow_verification_origin")
        != MAIN_FLOW_VERIFICATION_ORIGIN
        or platform.get("mainflow_verification_origin")
        != MAIN_FLOW_VERIFICATION_ORIGIN
        or platform.get("mainflow_host_verified") is not True
        or host_stages != MAIN_FLOW_STAGE_COUNT
        or platform_stages != MAIN_FLOW_STAGE_COUNT
    ):
        raise ValueError("Host-derived Mainflow evidence is incomplete or inconsistent")
    return guest_records, host_stages


def evaluation_items(meta: dict[str, object]) -> list[tuple[str, str]]:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    tests = meta.get("tests", {}) if isinstance(meta.get("tests"), dict) else {}
    items: list[tuple[str, str]] = []
    guest_records, host_stages = mainflow_evidence(meta)

    if state.get("status") == "ready" and as_number(state.get("run_result_match")) == 1:
        items.append(("通过", "两个目标运行结果可对照，普通状态兼容记录已核对；该结论不包含 demo/reference 目录。"))
    else:
        items.append(("关注", "双目标状态对照没有给出 ready/match 结果，需要查看 state-compare-summary.json。"))
    if tests.get("runtime_evidence_verified") is True:
        items.append(("通过", "Host runtime assertion sets 已独立校验；Guest 来源绑定记录只作观测计数，不构成 Mainflow 通过证据。"))
    else:
        items.append(("关注", "Host runtime assertion 校验缺失，Guest 记录不能替代该验证。"))
    _, tick_reason = runner_tick_evidence(meta)
    items.append((
        "通过",
        "runner tick 明确标记为 unavailable：普通目标 runtime_cases=0，"
        f"原因码为 {tick_reason}，没有用参考目录合成性能数据。",
    ))
    if as_number(state.get("agentos_extra_files")) > 0 and as_number(state.get("agentos_evidence_checks")) >= 20:
        items.append(("通过", "AgentOS target 额外输出内核证据文件，并通过多项内核事实检查。"))
    else:
        items.append(("关注", "AgentOS 额外内核证据不足，需要检查 rp_agentos_* 状态文件。"))
    if host_stages == MAIN_FLOW_STAGE_COUNT:
        items.append(("通过", "Host 已从安全状态清单复验全部有序 AgentOS 主流程阶段。"))
    else:
        items.append(("关注", "AgentOS 主流程阶段数量偏少，需要检查 rp_agentos_mainflow。"))
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


def runtime_observation_svg(rows: list[MetricRow], meta: dict[str, object], out_path: Path) -> None:
    by_metric = {row.metric: row for row in rows}
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    stage_rows = meta.get("stage_rows", [])
    stages = [row for row in stage_rows if isinstance(row, dict) and row.get("stage")] if isinstance(stage_rows, list) else []
    if not stages:
        stages = [{"stage": "未记录阶段耗时", "duration_seconds": "0", "status": "unknown"}]
    stages = stages[:len(DUAL_STAGES)]

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
            ("Guest 运行记录（非通过门）", fmt_number(as_number(state.get("guest_source_bound_runtime_records")))),
            ("预置请求", fmt_number(as_number(state.get("embedded_action_records")))),
        ],
        PALETTE["shared"],
    )
    card(
        395,
        202,
        "状态产物",
        [
            ("普通状态文件", fmt_number(as_number(state.get("plain_files")))),
            ("AgentOS状态文件", fmt_number(as_number(state.get("agentos_files")))),
            ("AgentOS额外状态", fmt_number(as_number(state.get("agentos_extra_files")))),
            ("共有状态文件", fmt_number(as_number(state.get("common_files")))),
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


def write_experiment_outputs(meta: dict[str, object], out_dir: Path) -> list[dict[str, object]]:
    rows = experiment_rows(meta)
    if not rows:
        return []
    write_experiment_raw_csvs(rows, out_dir)
    write_experiment_stats_csv(rows, out_dir / "experiments" / "experiment-stats.csv")
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


def write_charts(rows: list[MetricRow], meta: dict[str, object], charts_dir: Path) -> list[Path]:
    charts_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    observation_chart = charts_dir / "runtime-observation.svg"
    runtime_observation_svg(rows, meta, observation_chart)
    paths.append(observation_chart)

    runner_tick_evidence(meta)
    for stale in (
        charts_dir / "cost-replacement.svg",
        charts_dir / "runner-ticks.svg",
        charts_dir / "runner-speedup.svg",
    ):
        if stale.is_symlink() or stale.is_file():
            stale.unlink()
        elif stale.exists():
            raise ValueError(f"stale runner chart path is not a file: {stale}")

    experiment_data = experiment_rows(meta)
    if experiment_data:
        file_chart = charts_dir / "experiment-file-query-bar.svg"
        experiment_file_bar_svg(experiment_data, file_chart)
        paths.append(file_chart)

    return paths


def write_report(rows: list[MetricRow], meta: dict[str, object], charts: list[Path], out_path: Path) -> None:
    state = meta.get("state", {}) if isinstance(meta.get("state"), dict) else {}
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    runner_status, runner_reason = runner_tick_evidence(meta)

    lines = [
        "# 双目标运行结果摘要",
        "",
        "本报告由双目标运行脚本在验证结束后生成，数据来自 QEMU 运行日志、文件系统镜像提取结果和状态文件对照结果。",
        "",
        "## 关键结论",
        "",
        f"- 普通 uCore 提取状态文件 {fmt_number(as_number(state.get('plain_files')))} 个，AgentOS-uCore 提取 {fmt_number(as_number(state.get('agentos_files')))} 个，其中 AgentOS 额外状态文件 {fmt_number(as_number(state.get('agentos_extra_files')))} 个。",
        f"- 同一批预置 action 请求数为 {fmt_number(as_number(state.get('embedded_action_records')))}；另核对 {fmt_number(as_number(state.get('checked_compatibility_records')))} 条非证据状态兼容记录；Guest 来源绑定运行记录为 {fmt_number(as_number(state.get('guest_source_bound_runtime_records')))}，仅作观测计数。",
        f"- Host 从安全状态清单独立复验 {fmt_number(as_number(state.get('host_derived_mainflow_stages')))} 个有序 AgentOS 主流程阶段和 {fmt_number(as_number(state.get('agentos_mainflow_facts')))} 条主流程事实。",
        f"- QEMU 诊断：普通目标耗时 {plain_result.get('qemu_elapsed_seconds', '0')} 秒、无输出提示 {plain_result.get('qemu_idle_notices', '0')} 次；AgentOS 目标耗时 {agentos_result.get('qemu_elapsed_seconds', '0')} 秒、无输出提示 {agentos_result.get('qemu_idle_notices', '0')} 次。",
        f"- {runner_tick_summary_text(meta)}",
        "",
        "## 图表",
        "",
    ]
    for chart in charts:
        lines.append(f"- `{chart.relative_to(out_path.parent.parent).as_posix()}`")
    lines.append("- `experiments/status.json`")
    if experiment_rows(meta):
        lines.append("- `experiments/experiment-stats.csv`")
        for spec in EXPERIMENT_SPECS.values():
            lines.append(f"- `experiments/raw/{spec['raw_file']}`")
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
    lines.extend(
        [
            "",
            "## Runner Tick 对照",
            "",
            f"- 证据状态：`{runner_status}`；原因码：`{runner_reason}`。",
            "- 普通目标声明 `runtime_cases=0`，本轮没有可比较的 tick 测量；摘要只保留状态原因，不提供 runner 性能结论或占位图。",
        ]
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
    plain_result = meta.get("plain_result", {}) if isinstance(meta.get("plain_result"), dict) else {}
    agentos_result = meta.get("agentos_result", {}) if isinstance(meta.get("agentos_result"), dict) else {}
    chart_cards = []
    chart_titles = {
        "runtime-observation.svg": "双目标运行观测面板",
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
        '<a href="charts/experiment-file-query-bar.svg">打开文件查询实测图</a>'
        if experiment_rows(meta)
        else '<a href="experiments/status.json">实验测量 unavailable</a>'
    )
    runner_summary = escape(runner_tick_summary_text(meta))
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
    <p>本页由双目标运行结果自动生成。它把 QEMU 运行、状态文件对照和阶段耗时整理成便于复查的图表页面。</p>
    <div class="summary">
      <div class="metric"><strong>{fmt_number(as_number(state.get("plain_files")))}</strong><span>普通 uCore 状态文件</span></div>
      <div class="metric"><strong>{fmt_number(as_number(state.get("agentos_files")))}</strong><span>AgentOS 状态文件</span></div>
      <div class="metric"><strong>{fmt_number(as_number(state.get("agentos_extra_files")))}</strong><span>AgentOS 额外状态文件</span></div>
      <div class="metric"><strong>{agentos_result.get("qemu_idle_notices", "0")}</strong><span>AgentOS QEMU 无输出提示</span></div>
    </div>
  </header>
  <main>
    <div class="links">
      <a href="report.md">查看 Markdown 报告</a>
      <a href="summary.csv">下载 CSV 明细</a>
      <a href="charts/runtime-observation.svg">打开观测图</a>
      {experiment_links}
    </div>
    <p>{runner_summary}</p>
    <p>运行 <code>make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-</code> 后，可直接打开本页查看测试数据图表和 AgentOS 主流程摘要。</p>
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


def reset_generated_result_surfaces(out_dir: Path) -> None:
    """Remove obsolete and replaceable surfaces before rebuilding a result."""
    if out_dir.is_symlink() or getattr(out_dir, "is_junction", lambda: False)():
        raise ValueError("result output directory is a link")
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve(strict=True)
    for name in REMOVED_RESULT_ARTIFACTS:
        obsolete = root / name
        if obsolete.is_symlink() or obsolete.is_file():
            obsolete.unlink()
        elif obsolete.exists():
            raise ValueError(f"obsolete result artifact is unsafe: {name}")

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


def _published_artifact_path(
    artifact: Path, out_dir: Path, published_dir: Path
) -> str:
    physical_root = out_dir.resolve(strict=True)
    physical = artifact.resolve(strict=True)
    try:
        relative = physical.relative_to(physical_root)
    except ValueError as error:
        raise ValueError("generated result artifact escaped its staging directory") from error
    return str(published_dir.resolve(strict=False) / relative)


def summarize(
    work_dir: Path,
    out_dir: Path,
    require_measured_experiments: bool = False,
    published_dir: Path | None = None,
) -> dict[str, object]:
    rows, meta = collect_rows(work_dir)
    mainflow_evidence(meta)
    runner_status, runner_reason = runner_tick_evidence(meta)
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
    reset_generated_result_surfaces(out_dir)
    if measurement_manifest.is_file():
        bundled = persist_verified_measurement(measured, work_dir, out_dir)
        meta["measured_experiment_manifest"] = bundled
        meta["measured_experiment_rows"] = bundled["rows"]
    write_csv(rows, out_dir / "summary.csv")
    experiment_data = write_experiment_outputs(meta, out_dir)
    experiment_status = write_experiment_status(meta, out_dir)
    charts = write_charts(rows, meta, out_dir / "charts")
    write_report(rows, meta, charts, out_dir / "report.md")
    write_index(rows, meta, charts, out_dir / "index.html")
    logical_root = published_dir if published_dir is not None else out_dir

    def published(relative: str) -> str:
        return _published_artifact_path(out_dir / relative, out_dir, logical_root)

    summary = {
        "status": "ready",
        "rows": len(rows),
        "charts": [
            _published_artifact_path(path, out_dir, logical_root) for path in charts
        ],
        "report": published("report.md"),
        "index": published("index.html"),
        "csv": published("summary.csv"),
        "runner_tick_status": runner_status,
        "runner_tick_reason": runner_reason,
        "experiment_status": experiment_status["status"],
        "experiment_status_json": published("experiments/status.json"),
        "experiment_stats_csv": published("experiments/experiment-stats.csv") if experiment_data else None,
        "experiment_raw_csvs": [
            published("experiments/raw/" + str(spec["raw_file"]))
            for spec in EXPERIMENT_SPECS.values() if experiment_data
        ],
        "experiment_rows": len(experiment_data),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Create chart and report artifacts from dual-platform verification outputs.")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--published-dir", type=Path, default=None)
    parser.add_argument("--require-measured-experiments", action="store_true")
    args = parser.parse_args()

    summary = summarize(
        args.work_dir,
        args.out_dir,
        require_measured_experiments=args.require_measured_experiments,
        published_dir=args.published_dir,
    )
    print(
        "dual_platform_result_summary: rows={rows} charts={charts} report={report} index={index} status={status}".format(
            rows=summary["rows"],
            charts=len(summary["charts"]),
            report=summary["report"],
            index=summary["index"],
            status=summary["status"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
