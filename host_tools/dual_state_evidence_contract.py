#!/usr/bin/env python3
"""Canonical, side-effect-free contracts for dual-platform evidence."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MainflowSourceSpec:
    stage: str
    source: str
    claim_key: str
    claim_value: str
    source_status: str
    telemetry_fields: tuple[tuple[str, str], ...]


MAIN_FLOW_SOURCE_SPECS = (
    MainflowSourceSpec(
        "entry", "rp_agentos_kernel", "context_snapshot", "present", "ready",
        (("context_trusted", "kernel_shadow"),),
    ),
    MainflowSourceSpec(
        "entry_dependency",
        "rp_agentos_roles",
        "stage_launch",
        "agent_create_role",
        "ready",
        (("dependency_graph", "kernel_records"),),
    ),
    MainflowSourceSpec(
        "recovery",
        "rp_agentos_recovery",
        "kernel_tool",
        "action_commit,artifact_update",
        "ready",
        (("failure_recovery", "generic_action"),),
    ),
    MainflowSourceSpec(
        "audit", "rp_agentos_audit", "audit_source", "kernel_ledger", "verified",
        (("provenance_audit", "kernel_ledger"),),
    ),
    MainflowSourceSpec(
        "query", "rp_agentos_query", "metadata_source", "kernel_file_index", "ready",
        (("metadata_query", "used_index"),),
    ),
    MainflowSourceSpec(
        "timeline",
        "rp_agentos_timeline",
        "event_delivery",
        "kernel_agent_queue",
        "verified",
        (("agent_event_notify", "kernel_queue"), ("timeline_observe", "kernel_snapshot")),
    ),
    MainflowSourceSpec(
        "workbench",
        "rp_agentos_workbench",
        "file_verify",
        "kernel_metadata_index",
        "ready",
        (("workbench_file_verify", "kernel_metadata_index"),),
    ),
    MainflowSourceSpec(
        "collaboration",
        "rp_agentos_collab_ack",
        "delivery",
        "kernel_event_queue",
        "ready",
        (("permission_control", "sentinel_action_denied"),),
    ),
    MainflowSourceSpec(
        "package",
        "rp_agentos_package",
        "package_trace",
        "kernel_provenance",
        "ready",
        (("package_provenance", "kernel_ledger"),),
    ),
    MainflowSourceSpec(
        "real_task",
        "rp_agentos_real_task",
        "report_answer",
        "kernel_context_record",
        "ready",
        (("real_task_context", "kernel_shadow"),),
    ),
    MainflowSourceSpec(
        "edit_conflict",
        "rp_agentos_conflict",
        "holder_write",
        "checked",
        "ready",
        (("edit_lease", "kernel_exclusive"),),
    ),
)

MAIN_FLOW_SOURCE_ARTIFACTS = {
    spec.source: f"dual-mainflow-source-{spec.source}.state"
    for spec in MAIN_FLOW_SOURCE_SPECS
}
MAIN_FLOW_TELEMETRY_ARTIFACT = "dual-mainflow-telemetry.state"
MAIN_FLOW_RAW_ARTIFACTS = (
    MAIN_FLOW_TELEMETRY_ARTIFACT,
    *MAIN_FLOW_SOURCE_ARTIFACTS.values(),
)
PROGRAM_LEDGER_ARTIFACTS = {
    "plain": "dual-plain-orch-timing.state",
    "agentos": "dual-agentos-orch-timing.state",
}
BACKEND_REPORT_ARTIFACTS = {
    "plain": "dual-plain-backend-report.state",
    "agentos": "dual-agentos-backend-report.state",
}
STATE_ARCHIVE_ARTIFACTS = {
    "plain": "dual-plain-complete-state.zip",
    "agentos": "dual-agentos-complete-state.zip",
}
RUN_RESULT_WORK_FILES = {
    "plain": "plain-host-run-result.state",
    "agentos": "agentos-host-run-result.state",
}
RUN_RESULT_ARTIFACTS = {
    "plain": "dual-plain-host-run-result.state",
    "agentos": "dual-agentos-host-run-result.state",
}
RUN_RESULT_IDENTITIES = {
    "plain": ("platform_seeded", "rp_seed_orch"),
    "agentos": ("platform_agentos", "rp_agentos_orch"),
}
SEEDED_ACTION_SUMMARY_ARTIFACT = "dual-seeded-action-state.json"
HOST_RUN_RESULT_STATE_NAME = "rp_host_run_result"
DUAL_STATE_RAW_ARTIFACTS = (
    *MAIN_FLOW_RAW_ARTIFACTS,
    *PROGRAM_LEDGER_ARTIFACTS.values(),
    *BACKEND_REPORT_ARTIFACTS.values(),
    *RUN_RESULT_ARTIFACTS.values(),
    SEEDED_ACTION_SUMMARY_ARTIFACT,
    *STATE_ARCHIVE_ARTIFACTS.values(),
)

AGENTOS_EVIDENCE_REQUIREMENTS = {
    "rp_agentos_kernel": (
        "mode=kernel_agent_orchestrated",
        "context_snapshot=present",
        "dependency_update=generic_record",
        "dependency_query=generic_record",
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
        "support_launch=agent_worker_create",
        "support_role=delegated_non_agent_worker",
        "agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_service_surface,rp_backend",
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

AGENTOS_MAINFLOW_STAGES = tuple(spec.stage for spec in MAIN_FLOW_SOURCE_SPECS)
AGENTOS_MAINFLOW_FACTS = AGENTOS_EVIDENCE_REQUIREMENTS["rp_agentos_mainflow"]

PLATFORM_PROGRAMS = tuple("""
rp_catalog rp_state_catalog rp_object_store rp_object_query rp_lineage rp_site_export rp_planner rp_portability rp_retriever rp_analyst rp_reviewer rp_lab rp_governance rp_writer
rp_repair rp_auditor rp_query rp_evidence rp_llm_bridge rp_llm_relay rp_privacy rp_runconf rp_execobs rp_invoke rp_complete rp_artifact_ops rp_data_pipeline rp_workflow_runner
rp_workbench rp_agent_collab rp_package rp_calculation rp_realtask rp_analysisres rp_campaign rp_delta rp_release rp_dossier rp_service_surface rp_startup_doctor rp_notebook_export
rp_backend rp_consistency rp_metrics rp_ui_export rp_web_export rp_revdash rp_modelreg rp_sysreview rp_expsched rp_traincomp rp_publication rp_runbooks rp_projectrel rp_studyproto
rp_stdesign rp_opsboard rp_reviewboard rp_controlplane rp_integrityplane rp_coherenceplane rp_mature rp_prov_view rp_prov_query rp_reldossier rp_decsupport rp_usable rp_usableproject rp_compare_plain rp_test_suite
""".split())

AGENTOS_REQUIRED_AGENT_ROLES = {
    "rp_query": "artifact",
    "rp_repair": "recovery",
    "rp_execobs": "artifact",
    "rp_agent_collab": "orchestrator",
    "rp_auditor": "orchestrator",
    "rp_workbench": "artifact",
    "rp_package": "orchestrator",
    "rp_realtask": "orchestrator",
    "rp_service_surface": "artifact",
    "rp_backend": "orchestrator",
}
AGENTOS_ROLE_NUMBERS = {
    "sentinel": 1,
    "investigator": 2,
    "recovery": 3,
    "orchestrator": 4,
    "artifact": 5,
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
    ("Dependency", "依赖查询", ("dependency_graph=kernel_records", "dependency_update=generic_record", "dependency_query=generic_record")),
)

BACKEND_REPORT_CASES = {
    "plain": (
        "plain-ucore", "retry-recovery", "user-context", "user-fsmeta",
        "user-recovery", "user-event", "user-audit",
    ),
    "agentos": (
        "plain-ucore", "retry-recovery", "agentos-context", "agentos-fsmeta",
        "agentos-recovery", "agentos-event", "agentos-audit", "agentos-edit",
    ),
}

AGENT_TO_PLAIN_CASE = {
    "plain-ucore": "plain-ucore",
    "retry-recovery": "retry-recovery",
    "agentos-context": "user-context",
    "agentos-fsmeta": "user-fsmeta",
    "agentos-recovery": "user-recovery",
    "agentos-event": "user-event",
    "agentos-audit": "user-audit",
    "agentos-edit": "",
}

DUAL_STATE_FIELDS = {
    "plain_files", "agentos_files", "common_files", "agentos_extra_files",
    "checked_compatibility_records", "plain_reference_products",
    "agentos_reference_products", "plain_reference_records",
    "agentos_reference_records", "plain_reference_identities",
    "agentos_reference_identities", "guest_source_bound_runtime_records",
    "preserved_plain_costs", "cost_replacements", "cost_replacement_count",
    "runner_tick_status", "runner_tick_reason", "embedded_action_records",
    "run_result_match", "agentos_evidence_checks", "scenario_evidence",
    "host_derived_mainflow_stages", "agentos_mainflow_facts",
    "agentos_mainflow_verification_origin", "plain_timing_records",
    "plain_agent_launches", "plain_fork_launches", "agentos_timing_records",
    "agentos_agent_launches", "agentos_worker_launches", "status",
}

SAFE_VALUE = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z")


class DualStateContractError(ValueError):
    pass


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_cost_replacements(value: dict[str, object]) -> None:
    rows = value.get("cost_replacements")
    fields = {
        "case", "plain_cost", "agentos_replace", "risk", "plain_case",
        "preserved_from_plain", "status",
    }
    if not isinstance(rows, list) or len(rows) != len(BACKEND_REPORT_CASES["agentos"]):
        raise DualStateContractError("cost replacement inventory differs")
    seen: set[str] = set()
    plain_costs: set[str] = set()
    replacements: set[str] = set()
    preserved = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise DualStateContractError("cost replacement row schema differs")
        case = row.get("case")
        expected_plain = AGENT_TO_PLAIN_CASE.get(case) if isinstance(case, str) else None
        expected_preserved = int(bool(expected_plain))
        strings = (row.get("plain_cost"), row.get("agentos_replace"), row.get("risk"))
        if (
            expected_plain is None
            or case in seen
            or row.get("plain_case") != expected_plain
            or row.get("preserved_from_plain") != expected_preserved
            or row.get("status") != "reference_ready"
            or any(not isinstance(item, str) or not SAFE_VALUE.fullmatch(item) for item in strings)
        ):
            raise DualStateContractError("cost replacement row semantics differ")
        seen.add(case)
        plain_costs.add(str(row["plain_cost"]))
        replacements.add(str(row["agentos_replace"]))
        preserved += expected_preserved
    if (
        seen != set(BACKEND_REPORT_CASES["agentos"])
        or len(plain_costs) != len(rows)
        or len(replacements) != len(rows)
        or preserved != len(BACKEND_REPORT_CASES["plain"])
        or value.get("preserved_plain_costs") != preserved
        or value.get("cost_replacement_count") != len(rows)
    ):
        raise DualStateContractError("cost replacement aggregate differs from its rows")


def validate_dual_state(
    value: object,
    reference_identities: dict[str, tuple[str, ...]],
    plain_programs: int | None = None,
    agentos_programs: int | None = None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != DUAL_STATE_FIELDS or value.get("status") != "ready":
        raise DualStateContractError("dual state comparison schema or status differs")
    integers = DUAL_STATE_FIELDS - {
        "status", "cost_replacements", "scenario_evidence", "runner_tick_status",
        "runner_tick_reason", "plain_reference_identities",
        "agentos_reference_identities", "agentos_mainflow_verification_origin",
    }
    if any(not _nonnegative_int(value.get(name)) for name in integers):
        raise DualStateContractError("dual state comparison counters are invalid")
    if (
        value["plain_files"] < 240
        or value["common_files"] != value["plain_files"]
        or value["agentos_files"] != value["common_files"] + value["agentos_extra_files"]
        or value["run_result_match"] != 1
        or value["embedded_action_records"] <= 0
        or value["checked_compatibility_records"] <= 0
        or value["agentos_evidence_checks"] != evidence_check_count()
        or value["host_derived_mainflow_stages"] != len(MAIN_FLOW_SOURCE_SPECS)
        or value["agentos_mainflow_facts"] != len(AGENTOS_MAINFLOW_FACTS)
        or value["agentos_mainflow_verification_origin"] != "host_inventory"
        or value["scenario_evidence"] != expected_scenario_rows()
    ):
        raise DualStateContractError("dual state comparison claims are incomplete")
    _validate_cost_replacements(value)
    for target in ("plain", "agentos"):
        identities = list(reference_identities[target])
        products = sum(identity.startswith("file:") for identity in identities)
        if (
            value[f"{target}_reference_identities"] != identities
            or value[f"{target}_reference_products"] != products
            or value[f"{target}_reference_records"] != len(identities) - products
        ):
            raise DualStateContractError(f"{target} reference identity inventory differs")
    if (
        value["runner_tick_status"] != "unavailable"
        or value["runner_tick_reason"] != "plain_runtime_cases_zero"
    ):
        raise DualStateContractError("runner availability evidence is inconsistent")
    if (
        value["plain_agent_launches"] != 0
        or value["plain_fork_launches"] != value["plain_timing_records"]
        or value["agentos_agent_launches"] != len(AGENTOS_REQUIRED_AGENT_ROLES)
        or value["agentos_worker_launches"] <= 0
        or value["agentos_agent_launches"] + value["agentos_worker_launches"]
        != value["agentos_timing_records"]
        or value["agentos_timing_records"] < value["plain_timing_records"]
        or (plain_programs is not None and value["plain_timing_records"] != plain_programs)
        or (agentos_programs is not None and value["agentos_timing_records"] != agentos_programs)
    ):
        raise DualStateContractError("dual launch counters differ from the program inventory")
    return value


def fnv1a64(data: bytes, value: int = 1469598103934665603) -> int:
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return value


def evidence_check_count() -> int:
    return sum(len(tokens) for tokens in AGENTOS_EVIDENCE_REQUIREMENTS.values())


def expected_scenario_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario, label, tokens in SCENARIO_EVIDENCE_SPECS:
        matched = [
            {"token": token, "source": source}
            for token in tokens
            for source, source_tokens in AGENTOS_EVIDENCE_REQUIREMENTS.items()
            if token in source_tokens
        ]
        rows.append({
            "scenario": scenario,
            "label": label,
            "expected": len(tokens),
            "matched": len(matched),
            "sources": sorted({item["source"] for item in matched}),
            "tokens": matched,
            "status": "ready",
        })
    return rows


__all__ = [
    "AGENTOS_EVIDENCE_REQUIREMENTS", "AGENTOS_MAINFLOW_FACTS",
    "AGENTOS_MAINFLOW_STAGES", "AGENTOS_REQUIRED_AGENT_ROLES",
    "AGENTOS_ROLE_NUMBERS",
    "AGENT_TO_PLAIN_CASE", "BACKEND_REPORT_ARTIFACTS", "BACKEND_REPORT_CASES",
    "DUAL_STATE_FIELDS",
    "DUAL_STATE_RAW_ARTIFACTS", "MAIN_FLOW_RAW_ARTIFACTS",
    "HOST_RUN_RESULT_STATE_NAME",
    "MAIN_FLOW_SOURCE_ARTIFACTS",
    "MAIN_FLOW_SOURCE_SPECS", "MAIN_FLOW_TELEMETRY_ARTIFACT", "MainflowSourceSpec",
    "PLATFORM_PROGRAMS",
    "PROGRAM_LEDGER_ARTIFACTS", "SCENARIO_EVIDENCE_SPECS",
    "RUN_RESULT_ARTIFACTS", "RUN_RESULT_IDENTITIES", "RUN_RESULT_WORK_FILES",
    "SEEDED_ACTION_SUMMARY_ARTIFACT",
    "STATE_ARCHIVE_ARTIFACTS",
    "DualStateContractError", "evidence_check_count",
    "expected_scenario_rows", "fnv1a64", "validate_dual_state",
]
