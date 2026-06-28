#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from check_host_action_kind_alignment import collect_action_routes, default_host_dir
from plain_ucore_action_runner import action_kind, prepare_action_state, run_seeded_ucore


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def seeded_actions() -> list[dict[str, object]]:
    return [
        {
            "sequence": 1,
            "path": "/actions/research/rerun",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999-rerun",
                "parent_run": "RUN-999",
                "provider": "template",
                "question": "seeded action smoke",
                "dataset_rows": "3",
                "reference_entries": "2",
                "workspace_files": "4",
            },
        },
        {
            "sequence": 2,
            "path": "/actions/research/dataset",
            "status": "accepted",
            "payload": {
                "title": "Reusable response table",
                "dataset_rows": "3",
                "columns": "sample,group,value",
            },
        },
        {
            "sequence": 3,
            "path": "/actions/research/library-source",
            "status": "accepted",
            "payload": {
                "citation_key": "agentlibrary2026",
                "tags": "agent reusable",
            },
        },
        {
            "sequence": 4,
            "path": "/actions/research/template",
            "status": "accepted",
            "payload": {
                "name": "Reusable response comparison",
                "question": "Which group is stronger?",
                "provider_id": "template",
            },
        },
        {
            "sequence": 5,
            "path": "/actions/research/literature-search",
            "status": "accepted",
            "payload": {
                "query": "agent workflow provenance",
                "provider": "template",
                "max_results": "5",
            },
        },
        {
            "sequence": 6,
            "path": "/actions/research/evidence-review",
            "status": "accepted",
            "payload": {
                "search_id": "usable-literature-search:RUN-999:1",
                "reviewer": "Wang",
                "included": "3",
            },
        },
        {
            "sequence": 7,
            "path": "/actions/research/evidence-protocol",
            "status": "accepted",
            "payload": {
                "title": "Agent workflow evidence protocol",
                "research_question": "How do kernel Agent records improve provenance?",
                "outcome": "reproducible_evidence",
            },
        },
        {
            "sequence": 8,
            "path": "/actions/research/artifact-input",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999",
                "file": "reads_R1.fastq",
                "artifact_kind": "fastq",
                "sha256": "sha-input-999",
                "bytes": "2048",
                "source": "upload",
            },
        },
        {
            "sequence": 9,
            "path": "/actions/research/artifact-derive",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999-rerun",
                "input": "raw-counts.csv",
                "output": "normalized-counts.csv",
                "operation": "normalize",
                "stage": "analyze",
                "sha256": "sha-derived-999",
            },
        },
        {
            "sequence": 10,
            "path": "/actions/research/artifact-log",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999",
                "stage": "align",
                "log": "align.log",
                "level": "warn",
                "message": "quality_gate_retry",
            },
        },
        {
            "sequence": 11,
            "path": "/actions/research/artifact-chart",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999",
                "chart": "qc-chart.json",
                "chart_type": "line",
                "data_file": "normalized-counts.csv",
                "points": "12",
            },
        },
        {
            "sequence": 12,
            "path": "/actions/research/artifact-package",
            "status": "accepted",
            "payload": {
                "run_id": "RUN-999",
                "package": "artifact-bundle.zip",
                "manifest": "artifact-manifest.json",
                "files": "5",
                "status": "ready",
            },
        },
        {
            "sequence": 13,
            "path": "/actions/host-workflow/run",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "engine": "host-runner",
                "dag": "ingest>analyze>report",
                "retry_stage": "align",
                "cache_hit_stage": "profile",
                "worker_slots": "3",
                "queue_depth": "5",
                "observer_events": "9",
                "retry_reason": "quality_gate",
            },
        },
        {
            "sequence": 14,
            "path": "/actions/host-workflow/stage-attempt",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "stage": "align",
                "attempt": "2",
                "status": "failed",
                "command": "align_reads",
                "duration_ms": "1200",
            },
        },
        {
            "sequence": 15,
            "path": "/actions/host-workflow/cache-decision",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "stage": "profile",
                "cache_key": "cache:RUN-999:profile",
                "cache_result": "hit",
                "cache_policy": "content",
            },
        },
        {
            "sequence": 16,
            "path": "/actions/host-workflow/retry-decision",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "stage": "align",
                "retry_reason": "quality_gate",
                "next_attempt": "3",
                "decision": "rerun_stage",
            },
        },
        {
            "sequence": 17,
            "path": "/actions/host-workflow/artifact-manifest",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "artifact": "align.bam",
                "artifact_kind": "alignment",
                "sha256": "sha-host-artifact",
                "bytes": "4096",
            },
        },
        {
            "sequence": 18,
            "path": "/actions/host-workflow/report-export",
            "status": "accepted",
            "payload": {
                "workflow_id": "wf-host-999",
                "run_id": "RUN-999",
                "report": "workflow-report.md",
                "format": "markdown",
                "sections": "5",
                "status": "ready",
            },
        },
        {
            "sequence": 19,
            "path": "/actions/research/llm-relay-request",
            "status": "accepted",
            "payload": {
                "request_id": "host-q1",
                "route": "review_summary",
                "provider": "template",
            },
        },
        {
            "sequence": 20,
            "path": "/actions/research/llm-relay-response",
            "status": "accepted",
            "payload": {
                "response_id": "host-r1",
                "summary": "host_response_ready",
            },
        },
        {
            "sequence": 21,
            "path": "/actions/research/llm-relay-fallback",
            "status": "accepted",
            "payload": {
                "case": "missing_cloud_key",
                "action": "template_response",
            },
        },
        {
            "sequence": 22,
            "path": "/actions/research/workbench",
            "status": "accepted",
            "payload": {
                "workbench": "usable-workbench:RUN-900",
                "workbench_title": "RUN-900 workbench",
                "literature_query": "agent workflow provenance",
            },
        },
        {
            "sequence": 23,
            "path": "/actions/research/workbench-task",
            "status": "accepted",
            "payload": {
                "workbench": "WB-999",
                "task": "draft",
                "status": "queued",
            },
        },
        {
            "sequence": 24,
            "path": "/actions/research/workbench-file-verify",
            "status": "accepted",
            "payload": {
                "workbench": "usable-workbench:RUN-900",
                "manifest": "delivery-manifest.json",
                "files": "9",
                "sha_records": "9",
                "verified": "9",
                "missing": "0",
            },
        },
        {
            "sequence": 25,
            "path": "/actions/research/dataset-preview",
            "status": "accepted",
            "payload": {
                "dataset_id": "usable-dataset:response-table",
                "rows": "6",
                "quality": "passed",
            },
        },
        {
            "sequence": 26,
            "path": "/actions/research/dataset-run",
            "status": "accepted",
            "payload": {
                "dataset_id": "usable-dataset:response-table",
                "run_id": "usable-run:dataset:1",
                "provider_id": "template",
                "question": "Which group is stronger?",
                "artifacts": "2",
            },
        },
        {
            "sequence": 27,
            "path": "/actions/research/dataset-run-comparison",
            "status": "accepted",
            "payload": {
                "dataset_id": "usable-dataset:response-table",
                "left_run": "usable-run:dataset:1",
                "right_run": "usable-run:dataset:2",
                "decision": "stable",
            },
        },
        {
            "sequence": 28,
            "path": "/actions/research/project-scaffold",
            "status": "accepted",
            "payload": {
                "template_id": "scaffold-template:starter",
                "project_id": "lab-gene-x",
                "title": "AgentOS research project",
                "dataset_id": "usable-dataset:response-table",
                "library_source_id": "usable-source:library2026:1",
                "files": "8",
                "workspace": "workspace/lab-gene-x",
            },
        },
        {
            "sequence": 29,
            "path": "/actions/research/project-launch",
            "status": "accepted",
            "payload": {
                "project_id": "lab-gene-x",
                "scaffold_id": "scaffold:lab-gene-x:starter",
                "workbench_id": "usable-workbench:RUN-900",
                "run_id": "usable-run:RUN-900",
                "provider_id": "template",
                "question": "Can the system preserve provenance?",
            },
        },
        {
            "sequence": 30,
            "path": "/actions/research/project-action-execute",
            "status": "accepted",
            "payload": {
                "project_id": "lab-gene-x",
                "action_id": "usable-project-action:RUN-042:1",
                "action_key": "build_reproduction_package",
                "provider_id": "template",
                "max_steps": "5",
                "result": "completed",
            },
        },
        {
            "sequence": 31,
            "path": "/actions/research/study-protocol",
            "status": "accepted",
            "payload": {
                "protocol_id": "usable-study-protocol:variant-calling-qc",
                "title": "Variant calling QC",
                "question": "Are all workflow artifacts reproducible?",
                "hypothesis": "kernel-visible provenance reduces repair steps",
                "dataset_tags": "qc,variant",
                "source_tags": "agent,workflow",
            },
        },
        {
            "sequence": 32,
            "path": "/actions/research/study-protocol-launch",
            "status": "accepted",
            "payload": {
                "launch_id": "study-protocol-launch:RUN-042",
                "protocol_id": "usable-study-protocol:variant-calling-qc",
                "run_id": "RUN-042",
                "provider_id": "template",
            },
        },
        {
            "sequence": 33,
            "path": "/actions/research/study-protocol-reproduction-package-action-execute",
            "status": "accepted",
            "payload": {
                "package_id": "study-protocol-reproduction-package:RUN-042",
                "steps_done": "5",
                "result": "passed",
                "provider_id": "template",
            },
        },
        {
            "sequence": 34,
            "path": "/actions/research/project-release-gate",
            "status": "accepted",
            "payload": {
                "project_id": "proj-999",
                "decision": "approved",
            },
        },
        {
            "sequence": 35,
            "path": "/actions/research/project-provenance-graph",
            "status": "accepted",
            "payload": {
                "project_id": "proj-999",
                "nodes": "12",
                "edges": "18",
                "dot": "project-provenance.dot",
            },
        },
        {
            "sequence": 36,
            "path": "/actions/research/project-delivery",
            "status": "accepted",
            "payload": {
                "project_id": "proj-999",
                "bundle": "project-bundle.zip",
                "decision": "approved",
                "release_gate": "passed",
                "handoff": "ready",
            },
        },
        {
            "sequence": 37,
            "path": "/actions/workflow-portability/run",
            "status": "accepted",
            "payload": {
                "import_id": "workflow-import:host-nextflow",
                "source_format": "nextflow",
                "source": "main.host.nf",
                "target_runtime": "agentos-ucore",
                "execution_plan": "workflow-migration-execution-plan:host-nextflow:agentcompare",
                "compare_profile": "compare-profile:host-nextflow:migration",
                "scenario_id": "backend-scenario:host-nextflow",
                "rehearsal_status": "passed",
                "readiness_decision": "ready_for_agentos",
                "package": "workflow-portability-host.zip",
            },
        },
        {
            "sequence": 38,
            "path": "/actions/workflow-portability/import",
            "status": "accepted",
            "payload": {
                "import_id": "workflow-import:host-nextflow",
                "source_format": "nextflow",
                "source": "main.host.nf",
                "normalized_steps": "15",
                "adapter_id": "adapter:nextflow",
            },
        },
        {
            "sequence": 39,
            "path": "/actions/workflow-portability/plan",
            "status": "accepted",
            "payload": {
                "import_id": "workflow-import:host-nextflow",
                "migration_plan": "workflow-migration-plan:host-nextflow",
                "target_runtime": "agentos-ucore",
                "migration_steps": "9",
                "risk_items": "4",
            },
        },
        {
            "sequence": 40,
            "path": "/actions/workflow-portability/bind",
            "status": "accepted",
            "payload": {
                "execution_plan": "workflow-migration-execution-plan:host-nextflow:agentcompare",
                "compare_profile": "compare-profile:host-nextflow:migration",
                "scenario_id": "backend-scenario:host-nextflow",
                "backend_cases": "4",
            },
        },
        {
            "sequence": 41,
            "path": "/actions/workflow-portability/rehearse",
            "status": "accepted",
            "payload": {
                "rehearsal_id": "workflow-rehearsal:host-nextflow",
                "binding_id": "workflow-migration-binding:RUN-042:plain-ucore",
                "rehearsal_status": "passed",
                "observed_ready": "3",
                "skipped": "0",
            },
        },
        {
            "sequence": 42,
            "path": "/actions/workflow-portability/review",
            "status": "accepted",
            "payload": {
                "review_id": "workflow-migration-readiness:RUN-042",
                "readiness_decision": "ready_for_agentos",
                "blocking_items": "0",
                "work_items": "6",
            },
        },
        {
            "sequence": 43,
            "path": "/actions/workflow-portability/package",
            "status": "accepted",
            "payload": {
                "import_id": "workflow-import:host-nextflow",
                "package": "workflow-portability-host.zip",
                "export_format": "zip",
                "bundle": "workflow-portability-host.zip",
            },
        },
        {
            "sequence": 44,
            "path": "/actions/agentcompare/run",
            "status": "accepted",
            "payload": {
                "profile": "plain-vs-agentos",
            },
        },
    ]


def seeded_action_kinds() -> list[str]:
    return [action_kind(str(action["path"])) for action in seeded_actions()]


def seeded_route_coverage(root: Path, host_dir: Path | None = None) -> dict[str, object]:
    resolved_host_dir = host_dir or default_host_dir(root)
    seeded_paths = [str(action["path"]) for action in seeded_actions()]
    seeded_path_set = set(seeded_paths)
    seeded_kinds = sorted(set(seeded_action_kinds()))
    if not resolved_host_dir.exists():
        return {
            "status": "skipped",
            "reason": "host_platform_not_found",
            "host_dir": str(resolved_host_dir),
            "seeded_actions": len(seeded_paths),
            "seeded_kinds": len(seeded_kinds),
        }

    failures: list[str] = []
    try:
        host_routes = collect_action_routes(resolved_host_dir)
    except FileNotFoundError as exc:
        host_routes = []
        failures.append(str(exc))

    host_route_set = set(host_routes)
    host_kinds = sorted(set(action_kind(route) for route in host_routes))
    known_seeded_routes = sorted(seeded_path_set & host_route_set)
    unknown_seeded_routes = sorted(seeded_path_set - host_route_set)

    seeded_kind_set = set(seeded_kinds)
    covered_host_kinds = sorted(kind for kind in host_kinds if kind in seeded_kind_set)
    uncovered_host_kinds = sorted(kind for kind in host_kinds if kind not in seeded_kind_set)
    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(resolved_host_dir),
        "host_action_routes": len(host_routes),
        "host_action_kinds": len(host_kinds),
        "seeded_actions": len(seeded_paths),
        "seeded_known_routes": len(known_seeded_routes),
        "seeded_extra_routes": unknown_seeded_routes,
        "seeded_kinds": len(seeded_kinds),
        "seeded_host_kinds": len(covered_host_kinds),
        "uncovered_host_kinds": uncovered_host_kinds,
        "uncovered_host_kind_sample": uncovered_host_kinds[:16],
        "failures": failures,
    }


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def require_contains(failures: list[str], label: str, state_dir: Path, name: str, token: str) -> None:
    path = state_dir / name
    text = read_text(path)
    if not text:
        failures.append(f"{label}: missing {name}")
        return
    if token not in text:
        failures.append(f"{label}: {name} missing {token}")


def validate_seed_package(label: str, run_dir: Path) -> list[str]:
    failures: list[str] = []
    state_dir = run_dir / "state-next"
    for kind in seeded_action_kinds():
        require_contains(failures, label, state_dir, "rp_host_action_seed", f"kind={kind}")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "run_id=RUN-999-rerun")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "parent_run=RUN-999")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "title=Reusable response table")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "citation_key=agentlibrary2026")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "workflow_id=wf-host-999")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "request_id=host-q1")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "response_id=host-r1")
    require_contains(failures, label, state_dir, "rp_host_action_seed", "compare_profile=compare-profile:host-nextflow:migration")
    return failures


def validate_extracted_state(label: str, run_dir: Path) -> list[str]:
    failures: list[str] = []
    state_dir = run_dir / "state-extracted"
    if not state_dir.is_dir():
        return [f"{label}: missing extracted state"]

    require_contains(failures, label, state_dir, "rp_input", "host_action_rerun_id=RUN-999-rerun")
    require_contains(failures, label, state_dir, "rp_input", "host_action_rerun_parent=RUN-999")
    require_contains(failures, label, state_dir, "rp_input", "host_action_rerun_provider=template")
    require_contains(failures, label, state_dir, "rp_input", "host_action_dataset=registered")
    require_contains(failures, label, state_dir, "rp_input", "host_action_dataset_title=Reusable response table")
    require_contains(failures, label, state_dir, "rp_input", "host_action_library_citation=agentlibrary2026")
    require_contains(failures, label, state_dir, "rp_input", "host_action_template_name=Reusable response comparison")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_rerun=usable-run:RUN-999-rerun;parent=RUN-999;status=completed")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_kind=research_rerun")
    require_contains(failures, label, state_dir, "rp_lit", "host_action_literature_query=agent workflow provenance")
    require_contains(failures, label, state_dir, "rp_lit", "host_action_protocol_title=Agent workflow evidence protocol")
    require_contains(failures, label, state_dir, "rp_knowledge", "host_action_evidence_included=3")
    require_contains(failures, label, state_dir, "rp_report_text", "host_report_rerun_id=RUN-999-rerun")
    require_contains(failures, label, state_dir, "rp_report_text", "host_report_rerun_parent=RUN-999")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_manifest_rerun=RUN-999-rerun;parent=RUN-999;status=ready")
    require_contains(failures, label, state_dir, "rp_artifact", "host_artifact_input=reads_R1.fastq;kind=fastq;sha256=sha-input-999;bytes=2048;source=upload")
    require_contains(failures, label, state_dir, "rp_artifact", "host_artifact_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999")
    require_contains(failures, label, state_dir, "rp_stage_log", "host_artifact_log=align.log;stage=align;level=warn;message=quality_gate_retry")
    require_contains(failures, label, state_dir, "rp_chart_data", "host_artifact_chart=qc-chart.json;type=line;data_file=normalized-counts.csv;points=12")
    require_contains(failures, label, state_dir, "rp_package", "host_artifact_package=artifact-bundle.zip;manifest=artifact-manifest.json;files=5;status=ready")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_artifact_manifest_derive=raw-counts.csv;output=normalized-counts.csv;operation=normalize;stage=analyze;sha256=sha-derived-999")
    require_contains(failures, label, state_dir, "rp_stage_dag", "host_workflow_id=wf-host-999")
    require_contains(failures, label, state_dir, "rp_stage_dag", "host_workflow_dag=ingest>analyze>report")
    require_contains(failures, label, state_dir, "rp_stage_state", "host_workflow_run_id=RUN-999")
    require_contains(failures, label, state_dir, "rp_stage_state", "host_workflow_stage_action=align;attempt=2;status=failed;command=align_reads;duration_ms=1200")
    require_contains(failures, label, state_dir, "rp_cache_index", "host_workflow_cache_action=profile;key=cache:RUN-999:profile;result=hit;policy=content")
    require_contains(failures, label, state_dir, "rp_retry_plan", "host_workflow_retry_action=align;reason=quality_gate;next_attempt=3;decision=rerun_stage")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_workflow=wf-host-999;run_id=RUN-999;engine=host-runner;status=ready")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_workflow_artifact_action=align.bam;kind=alignment;sha256=sha-host-artifact;bytes=4096")
    require_contains(failures, label, state_dir, "rp_report_text", "host_workflow_report_action=workflow-report.md;format=markdown;sections=5;status=ready")
    require_contains(failures, label, state_dir, "rp_llm_packets", "host_llm_packet_request=host-q1")
    require_contains(failures, label, state_dir, "rp_llm_resp", "host_llm_response_id=host-r1")
    require_contains(failures, label, state_dir, "rp_llm_fallback", "host_llm_fallback_case=missing_cloud_key")
    require_contains(failures, label, state_dir, "rp_llm_routes", "host_llm_route=review_summary")
    require_contains(failures, label, state_dir, "rp_llm_routes", "host_llm_route_provider=template")
    require_contains(failures, label, state_dir, "rp_llm_guard", "host_llm_guard_status=passed")
    require_contains(failures, label, state_dir, "rp_report_text", "host_report_workbench_task=draft")
    require_contains(failures, label, state_dir, "rp_artifact_manifest", "host_manifest_workbench_task=draft")
    require_contains(failures, label, state_dir, "rp_runner", "host_action_workbench_file_verify=passed")
    require_contains(failures, label, state_dir, "rp_usableds", "host_action_dataset_ops=applied")
    require_contains(failures, label, state_dir, "rp_usableds", "preview_dataset=usable-dataset:response-table")
    require_contains(failures, label, state_dir, "rp_usableds", "dataset_run=usable-run:dataset:1")
    require_contains(failures, label, state_dir, "rp_usableds", "run_comparison=stable")
    require_contains(failures, label, state_dir, "rp_usableproj", "host_action_project_scaffold=lab-gene-x;template=scaffold-template:starter;workspace=workspace/lab-gene-x;files=8")
    require_contains(failures, label, state_dir, "rp_usableproj", "host_action_project_launch=lab-gene-x;scaffold=scaffold:lab-gene-x:starter;workbench=usable-workbench:RUN-900;run=usable-run:RUN-900;provider=template")
    require_contains(failures, label, state_dir, "rp_usablepack", "host_action_project_action_execute=lab-gene-x;action=usable-project-action:RUN-042:1;key=build_reproduction_package;provider=template;result=completed")
    require_contains(failures, label, state_dir, "rp_studyproto", "host_action_study_protocol=applied;protocol=usable-study-protocol:variant-calling-qc;title=Variant calling QC;launch=study-protocol-launch:RUN-042")
    require_contains(failures, label, state_dir, "rp_studyproto", "action_execute_result=passed;status=ready")
    require_contains(failures, label, state_dir, "rp_web_bundle", "host_action_project_release_gate=approved")
    require_contains(failures, label, state_dir, "rp_web_bundle", "host_action_project_provenance_graph=exported")
    require_contains(failures, label, state_dir, "rp_web_bundle", "host_action_project_delivery=project-bundle.zip")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_import=workflow-import:host-nextflow;format=nextflow;source=main.host.nf")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_target=agentos-ucore")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_compare_profile=compare-profile:host-nextflow:migration")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_import_action=workflow-import:host-nextflow;format=nextflow;source=main.host.nf;normalized_steps=15;adapter=adapter:nextflow")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_plan_action=workflow-migration-plan:host-nextflow;target=agentos-ucore;steps=9;risks=4")
    require_contains(failures, label, state_dir, "rp_wfio", "host_portability_package_action=workflow-portability-host.zip;format=zip;import=workflow-import:host-nextflow;bundle=workflow-portability-host.zip")
    require_contains(failures, label, state_dir, "rp_agentcmp", "host_action_compare_profile=plain-vs-agentos")
    return failures


def run_target(
    label: str,
    repo_dir: Path,
    work_dir: Path,
    timeout: int,
    wsl_distro: str,
    chapter: str,
    init_proc: str,
    pass_marker: str,
) -> dict[str, object]:
    run_dir = work_dir / label
    state_dir = run_dir / "state-current"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"seeded_action_state: {label} start chapter={chapter} init={init_proc} action_count={len(seeded_actions())} log={run_dir / 'ucore-run.log'}",
        flush=True,
    )
    prepare_summary = prepare_action_state(seeded_actions(), state_dir, run_dir)
    run_summary = run_seeded_ucore(
        repo_dir,
        run_dir,
        timeout,
        wsl_distro,
        chapter=chapter,
        init_proc=init_proc,
        pass_marker=pass_marker,
    )
    failures = validate_seed_package(label, run_dir) + validate_extracted_state(label, run_dir)
    if not run_summary.get("passed"):
        failures.append(f"{label}: qemu run failed")
    if int(run_summary.get("extracted_state_files", 0) or 0) < 200:
        failures.append(f"{label}: extracted too few state files")

    print(
        "seeded_action_state: {label} done passed={passed} extracted_state_files={files} status={status}".format(
            label=label,
            passed=int(bool(run_summary.get("passed", False))),
            files=run_summary.get("extracted_state_files", 0),
            status="ready" if not failures else "failed",
        ),
        flush=True,
    )
    return {
        "label": label,
        "repo_dir": str(repo_dir),
        "run_dir": str(run_dir),
        "prepare": prepare_summary,
        "run": run_summary,
        "failures": failures,
        "status": "ready" if not failures else "failed",
    }


def run_check(root: Path, work_dir: Path, timeout: int, wsl_distro: str) -> dict[str, object]:
    plain = run_target(
        "plain",
        root / "baseline_ucore",
        work_dir,
        timeout,
        wsl_distro,
        chapter="platform_seeded",
        init_proc="rp_seed_orch",
        pass_marker="rp_orch: passed",
    )
    agentos = run_target(
        "agentos",
        root,
        work_dir,
        timeout,
        wsl_distro,
        chapter="platform_agentos",
        init_proc="rp_agentos_orch",
        pass_marker="rp_agentos_orch: passed",
    )
    failures = list(plain["failures"]) + list(agentos["failures"])
    coverage = seeded_route_coverage(root)
    if coverage.get("status") == "failed":
        failures.extend(str(item) for item in coverage.get("failures", []))
    return {
        "status": "ready" if not failures else "failed",
        "action": "/actions/research/rerun",
        "action_count": len(seeded_actions()),
        "action_kinds": seeded_action_kinds(),
        "coverage": coverage,
        "plain": plain,
        "agentos": agentos,
        "failures": failures,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Run seeded host actions through plain uCore and AgentOS-uCore.")
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/agentos-seeded-action-state"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--wsl-distro", default="Ubuntu")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    summary = run_check(root, args.work_dir, args.timeout, args.wsl_distro)
    if args.json_out:
        write_json(args.json_out, summary)
    coverage = summary.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    print(
        "seeded_action_state: action={action} action_count={action_count} host_routes={host_routes} seeded_routes={seeded_routes} seeded_kinds={seeded_kinds} plain={plain_status} agentos={agentos_status} status={status}".format(
            action=summary["action"],
            action_count=summary["action_count"],
            host_routes=coverage.get("host_action_routes", "skipped"),
            seeded_routes=coverage.get("seeded_known_routes", "skipped"),
            seeded_kinds=coverage.get("seeded_host_kinds", coverage.get("seeded_kinds", "skipped")),
            plain_status=summary["plain"]["status"],
            agentos_status=summary["agentos"]["status"],
            status=summary["status"],
        )
    )
    for failure in summary["failures"]:
        print(f"seeded_action_state: failed: {failure}")
    return 0 if summary["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
