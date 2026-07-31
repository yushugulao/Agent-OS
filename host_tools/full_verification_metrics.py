"""Parse and render the bounded metrics emitted by ``make full-verify``."""

from __future__ import annotations

import csv
import html
import math
import re
from pathlib import Path

try:
    from .strict_json import read_strict_json
except ImportError:
    from strict_json import read_strict_json


KERNEL_BUDGET_END = "[kernel-budget] kernel checks passed"
AGENT_BUDGET_START = "[kernel-budget] agent-modules checks begin"
AGENT_BUDGET_END = "[kernel-budget] agent-modules checks passed"
BUDGET_LINE = re.compile(
    r"^\[kernel-budget\] (?P<name>.+?): actual=(?P<actual>[0-9]+(?:\.[0-9]+)?)"
    r"(?P<unit> lines| bytes| seconds) baseline=(?P<baseline>[0-9]+(?:\.[0-9]+)?)"
    r"(?P=unit) limit=(?P<limit>[0-9]+(?:\.[0-9]+)?)(?P=unit)$"
)
STACK_LINE = re.compile(
    r"^kernel stack budget: .*required=(?P<required>[0-9]+) "
    r"limit=(?P<limit>[0-9]+)$"
)
BOOT_STACK_LINE = re.compile(
    r"^boot stack budget: .*required=(?P<required>[0-9]+) "
    r"limit=(?P<limit>[0-9]+)$"
)
AGENT_TIMING_LINE = re.compile(
    r"^(?P<case>[A-Za-z0-9_]+)[ \t]+(?P<seconds>[0-9]+\.[0-9]{9})$"
)
METRIC_NAMES = {
    "stripped kernel ELF": "stripped_kernel_elf_bytes",
    "raw kernel image": "raw_kernel_image_bytes",
    "kernel runtime text": "kernel_runtime_text_bytes",
    "kernel runtime data": "kernel_runtime_data_bytes",
    "kernel runtime bss": "kernel_runtime_bss_bytes",
    "kernel runtime total": "kernel_runtime_total_bytes",
    "struct proc": "struct_proc_bytes",
}
AGGREGATE_PREFIX = "Agent aggregate metadata_control_plane "
AGGREGATE_METRICS = {
    "source_lines": ("metadata_control_plane_source_lines", "lines"),
    "source_bytes": ("metadata_control_plane_source_bytes", "bytes"),
    "loaded_text_bytes": ("metadata_control_plane_loaded_text_bytes", "bytes"),
    "bss_bytes": ("metadata_control_plane_bss_bytes", "bytes"),
}
REQUIRED_KERNEL_METRICS = {"kernel_source_lines", *METRIC_NAMES.values()}
REQUIRED_AGGREGATE_METRICS = {
    value[0] for value in AGGREGATE_METRICS.values()
}
REQUIRED_EVIDENCE_METRICS = (
    REQUIRED_KERNEL_METRICS
    | REQUIRED_AGGREGATE_METRICS
    | {
        "kernel_stack_required_bytes",
        "boot_stack_required_bytes",
        "agent_suite_total_seconds",
    }
)


class FullVerificationEvidenceError(RuntimeError):
    """Raised when full-verification evidence cannot be replayed exactly."""


def metric_key(name: str, unit: str) -> str | None:
    if name.startswith("kernel source (") and unit == "lines":
        return "kernel_source_lines"
    return METRIC_NAMES.get(name)


def load_budget_config(path: Path) -> dict[str, object]:
    try:
        value = read_strict_json(path)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise FullVerificationEvidenceError(
            f"invalid kernel budget configuration: {error}"
        ) from error
    if not isinstance(value, dict) or not isinstance(
        value.get("agent_test_suite"), dict
    ):
        raise FullVerificationEvidenceError(
            "kernel budget configuration lacks Agent suite"
        )
    return value


def parse_measurements(
    full_log: Path, timing_log: Path, config: dict[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    text = full_log.read_text(encoding="utf-8", errors="replace")
    modules = config.get("agent_modules")
    groups = modules.get("aggregate_budgets") if isinstance(modules, dict) else None
    targets = [
        group
        for group in groups
        if isinstance(group, dict) and group.get("name") == "metadata_control_plane"
    ] if isinstance(groups, list) else []
    if len(targets) != 1:
        raise FullVerificationEvidenceError(
            "budget configuration lacks unique metadata_control_plane aggregate"
        )

    aggregate_expected: dict[str, tuple[float, float, str]] = {}
    for suffix, (key, unit) in AGGREGATE_METRICS.items():
        values = (
            targets[0].get(f"baseline_{suffix}"),
            targets[0].get(f"max_{suffix}"),
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in values
            )
            or values[0] < 0
            or values[1] <= 0
        ):
            raise FullVerificationEvidenceError(
                "metadata_control_plane aggregate configuration is invalid"
            )
        aggregate_expected[key] = (float(values[0]), float(values[1]), unit)

    metrics: dict[str, dict[str, object]] = {}
    stack_match = boot_match = None
    stack_line = boot_line = 0
    kernel_state = agent_state = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        match = BUDGET_LINE.fullmatch(stripped)
        unit = match.group("unit").strip() if match else ""
        key = metric_key(match.group("name"), unit) if match else None
        if line == AGENT_BUDGET_START:
            if kernel_state != 2 or agent_state != 0:
                raise FullVerificationEvidenceError(
                    "agent module budget block boundary is invalid"
                )
            agent_state = 1
            continue
        if line == AGENT_BUDGET_END:
            if agent_state != 1:
                raise FullVerificationEvidenceError(
                    "agent module budget block boundary is invalid"
                )
            agent_state = 2
            continue
        name = match.group("name") if match else ""
        if name.startswith(AGGREGATE_PREFIX):
            if agent_state != 1:
                raise FullVerificationEvidenceError(
                    "metadata aggregate metric is outside its budget block"
                )
            spec = AGGREGATE_METRICS.get(name[len(AGGREGATE_PREFIX):])
            if spec is None:
                raise FullVerificationEvidenceError(
                    "unexpected metadata aggregate metric"
                )
            key, expected_unit = spec
            logged = (
                float(match.group("baseline")),
                float(match.group("limit")),
                unit,
            )
            if logged != aggregate_expected[key] or unit != expected_unit:
                raise FullVerificationEvidenceError(
                    "metadata aggregate log and configuration differ"
                )
            if key in metrics:
                raise FullVerificationEvidenceError(
                    f"duplicate metadata aggregate metric: {key}"
                )
            metrics[key] = {
                "metric": key,
                "actual": float(match.group("actual")),
                "baseline": logged[0],
                "limit": logged[1],
                "unit": unit,
                "source_line": line_number,
            }
            continue
        if key == "kernel_source_lines":
            if kernel_state != 0:
                raise FullVerificationEvidenceError(
                    "full-verify log has multiple kernel budget blocks"
                )
            kernel_state = 1
        if line == KERNEL_BUDGET_END:
            if kernel_state != 1:
                raise FullVerificationEvidenceError(
                    "kernel budget block boundary is invalid"
                )
            kernel_state = 2
            continue
        if kernel_state != 1:
            continue
        if match and key:
            if key in metrics:
                raise FullVerificationEvidenceError(
                    f"duplicate kernel metric in budget block: {key}"
                )
            metrics[key] = {
                "metric": key,
                "actual": float(match.group("actual")),
                "baseline": float(match.group("baseline")),
                "limit": float(match.group("limit")),
                "unit": unit,
                "source_line": line_number,
            }
        new_stack = STACK_LINE.fullmatch(stripped)
        new_boot = BOOT_STACK_LINE.fullmatch(stripped)
        if (new_stack and stack_match) or (new_boot and boot_match):
            raise FullVerificationEvidenceError(
                "duplicate stack metric in kernel budget block"
            )
        stack_match = new_stack or stack_match
        boot_match = new_boot or boot_match
        stack_line = line_number if new_stack else stack_line
        boot_line = line_number if new_boot else boot_line

    if kernel_state == 0:
        raise FullVerificationEvidenceError("full-verify log lacks kernel budget block")
    if kernel_state == 1:
        raise FullVerificationEvidenceError("kernel budget block is unterminated")
    if agent_state == 0:
        raise FullVerificationEvidenceError(
            "full-verify log lacks agent module budget block"
        )
    if agent_state == 1:
        raise FullVerificationEvidenceError(
            "agent module budget block is unterminated"
        )
    missing = (REQUIRED_KERNEL_METRICS | REQUIRED_AGGREGATE_METRICS) - set(metrics)
    if stack_match is None:
        missing.add("kernel_stack_required_bytes")
    if boot_match is None:
        missing.add("boot_stack_required_bytes")
    if missing:
        raise FullVerificationEvidenceError(
            f"budget blocks lack required metrics: {sorted(missing)}"
        )

    kernel_stack = config.get("kernel_stack")
    if not isinstance(kernel_stack, dict):
        raise FullVerificationEvidenceError(
            "kernel stack budget configuration is invalid"
        )
    stack_capacity = int(stack_match.group("limit"))
    boot_capacity = int(boot_match.group("limit"))
    if (
        stack_capacity != int(kernel_stack["stack_size_bytes"])
        or boot_capacity != int(kernel_stack["boot_stack_size_bytes"])
    ):
        raise FullVerificationEvidenceError(
            "stack capacity log and configuration differ"
        )
    metrics["kernel_stack_required_bytes"] = {
        "metric": "kernel_stack_required_bytes",
        "actual": float(stack_match.group("required")),
        "baseline": float(kernel_stack["baseline_required_bytes"]),
        "limit": float(kernel_stack["max_required_bytes"]),
        "unit": "bytes",
        "source_line": stack_line,
    }
    metrics["boot_stack_required_bytes"] = {
        "metric": "boot_stack_required_bytes",
        "actual": float(boot_match.group("required")),
        "baseline": float(kernel_stack["baseline_boot_required_bytes"]),
        "limit": float(kernel_stack["max_boot_required_bytes"]),
        "unit": "bytes",
        "source_line": boot_line,
    }

    cases: list[dict[str, object]] = []
    for line_number, line in enumerate(
        timing_log.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = AGENT_TIMING_LINE.fullmatch(line.strip())
        if match is None or float(match.group("seconds")) <= 0:
            raise FullVerificationEvidenceError(
                f"invalid Agent timing row {line_number}"
            )
        cases.append({
            "sequence": len(cases) + 1,
            "case": match.group("case"),
            "seconds": match.group("seconds"),
            "source_line": line_number,
        })
    if not cases:
        raise FullVerificationEvidenceError("Agent timing log is empty")
    suite = config["agent_test_suite"]
    expected_cases = suite.get("expected_cases")
    actual_cases = [row["case"] for row in cases]
    if (
        not isinstance(expected_cases, list)
        or not expected_cases
        or actual_cases != expected_cases
        or len(actual_cases) != len(set(actual_cases))
    ):
        raise FullVerificationEvidenceError(
            "Agent timing cases do not match the configured suite"
        )
    total = sum(float(row["seconds"]) for row in cases)
    metrics["agent_suite_total_seconds"] = {
        "metric": "agent_suite_total_seconds",
        "actual": total,
        "baseline": float(suite["baseline_seconds"]),
        "limit": float(suite["max_seconds"]),
        "unit": "seconds",
        "source_line": 0,
    }
    rows = list(metrics.values())
    for row in rows:
        limit = float(row["limit"])
        if limit <= 0 or float(row["actual"]) > limit:
            raise FullVerificationEvidenceError(
                f"metric exceeds limit: {row['metric']}"
            )
        row["usage_ratio"] = float(row["actual"]) / limit
    return rows, cases


def write_metrics_csv(
    path: Path,
    rows: list[dict[str, object]],
    full_source: str,
    full_hash: str,
    timing_source: str,
    timing_hash: str,
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
            writer.writerow({
                **row,
                "source_log": timing_source if agent_total else full_source,
                "source_log_sha256": timing_hash if agent_total else full_hash,
            })


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
                **row,
                "source_log": source,
                "source_log_sha256": source_hash,
            })


def write_chart(
    path: Path,
    rows: list[dict[str, object]],
    measurements_hash: str,
    full_hash: str,
    timing_hash: str,
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
        usage = float(row["usage_ratio"])
        baseline = float(row["baseline"]) / float(row["limit"])
        bar_width = min(430, 430 * usage)
        baseline_x = 330 + min(430, 430 * baseline)
        label = html.escape(str(row["metric"]))
        detail = html.escape(
            f"actual={row['actual']:.9g} baseline={row['baseline']:.9g} "
            f"limit={row['limit']:.9g} usage={usage:.2%}"
        )
        parts.extend([
            f'<text x="20" y="{y + 16}" font-family="monospace" '
            f'font-size="12">{label}</text>',
            f'<rect x="330" y="{y + 3}" width="430" height="15" '
            'fill="#e5e7eb"/>',
            f'<rect x="330" y="{y + 3}" width="{bar_width:.2f}" height="15" '
            'fill="#167d68"/>',
            f'<line x1="{baseline_x:.2f}" y1="{y}" x2="{baseline_x:.2f}" '
            f'y2="{y + 21}" stroke="#b45309" stroke-width="2"/>',
            f'<text x="775" y="{y + 16}" font-family="sans-serif" '
            f'font-size="11">{detail}</text>',
        ])
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


__all__ = [
    "FullVerificationEvidenceError",
    "REQUIRED_EVIDENCE_METRICS",
    "load_budget_config",
    "metric_key",
    "parse_measurements",
    "write_agent_csv",
    "write_chart",
    "write_metrics_csv",
]
