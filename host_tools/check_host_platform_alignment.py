#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

SUCCESS_MARKERS = (
    "status=ready",
    "status=ok",
    "status=passed",
    "state=ready",
    "result=ok",
    "result=passed",
    "=passed",
    "_ok=1",
    "ok=1",
    "passed=1",
    "ready=1",
    "state_ok=1",
)


@dataclass(frozen=True)
class CapabilityGroup:
    name: str
    host_modules: tuple[str, ...]
    plain_sources: tuple[str, ...]
    agentos_sources: tuple[str, ...]
    reader_keywords: tuple[str, ...]
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
        ("Overview", "Catalog", "Status"),
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
        ("Workflow", "Invocations", "Portability"),
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
        ("Workbench", "Project", "Runbook"),
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
        ("Artifacts", "Object", "Package"),
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
        ("Data", "Lab", "Analysis", "Statistical"),
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
        ("LLM", "Prompt", "Model"),
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
        ("Agent", "Collaboration", "Worker"),
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
        ("Provenance", "Evidence", "Search", "Timeline"),
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
        ("Governance", "Privacy", "Quality"),
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
        ("Execution", "Queue", "Telemetry", "Metrics"),
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
        ("Review", "Release", "Publication", "Delivery"),
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
        ("Dashboard", "API", "Site", "Cards"),
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
        ("AgentOS", "Compare", "Backend"),
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


def has_successful_state(state_dir: Path | None, state_name: str) -> bool:
    if state_dir is None:
        return False
    path = state_dir / state_name
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return any(marker in text for marker in SUCCESS_MARKERS)


def read_reader_text(root: Path) -> str:
    chunks: list[str] = []
    for path in [
        root / "host_tools" / "plain_ucore_reader.py",
        root / "docs" / "dual-targets.md",
        root / "docs" / "verification.md",
    ]:
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def missing_items(items: tuple[str, ...], available: set[str]) -> list[str]:
    return [item for item in items if item not in available]


def run_check(
    root: Path,
    host_dir: Path,
    require_host: bool,
    plain_state_dir: Path | None = None,
    agentos_state_dir: Path | None = None,
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
    plain_sources = collect_source_names(root, "user/src")
    agentos_sources = collect_source_names(root, "agentos_ucore/user/src")
    plain_state_names = collect_state_names(plain_state_dir) if plain_state_dir else set()
    agentos_state_names = collect_state_names(agentos_state_dir) if agentos_state_dir else set()
    check_runtime_state = plain_state_dir is not None or agentos_state_dir is not None
    if check_runtime_state and (plain_state_dir is None or agentos_state_dir is None):
        raise ValueError("plain and AgentOS state directories must be supplied together")
    reader_text = read_reader_text(root)
    groups: list[dict[str, object]] = []
    failures: list[str] = []

    for group in CAPABILITY_GROUPS:
        missing_host = missing_items(group.host_modules, host_modules)
        missing_plain = missing_items(group.plain_sources, plain_sources)
        missing_agentos = missing_items(group.agentos_sources, agentos_sources)
        missing_reader = []
        if group.reader_keywords and not any(keyword in reader_text for keyword in group.reader_keywords):
            missing_reader = list(group.reader_keywords)
        plain_runtime_candidates = runtime_candidates(group, group.plain_sources)
        agentos_runtime_candidates = runtime_candidates(group, group.agentos_sources)
        plain_runtime_hits = [
            name
            for name in plain_runtime_candidates
            if name in plain_state_names and has_successful_state(plain_state_dir, name)
        ]
        agentos_runtime_hits = [
            name
            for name in agentos_runtime_candidates
            if name in agentos_state_names and has_successful_state(agentos_state_dir, name)
        ]

        if missing_host:
            failures.append(f"{group.name}: missing host modules: {', '.join(missing_host)}")
        if missing_plain:
            failures.append(f"{group.name}: missing plain sources: {', '.join(missing_plain)}")
        if missing_agentos:
            failures.append(f"{group.name}: missing AgentOS sources: {', '.join(missing_agentos)}")
        if missing_reader:
            failures.append(f"{group.name}: missing Reader/doc keywords: {', '.join(missing_reader)}")
        if check_runtime_state and not plain_runtime_hits:
            failures.append(f"{group.name}: no successful plain runtime state file was produced")
        if check_runtime_state and not agentos_runtime_hits:
            failures.append(f"{group.name}: no successful AgentOS runtime state file was produced")
        group_failed = bool(
            missing_host
            or missing_plain
            or missing_agentos
            or missing_reader
            or (check_runtime_state and not plain_runtime_hits)
            or (check_runtime_state and not agentos_runtime_hits)
        )

        groups.append(
            {
                "name": group.name,
                "host_modules": len(group.host_modules),
                "plain_sources": len(group.plain_sources),
                "agentos_sources": len(group.agentos_sources),
                "reader_keywords": len(group.reader_keywords),
                "status": "failed" if group_failed else "ok",
                "missing_host": missing_host,
                "missing_plain": missing_plain,
                "missing_agentos": missing_agentos,
                "missing_reader": missing_reader,
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
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--require-host", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    host_dir = args.host_dir or default_host_dir(root)
    summary = run_check(root, host_dir, args.require_host, args.plain_state_dir, args.agentos_state_dir)

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
