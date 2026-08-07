"""解析并渲染 ``make full-verify`` 输出的有界指标。"""

from __future__ import annotations

import math
import re
from pathlib import Path

try:
    from .full_verification_metrics_render import (
        measurement_values_match, write_agent_csv, write_chart, write_metrics_csv,
    )
    from .strict_json import read_strict_json
except ImportError:
    from full_verification_metrics_render import (
        measurement_values_match, write_agent_csv, write_chart, write_metrics_csv,
    )
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
    """完整验证证据无法精确重放时抛出。"""


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
    full_log: Path,
    timing_log: Path,
    config: dict[str, object],
    *,
    duration_profile: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if duration_profile not in {"local-e3", "none"}:
        raise FullVerificationEvidenceError(
            "Agent duration profile must be exactly local-e3 or none"
        )
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
    calibration_status = suite.get("calibration_status")
    if duration_profile == "local-e3":
        if calibration_status != "calibrated_full_suite":
            raise FullVerificationEvidenceError(
                "local-e3 Agent duration policy is not fully calibrated"
            )
        duration_budget = (suite.get("baseline_seconds"), suite.get("max_seconds"))
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in duration_budget
            )
            or duration_budget[0] < 0
            or duration_budget[1] <= 0
        ):
            raise FullVerificationEvidenceError(
                "local-e3 Agent duration budget is invalid"
            )
        duration_baseline: float | None = float(duration_budget[0])
        duration_limit: float | None = float(duration_budget[1])
    else:
        # 其他 runner 仍可提供语义与清单证据，但其墙钟总时长不与 local-e3 比较。
        duration_baseline = None
        duration_limit = None
    metrics["agent_suite_total_seconds"] = {
        "metric": "agent_suite_total_seconds",
        "actual": total,
        "baseline": duration_baseline,
        "limit": duration_limit,
        "unit": "seconds",
        "source_line": 0,
    }
    rows = list(metrics.values())
    for row in rows:
        if row["metric"] == "agent_suite_total_seconds" and duration_profile == "none":
            row["usage_ratio"] = None
            continue
        limit = float(row["limit"])
        if limit <= 0 or float(row["actual"]) > limit:
            raise FullVerificationEvidenceError(
                f"metric exceeds limit: {row['metric']}"
            )
        row["usage_ratio"] = float(row["actual"]) / limit
    return rows, cases


__all__ = [
    "FullVerificationEvidenceError",
    "REQUIRED_EVIDENCE_METRICS",
    "load_budget_config",
    "measurement_values_match",
    "metric_key",
    "parse_measurements",
    "write_agent_csv",
    "write_chart",
    "write_metrics_csv",
]
