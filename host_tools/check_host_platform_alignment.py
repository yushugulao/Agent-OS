#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from check_host_test_alignment import (
    FNV_OFFSET,
    U64_MASK,
    fnv1a64,
    parse_record,
    read_runtime_manifest,
)
from dual_state_evidence_contract import (
    AGENTOS_EVIDENCE_REQUIREMENTS,
    AGENTOS_PROGRAM_FILESYSTEM_CAPABILITIES,
    AGENTOS_REQUIRED_AGENT_ROLES,
    AGENTOS_WORKER_BATCH_GROUPS,
    AGENTOS_WORKER_BATCH_PROGRAMS,
    AGENTOS_WORKER_DIRECT_PROGRAMS,
    MAIN_FLOW_SOURCE_SPECS,
    PLATFORM_PROGRAMS,
    agentos_program_launch_contract,
)

@dataclass(frozen=True)
class CapabilityGroup:
    name: str
    host_modules: tuple[str, ...]
    plain_sources: tuple[str, ...]
    agentos_sources: tuple[str, ...]
    runtime_state_names: tuple[str, ...] = ()


CAPABILITY_GROUPS: tuple[CapabilityGroup, ...] = (
    CapabilityGroup(
        "platform_core",
        (
            "__init__.py",
            "agents.py",
            "cli.py",
            "history.py",
            "models.py",
            "organization.py",
            "platform.py",
            "plugins.py",
            "query.py",
            "registry.py",
            "runtime.py",
            "snapshots.py",
            "store.py",
            "toolbox.py",
        ),
        ("rp_state_catalog.c", "rp_startup_doctor.c", "rp_catalog.c", "rp_mature.c", "rp_test_suite.c"),
        ("rp_state_catalog.c", "rp_startup_doctor.c", "rp_catalog.c", "rp_mature.c", "rp_test_suite.c"),
        ("rp_state_catalog", "rp_objects", "rp_services", "rp_agentcmp", "rp_status", "rp_ack"),
    ),
    CapabilityGroup(
        "workflow_runner",
        (
            "host_workflow.py",
            "workflow.py",
            "workflow_comparison.py",
            "workflow_dsl.py",
            "workflow_engine.py",
            "workflow_invocations.py",
            "workflow_templates.py",
            "workflow_completion.py",
            "workflow_portability.py",
            "workflow_lint.py",
        ),
        ("rp_workflow_runner.c", "rp_invoke.c", "rp_complete.c", "rp_portability.c"),
        ("rp_workflow_runner.c", "rp_invoke.c", "rp_complete.c", "rp_portability.c"),
        (
            "rp_stage_state",
            "rp_cache_index",
            "rp_retry_plan",
            "rp_run_events",
            "rp_artifact_manifest",
            "rp_invocation",
            "rp_steps",
            "rp_attempts",
            "rp_invoke_export",
            "rp_hooks",
            "rp_completion",
            "rp_actions",
            "rp_complete_export",
        ),
    ),
    CapabilityGroup(
        "workbench_project",
        (
            "usable_research.py",
            "project_factory.py",
            "research_package.py",
            "research_object.py",
            "runbooks.py",
            "workspace.py",
        ),
        ("rp_workbench.c", "rp_usable.c", "rp_usableproject.c", "rp_projectrel.c", "rp_runbooks.c"),
        ("rp_workbench.c", "rp_usable.c", "rp_usableproject.c", "rp_projectrel.c", "rp_runbooks.c"),
    ),
    CapabilityGroup(
        "artifacts_objects_packages",
        (
            "catalog.py",
            "artifact_diff.py",
            "object_store.py",
            "object_namespace.py",
            "bundle.py",
            "site_export.py",
        ),
        ("rp_catalog.c", "rp_artifact_ops.c", "rp_object_store.c", "rp_object_query.c", "rp_package.c"),
        ("rp_catalog.c", "rp_artifact_ops.c", "rp_object_store.c", "rp_object_query.c", "rp_package.c"),
    ),
    CapabilityGroup(
        "data_lab_analysis",
        (
            "ingestion.py",
            "data_preview.py",
            "data_quality.py",
            "data_transform.py",
            "dataset_collection.py",
            "calculation.py",
            "analysis_results.py",
            "statistical_design.py",
            "sample_registry.py",
            "annotation.py",
            "assay_plate.py",
            "cohort_monitoring.py",
            "data_dictionary.py",
            "data_product.py",
            "lab_operations.py",
            "eln.py",
            "experiment_campaigns.py",
            "experiment_result_review.py",
            "experiment_scheduling.py",
            "experiments.py",
            "fair_data.py",
            "instrument_maintenance.py",
            "inventory.py",
            "notebook.py",
            "procurement.py",
            "protocols.py",
            "protocol_amendments.py",
            "protocol_compliance.py",
            "sample_aliquots.py",
            "sample_custody.py",
            "sop_execution.py",
            "study_design.py",
            "sweep_analysis.py",
            "sweeps.py",
            "systematic_review.py",
            "training_compliance.py",
            "visualization.py",
        ),
        (
            "rp_data_pipeline.c",
            "rp_lab.c",
            "rp_calculation.c",
            "rp_analysisres.c",
            "rp_stdesign.c",
            "rp_campaign.c",
            "rp_expsched.c",
            "rp_traincomp.c",
            "rp_studyproto.c",
            "rp_service_surface.c",
            "rp_publication.c",
        ),
        (
            "rp_data_pipeline.c",
            "rp_lab.c",
            "rp_calculation.c",
            "rp_analysisres.c",
            "rp_stdesign.c",
            "rp_campaign.c",
            "rp_expsched.c",
            "rp_traincomp.c",
            "rp_studyproto.c",
            "rp_service_surface.c",
            "rp_publication.c",
        ),
    ),
    CapabilityGroup(
        "llm_prompt_model",
        (
            "llm_bridge.py",
            "llm_gateway.py",
            "llm_providers.py",
            "llm_proxy.py",
            "prompt_ops.py",
            "model_registry.py",
            "secrets.py",
        ),
        ("rp_llm_bridge.c", "rp_llm_relay.c", "rp_modelreg.c"),
        ("rp_llm_bridge.c", "rp_llm_relay.c", "rp_modelreg.c"),
    ),
    CapabilityGroup(
        "multi_agent_work",
        (
            "agent_coordination.py",
            "agent_coordination_coherence.py",
            "collaboration.py",
            "worker.py",
            "worker_operations.py",
            "notifications.py",
            "approval.py",
        ),
        (
            "rp_agent_collab.c",
            "rp_analyst.c",
            "rp_retriever.c",
            "rp_reviewer.c",
            "rp_writer.c",
            "rp_auditor.c",
        ),
        (
            "rp_agent_collab.c",
            "rp_analyst.c",
            "rp_retriever.c",
            "rp_reviewer.c",
            "rp_writer.c",
            "rp_auditor.c",
        ),
        (
            "rp_agents",
            "rp_decisions",
            "rp_handoff",
            "rp_deliberation",
            "rp_agent_run",
            "rp_data",
            "rp_lit",
            "rp_review",
            "rp_report",
            "rp_audit",
        ),
    ),
    CapabilityGroup(
        "provenance_observability",
        (
            "provenance.py",
            "provenance_query.py",
            "provenance_view.py",
            "lineage.py",
            "observability.py",
            "search_index.py",
            "evidence.py",
            "evidence_traceability.py",
            "claim_review.py",
            "analytics.py",
            "knowledge.py",
            "semantic.py",
        ),
        ("rp_prov_query.c", "rp_prov_view.c", "rp_lineage.c", "rp_evidence.c", "rp_query.c", "rp_sysreview.c"),
        ("rp_prov_query.c", "rp_prov_view.c", "rp_lineage.c", "rp_evidence.c", "rp_query.c", "rp_sysreview.c"),
    ),
    CapabilityGroup(
        "governance_privacy_security",
        (
            "governance.py",
            "privacy_review.py",
            "ethics.py",
            "data_access_governance.py",
            "quality.py",
            "tool_validation.py",
            "capa.py",
            "reference_integrity.py",
            "risk_register.py",
        ),
        ("rp_governance.c", "rp_privacy.c", "rp_sysreview.c", "rp_integrityplane.c", "rp_coherenceplane.c"),
        ("rp_governance.c", "rp_privacy.c", "rp_sysreview.c", "rp_integrityplane.c", "rp_coherenceplane.c"),
    ),
    CapabilityGroup(
        "operations_runtime",
        (
            "execution_control.py",
            "execution_cache.py",
            "execution_observer.py",
            "resource_budget.py",
            "run_queue.py",
            "run_telemetry.py",
            "scheduler.py",
            "accounting.py",
            "environment.py",
            "evaluation.py",
            "lifecycle_order.py",
            "mature_capabilities.py",
            "platform_consistency.py",
            "run_configuration.py",
            "run_state_coherence.py",
            "status_semantics.py",
            "surface_coverage.py",
        ),
        (
            "rp_controlplane.c",
            "rp_execobs.c",
            "rp_metrics.c",
            "rp_opsboard.c",
            "rp_runconf.c",
            "rp_consistency.c",
            "rp_mature.c",
        ),
        (
            "rp_controlplane.c",
            "rp_execobs.c",
            "rp_metrics.c",
            "rp_opsboard.c",
            "rp_runconf.c",
            "rp_consistency.c",
            "rp_mature.c",
        ),
    ),
    CapabilityGroup(
        "review_release_delivery",
        (
            "review_board.py",
            "review_operations.py",
            "report_validation.py",
            "result_review.py",
            "release_gate.py",
            "release_dossier.py",
            "release_delta_review.py",
            "publication.py",
            "delivery_coherence.py",
            "decision_support.py",
            "diff_review.py",
            "peer_review_response.py",
            "reproducibility.py",
            "reproduction_audit.py",
            "research_execution.py",
            "research_orchestration.py",
            "review_alignment.py",
            "writing.py",
        ),
        (
            "rp_reviewboard.c",
            "rp_revdash.c",
            "rp_reldossier.c",
            "rp_release.c",
            "rp_delta.c",
            "rp_publication.c",
            "rp_decsupport.c",
            "rp_repair.c",
            "rp_dossier.c",
        ),
        (
            "rp_reviewboard.c",
            "rp_revdash.c",
            "rp_reldossier.c",
            "rp_release.c",
            "rp_delta.c",
            "rp_publication.c",
            "rp_decsupport.c",
            "rp_repair.c",
            "rp_dossier.c",
        ),
    ),
    CapabilityGroup(
        "ui_api_export",
        (
            "api_server.py",
            "dashboard.py",
            "dashboard_data.py",
            "interactive_dashboard.py",
            "cards.py",
        ),
        ("rp_web_export.c", "rp_ui_export.c", "rp_site_export.c", "rp_service_surface.c"),
        ("rp_web_export.c", "rp_ui_export.c", "rp_site_export.c", "rp_service_surface.c"),
        (
            "rp_ui_home",
            "rp_ui_run",
            "rp_ui_agent",
            "rp_ui_evidence",
            "rp_ui_compare",
            "rp_site",
            "rp_web_routes",
            "rp_api_home",
            "rp_api_run",
            "rp_api_agents",
            "rp_api_evidence",
            "rp_api_compare",
            "rp_web_bundle",
        ),
    ),
    CapabilityGroup(
        "agentos_comparison",
        (
            "agent_os_adapter.py",
            "agent_os_abi.py",
            "agentos_readiness_coherence.py",
            "compare_runner.py",
            "backend_scenarios.py",
            "comparative_study.py",
        ),
        ("rp_backend.c", "rp_compare_plain.c", "rp_test_suite.c", "rp_mature.c"),
        ("rp_backend.c", "rp_compare_plain.c", "rp_test_suite.c", "rp_mature.c", "rp_agentos_orch.c"),
    ),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_host_dir(root: Path) -> Path:
    override = os.environ.get("HOST_PLATFORM_DIR")
    if override:
        return Path(override)
    return root.parent / "research-agent-platform-userland"


def collect_host_modules(host_dir: Path) -> set[str]:
    module_dir = host_dir / "agent_platform"
    return {path.name for path in module_dir.glob("*.py") if path.is_file()}


def collect_source_names(root: Path, relative: str) -> set[str]:
    return {path.name for path in (root / relative).glob("rp_*.c") if path.is_file()}


def collect_state_names(state_dir: Path | None) -> set[str]:
    if state_dir is None:
        return set()
    if not state_dir.is_dir():
        raise ValueError(f"state directory is missing: {state_dir}")
    return {path.name for path in state_dir.iterdir() if path.is_file() and path.name.startswith("rp_")}


def source_to_state_name(source: str) -> str:
    return source[:-2] if source.endswith(".c") else source


def runtime_candidates(group: CapabilityGroup, sources: tuple[str, ...]) -> tuple[str, ...]:
    if group.runtime_state_names:
        return group.runtime_state_names
    return tuple(source_to_state_name(source) for source in sources)


PROGRAM_MANIFEST_ENTRY = re.compile(r'^\s*APPLY\("(rp_[a-z0-9_]+)"\)\s*(?:\\)?\s*$')
ROLE_MANIFEST_ENTRY = re.compile(
    r'^\s*APPLY\("(rp_[a-z0-9_]+)",\s*'
    r'"(orchestrator|recovery|artifact|investigator|sentinel)"\)\s*(?:\\)?\s*$'
)
WORKER_BATCH_ENTRY = re.compile(
    r"^\s*APPLY\((0|[1-9][0-9]*),\s*(rp_[a-z0-9_]+)\)\s*(?:\\)?\s*$"
)
WORKER_BATCH_GROUP_ENTRY = re.compile(
    r"^\s*APPLY\((0|[1-9][0-9]*),\s*(rp_wbatch[0-9]+),\s*"
    r"(0|[1-9][0-9]*)\)\s*(?:\\)?\s*$"
)
WORKER_DIRECT_ENTRY = re.compile(
    r"^\s*APPLY\((rp_[a-z0-9_]+)\)\s*(?:\\)?\s*$"
)
AGENT_ROLE_NUMBERS = {
    "sentinel": 1,
    "investigator": 2,
    "recovery": 3,
    "orchestrator": 4,
    "artifact": 5,
}
PROGRAM_LEDGER_LINE_MAX = 255
PLAIN_PROGRAM_PROFILES = {
    "standard": ("rp_orch", "fork"),
    "seeded": ("rp_seed_orch", "fork_seeded"),
}
PLAIN_PROGRAM_EVIDENCE_SCHEMA = (
    "evidence_role",
    "evidence_generation",
    "observation_source",
    "program_source",
    "program_source_bytes",
    "program_source_hash",
    "program_names_digest",
    "programs_observed",
    "status",
)
AGENTOS_PROGRAM_EVIDENCE_SCHEMA = (
    "evidence_role",
    "evidence_generation",
    "program_source",
    "program_source_bytes",
    "program_source_hash",
    "program_names_digest",
    "programs_observed",
    "status",
)
MAINFLOW_RUNTIME_SPECS = MAIN_FLOW_SOURCE_SPECS


def parse_canonical_mainflow_telemetry(
    data: bytes,
    *,
    label: str = "mainflow telemetry",
) -> tuple[dict[str, str], ...]:
    """解析完整且模式闭合的 Mainflow 遥测流。"""
    if not data or not data.endswith(b"\n"):
        raise ValueError(f"{label} is empty or has an incomplete final record")
    if b"\r" in data or b"\0" in data:
        raise ValueError(f"{label} has non-canonical line content")

    raw_lines = data[:-1].split(b"\n")
    if any(not line for line in raw_lines):
        raise ValueError(f"{label} contains an empty record")

    records: list[dict[str, str]] = []
    for number, raw_line in enumerate(raw_lines, 1):
        try:
            line = raw_line.decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"{label} line {number} is not canonical ASCII"
            ) from error
        record = parse_record(line)
        if record is None:
            raise ValueError(f"{label} line {number} is malformed")
        generation = record.get(
            "generation", record.get("evidence_generation", "")
        )
        if record.get("evidence_role") == "runtime_verified" or (
            generation == "runtime" and record.get("status") == "verified"
        ):
            raise ValueError(
                f"{label} Guest runtime verification is forbidden at line {number}"
            )
        if "stage" not in record:
            raise ValueError(f"{label} contains a non-stage record at line {number}")
        records.append(record)

    expected_stages = tuple(spec.stage for spec in MAIN_FLOW_SOURCE_SPECS)
    found_stages = tuple(record["stage"] for record in records)
    unknown = [stage for stage in found_stages if stage not in expected_stages]
    if unknown:
        raise ValueError(f"{label} has an unknown telemetry stage: {unknown[0]}")
    if len(found_stages) != len(set(found_stages)):
        raise ValueError(f"{label} telemetry stages are duplicated")
    missing = [stage for stage in expected_stages if stage not in found_stages]
    if missing:
        raise ValueError(
            f"{label} telemetry stages are missing: " + ",".join(missing)
        )
    if len(records) != len(MAIN_FLOW_SOURCE_SPECS):
        raise ValueError(
            f"{label} must contain exactly {len(MAIN_FLOW_SOURCE_SPECS)} records"
        )
    if found_stages != expected_stages:
        raise ValueError(f"{label} telemetry stages are out of order")

    for number, (record, spec) in enumerate(
        zip(records, MAIN_FLOW_SOURCE_SPECS), 1
    ):
        expected = {
            "stage": spec.stage,
            **dict(spec.telemetry_fields),
            "status": "ready",
        }
        if record != expected:
            raise ValueError(
                f"{label} stage {spec.stage} schema differs at line {number}"
            )
    return tuple(records)



















def _parse_program_manifest(
    path: Path,
) -> tuple[tuple[str, ...], dict[str, str], list[str]]:
    if not path.is_file():
        return (), {}, [f"program manifest is missing: {path}"]
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return (), {}, [f"program manifest is not valid UTF-8: {path}"]
    start = next(
        (index for index, line in enumerate(lines) if line.startswith("#define RP_PLATFORM_PROGRAMS(APPLY)")),
        None,
    )
    if start is None:
        return (), {}, [f"RP_PLATFORM_PROGRAMS is missing from {path}"]
    programs: list[str] = []
    for line in lines[start + 1 :]:
        match = PROGRAM_MANIFEST_ENTRY.fullmatch(line)
        if match is None:
            break
        programs.append(match.group(1))
        if not line.rstrip().endswith("\\"):
            break
    if not programs:
        return (), {}, [f"RP_PLATFORM_PROGRAMS is empty in {path}"]
    if len(programs) != len(set(programs)):
        return (), {}, [f"RP_PLATFORM_PROGRAMS contains duplicate names in {path}"]
    role_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("#define RP_AGENTOS_ROLE_PROGRAMS(APPLY)")
        ),
        None,
    )
    if role_start is None:
        return tuple(programs), {}, [f"RP_AGENTOS_ROLE_PROGRAMS is missing from {path}"]
    roles: dict[str, str] = {}
    for line in lines[role_start + 1 :]:
        match = ROLE_MANIFEST_ENTRY.fullmatch(line)
        if match is None:
            break
        program, role = match.groups()
        if program in roles:
            return tuple(programs), {}, [f"RP_AGENTOS_ROLE_PROGRAMS contains duplicate names in {path}"]
        roles[program] = role
        if not line.rstrip().endswith("\\"):
            break
    if not roles:
        return tuple(programs), {}, [f"RP_AGENTOS_ROLE_PROGRAMS is empty in {path}"]
    unknown = sorted(set(roles) - set(programs))
    if unknown:
        return tuple(programs), roles, [
            f"RP_AGENTOS_ROLE_PROGRAMS contains unknown programs in {path}: {','.join(unknown)}"
        ]
    return tuple(programs), roles, []


def _manifest_macro_entries(
    lines: list[str],
    macro: str,
    pattern: re.Pattern[str],
    path: Path,
) -> tuple[list[tuple[str, ...]], list[str]]:
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith(f"#define {macro}(APPLY)")
        ),
        None,
    )
    if start is None:
        return [], [f"{macro} is missing from {path}"]
    entries: list[tuple[str, ...]] = []
    for line in lines[start + 1 :]:
        match = pattern.fullmatch(line)
        if match is None:
            break
        entries.append(match.groups())
        if not line.rstrip().endswith("\\"):
            break
    if not entries:
        return [], [f"{macro} is empty in {path}"]
    return entries, []


def _manifest_count(
    lines: list[str], name: str, path: Path
) -> tuple[int | None, list[str]]:
    pattern = re.compile(rf"^#define {re.escape(name)} (0|[1-9][0-9]*)$")
    matches = [pattern.fullmatch(line) for line in lines]
    values = [int(match.group(1)) for match in matches if match is not None]
    if len(values) != 1:
        return None, [f"{name} must be defined exactly once in {path}"]
    return values[0], []


def _validate_agentos_worker_manifest(
    path: Path,
    programs: tuple[str, ...],
    roles: dict[str, str],
) -> list[str]:
    if not path.is_file():
        return [f"program manifest is missing: {path}"]
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return [f"program manifest is not valid UTF-8: {path}"]
    errors: list[str] = []
    group_rows, group_errors = _manifest_macro_entries(
        lines, "RP_WORKER_BATCH_GROUPS", WORKER_BATCH_GROUP_ENTRY, path
    )
    errors.extend(group_errors)
    observed_groups: list[tuple[int, str, tuple[str, ...]]] = []
    for expected_group, raw_row in enumerate(group_rows):
        group_text, runner, count_text = raw_row
        group = int(group_text)
        count = int(count_text)
        if group != expected_group:
            errors.append(
                f"RP_WORKER_BATCH_GROUPS has a non-canonical group index in {path}"
            )
            continue
        rows, row_errors = _manifest_macro_entries(
            lines,
            f"RP_WORKER_BATCH_{group}_PROGRAMS",
            WORKER_BATCH_ENTRY,
            path,
        )
        errors.extend(row_errors)
        indices = tuple(int(index) for index, _program in rows)
        names = tuple(program for _index, program in rows)
        if indices != tuple(range(count)) or len(rows) != count:
            errors.append(
                f"RP_WORKER_BATCH_{group}_PROGRAMS indices or count differ in {path}"
            )
        observed_groups.append((group, runner, names))
    direct_rows, direct_errors = _manifest_macro_entries(
        lines, "RP_WORKER_DIRECT_PROGRAMS", WORKER_DIRECT_ENTRY, path
    )
    errors.extend(direct_errors)
    direct_programs = tuple(row[0] for row in direct_rows)
    declared_counts: dict[str, int | None] = {}
    for name in (
        "RP_WORKER_BATCH_GROUP_COUNT",
        "RP_WORKER_BATCH_PROGRAM_COUNT",
        "RP_WORKER_DIRECT_PROGRAM_COUNT",
    ):
        declared_counts[name], count_errors = _manifest_count(lines, name, path)
        errors.extend(count_errors)
    if tuple(programs) != PLATFORM_PROGRAMS:
        errors.append(f"RP_PLATFORM_PROGRAMS differs from the canonical 70-program order in {path}")
    if tuple(roles.items()) != tuple(AGENTOS_REQUIRED_AGENT_ROLES.items()):
        errors.append(f"RP_AGENTOS_ROLE_PROGRAMS differs from the canonical 10-role set in {path}")
    if tuple(observed_groups) != AGENTOS_WORKER_BATCH_GROUPS:
        errors.append(f"AgentOS worker batch groups or order differ in {path}")
    if direct_programs != AGENTOS_WORKER_DIRECT_PROGRAMS:
        errors.append(f"AgentOS direct-worker set or order differs in {path}")
    expected_counts = {
        "RP_WORKER_BATCH_GROUP_COUNT": len(AGENTOS_WORKER_BATCH_GROUPS),
        "RP_WORKER_BATCH_PROGRAM_COUNT": len(AGENTOS_WORKER_BATCH_PROGRAMS),
        "RP_WORKER_DIRECT_PROGRAM_COUNT": len(AGENTOS_WORKER_DIRECT_PROGRAMS),
    }
    for name, expected in expected_counts.items():
        if declared_counts[name] != expected:
            errors.append(f"{name} differs from {expected} in {path}")
    role_set = set(roles)
    batch_set = set(AGENTOS_WORKER_BATCH_PROGRAMS)
    direct_set = set(direct_programs)
    if (
        role_set & batch_set
        or role_set & direct_set
        or batch_set & direct_set
        or role_set | batch_set | direct_set != set(programs)
        or len(role_set) != 10
        or len(batch_set) != 58
        or len(direct_set) != 2
    ):
        errors.append(
            "AgentOS roles, batched workers, and direct workers do not form "
            "the required disjoint 10+58+2 partition"
        )
    return errors


def read_expected_programs(
    root: Path,
) -> tuple[tuple[str, ...], dict[str, str], list[str]]:
    agentos_path = root / "user" / "include" / "rp_program_manifest.h"
    plain_path = root / "baseline_ucore" / "user" / "include" / "rp_program_manifest.h"
    agentos, agentos_roles, agentos_errors = _parse_program_manifest(agentos_path)
    plain, plain_roles, plain_errors = _parse_program_manifest(plain_path)
    errors = agentos_errors + plain_errors
    if agentos and plain and agentos != plain:
        errors.append("plain and AgentOS ordered program manifests differ")
    if (
        agentos_roles
        and plain_roles
        and tuple(agentos_roles.items()) != tuple(plain_roles.items())
    ):
        errors.append("plain and AgentOS Agent-role launch manifests differ")
    if agentos and agentos_roles:
        errors.extend(
            _validate_agentos_worker_manifest(
                agentos_path, agentos, agentos_roles
            )
        )
    return agentos, agentos_roles, errors


def read_program_ledger(
    state_dir: Path,
    expected_programs: tuple[str, ...],
    expected_agent_roles: dict[str, str],
    target: str,
    plain_profile: str = "standard",
) -> tuple[dict[str, int], list[str]]:
    path = state_dir / "rp_orch_timing"
    if not path.is_file():
        return {}, ["rp_orch_timing program ledger is missing"]
    data = path.read_bytes()
    errors: list[str] = []
    if not data or not data.endswith(b"\n"):
        return {}, ["rp_orch_timing is empty or has an incomplete final record"]
    if b"\r" in data:
        return {}, ["rp_orch_timing contains non-canonical line endings"]
    try:
        raw_lines = data[:-1].split(b"\n")
        lines = [line.decode("ascii", errors="strict") for line in raw_lines]
    except UnicodeDecodeError:
        return {}, ["rp_orch_timing is not canonical ASCII"]
    if any(not line for line in raw_lines):
        return {}, ["rp_orch_timing contains an empty record"]
    if any(len(line) > PROGRAM_LEDGER_LINE_MAX for line in raw_lines):
        return {}, ["rp_orch_timing contains a record longer than the Guest parser limit"]
    if not 1 <= len(expected_programs) <= 128 or len(set(expected_programs)) != len(
        expected_programs
    ):
        return {}, ["trusted program manifest has an invalid count or duplicate name"]
    if len(lines) < 2:
        return {}, ["rp_orch_timing is missing canonical headers"]
    if len(lines) != len(expected_programs) + 2:
        errors.append(
            "rp_orch_timing record count does not match the trusted program manifest"
        )
    orchestrator = parse_record(lines[0])
    launcher = parse_record(lines[1])
    expected_plain_launcher = "fork"
    if target == "plain":
        profile = PLAIN_PROGRAM_PROFILES.get(plain_profile)
        if profile is None:
            errors.append(f"unknown plain program profile: {plain_profile}")
        else:
            expected_orchestrator, expected_plain_launcher = profile
            if orchestrator != {"orchestrator": expected_orchestrator} or launcher != {
                "launcher": expected_plain_launcher
            }:
                errors.append(
                    f"rp_orch_timing does not match the required plain {plain_profile} profile"
                )
    else:
        if orchestrator != {"orchestrator": "rp_orch"}:
            errors.append("rp_orch_timing has an invalid orchestrator header")
        if launcher != {"launcher": "mixed_attested"}:
            errors.append("rp_orch_timing has an invalid launcher header")

    program_digest = FNV_OFFSET
    agentos_filesystem_domain: str | None = None
    for number, line in enumerate(lines[2:], 3):
        record = parse_record(line)
        if record is None:
            errors.append(f"rp_orch_timing line {number} is not a strict key/value record")
            continue
        index = number - 3
        if index >= len(expected_programs):
            errors.append(f"rp_orch_timing line {number} is an unexpected program record")
            continue
        program = record.get("program", "")
        expected_program = expected_programs[index]
        expected_keys = (
            ("program", "launcher", "ok", "code", "elapsed_ms")
            if target == "plain"
            else (
                "program",
                "role",
                "launcher",
                "identity_source",
                "is_agent",
                "agent_role",
                "filesystem_domain",
                "filesystem_capabilities",
                "ok",
                "code",
                "elapsed_ms",
            )
        )
        if tuple(record) != expected_keys:
            errors.append(f"rp_orch_timing line {number} has an invalid record schema")
        if program != expected_program:
            errors.append(
                f"rp_orch_timing line {number} expected {expected_program}, found {program or '<missing>'}"
            )
        if record.get("ok") != "1" or record.get("code") != "0":
            errors.append(f"rp_orch_timing program {program} did not complete successfully")
        if re.fullmatch(r"[0-9]+", record.get("elapsed_ms", "")) is None:
            errors.append(f"rp_orch_timing program {program} has invalid elapsed time")
        if target == "plain":
            if record.get("launcher") != expected_plain_launcher:
                errors.append(f"rp_orch_timing program {program} has invalid plain launcher")
        else:
            role = record.get("role")
            launcher_name = record.get("launcher")
            expected_role = expected_agent_roles.get(program, "plain")
            try:
                expected_launcher, expected_identity_source = (
                    agentos_program_launch_contract(program)
                )
            except ValueError:
                expected_launcher, expected_identity_source = "", ""
                errors.append(
                    f"rp_orch_timing program {program} is outside the AgentOS launch contract"
                )
            if role != expected_role:
                errors.append(f"rp_orch_timing program {program} has invalid AgentOS role")
            if launcher_name != expected_launcher:
                errors.append(f"rp_orch_timing program {program} has invalid AgentOS launcher")
            expected_is_agent = "1" if program in expected_agent_roles else "0"
            expected_role_number = str(AGENT_ROLE_NUMBERS.get(expected_role, 0))
            if record.get("identity_source") != expected_identity_source:
                errors.append(
                    f"rp_orch_timing program {program} has invalid trusted CRT identity evidence"
                )
            if record.get("is_agent") != expected_is_agent or record.get("agent_role") != expected_role_number:
                errors.append(f"rp_orch_timing program {program} has mismatched self-checked identity")
            domain = record.get("filesystem_domain", "")
            capabilities = record.get("filesystem_capabilities", "")
            if (
                re.fullmatch(r"[1-9][0-9]*", domain) is None
                or int(domain) > U64_MASK
            ):
                errors.append(f"rp_orch_timing program {program} has invalid self-checked domain")
            elif agentos_filesystem_domain is None:
                agentos_filesystem_domain = domain
            elif domain != agentos_filesystem_domain:
                errors.append(
                    f"rp_orch_timing program {program} has mismatched self-checked domain"
                )
            if (
                capabilities != str(AGENTOS_PROGRAM_FILESYSTEM_CAPABILITIES)
            ):
                errors.append(f"rp_orch_timing program {program} has invalid self-checked capabilities")
        program_digest = fnv1a64(
            expected_program.encode("utf-8") + b"\0", program_digest
        )
    return {
        "program_source_bytes": len(data),
        "program_source_hash": fnv1a64(data),
        "program_names_digest": program_digest,
        "programs_observed": len(lines) - 2,
    }, errors


def _program_evidence_records(
    path: Path, expected_schema: tuple[str, ...]
) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], [f"{path.name} program evidence is missing"]
    records: list[dict[str, str]] = []
    errors: list[str] = []
    data = path.read_bytes()
    if not data or not data.endswith(b"\n"):
        return [], [f"{path.name} is empty or has an incomplete final record"]
    if b"\r" in data:
        return [], [f"{path.name} contains non-canonical line endings"]
    raw_lines = data[:-1].split(b"\n")
    if any(not line for line in raw_lines):
        return [], [f"{path.name} contains an empty record"]
    try:
        lines = [line.decode("ascii", errors="strict") for line in raw_lines]
    except UnicodeDecodeError:
        return [], [f"{path.name} program evidence is not canonical ASCII"]
    for number, line in enumerate(lines, 1):
        if "program_source=" not in line and "programs_observed=" not in line:
            continue
        record = parse_record(line)
        if record is None:
            errors.append(f"{path.name} line {number} has malformed program evidence")
            continue
        if tuple(record) != expected_schema:
            errors.append(f"{path.name} line {number} has an invalid program evidence schema")
        records.append(record)
    return records, errors


def _validate_program_record(
    record: dict[str, str], measured: dict[str, int], label: str
) -> list[str]:
    errors: list[str] = []
    if record.get("program_source") != "rp_orch_timing":
        errors.append(f"{label} program evidence is not bound to rp_orch_timing")
    for key, actual in measured.items():
        value = record.get(key)
        declared = (
            int(value)
            if value is not None and re.fullmatch(r"[1-9][0-9]*", value)
            else None
        )
        if declared != actual:
            errors.append(f"{label} {key} does not match rp_orch_timing")
    return errors


def _validate_program_log(
    path: Path, record: dict[str, str], label: str
) -> list[str]:
    if not path.is_file():
        return [f"{label} QEMU log is missing"]
    canonical = ";".join(f"{key}={value}" for key, value in record.items())
    marker = ("rp_orch: " + canonical.replace(";", " ")).encode("ascii")
    prefix = f"rp_orch: evidence_role={record['evidence_role']} ".encode("ascii")
    lines = [
        line[:-1] if line.endswith(b"\r") else line
        for line in path.read_bytes().split(b"\n")
    ]
    candidates = [
        line
        for line in lines
        if line.startswith(prefix) and b"program_source=rp_orch_timing " in line
    ]
    if len(candidates) != 1:
        return [f"{label} QEMU log does not contain exactly one program inventory marker"]
    if candidates[0] != marker:
        return [f"{label} QEMU program inventory marker does not match extracted state"]
    return []


def validate_program_inventory(
    root: Path,
    plain_state_dir: Path,
    agentos_state_dir: Path,
    plain_profile: str = "standard",
    plain_log: Path | None = None,
    agentos_log: Path | None = None,
) -> tuple[dict[str, object], list[str]]:
    expected_programs, expected_agent_roles, manifest_errors = read_expected_programs(root)
    plain_measured, plain_errors = read_program_ledger(
        plain_state_dir, expected_programs, expected_agent_roles, "plain", plain_profile
    )
    agentos_measured, agentos_errors = read_program_ledger(
        agentos_state_dir, expected_programs, expected_agent_roles, "agentos"
    )
    plain_records, plain_record_errors = _program_evidence_records(
        plain_state_dir / "rp_agentcmp", PLAIN_PROGRAM_EVIDENCE_SCHEMA
    )
    agentos_records, agentos_record_errors = _program_evidence_records(
        agentos_state_dir / "rp_agentcmp", AGENTOS_PROGRAM_EVIDENCE_SCHEMA
    )
    errors = list(manifest_errors)
    errors.extend(f"plain: {error}" for error in plain_errors + plain_record_errors)
    errors.extend(f"AgentOS: {error}" for error in agentos_errors + agentos_record_errors)

    plain_observations = [
        record
        for record in plain_records
        if record.get("evidence_role") == "demo_reference"
        and record.get("evidence_generation") == "runtime"
        and record.get("observation_source") == "guest_runtime"
        and record.get("status") == "reference_observed"
    ]
    agentos_verified = [
        record
        for record in agentos_records
        if record.get("evidence_role") == "runtime_verified"
        and record.get("evidence_generation") == "runtime"
        and record.get("status") == "verified"
    ]
    if len(plain_observations) != 1:
        errors.append("plain: expected exactly one demo-reference program observation")
    elif plain_measured:
        errors.extend(_validate_program_record(plain_observations[0], plain_measured, "plain"))
    if len(agentos_verified) != 1:
        errors.append("AgentOS: expected exactly one runtime-verified program inventory")
    elif agentos_measured:
        errors.extend(_validate_program_record(agentos_verified[0], agentos_measured, "AgentOS"))
    logs_supplied = plain_log is not None or agentos_log is not None
    if logs_supplied and (plain_log is None or agentos_log is None):
        errors.append("plain and AgentOS QEMU logs must be supplied together")
    if plain_profile == "seeded" and not logs_supplied:
        errors.append("seeded program inventory requires QEMU log binding")
    if plain_log is not None and agentos_log is not None:
        if len(plain_observations) == 1:
            errors.extend(_validate_program_log(plain_log, plain_observations[0], "plain"))
        if len(agentos_verified) == 1:
            errors.extend(_validate_program_log(agentos_log, agentos_verified[0], "AgentOS"))
    if any(
        record.get("evidence_role") == "runtime_verified"
        or (
            record.get("evidence_generation") == "runtime"
            and record.get("status") == "verified"
        )
        for record in plain_records
    ):
        errors.append("plain: demo/reference evidence impersonates AgentOS runtime verification")
    return {
        "plain_programs_observed": plain_measured.get("programs_observed"),
        "agentos_programs_observed": agentos_measured.get("programs_observed"),
        "verified": not errors,
    }, errors




def _read_bound_state_source(state_dir: Path, source: str) -> bytes | None:
    state_root = state_dir.resolve(strict=True)
    path = state_dir / source
    if path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None
    if resolved.parent != state_root or not resolved.is_file():
        return None
    return resolved.read_bytes()


def source_records_are_canonical(data: bytes) -> bool:
    if (
        not data
        or not data.endswith(b"\n")
        or b"\r" in data
        or b"\0" in data
    ):
        return False
    try:
        lines = data[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeDecodeError:
        return False
    if any(not line for line in lines):
        return False
    records = [parse_record(line) for line in lines]
    return not any(record is None for record in records)


def source_has_unique_exact_field(data: bytes, key: str, value: str) -> bool:
    if (
        not data
        or not data.endswith(b"\n")
        or b"\r" in data
        or b"\0" in data
        or not key
        or not value
    ):
        return False
    try:
        lines = data[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeDecodeError:
        return False
    if any(not line for line in lines):
        return False
    values = [
        field.split("=", 1)[1]
        for line in lines
        for field in line.split(";")
        if "=" in field and field.split("=", 1)[0] == key
    ]
    return values == [value]


def validate_mainflow_runtime_evidence(
    state_dir: Path,
) -> tuple[dict[str, object], list[str]]:
    data = _read_bound_state_source(state_dir, "rp_agentos_mainflow")
    empty_summary: dict[str, object] = {
        "verification_origin": "host_inventory",
        "stages": 0,
        "assertions_executed": 0,
        "assertions_passed": 0,
        "telemetry_source": "rp_agentos_mainflow",
        "telemetry_bytes": 0,
        "telemetry_hash": None,
        "telemetry_sequence": [],
        "sources": [],
        "verified": False,
    }
    if data is None:
        return empty_summary, [
            "mainflow host verification: rp_agentos_mainflow is missing or unsafe"
        ]
    errors: list[str] = []
    try:
        telemetry_records = parse_canonical_mainflow_telemetry(
            data, label="mainflow host verification: telemetry"
        )
    except ValueError as error:
        errors.append(str(error))
        telemetry_records = ()
    found_stages = tuple(record["stage"] for record in telemetry_records)
    telemetry_by_stage = {stage: True for stage in found_stages}

    assertions_executed = 0
    assertions_passed = 0
    verified_stages = 0
    derived_sources: list[dict[str, object]] = []
    for spec in MAINFLOW_RUNTIME_SPECS:
        source_data = _read_bound_state_source(state_dir, spec.source)
        assertions_executed += 2
        claim_verified = False
        status_verified = False
        telemetry_verified = telemetry_by_stage.get(spec.stage, False)
        if source_data is None:
            errors.append(
                f"mainflow host verification: stage {spec.stage} source is missing or unsafe"
            )
            source_bytes = 0
            source_hash = None
        else:
            source_bytes = len(source_data)
            source_hash = fnv1a64(source_data)
            source_canonical = source_records_are_canonical(source_data)
            claim_verified = source_has_unique_exact_field(
                source_data, spec.claim_key, spec.claim_value
            )
            if spec.source == "rp_agentos_roles":
                claim_verified = claim_verified and all(
                    source_has_unique_exact_field(
                        source_data, key, expected
                    )
                    for token in AGENTOS_EVIDENCE_REQUIREMENTS[spec.source]
                    for key, expected in (token.split("=", 1),)
                )
            status_verified = source_has_unique_exact_field(
                source_data, "status", spec.source_status
            )
            assertions_passed += int(claim_verified) + int(status_verified)
            if not claim_verified:
                errors.append(
                    f"mainflow host verification: stage {spec.stage} exact-field assertion failed"
                )
            if not status_verified:
                errors.append(
                    f"mainflow host verification: stage {spec.stage} source-status assertion failed"
                )
            if not source_canonical:
                errors.append(
                    f"mainflow host verification: stage {spec.stage} source records are not canonical"
                )
            if (
                source_canonical
                and claim_verified
                and status_verified
                and telemetry_verified
            ):
                verified_stages += 1
        derived_sources.append(
            {
                "stage": spec.stage,
                "source": spec.source,
                "claim_key": spec.claim_key,
                "claim_value": spec.claim_value,
                "source_status": spec.source_status,
                "source_bytes": source_bytes,
                "source_hash": source_hash,
                "claim_verified": claim_verified,
                "status_verified": status_verified,
                "telemetry_fields": [
                    {"key": key, "value": value}
                    for key, value in spec.telemetry_fields
                ],
                "telemetry_verified": telemetry_verified,
            }
        )

    summary: dict[str, object] = {
        "verification_origin": "host_inventory",
        "stages": verified_stages,
        "assertions_executed": assertions_executed,
        "assertions_passed": assertions_passed,
        "telemetry_source": "rp_agentos_mainflow",
        "telemetry_bytes": len(data),
        "telemetry_hash": fnv1a64(data),
        "telemetry_sequence": list(found_stages),
        "sources": derived_sources,
        "verified": not errors,
    }
    return summary, errors




def missing_items(items: tuple[str, ...], available: set[str]) -> list[str]:
    return [item for item in items if item not in available]


def run_check(
    root: Path,
    host_dir: Path,
    require_host: bool,
    plain_state_dir: Path | None = None,
    agentos_state_dir: Path | None = None,
    plain_profile: str = "standard",
    plain_log: Path | None = None,
    agentos_log: Path | None = None,
) -> dict[str, object]:
    if not host_dir.exists():
        if require_host:
            raise SystemExit(f"host platform is missing: {host_dir}")
        return {
            "status": "skipped",
            "reason": "host_platform_not_found",
            "host_dir": str(host_dir),
        }

    host_modules = collect_host_modules(host_dir)
    plain_sources = collect_source_names(root, "baseline_ucore/user/src")
    agentos_sources = collect_source_names(root, "user/src")
    plain_state_names = collect_state_names(plain_state_dir) if plain_state_dir else set()
    agentos_state_names = collect_state_names(agentos_state_dir) if agentos_state_dir else set()
    check_runtime_state = plain_state_dir is not None or agentos_state_dir is not None
    if check_runtime_state and (plain_state_dir is None or agentos_state_dir is None):
        raise ValueError("plain and AgentOS state directories must be supplied together")
    groups: list[dict[str, object]] = []
    failures: list[str] = []
    runtime_manifest_errors: list[str] = []
    program_inventory: dict[str, object] = {}
    program_inventory_errors: list[str] = []
    mainflow_runtime: dict[str, object] = {}
    mainflow_runtime_errors: list[str] = []
    if check_runtime_state:
        _, runtime_manifest_errors = read_runtime_manifest(agentos_state_dir)
        failures.extend(f"AgentOS runtime manifest: {error}" for error in runtime_manifest_errors)
        program_inventory, program_inventory_errors = validate_program_inventory(
            root,
            plain_state_dir,
            agentos_state_dir,
            plain_profile,
            plain_log,
            agentos_log,
        )
        failures.extend(f"program inventory: {error}" for error in program_inventory_errors)
        mainflow_runtime, mainflow_runtime_errors = (
            validate_mainflow_runtime_evidence(agentos_state_dir)
        )
        failures.extend(mainflow_runtime_errors)

    for group in CAPABILITY_GROUPS:
        missing_host = missing_items(group.host_modules, host_modules)
        missing_plain = missing_items(group.plain_sources, plain_sources)
        missing_agentos = missing_items(group.agentos_sources, agentos_sources)
        plain_runtime_candidates = runtime_candidates(group, group.plain_sources)
        agentos_runtime_candidates = runtime_candidates(group, group.agentos_sources)
        plain_runtime_hits = [
            name
            for name in plain_runtime_candidates
            if name in plain_state_names and (plain_state_dir / name).stat().st_size > 0
        ]
        agentos_runtime_hits = [
            name
            for name in agentos_runtime_candidates
            if name in agentos_state_names and (agentos_state_dir / name).stat().st_size > 0
        ]

        if missing_host:
            failures.append(f"{group.name}: missing host modules: {', '.join(missing_host)}")
        if missing_plain:
            failures.append(f"{group.name}: missing plain sources: {', '.join(missing_plain)}")
        if missing_agentos:
            failures.append(f"{group.name}: missing AgentOS sources: {', '.join(missing_agentos)}")
        if check_runtime_state and not plain_runtime_hits:
            failures.append(f"{group.name}: no plain reference state file was produced")
        if check_runtime_state and not agentos_runtime_hits:
            failures.append(f"{group.name}: no AgentOS runtime state file was produced")
        group_failed = bool(
            missing_host
            or missing_plain
            or missing_agentos
            or (check_runtime_state and not plain_runtime_hits)
            or (check_runtime_state and not agentos_runtime_hits)
        )

        groups.append(
            {
                "name": group.name,
                "host_modules": len(group.host_modules),
                "plain_sources": len(group.plain_sources),
                "agentos_sources": len(group.agentos_sources),
                "status": "failed" if group_failed else "ok",
                "missing_host": missing_host,
                "missing_plain": missing_plain,
                "missing_agentos": missing_agentos,
                "plain_runtime_hits": plain_runtime_hits,
                "agentos_runtime_hits": agentos_runtime_hits,
            }
        )

    tracked_host_modules = {module for group in CAPABILITY_GROUPS for module in group.host_modules}
    untracked_host_modules = sorted(host_modules - tracked_host_modules)
    required_host_modules = sum(len(group.host_modules) for group in CAPABILITY_GROUPS)

    if untracked_host_modules:
        failures.append("host modules are not mapped to capability groups: " + ", ".join(untracked_host_modules[:20]))
    if len(host_modules) < required_host_modules:
        failures.append(
            f"host module count is smaller than required tracked modules: {len(host_modules)} < {required_host_modules}"
        )
    required_plain_sources = {source for group in CAPABILITY_GROUPS for source in group.plain_sources}
    if len(plain_sources) < len(required_plain_sources):
        failures.append(
            f"plain rp source count is smaller than required capability sources: {len(plain_sources)} < {len(required_plain_sources)}"
        )
    if len(agentos_sources) < len(plain_sources):
        failures.append(f"AgentOS rp source count is smaller than plain target: {len(agentos_sources)} < {len(plain_sources)}")

    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(host_dir),
        "host_modules": len(host_modules),
        "tracked_host_modules": len(tracked_host_modules),
        "untracked_host_modules": len(untracked_host_modules),
        "plain_sources": len(plain_sources),
        "agentos_sources": len(agentos_sources),
        "plain_state_files": len(plain_state_names) if check_runtime_state else None,
        "agentos_state_files": len(agentos_state_names) if check_runtime_state else None,
        "runtime_state_checked": check_runtime_state,
        "runtime_evidence_verified": check_runtime_state
        and not runtime_manifest_errors
        and not program_inventory_errors
        and not mainflow_runtime_errors,
        "program_inventory_verified": check_runtime_state and not program_inventory_errors,
        "mainflow_host_verified": check_runtime_state and not mainflow_runtime_errors,
        "mainflow_verification_origin": mainflow_runtime.get("verification_origin"),
        "mainflow_host_stages": mainflow_runtime.get("stages"),
        "mainflow_host_assertions_executed": mainflow_runtime.get(
            "assertions_executed"
        ),
        "mainflow_host_assertions_passed": mainflow_runtime.get(
            "assertions_passed"
        ),
        "mainflow_host_telemetry_source": mainflow_runtime.get(
            "telemetry_source"
        ),
        "mainflow_host_telemetry_bytes": mainflow_runtime.get("telemetry_bytes"),
        "mainflow_host_telemetry_hash": mainflow_runtime.get("telemetry_hash"),
        "mainflow_host_telemetry_sequence": mainflow_runtime.get(
            "telemetry_sequence"
        ),
        "mainflow_host_sources": mainflow_runtime.get("sources"),
        "plain_programs_observed": program_inventory.get("plain_programs_observed"),
        "agentos_programs_observed": program_inventory.get("agentos_programs_observed"),
        "plain_evidence_role": "demo_reference",
        "groups_ok": sum(1 for group in groups if group["status"] == "ok"),
        "groups_total": len(groups),
        "groups": groups,
        "failures": failures,
        "untracked_host_module_sample": untracked_host_modules[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check host research platform capability alignment with uCore targets.")
    parser.add_argument("--host-dir", type=Path, default=None)
    parser.add_argument("--plain-state-dir", type=Path, default=None)
    parser.add_argument("--agentos-state-dir", type=Path, default=None)
    parser.add_argument(
        "--plain-profile", choices=tuple(PLAIN_PROGRAM_PROFILES), default="standard"
    )
    parser.add_argument("--plain-log", type=Path, default=None)
    parser.add_argument("--agentos-log", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--require-host", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    host_dir = args.host_dir or default_host_dir(root)
    summary = run_check(
        root,
        host_dir,
        args.require_host,
        args.plain_state_dir,
        args.agentos_state_dir,
        args.plain_profile,
        args.plain_log,
        args.agentos_log,
    )

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status = summary["status"]
    if status == "skipped":
        print(f"host_platform_alignment: status=skipped reason={summary['reason']} host_dir={summary['host_dir']}")
        return 0

    print(
        "host_platform_alignment: "
        f"host_modules={summary['host_modules']} "
        f"tracked_host_modules={summary['tracked_host_modules']} "
        f"plain_sources={summary['plain_sources']} "
        f"agentos_sources={summary['agentos_sources']} "
        f"runtime_state_checked={int(bool(summary['runtime_state_checked']))} "
        f"runtime_evidence_verified={int(bool(summary['runtime_evidence_verified']))} "
        f"mainflow_host_verified={int(bool(summary['mainflow_host_verified']))} "
        f"plain_evidence_role={summary['plain_evidence_role']} "
        f"groups_ok={summary['groups_ok']} "
        f"groups_total={summary['groups_total']} "
        f"untracked_host_modules={summary['untracked_host_modules']} "
        f"status={status}"
    )
    if status == "failed":
        for failure in summary["failures"]:
            print(f"host_platform_alignment: failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
