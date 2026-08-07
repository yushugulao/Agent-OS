"""在不改变策略的前提下渲染已重放的完整验证指标。"""

from __future__ import annotations

import csv
import html
from pathlib import Path


def measurement_values_match(
    serialized: dict[str, object], replayed: dict[str, object]
) -> bool:
    try:
        if float(serialized["actual"]) != float(replayed["actual"]):
            return False
        return all(
            serialized[key] == "not-applicable"
            if replayed[key] is None
            else float(serialized[key]) == float(replayed[key])
            for key in ("baseline", "limit", "usage_ratio")
        )
    except (KeyError, TypeError, ValueError):
        return False


def write_metrics_csv(
    path: Path, rows: list[dict[str, object]], full_source: str, full_hash: str,
    timing_source: str, timing_hash: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "metric", "actual", "baseline", "limit", "unit", "usage_ratio",
        "source_line", "source_log", "source_log_sha256",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            agent_total = row["metric"] == "agent_suite_total_seconds"
            serialized = {
                **row,
                "source_log": timing_source if agent_total else full_source,
                "source_log_sha256": timing_hash if agent_total else full_hash,
            }
            for name in ("baseline", "limit", "usage_ratio"):
                if serialized[name] is None:
                    serialized[name] = "not-applicable"
            writer.writerow(serialized)


def write_agent_csv(
    path: Path, rows: list[dict[str, object]], source: str, source_hash: str
) -> None:
    fields = [
        "sequence", "case", "seconds", "source_line", "source_log",
        "source_log_sha256",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **row, "source_log": source, "source_log_sha256": source_hash,
            })


def write_chart(
    path: Path, rows: list[dict[str, object]], measurements_hash: str,
    full_hash: str, timing_hash: str,
) -> None:
    width, row_height, top = 980, 34, 54
    height = top + row_height * len(rows) + 24
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="20" y="28" font-family="sans-serif" font-size="18" fill="#111">'
        'AgentOS acceptance budgets</text>',
        f'<metadata>source_measurements_sha256={measurements_hash} '
        f'full_verify_sha256={full_hash} agent_timing_sha256={timing_hash}</metadata>',
    ]
    for index, row in enumerate(rows):
        y = top + index * row_height
        label = html.escape(str(row["metric"]))
        if row["limit"] is None:
            detail = html.escape(
                f"actual={row['actual']:.9g} baseline=not-applicable "
                "limit=not-applicable usage=not-applicable"
            )
            parts.extend([
                f'<text x="20" y="{y + 16}" font-family="monospace" font-size="12">{label}</text>',
                f'<rect x="330" y="{y + 3}" width="430" height="15" fill="#e5e7eb"/>',
                f'<text x="775" y="{y + 16}" font-family="sans-serif" font-size="11">{detail}</text>',
            ])
            continue
        usage = float(row["usage_ratio"])
        baseline = float(row["baseline"]) / float(row["limit"])
        bar_width = min(430, 430 * usage)
        baseline_x = 330 + min(430, 430 * baseline)
        detail = html.escape(
            f"actual={row['actual']:.9g} baseline={row['baseline']:.9g} "
            f"limit={row['limit']:.9g} usage={usage:.2%}"
        )
        parts.extend([
            f'<text x="20" y="{y + 16}" font-family="monospace" font-size="12">{label}</text>',
            f'<rect x="330" y="{y + 3}" width="430" height="15" fill="#e5e7eb"/>',
            f'<rect x="330" y="{y + 3}" width="{bar_width:.2f}" height="15" fill="#167d68"/>',
            f'<line x1="{baseline_x:.2f}" y1="{y}" x2="{baseline_x:.2f}" y2="{y + 21}" stroke="#b45309" stroke-width="2"/>',
            f'<text x="775" y="{y + 16}" font-family="sans-serif" font-size="11">{detail}</text>',
        ])
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


__all__ = [
    "measurement_values_match", "write_agent_csv", "write_chart",
    "write_metrics_csv",
]
