#!/usr/bin/env python3
"""Compare extracted research-platform state from plain uCore and AgentOS-uCore."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


GOOD_STATUS = {"ready", "passed", "ok"}
ADAPTED_ANCHORS = {
    ("rp_agentcmp", "backend_runner_checks"),
}
AGENTOS_EVIDENCE_REQUIREMENTS = {
    "rp_agentos_kernel": (
        "mode=kernel_agent_orchestrated",
        "context_snapshot=present",
        "dependency_update=generic_record",
        "prefetch_hint=dependency_driven",
    ),
    "rp_agentos_mainflow": (
        "context_trusted=kernel_shadow",
        "dependency_graph=kernel_records",
        "metadata_query=used_index",
        "agent_event_notify=kernel_queue",
        "failure_recovery=generic_action",
        "provenance_audit=kernel_ledger",
        "permission_control=sentinel_action_denied",
        "timeline_observe=kernel_snapshot",
        "workbench_file_verify=kernel_metadata_index",
        "package_provenance=kernel_ledger",
        "real_task_context=kernel_shadow",
        "edit_lease=kernel_exclusive",
    ),
    "rp_agentos_roles": (
        "stage_launch=agent_create_role",
        "support_launch=fork",
        "support_role=plain_process",
        "agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_backend",
    ),
    "rp_agentos_query": ("metadata_source=kernel_file_index",),
    "rp_agentos_recovery": (
        "kernel_tool=action_commit,artifact_update",
        "context_snapshot=trusted",
    ),
    "rp_agentos_timeline": (
        "event_delivery=kernel_agent_queue",
        "timeline_snapshot=ready",
    ),
    "rp_agentos_collab_ack": ("delivery=kernel_event_queue",),
    "rp_agentos_audit": ("audit_source=kernel_ledger",),
    "rp_agentos_workbench": ("file_verify=kernel_metadata_index",),
    "rp_agentos_package": ("package_trace=kernel_provenance",),
    "rp_agentos_real_task": ("report_answer=kernel_context_record",),
    "rp_agentos_conflict": (
        "edit_lease=kernel_exclusive",
        "holder_write=checked",
    ),
}
AGENTOS_MAINFLOW_FACTS = AGENTOS_EVIDENCE_REQUIREMENTS["rp_agentos_mainflow"]
AGENTOS_MAINFLOW_STAGES = (
    "entry",
    "entry_dependency",
    "recovery",
    "audit",
    "query",
    "timeline",
    "workbench",
    "collaboration",
    "package",
    "real_task",
    "edit_conflict",
)
AGENTOS_REQUIRED_AGENT_PROGRAMS = {
    "rp_query",
    "rp_repair",
    "rp_execobs",
    "rp_agent_collab",
    "rp_auditor",
    "rp_workbench",
    "rp_package",
    "rp_realtask",
    "rp_backend",
}
SCENARIO_EVIDENCE_SPECS = (
    ("Context Path", "上下文可信记录", ("context_trusted=kernel_shadow", "context_snapshot=trusted", "report_answer=kernel_context_record")),
    ("File Metadata", "文件对象查询", ("metadata_query=used_index", "metadata_source=kernel_file_index", "file_verify=kernel_metadata_index")),
    ("Event Loop", "事件通知与等待", ("agent_event_notify=kernel_queue", "event_delivery=kernel_agent_queue", "delivery=kernel_event_queue")),
    ("Recovery Action", "失败恢复动作", ("failure_recovery=generic_action", "kernel_tool=action_commit,artifact_update")),
    ("Audit Ledger", "审计记录", ("provenance_audit=kernel_ledger", "audit_source=kernel_ledger")),
    ("Provenance", "来源关系追踪", ("package_provenance=kernel_ledger", "package_trace=kernel_provenance")),
    ("Permission", "权限控制", ("permission_control=sentinel_action_denied",)),
    ("Timeline", "时间线观察", ("timeline_observe=kernel_snapshot", "timeline_snapshot=ready")),
    ("Edit Lease", "文件编辑租约", ("edit_lease=kernel_exclusive", "holder_write=checked")),
    ("Dependency", "依赖与预取提示", ("dependency_graph=kernel_records", "dependency_update=generic_record", "prefetch_hint=dependency_driven")),
)
RUNNER_TICK_PAIRS = (
    ("基础执行计划", "plain-ucore", "plain-ucore"),
    ("失败重试记录", "retry-recovery", "retry-recovery"),
    ("上下文路径", "user-context", "agentos-context"),
    ("文件对象查询", "user-fsmeta", "agentos-fsmeta"),
    ("恢复动作", "user-recovery", "agentos-recovery"),
    ("事件交接", "user-event", "agentos-event"),
    ("审计记录", "user-audit", "agentos-audit"),
)


@dataclass(frozen=True)
class StateLine:
    file_name: str
    line_no: int
    anchor: str
    status: str
    text: str


def read_summary(state_dir: Path) -> dict[str, object]:
    summary_path = state_dir / "extract-summary.json"
    if not summary_path.is_file():
        raise ValueError(f"missing extract summary: {summary_path}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require_file_text(state_dir: Path, file_name: str) -> str:
    path = state_dir / file_name
    if not path.is_file():
        raise ValueError(f"missing required state file: {path}")
    return read_text(path)


def parse_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        fields[key] = value.strip()
    return fields


def parse_key_value_file(state_dir: Path, file_name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in require_file_text(state_dir, file_name).splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        if ";" in line:
            values.update(parse_fields(line))
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def line_anchor(fields: dict[str, str]) -> str:
    if not fields:
        return ""
    first_key = next(iter(fields))
    if first_key == "status":
        return "status"
    return f"{first_key}={fields[first_key]}"


def collect_good_status_lines(state_dir: Path, files: set[str]) -> list[StateLine]:
    result: list[StateLine] = []
    for file_name in sorted(files):
        path = state_dir / file_name
        if file_name == "extract-summary.json" or not path.is_file():
            continue
        for index, raw in enumerate(read_text(path).splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            fields = parse_fields(line)
            status = fields.get("status", "").lower()
            if status not in GOOD_STATUS:
                continue
            anchor = line_anchor(fields)
            if not anchor:
                continue
            result.append(StateLine(file_name, index, anchor, status, line))
    return result


def collect_agentos_status_index(state_dir: Path, files: set[str]) -> dict[tuple[str, str], set[str]]:
    index: dict[tuple[str, str], set[str]] = {}
    for file_name in sorted(files):
        path = state_dir / file_name
        if file_name == "extract-summary.json" or not path.is_file():
            continue
        for raw in read_text(path).splitlines():
            fields = parse_fields(raw.strip())
            status = fields.get("status", "").lower()
            if status not in GOOD_STATUS:
                continue
            anchor = line_anchor(fields)
            if not anchor:
                continue
            index.setdefault((file_name, anchor), set()).add(status)
    return index


def first_key(anchor: str) -> str:
    return anchor.split("=", 1)[0]


def has_good_record_with_key(state_dir: Path, file_name: str, key: str) -> bool:
    path = state_dir / file_name
    if not path.is_file():
        return False
    for raw in read_text(path).splitlines():
        fields = parse_fields(raw.strip())
        if fields.get("status", "").lower() in GOOD_STATUS and first_key(line_anchor(fields)) == key:
            return True
    return False


def collect_plain_costs(state_dir: Path) -> set[str]:
    path = state_dir / "rp_backend_exec"
    if not path.is_file():
        return set()
    costs: set[str] = set()
    for raw in read_text(path).splitlines():
        fields = parse_fields(raw.strip())
        if fields.get("runner_report") and fields.get("status", "").lower() in GOOD_STATUS:
            cost = fields.get("plain_cost", "")
            if cost:
                costs.add(cost)
    return costs


def collect_backend_reports(state_dir: Path) -> list[dict[str, str]]:
    path = state_dir / "rp_backend_exec"
    if not path.is_file():
        return []
    reports: list[dict[str, str]] = []
    for raw in read_text(path).splitlines():
        fields = parse_fields(raw.strip())
        if fields.get("runner_report") and fields.get("status", "").lower() in GOOD_STATUS:
            reports.append(
                {
                    "case": fields.get("runner_report", ""),
                    "plain_cost": fields.get("plain_cost", ""),
                    "agentos_replace": fields.get("agentos_replace", ""),
                    "risk": fields.get("risk", ""),
                    "status": fields.get("status", ""),
                }
            )
    return reports


def collect_cost_replacements(plain_dir: Path, agentos_dir: Path) -> list[dict[str, object]]:
    plain_reports = collect_backend_reports(plain_dir)
    agentos_reports = collect_backend_reports(agentos_dir)
    plain_by_cost = {row["plain_cost"]: row for row in plain_reports if row.get("plain_cost")}
    rows: list[dict[str, object]] = []
    for row in agentos_reports:
        plain_cost = row.get("plain_cost", "")
        if not plain_cost:
            continue
        plain_row = plain_by_cost.get(plain_cost, {})
        rows.append(
            {
                "case": row.get("case", ""),
                "plain_cost": plain_cost,
                "agentos_replace": row.get("agentos_replace", ""),
                "risk": row.get("risk", ""),
                "plain_case": plain_row.get("case", ""),
                "preserved_from_plain": 1 if plain_cost in plain_by_cost else 0,
                "status": row.get("status", ""),
            }
        )
    return rows


def collect_runner_cases(state_dir: Path) -> dict[str, dict[str, str]]:
    path = state_dir / "rp_backend_exec"
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for raw in read_text(path).splitlines():
        fields = parse_fields(raw.strip())
        case = fields.get("runner_case", "")
        if not case or fields.get("result", "").lower() not in GOOD_STATUS:
            continue
        rows[case] = fields
    return rows


def int_field(fields: dict[str, str], key: str) -> int:
    try:
        return int(fields.get(key, "0"))
    except ValueError:
        return 0


def collect_runner_tick_comparison(plain_dir: Path, agentos_dir: Path) -> list[dict[str, object]]:
    plain_cases = collect_runner_cases(plain_dir)
    agentos_cases = collect_runner_cases(agentos_dir)
    rows: list[dict[str, object]] = []
    for label, plain_case, agentos_case in RUNNER_TICK_PAIRS:
        plain = plain_cases.get(plain_case)
        agentos = agentos_cases.get(agentos_case)
        if plain is None or agentos is None:
            continue
        plain_ticks = int_field(plain, "ticks")
        agentos_ticks = int_field(agentos, "ticks")
        saved = plain_ticks - agentos_ticks
        speedup_x100 = int(round((plain_ticks * 100) / agentos_ticks)) if agentos_ticks > 0 else 0
        rows.append(
            {
                "label": label,
                "plain_case": plain_case,
                "agentos_case": agentos_case,
                "plain_ticks": plain_ticks,
                "agentos_ticks": agentos_ticks,
                "saved_ticks": saved,
                "speedup_x100": speedup_x100,
                "plain_reason": plain.get("reason", ""),
                "agentos_reason": agentos.get("reason", ""),
            }
        )
    return rows


def verify_backend_costs(plain_dir: Path, agentos_dir: Path) -> int:
    plain_costs = collect_plain_costs(plain_dir)
    agentos_costs = collect_plain_costs(agentos_dir)
    missing = sorted(plain_costs - agentos_costs)
    if missing:
        raise ValueError("AgentOS backend report is missing plain_cost items: " + ", ".join(missing))
    return len(plain_costs)


def verify_run_result(plain_dir: Path, agentos_dir: Path) -> int:
    plain = parse_key_value_file(plain_dir, "rp_host_run_result")
    agentos = parse_key_value_file(agentos_dir, "rp_host_run_result")
    for label, values in (("plain", plain), ("AgentOS", agentos)):
        if values.get("status") != "ready":
            raise ValueError(f"{label} host run result is not ready")
        if values.get("passed") != "1":
            raise ValueError(f"{label} host run did not pass")
        if values.get("qemu_orch_passed") != "1":
            raise ValueError(f"{label} host run result is missing qemu_orch_passed=1")
    if plain.get("embedded_action_records") != agentos.get("embedded_action_records"):
        raise ValueError("embedded action record count differs between plain and AgentOS")
    try:
        return int(plain.get("embedded_action_records", "0"))
    except ValueError as exc:
        raise ValueError("embedded action record count is not numeric") from exc


def verify_agentos_evidence(agentos_dir: Path) -> int:
    checked = 0
    for file_name, tokens in AGENTOS_EVIDENCE_REQUIREMENTS.items():
        text = require_file_text(agentos_dir, file_name)
        for token in tokens:
            if token not in text:
                raise ValueError(f"AgentOS evidence file {file_name} missing token: {token}")
            checked += 1
    return checked


def collect_scenario_evidence(agentos_dir: Path) -> list[dict[str, object]]:
    texts: dict[str, str] = {}
    for file_name in AGENTOS_EVIDENCE_REQUIREMENTS:
        path = agentos_dir / file_name
        if path.is_file():
            texts[file_name] = read_text(path)

    rows: list[dict[str, object]] = []
    for scenario, label, tokens in SCENARIO_EVIDENCE_SPECS:
        matched: list[dict[str, str]] = []
        for token in tokens:
            sources = [file_name for file_name, text in texts.items() if token in text]
            for source in sources:
                matched.append({"token": token, "source": source})
        rows.append(
            {
                "scenario": scenario,
                "label": label,
                "expected": len(tokens),
                "matched": len(matched),
                "sources": sorted({item["source"] for item in matched}),
                "tokens": matched,
                "status": "ready" if len(matched) >= len(tokens) else "partial",
            }
        )
    return rows


def verify_agentos_mainflow_stages(agentos_dir: Path) -> int:
    text = require_file_text(agentos_dir, "rp_agentos_mainflow")
    found: list[str] = []
    for raw in text.splitlines():
        fields = parse_fields(raw.strip())
        stage = fields.get("stage")
        if stage and fields.get("status", "").lower() in GOOD_STATUS:
            found.append(stage)
    missing = [stage for stage in AGENTOS_MAINFLOW_STAGES if stage not in found]
    if missing:
        raise ValueError("AgentOS mainflow is missing kernel stage records: " + ",".join(missing))
    positions = [found.index(stage) for stage in AGENTOS_MAINFLOW_STAGES]
    if positions != sorted(positions):
        raise ValueError("AgentOS mainflow kernel stage records are out of order")
    return len(AGENTOS_MAINFLOW_STAGES)


def verify_agentos_mainflow_facts(agentos_dir: Path) -> int:
    text = require_file_text(agentos_dir, "rp_agentos_mainflow")
    missing = [token for token in AGENTOS_MAINFLOW_FACTS if token not in text]
    if missing:
        raise ValueError("AgentOS mainflow is missing kernel fact records: " + ",".join(missing))
    return len(AGENTOS_MAINFLOW_FACTS)


def verify_orch_timing(
    state_dir: Path,
    label: str,
    required_agent_programs: set[str] | None = None,
) -> tuple[int, int, int]:
    text = require_file_text(state_dir, "rp_orch_timing")
    program_count = 0
    agent_launcher_count = 0
    fork_launcher_count = 0
    agent_programs: set[str] = set()
    for raw in text.splitlines():
        fields = parse_fields(raw.strip())
        program = fields.get("program", "")
        if not program:
            continue
        program_count += 1
        if fields.get("ok") != "1":
            raise ValueError(f"{label} timing record is not ok: {raw}")
        elapsed = fields.get("elapsed_ms", "")
        if not elapsed.isdigit():
            raise ValueError(f"{label} timing record has nonnumeric elapsed_ms: {raw}")
        launcher = fields.get("launcher")
        if launcher == "agent_create_role":
            agent_launcher_count += 1
            agent_programs.add(program)
        elif launcher and launcher.startswith("fork"):
            if required_agent_programs and fields.get("role") != "plain":
                raise ValueError(
                    f"{label} fork support program is not recorded as plain process: {raw}"
                )
            fork_launcher_count += 1
    if program_count < 60:
        raise ValueError(f"{label} timing records too few: {program_count}")
    if required_agent_programs:
        missing = sorted(required_agent_programs - agent_programs)
        if missing:
            raise ValueError(
                f"{label} timing records are missing required Agent launches: {','.join(missing)}"
            )
        if fork_launcher_count == 0:
            raise ValueError(f"{label} timing records do not show ordinary fork support programs")
    return program_count, agent_launcher_count, fork_launcher_count


def compare_state(plain_dir: Path, agentos_dir: Path, min_common_files: int) -> dict[str, object]:
    plain_summary = read_summary(plain_dir)
    agentos_summary = read_summary(agentos_dir)
    plain_files = set(plain_summary.get("files", []))
    agentos_files = set(agentos_summary.get("files", []))
    missing_files = sorted(plain_files - agentos_files)
    common_files = plain_files & agentos_files
    if missing_files:
        raise ValueError("AgentOS state is missing plain files: " + ", ".join(missing_files[:20]))
    if len(common_files) < min_common_files:
        raise ValueError(f"common state files too few: {len(common_files)} < {min_common_files}")
    if int(agentos_summary.get("extracted_state_files", 0)) < int(plain_summary.get("extracted_state_files", 0)):
        raise ValueError("AgentOS extracted fewer state files than plain uCore")

    plain_good_lines = collect_good_status_lines(plain_dir, plain_files)
    agentos_index = collect_agentos_status_index(agentos_dir, agentos_files)
    missing_status: list[StateLine] = []
    for line in plain_good_lines:
        if line.file_name == "rp_backend_exec":
            continue
        key = first_key(line.anchor)
        if (line.file_name, key) in ADAPTED_ANCHORS:
            if not has_good_record_with_key(agentos_dir, line.file_name, key):
                missing_status.append(line)
            continue
        statuses = agentos_index.get((line.file_name, line.anchor), set())
        if line.status not in statuses and not statuses:
            missing_status.append(line)
    if missing_status:
        details = [
            f"{line.file_name}:{line.line_no}:{line.anchor}:status={line.status}"
            for line in missing_status[:20]
        ]
        raise ValueError("AgentOS state is missing plain success records: " + ", ".join(details))

    preserved_costs = verify_backend_costs(plain_dir, agentos_dir)
    cost_replacements = collect_cost_replacements(plain_dir, agentos_dir)
    runner_tick_comparison = collect_runner_tick_comparison(plain_dir, agentos_dir)
    embedded_action_records = verify_run_result(plain_dir, agentos_dir)
    agentos_evidence_checks = verify_agentos_evidence(agentos_dir)
    scenario_evidence = collect_scenario_evidence(agentos_dir)
    agentos_mainflow_stages = verify_agentos_mainflow_stages(agentos_dir)
    agentos_mainflow_facts = verify_agentos_mainflow_facts(agentos_dir)
    plain_timing_records, plain_agent_launches, plain_fork_launches = verify_orch_timing(
        plain_dir, "plain"
    )
    (
        agentos_timing_records,
        agentos_agent_launches,
        agentos_fork_launches,
    ) = verify_orch_timing(
        agentos_dir,
        "AgentOS",
        required_agent_programs=AGENTOS_REQUIRED_AGENT_PROGRAMS,
    )
    if agentos_timing_records < plain_timing_records:
        raise ValueError("AgentOS timing record count is less than plain uCore")
    return {
        "plain_files": int(plain_summary.get("extracted_state_files", 0)),
        "agentos_files": int(agentos_summary.get("extracted_state_files", 0)),
        "common_files": len(common_files),
        "agentos_extra_files": len(agentos_files - plain_files),
        "checked_success_records": len(plain_good_lines),
        "preserved_plain_costs": preserved_costs,
        "cost_replacements": cost_replacements,
        "cost_replacement_count": len(cost_replacements),
        "runner_tick_comparison": runner_tick_comparison,
        "runner_tick_pairs": len(runner_tick_comparison),
        "embedded_action_records": embedded_action_records,
        "run_result_match": 1,
        "agentos_evidence_checks": agentos_evidence_checks,
        "scenario_evidence": scenario_evidence,
        "agentos_mainflow_stages": agentos_mainflow_stages,
        "agentos_mainflow_facts": agentos_mainflow_facts,
        "plain_timing_records": plain_timing_records,
        "plain_agent_launches": plain_agent_launches,
        "plain_fork_launches": plain_fork_launches,
        "agentos_timing_records": agentos_timing_records,
        "agentos_agent_launches": agentos_agent_launches,
        "agentos_fork_launches": agentos_fork_launches,
        "status": "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare plain and AgentOS research-platform state files.")
    parser.add_argument("--plain-dir", type=Path, required=True)
    parser.add_argument("--agentos-dir", type=Path, required=True)
    parser.add_argument("--min-common-files", type=int, default=240)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    summary = compare_state(args.plain_dir, args.agentos_dir, args.min_common_files)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        "dual_platform_state_compare: plain_files={plain_files} agentos_files={agentos_files} common_files={common_files} agentos_extra_files={agentos_extra_files} checked_success_records={checked_success_records} preserved_plain_costs={preserved_plain_costs} embedded_action_records={embedded_action_records} run_result_match={run_result_match} agentos_evidence_checks={agentos_evidence_checks} agentos_mainflow_stages={agentos_mainflow_stages} agentos_mainflow_facts={agentos_mainflow_facts} plain_timing_records={plain_timing_records} plain_agent_launches={plain_agent_launches} plain_fork_launches={plain_fork_launches} agentos_timing_records={agentos_timing_records} agentos_agent_launches={agentos_agent_launches} agentos_fork_launches={agentos_fork_launches} status={status}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
