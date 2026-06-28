#!/usr/bin/env python3
"""Unit checks for plain_ucore_action_runner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import plain_ucore_action_runner as runner


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_dir = root / "state"
        run_dir = root / "run"
        state_dir.mkdir()
        (state_dir / "rp_input").write_text("input=ready\n", encoding="utf-8")
        (state_dir / "rp_web_bundle").write_text("bundle=ready\n", encoding="utf-8")
        (state_dir / "rp_agentcmp").write_text("compare=ready\n", encoding="utf-8")
        (state_dir / "ignore.txt").write_text("ignore\n", encoding="utf-8")

        actions_path = root / "host-actions.jsonl"
        actions = [
            {
                "sequence": 1,
                "path": "/actions/research/run",
                "status": "accepted",
                "payload": {"run_id": "RUN-999", "source": "test"},
            },
            {
                "sequence": 2,
                "path": "/actions/agentcompare/run",
                "status": "accepted",
                "payload": {"profile": "plain_ucore_batch"},
            },
        ]
        actions_path.write_text(
            "\n".join(json.dumps(action, ensure_ascii=False) for action in actions) + "\n",
            encoding="utf-8",
        )

        loaded = runner.read_jsonl(actions_path)
        loaded = runner.append_records(
            loaded,
            [
                {
                    "path": "/actions/research/rerun",
                    "payload": {"run_id": "RUN-999-rerun", "parent_run": "RUN-999", "provider": "template", "question": "Repeat the host run with saved inputs"},
                },
                {
                    "path": "/actions/research/dataset",
                    "payload": {"title": "Host reusable response table", "dataset_rows": "6", "columns": "sample,group,value"},
                },
                {
                    "path": "/actions/research/studio-launch",
                    "payload": {"title": "Studio cytokine evidence", "goal": "Determine whether recovery evidence is ready", "direction": "evidence review", "material_notes": "Small demonstration table for the studio workflow.", "provider_id": "template", "workbench_id": "W1", "latest_run_id": "R1", "latest_answer_id": "answer1"},
                },
                {
                    "path": "/actions/research/library-source",
                    "payload": {"citation_key": "hostlibrary2026", "tags": "host reusable"},
                },
                {
                    "path": "/actions/research/template",
                    "payload": {"name": "Host response template", "question": "Which host dataset group is stronger?", "provider_id": "template"},
                },
                {
                    "path": "/actions/research/inspect-workspace",
                    "payload": {"root": "host-workspace", "max_files": "9"},
                },
                {
                    "path": "/actions/research/import-workspace",
                    "payload": {"root": "host-workspace", "manifest": "host-workspace-manifest.json"},
                },
                {
                    "path": "/actions/research/literature-search",
                    "payload": {"query": "agent workflow provenance", "provider": "local", "max_results": "7"},
                },
                {
                    "path": "/actions/research/evidence-review",
                    "payload": {"search_id": "usable-literature-search:RUN-999:1", "reviewer": "Wang", "included": "4"},
                },
                {
                    "path": "/actions/research/evidence-protocol",
                    "payload": {"title": "Host evidence protocol", "research_question": "Which mechanisms improve traceability?", "outcome": "traceability"},
                },
                {
                    "path": "/actions/research/workbench",
                    "payload": {"workbench": "usable-workbench:RUN-900", "workbench_title": "RUN-900 workbench", "literature_query": "agent workflow provenance"},
                },
                {
                    "path": "/actions/research/workbench-complete",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-advance",
                    "payload": {"workbench": "usable-workbench:RUN-900", "task": "delivery_manifest"},
                },
                {
                    "path": "/actions/research/workbench-auto-advance",
                    "payload": {"workbench": "usable-workbench:RUN-900", "step_limit": "8"},
                },
                {
                    "path": "/actions/research/workbench-answer",
                    "payload": {"workbench": "usable-workbench:RUN-900", "question": "What is ready for review?"},
                },
                {
                    "path": "/actions/research/workbench-answer-audit",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-evidence-search",
                    "payload": {"workbench": "usable-workbench:RUN-900", "query": "recovery evidence"},
                },
                {
                    "path": "/actions/research/workbench-task",
                    "payload": {"workbench": "usable-workbench:RUN-900", "task": "human_review", "status": "waiting"},
                },
                {
                    "path": "/actions/research/workbench-note",
                    "payload": {"workbench": "usable-workbench:RUN-900", "note_kind": "decision", "title": "Scope decision", "body": "Use recovered evidence first."},
                },
                {
                    "path": "/actions/research/workbench-notes",
                    "payload": {"workbench": "usable-workbench:RUN-900", "notes_filter": "decision"},
                },
                {
                    "path": "/actions/research/workbench-handoff-package",
                    "payload": {"workbench": "usable-workbench:RUN-900", "handoff_scope": "full"},
                },
                {
                    "path": "/actions/research/workbench-readiness",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-brief",
                    "payload": {"workbench": "usable-workbench:RUN-900", "brief_format": "html"},
                },
                {
                    "path": "/actions/research/workbench-evidence-dossier",
                    "payload": {"workbench": "usable-workbench:RUN-900", "dossier_format": "markdown"},
                },
                {
                    "path": "/actions/research/workbench-evidence-graph",
                    "payload": {"workbench": "usable-workbench:RUN-900", "graph_format": "dot"},
                },
                {
                    "path": "/actions/research/workbench-citations",
                    "payload": {"workbench": "usable-workbench:RUN-900", "citation_format": "bibtex"},
                },
                {
                    "path": "/actions/research/workbench-manuscript",
                    "payload": {"workbench": "usable-workbench:RUN-900", "manuscript_format": "markdown"},
                },
                {
                    "path": "/actions/research/workbench-manuscript-audit",
                    "payload": {"workbench": "usable-workbench:RUN-900", "audit_scope": "citations"},
                },
                {
                    "path": "/actions/research/workbench-manuscript-revision-plan",
                    "payload": {"workbench": "usable-workbench:RUN-900", "revision_area": "methods"},
                },
                {
                    "path": "/actions/research/workbench-manuscript-revision-task",
                    "payload": {"workbench": "usable-workbench:RUN-900", "revision_task": "1", "revision_status": "done"},
                },
                {
                    "path": "/actions/research/workbench-task-board",
                    "payload": {"workbench": "usable-workbench:RUN-900", "board_filter": "open"},
                },
                {
                    "path": "/actions/research/workbench-task-board-row",
                    "payload": {"workbench": "usable-workbench:RUN-900", "row_id": "usable-workbench:RUN-900:board:task:human_review", "row_status": "done"},
                },
                {
                    "path": "/actions/research/workbench-runbook",
                    "payload": {"workbench": "usable-workbench:RUN-900", "runbook_format": "markdown"},
                },
                {
                    "path": "/actions/research/workbench-timeline",
                    "payload": {"workbench": "usable-workbench:RUN-900", "timeline_format": "html"},
                },
                {
                    "path": "/actions/research/workbench-file-manifest",
                    "payload": {"workbench": "usable-workbench:RUN-900", "manifest": "delivery-manifest.json", "files": "9", "sha_records": "9"},
                },
                {
                    "path": "/actions/research/workbench-file-verify",
                    "payload": {"workbench": "usable-workbench:RUN-900", "manifest": "delivery-manifest.json", "files": "9", "sha_records": "9", "verified": "9", "missing": "0"},
                },
                {
                    "path": "/actions/research/export-workbench",
                    "payload": {"workbench": "usable-workbench:RUN-900", "bundle": "workbench-bundle.zip"},
                },
                {
                    "path": "/actions/research/operations-report",
                    "payload": {"format": "markdown"},
                },
                {
                    "path": "/actions/research/operations-advance-next",
                    "payload": {"provider_id": "template", "max_steps": "5", "review_decision": "approved", "delivery_audience": "reviewer"},
                },
                {
                    "path": "/actions/research/operations-execute-next-plan",
                    "payload": {"provider_id": "template", "max_steps": "6", "answer_question": "What next?", "delivery_audience": "reviewer"},
                },
                {
                    "path": "/actions/research/workbench-delivery-dashboard",
                    "payload": {"query": "ready", "include_clean": "1"},
                },
                {
                    "path": "/actions/research/workbench-delivery-execute-next",
                    "payload": {"query": "ready", "provider_id": "template", "max_steps": "4", "answer_question": "Repair delivery?"},
                },
                {
                    "path": "/actions/research/workbench-quality-gate",
                    "payload": {"workbench_id": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-quality-repair-plan",
                    "payload": {"workbench_id": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-quality-repair-execute",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "repair_id": "repair1", "action_key": "export_file_manifest_and_verify", "provider_id": "template", "max_steps": "3"},
                },
                {
                    "path": "/actions/research/workbench-plan-queue-row",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "source_type": "readiness", "source_id": "literature_search", "status": "done"},
                },
                {
                    "path": "/actions/research/workbench-plan-queue-execute",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "source_type": "readiness", "source_id": "literature_search", "provider_id": "template", "max_steps": "4"},
                },
                {
                    "path": "/actions/research/workbench-action-item",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "title": "Review search hits", "instruction": "Check citations", "priority": "high", "status": "open"},
                },
                {
                    "path": "/actions/research/project-space",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "project_id": "lab-gene-x", "query": "recovery"},
                },
                {
                    "path": "/actions/research/project-space-note",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "kind": "decision", "title": "Project scope", "body": "Keep recovered evidence first."},
                },
                {
                    "path": "/actions/research/project-space-action-item",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "title": "Project task", "instruction": "Prepare handoff", "priority": "high"},
                },
                {
                    "path": "/actions/research/project-space-answer",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "question": "What is ready?", "limit": "6"},
                },
                {
                    "path": "/actions/research/project-space-repair-execute",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "repair_id": "repair1", "provider_id": "template", "max_steps": "4"},
                },
                {
                    "path": "/actions/research/project-handoff-audit",
                    "payload": {"project_id": "lab-gene-x", "scope": "full", "decision": "ready"},
                },
                {
                    "path": "/actions/research/project-release-gate",
                    "payload": {"project_id": "lab-gene-x", "decision": "release", "checks": "6", "required_actions": "0", "suggested_actions": "2"},
                },
                {
                    "path": "/actions/research/project-snapshot",
                    "payload": {"project_id": "lab-gene-x", "snapshot_id": "project-snapshot:lab-gene-x:1", "files": "11", "hash_records": "11", "changes": "0"},
                },
                {
                    "path": "/actions/research/project-snapshot-comparison",
                    "payload": {"project_id": "lab-gene-x", "left": "snapshot0", "right": "snapshot1", "changed_files": "0", "decision": "stable"},
                },
                {
                    "path": "/actions/research/project-reproducibility-audit",
                    "payload": {"project_id": "lab-gene-x", "inputs": "2", "outputs": "8", "notebooks": "2", "claim_audits": "1", "decision": "passed"},
                },
                {
                    "path": "/actions/research/project-provenance-graph",
                    "payload": {"project_id": "lab-gene-x", "nodes": "9", "edges": "12", "dot": "project-provenance.dot"},
                },
                {
                    "path": "/actions/research/project-delivery",
                    "payload": {"project_id": "lab-gene-x", "bundle": "project-bundle.zip", "decision": "ready", "release_gate": "release", "handoff": "ready"},
                },
                {
                    "path": "/actions/research/package-intake",
                    "payload": {"package_id": "external-review", "label": "External review package", "files": "5", "sha256": "checked", "decision": "accepted"},
                },
                {
                    "path": "/actions/research-search/save",
                    "payload": {"query": "recovery evidence", "name": "Recovery search"},
                },
                {
                    "path": "/actions/research-search/export",
                    "payload": {"query": "recovery evidence", "limit": "20"},
                },
                {
                    "path": "/actions/research-search/note",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "query": "recovery evidence", "title": "Search note", "note": "Keep hits."},
                },
                {
                    "path": "/actions/research-search/action-item",
                    "payload": {"workbench_id": "usable-workbench:RUN-900", "query": "recovery evidence", "title": "Review search hits", "instruction": "Promote key hit", "priority": "high"},
                },
                {
                    "path": "/actions/host-workflow/run",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "engine": "plain-c-runner", "stages": "6", "dag": "ingest>clean>analyze>review>package", "max_workers": "2", "worker_slots": "2", "queue_depth": "5", "observer_events": "12", "failed_stage": "clean", "retry_stage": "clean", "cache_hit_stage": "analyze", "retry_reason": "checksum_mismatch", "cache": "content"},
                },
                {
                    "path": "/actions/host-workflow/export",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "format": "json", "bundle": "wf.zip"},
                },
                {
                    "path": "/actions/host-workflow/stage-attempt",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "stage": "clean", "attempt": "2", "status": "failed", "command": "clean_reads", "duration_ms": "1200"},
                },
                {
                    "path": "/actions/host-workflow/cache-decision",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "stage": "analyze", "cache_key": "cache:WF1:analyze", "cache_result": "hit", "cache_policy": "content"},
                },
                {
                    "path": "/actions/host-workflow/retry-decision",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "stage": "clean", "retry_reason": "checksum_mismatch", "next_attempt": "3", "decision": "rerun_stage"},
                },
                {
                    "path": "/actions/host-workflow/artifact-manifest",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "artifact": "clean.metrics.json", "artifact_kind": "metrics", "sha256": "sha-host-wf1", "bytes": "4096"},
                },
                {
                    "path": "/actions/host-workflow/report-export",
                    "payload": {"workflow_id": "WF1", "run_id": "RUN-999", "report": "workflow-report.md", "format": "markdown", "sections": "5", "status": "ready"},
                },
                {
                    "path": "/actions/research/artifact-input",
                    "payload": {"run_id": "RUN-999", "file": "reads_R1.fastq", "artifact_kind": "fastq", "sha256": "sha-host-input", "bytes": "2048", "source": "upload"},
                },
                {
                    "path": "/actions/research/artifact-derive",
                    "payload": {"run_id": "RUN-999", "input": "reads_R1.fastq", "output": "clean_reads.fastq", "operation": "trim", "stage": "clean", "sha256": "sha-host-derived"},
                },
                {
                    "path": "/actions/research/artifact-log",
                    "payload": {"run_id": "RUN-999", "stage": "clean", "log": "clean.log", "level": "warn", "message": "adapter_trimmed"},
                },
                {
                    "path": "/actions/research/artifact-chart",
                    "payload": {"run_id": "RUN-999", "chart": "qc-chart.json", "chart_type": "line", "data_file": "clean.metrics.json", "points": "12"},
                },
                {
                    "path": "/actions/research/artifact-package",
                    "payload": {"run_id": "RUN-999", "package": "artifact-bundle.zip", "manifest": "artifact-manifest.json", "files": "5", "status": "ready"},
                },
                {
                    "path": "/actions/workflow-portability/run",
                    "payload": {"import_id": "workflow-import:WF1:nextflow", "source_format": "nextflow", "source": "main.wf1.nf", "target_runtime": "agentos-ucore", "execution_plan": "workflow-migration-execution-plan:WF1:agentcompare", "compare_profile": "compare-profile:WF1:migration", "scenario_id": "backend-scenario:WF1", "rehearsal_status": "passed", "readiness_decision": "ready_for_agentos", "package": "wf-portability.zip"},
                },
                {
                    "path": "/actions/workflow-portability/import",
                    "payload": {"import_id": "workflow-import:WF1:nextflow", "source_format": "nextflow", "source": "main.wf1.nf", "normalized_steps": "15", "adapter_id": "adapter:WF1:nextflow"},
                },
                {
                    "path": "/actions/workflow-portability/plan",
                    "payload": {"import_id": "workflow-import:WF1:nextflow", "migration_plan": "workflow-migration-plan:WF1", "target_runtime": "agentos-ucore", "migration_steps": "9", "risk_items": "4"},
                },
                {
                    "path": "/actions/workflow-portability/bind",
                    "payload": {"execution_plan": "workflow-migration-execution-plan:WF1:agentcompare", "compare_profile": "compare-profile:WF1:migration", "scenario_id": "backend-scenario:WF1", "backend_cases": "4"},
                },
                {
                    "path": "/actions/workflow-portability/rehearse",
                    "payload": {"rehearsal_id": "workflow-rehearsal:WF1", "binding_id": "workflow-migration-binding:WF1", "rehearsal_status": "passed", "observed_ready": "3", "skipped": "1"},
                },
                {
                    "path": "/actions/workflow-portability/review",
                    "payload": {"review_id": "workflow-migration-readiness:WF1", "readiness_decision": "ready_for_agentos", "blocking_items": "0", "work_items": "6"},
                },
                {
                    "path": "/actions/workflow-portability/package",
                    "payload": {"import_id": "workflow-import:WF1:nextflow", "package": "wf-portability.zip", "export_format": "zip", "bundle": "wf-portability.zip"},
                },
                {
                    "path": "/actions/research/llm-relay-request",
                    "payload": {"request_id": "llm-q1", "run_id": "RUN-999", "route": "review_summary", "provider": "host-relay", "prompt": "summarize_recovery_evidence", "budget": "2048", "secret_ref": "host_env"},
                },
                {
                    "path": "/actions/research/llm-relay-response",
                    "payload": {"request_id": "llm-q1", "response_id": "llm-r1", "provider": "host-relay", "mode": "template", "summary": "Recovered_evidence_ready", "citations": "5"},
                },
                {
                    "path": "/actions/research/llm-relay-fallback",
                    "payload": {"case": "missing_cloud_key", "action": "template_response", "reason": "host_env_absent", "fallback_status": "ready"},
                },
                {
                    "path": "/actions/research/review",
                    "payload": {"run_id": "RUN-999", "reviewer": "Wang", "decision": "needs_revision"},
                },
                {
                    "path": "/actions/research/revision-task",
                    "payload": {"review_id": "usable-review:Wang:1", "targets": "methods,chart_caption,statistics"},
                },
                {
                    "path": "/actions/research/export-notebook",
                    "payload": {"format": "ipynb"},
                },
                {
                    "path": "/actions/research/export-bundle",
                    "payload": {"run_id": "RUN-999", "bundle": "reviewer-evidence"},
                },
            ],
        )
        summary = runner.prepare_action_state(loaded, state_dir, run_dir)
        expected_actions = 93

        assert summary["actions"] == expected_actions
        assert summary["accepted"] == expected_actions
        assert "research_run" in summary["kinds"]
        assert "dataset" in summary["kinds"]
        assert "studio_launch" in summary["kinds"]
        assert "library_source" in summary["kinds"]
        assert "template" in summary["kinds"]
        assert "workspace_inspect" in summary["kinds"]
        assert "workspace_import" in summary["kinds"]
        assert "literature_search" in summary["kinds"]
        assert "evidence_review" in summary["kinds"]
        assert "evidence_protocol" in summary["kinds"]
        assert "agentcompare" in summary["kinds"]
        assert "workbench" in summary["kinds"]
        assert "workbench_complete" in summary["kinds"]
        assert "workbench_advance" in summary["kinds"]
        assert "workbench_auto_advance" in summary["kinds"]
        assert "workbench_answer" in summary["kinds"]
        assert "workbench_answer_audit" in summary["kinds"]
        assert "workbench_evidence_search" in summary["kinds"]
        assert "workbench_task" in summary["kinds"]
        assert "workbench_note" in summary["kinds"]
        assert "workbench_notes" in summary["kinds"]
        assert "workbench_handoff_package" in summary["kinds"]
        assert "workbench_readiness" in summary["kinds"]
        assert "workbench_brief" in summary["kinds"]
        assert "workbench_evidence_dossier" in summary["kinds"]
        assert "workbench_evidence_graph" in summary["kinds"]
        assert "workbench_citations" in summary["kinds"]
        assert "workbench_manuscript" in summary["kinds"]
        assert "workbench_manuscript_audit" in summary["kinds"]
        assert "workbench_manuscript_revision_plan" in summary["kinds"]
        assert "workbench_manuscript_revision_task" in summary["kinds"]
        assert "workbench_task_board" in summary["kinds"]
        assert "workbench_task_board_row" in summary["kinds"]
        assert "workbench_runbook" in summary["kinds"]
        assert "workbench_timeline" in summary["kinds"]
        assert "workbench_file_manifest" in summary["kinds"]
        assert "workbench_file_verify" in summary["kinds"]
        assert "workbench_export" in summary["kinds"]
        assert "operations_report" in summary["kinds"]
        assert "operations_advance_next" in summary["kinds"]
        assert "operations_execute_next_plan" in summary["kinds"]
        assert "workbench_delivery_dashboard" in summary["kinds"]
        assert "workbench_delivery_execute_next" in summary["kinds"]
        assert "workbench_quality_gate" in summary["kinds"]
        assert "workbench_quality_repair_plan" in summary["kinds"]
        assert "workbench_quality_repair_execute" in summary["kinds"]
        assert "workbench_plan_queue_row" in summary["kinds"]
        assert "workbench_plan_queue_execute" in summary["kinds"]
        assert "workbench_action_item" in summary["kinds"]
        assert "project_space" in summary["kinds"]
        assert "project_space_note" in summary["kinds"]
        assert "project_space_action_item" in summary["kinds"]
        assert "project_space_answer" in summary["kinds"]
        assert "project_space_repair_execute" in summary["kinds"]
        assert "project_handoff_audit" in summary["kinds"]
        assert "project_release_gate" in summary["kinds"]
        assert "project_snapshot" in summary["kinds"]
        assert "project_snapshot_comparison" in summary["kinds"]
        assert "project_reproducibility_audit" in summary["kinds"]
        assert "project_provenance_graph" in summary["kinds"]
        assert "project_delivery" in summary["kinds"]
        assert "package_intake" in summary["kinds"]
        assert "research_search_save" in summary["kinds"]
        assert "research_search_export" in summary["kinds"]
        assert "research_search_note" in summary["kinds"]
        assert "research_search_action_item" in summary["kinds"]
        assert "host_workflow" in summary["kinds"]
        assert "host_workflow_export" in summary["kinds"]
        assert "host_workflow_stage" in summary["kinds"]
        assert "host_workflow_cache" in summary["kinds"]
        assert "host_workflow_retry" in summary["kinds"]
        assert "host_workflow_artifact" in summary["kinds"]
        assert "host_workflow_report" in summary["kinds"]
        assert "artifact_input" in summary["kinds"]
        assert "artifact_derive" in summary["kinds"]
        assert "artifact_log" in summary["kinds"]
        assert "artifact_chart" in summary["kinds"]
        assert "artifact_package" in summary["kinds"]
        assert "workflow_portability" in summary["kinds"]
        assert "workflow_portability_import" in summary["kinds"]
        assert "workflow_portability_plan" in summary["kinds"]
        assert "workflow_portability_bind" in summary["kinds"]
        assert "workflow_portability_rehearse" in summary["kinds"]
        assert "workflow_portability_review" in summary["kinds"]
        assert "workflow_portability_package" in summary["kinds"]
        assert "llm_relay_request" in summary["kinds"]
        assert "llm_relay_response" in summary["kinds"]
        assert "llm_relay_fallback" in summary["kinds"]
        assert "human_review" in summary["kinds"]
        assert "revision_task" in summary["kinds"]
        assert "notebook_export" in summary["kinds"]
        assert "bundle_export" in summary["kinds"]

        next_state = run_dir / "state-next"
        assert (next_state / "rp_input").exists()
        assert (next_state / "rp_web_bundle").exists()
        assert (next_state / "rp_agentcmp").exists()
        assert not (next_state / "ignore.txt").exists()

        queue = read(next_state / "rp_host_action_queue")
        assert "kind=research_run" in queue
        assert "kind=research_rerun" in queue
        assert "parent_run=RUN-999" in queue
        assert "kind=dataset" in queue
        assert "kind=studio_launch" in queue
        assert "kind=library_source" in queue
        assert "kind=template" in queue
        assert "kind=workspace_inspect" in queue
        assert "kind=workspace_import" in queue
        assert "kind=literature_search" in queue
        assert "kind=evidence_review" in queue
        assert "kind=evidence_protocol" in queue
        assert "kind=agentcompare" in queue
        assert "kind=workbench" in queue
        assert "kind=workbench_complete" in queue
        assert "kind=workbench_advance" in queue
        assert "kind=workbench_auto_advance" in queue
        assert "kind=workbench_answer" in queue
        assert "kind=workbench_answer_audit" in queue
        assert "kind=workbench_evidence_search" in queue
        assert "kind=workbench_task" in queue
        assert "kind=workbench_note" in queue
        assert "kind=workbench_notes" in queue
        assert "kind=workbench_handoff_package" in queue
        assert "kind=workbench_readiness" in queue
        assert "kind=workbench_brief" in queue
        assert "kind=workbench_evidence_dossier" in queue
        assert "kind=workbench_evidence_graph" in queue
        assert "kind=workbench_citations" in queue
        assert "kind=workbench_manuscript" in queue
        assert "kind=workbench_manuscript_audit" in queue
        assert "kind=workbench_manuscript_revision_plan" in queue
        assert "kind=workbench_manuscript_revision_task" in queue
        assert "kind=workbench_task_board" in queue
        assert "kind=workbench_task_board_row" in queue
        assert "kind=workbench_runbook" in queue
        assert "kind=workbench_timeline" in queue
        assert "kind=workbench_file_manifest" in queue
        assert "kind=workbench_file_verify" in queue
        assert "sha_records=9" in queue
        assert "verified=9" in queue
        assert "missing=0" in queue
        assert "kind=workbench_export" in queue
        assert "kind=operations_report" in queue
        assert "kind=operations_advance_next" in queue
        assert "kind=operations_execute_next_plan" in queue
        assert "kind=workbench_delivery_dashboard" in queue
        assert "kind=workbench_delivery_execute_next" in queue
        assert "kind=workbench_quality_gate" in queue
        assert "kind=workbench_quality_repair_plan" in queue
        assert "kind=workbench_quality_repair_execute" in queue
        assert "kind=workbench_plan_queue_row" in queue
        assert "kind=workbench_plan_queue_execute" in queue
        assert "kind=workbench_action_item" in queue
        assert "kind=project_space" in queue
        assert "kind=project_space_note" in queue
        assert "kind=project_space_action_item" in queue
        assert "kind=project_space_answer" in queue
        assert "kind=project_space_repair_execute" in queue
        assert "kind=project_handoff_audit" in queue
        assert "kind=project_release_gate" in queue
        assert "kind=project_snapshot" in queue
        assert "kind=project_snapshot_comparison" in queue
        assert "kind=project_reproducibility_audit" in queue
        assert "kind=project_provenance_graph" in queue
        assert "kind=project_delivery" in queue
        assert "kind=package_intake" in queue
        assert "kind=research_search_save" in queue
        assert "kind=research_search_export" in queue
        assert "kind=research_search_note" in queue
        assert "kind=research_search_action_item" in queue
        assert "kind=host_workflow" in queue
        assert "kind=host_workflow_export" in queue
        assert "kind=host_workflow_stage" in queue
        assert "kind=host_workflow_cache" in queue
        assert "kind=host_workflow_retry" in queue
        assert "kind=host_workflow_artifact" in queue
        assert "kind=host_workflow_report" in queue
        assert "kind=workflow_portability" in queue
        assert "kind=workflow_portability_import" in queue
        assert "kind=workflow_portability_plan" in queue
        assert "kind=workflow_portability_bind" in queue
        assert "kind=workflow_portability_rehearse" in queue
        assert "kind=workflow_portability_review" in queue
        assert "kind=workflow_portability_package" in queue
        assert "kind=human_review" in queue
        assert "kind=revision_task" in queue
        assert "kind=notebook_export" in queue
        assert "kind=bundle_export" in queue
        assert "run_id=RUN-999" in queue
        assert "title=Host reusable response table" in queue
        assert "title=Studio cytokine evidence" in queue
        assert "goal=Determine whether recovery evidence is ready" in queue
        assert "direction=evidence review" in queue
        assert "material_notes=Small demonstration table for the studio workflow." in queue
        assert "workbench_id=W1" in queue
        assert "latest_run_id=R1" in queue
        assert "latest_answer_id=answer1" in queue
        assert "citation_key=hostlibrary2026" in queue
        assert "name=Host response template" in queue
        assert "root=host-workspace" in queue
        assert "manifest=host-workspace-manifest.json" in queue
        assert "max_results=7" in queue
        assert "included=4" in queue
        assert "outcome=traceability" in queue
        assert "reviewer=Wang" in queue
        assert "targets=methods,chart_caption,statistics" in queue
        assert "bundle=reviewer-evidence" in queue
        assert "profile=plain_ucore_batch" in queue
        assert "workbench=usable-workbench:RUN-900" in queue
        assert "workbench_title=RUN-900 workbench" in queue
        assert "literature_query=agent workflow provenance" in queue
        assert "question=What is ready for review?" in queue
        assert "query=recovery evidence" in queue
        assert "task=human_review" in queue
        assert "step_limit=8" in queue
        assert "note_kind=decision" in queue
        assert "title=Scope decision" in queue
        assert "body=Use recovered evidence first." in queue
        assert "notes_filter=decision" in queue
        assert "handoff_scope=full" in queue
        assert "brief_format=html" in queue
        assert "dossier_format=markdown" in queue
        assert "graph_format=dot" in queue
        assert "citation_format=bibtex" in queue
        assert "manuscript_format=markdown" in queue
        assert "audit_scope=citations" in queue
        assert "revision_area=methods" in queue
        assert "revision_task=1" in queue
        assert "revision_status=done" in queue
        assert "board_filter=open" in queue
        assert "row_id=usable-workbench:RUN-900:board:task:human_review" in queue
        assert "row_status=done" in queue
        assert "runbook_format=markdown" in queue
        assert "timeline_format=html" in queue
        assert "manifest=delivery-manifest.json" in queue
        assert "bundle=workbench-bundle.zip" in queue
        assert "repair_id=repair1" in queue
        assert "action_key=export_file_manifest_and_verify" in queue
        assert "project_id=lab-gene-x" in queue
        assert "name=Recovery search" in queue
        assert "workflow_id=WF1" in queue
        assert "engine=plain-c-runner" in queue
        assert "dag=ingest>clean>analyze>review>package" in queue
        assert "retry_stage=clean" in queue
        assert "cache_hit_stage=analyze" in queue
        assert "observer_events=12" in queue
        assert "bundle=wf.zip" in queue
        assert "stage=clean" in queue
        assert "attempt=2" in queue
        assert "command=clean_reads" in queue
        assert "cache_key=cache:WF1:analyze" in queue
        assert "cache_result=hit" in queue
        assert "next_attempt=3" in queue
        assert "artifact=clean.metrics.json" in queue
        assert "sha256=sha-host-wf1" in queue
        assert "report=workflow-report.md" in queue
        assert "import_id=workflow-import:WF1:nextflow" in queue
        assert "source_format=nextflow" in queue
        assert "source=main.wf1.nf" in queue
        assert "target_runtime=agentos-ucore" in queue
        assert "execution_plan=workflow-migration-execution-plan:WF1:agentcompare" in queue
        assert "compare_profile=compare-profile:WF1:migration" in queue
        assert "scenario_id=backend-scenario:WF1" in queue
        assert "normalized_steps=15" in queue
        assert "adapter_id=adapter:WF1:nextflow" in queue
        assert "migration_plan=workflow-migration-plan:WF1" in queue
        assert "backend_cases=4" in queue
        assert "rehearsal_id=workflow-rehearsal:WF1" in queue
        assert "readiness_decision=ready_for_agentos" in queue
        assert "export_format=zip" in queue
        assert "package=wf-portability.zip" in queue
        assert "status=ready" in queue

        plan = read(next_state / "rp_host_action_plan")
        assert "collect=rp_web_bundle" in plan
        assert "collect=rp_compare_plain" in plan
        assert "kind=dataset" in plan
        assert "kind=research_rerun" in plan
        assert "kind=studio_launch" in plan
        assert "kind=library_source" in plan
        assert "kind=template" in plan
        assert "kind=workspace_inspect" in plan
        assert "kind=workspace_import" in plan
        assert "kind=literature_search" in plan
        assert "kind=evidence_review" in plan
        assert "kind=evidence_protocol" in plan
        assert "kind=workbench" in plan
        assert "kind=workbench_complete" in plan
        assert "kind=workbench_advance" in plan
        assert "kind=workbench_auto_advance" in plan
        assert "kind=workbench_answer" in plan
        assert "kind=workbench_answer_audit" in plan
        assert "kind=workbench_evidence_search" in plan
        assert "kind=workbench_task" in plan
        assert "kind=workbench_note" in plan
        assert "kind=workbench_notes" in plan
        assert "kind=workbench_handoff_package" in plan
        assert "kind=workbench_readiness" in plan
        assert "kind=workbench_brief" in plan
        assert "kind=workbench_evidence_dossier" in plan
        assert "kind=workbench_evidence_graph" in plan
        assert "kind=workbench_citations" in plan
        assert "kind=workbench_manuscript" in plan
        assert "kind=workbench_manuscript_audit" in plan
        assert "kind=workbench_manuscript_revision_plan" in plan
        assert "kind=workbench_manuscript_revision_task" in plan
        assert "kind=workbench_task_board" in plan
        assert "kind=workbench_task_board_row" in plan
        assert "kind=workbench_runbook" in plan
        assert "kind=workbench_timeline" in plan
        assert "kind=workbench_file_manifest" in plan
        assert "kind=workbench_file_verify" in plan
        assert "kind=workbench_export" in plan
        assert "kind=operations_report" in plan
        assert "kind=project_space" in plan
        assert "kind=project_release_gate" in plan
        assert "kind=research_search_export" in plan
        assert "kind=host_workflow" in plan
        assert "kind=host_workflow_export" in plan
        assert "kind=host_workflow_stage" in plan
        assert "kind=host_workflow_cache" in plan
        assert "kind=host_workflow_retry" in plan
        assert "kind=host_workflow_artifact" in plan
        assert "kind=host_workflow_report" in plan
        assert "kind=artifact_input" in plan
        assert "kind=artifact_derive" in plan
        assert "kind=artifact_log" in plan
        assert "kind=artifact_chart" in plan
        assert "kind=artifact_package" in plan
        assert "kind=workflow_portability" in plan
        assert "kind=workflow_portability_import" in plan
        assert "kind=workflow_portability_plan" in plan
        assert "kind=workflow_portability_bind" in plan
        assert "kind=workflow_portability_rehearse" in plan
        assert "kind=workflow_portability_review" in plan
        assert "kind=workflow_portability_package" in plan
        assert "kind=llm_relay_request" in plan
        assert "kind=llm_relay_response" in plan
        assert "kind=llm_relay_fallback" in plan

        inbox = read(next_state / "rp_host_action_inbox")
        assert "/actions/research/run" in inbox
        assert "/actions/research/rerun" in inbox
        assert "/actions/research/dataset" in inbox
        assert "/actions/research/studio-launch" in inbox
        assert "/actions/research/library-source" in inbox
        assert "/actions/research/template" in inbox
        assert "/actions/research/inspect-workspace" in inbox
        assert "/actions/research/import-workspace" in inbox
        assert "/actions/research/literature-search" in inbox
        assert "/actions/research/evidence-review" in inbox
        assert "/actions/research/evidence-protocol" in inbox
        assert "/actions/agentcompare/run" in inbox
        assert "/actions/research/workbench" in inbox
        assert "/actions/research/workbench-answer" in inbox
        assert "/actions/research/workbench-answer-audit" in inbox
        assert "/actions/research/workbench-evidence-search" in inbox
        assert "/actions/research/workbench-task" in inbox
        assert "/actions/research/workbench-note" in inbox
        assert "/actions/research/workbench-notes" in inbox
        assert "/actions/research/workbench-handoff-package" in inbox
        assert "/actions/research/workbench-readiness" in inbox
        assert "/actions/research/workbench-brief" in inbox
        assert "/actions/research/workbench-evidence-dossier" in inbox
        assert "/actions/research/workbench-evidence-graph" in inbox
        assert "/actions/research/workbench-citations" in inbox
        assert "/actions/research/workbench-manuscript" in inbox
        assert "/actions/research/workbench-manuscript-audit" in inbox
        assert "/actions/research/workbench-manuscript-revision-plan" in inbox
        assert "/actions/research/workbench-manuscript-revision-task" in inbox
        assert "/actions/research/workbench-task-board" in inbox
        assert "/actions/research/workbench-task-board-row" in inbox
        assert "/actions/research/workbench-runbook" in inbox
        assert "/actions/research/workbench-timeline" in inbox
        assert "/actions/research/workbench-file-manifest" in inbox
        assert "/actions/research/workbench-file-verify" in inbox
        assert "/actions/research/export-workbench" in inbox
        assert "/actions/research/operations-report" in inbox
        assert "/actions/research/workbench-quality-gate" in inbox
        assert "/actions/research/workbench-plan-queue-execute" in inbox
        assert "/actions/research/project-space" in inbox
        assert "/actions/research-search/export" in inbox
        assert "/actions/host-workflow/run" in inbox
        assert "/actions/host-workflow/export" in inbox
        assert "/actions/host-workflow/stage-attempt" in inbox
        assert "/actions/host-workflow/cache-decision" in inbox
        assert "/actions/host-workflow/retry-decision" in inbox
        assert "/actions/host-workflow/artifact-manifest" in inbox
        assert "/actions/host-workflow/report-export" in inbox
        assert "/actions/research/artifact-input" in inbox
        assert "/actions/research/artifact-derive" in inbox
        assert "/actions/research/artifact-log" in inbox
        assert "/actions/research/artifact-chart" in inbox
        assert "/actions/research/artifact-package" in inbox
        assert "/actions/workflow-portability/run" in inbox
        assert "/actions/workflow-portability/import" in inbox
        assert "/actions/workflow-portability/plan" in inbox
        assert "/actions/workflow-portability/bind" in inbox
        assert "/actions/workflow-portability/rehearse" in inbox
        assert "/actions/workflow-portability/review" in inbox
        assert "/actions/workflow-portability/package" in inbox
        assert "/actions/research/llm-relay-request" in inbox
        assert "/actions/research/llm-relay-response" in inbox
        assert "/actions/research/llm-relay-fallback" in inbox

        assert (run_dir / "actions.json").exists()
        assert (run_dir / "runner-summary.json").exists()

        assert runner.action_kind("/actions/research/rerun") == "research_rerun"
        assert runner.action_kind("/actions/research/run-revision") == "revision_run"
        assert runner.action_kind("/actions/research/dataset") == "dataset"
        assert runner.action_kind("/actions/research/studio-launch") == "studio_launch"
        assert runner.action_kind("/actions/research/library-source") == "library_source"
        assert runner.action_kind("/actions/research/template") == "template"
        assert runner.action_kind("/actions/research/inspect-workspace") == "workspace_inspect"
        assert runner.action_kind("/actions/research/import-workspace") == "workspace_import"
        assert runner.action_kind("/actions/research/import-and-run") == "workspace_import_run"
        assert runner.action_kind("/actions/research/literature-search") == "literature_search"
        assert runner.action_kind("/actions/research/evidence-review") == "evidence_review"
        assert runner.action_kind("/actions/research/evidence-protocol") == "evidence_protocol"
        assert runner.action_kind("/actions/research/workbench") == "workbench"
        assert runner.action_kind("/actions/research/workbench-advance") == "workbench_advance"
        assert runner.action_kind("/actions/research/workbench-auto-advance") == "workbench_auto_advance"
        assert runner.action_kind("/actions/research/workbench-answer") == "workbench_answer"
        assert runner.action_kind("/actions/research/workbench-answer-audit") == "workbench_answer_audit"
        assert runner.action_kind("/actions/research/workbench-evidence-search") == "workbench_evidence_search"
        assert runner.action_kind("/actions/research/workbench-task") == "workbench_task"
        assert runner.action_kind("/actions/research/workbench-note") == "workbench_note"
        assert runner.action_kind("/actions/research/workbench-notes") == "workbench_notes"
        assert runner.action_kind("/actions/research/workbench-handoff-package") == "workbench_handoff_package"
        assert runner.action_kind("/actions/research/workbench-readiness") == "workbench_readiness"
        assert runner.action_kind("/actions/research/workbench-brief") == "workbench_brief"
        assert runner.action_kind("/actions/research/workbench-evidence-dossier") == "workbench_evidence_dossier"
        assert runner.action_kind("/actions/research/workbench-evidence-graph") == "workbench_evidence_graph"
        assert runner.action_kind("/actions/research/workbench-citations") == "workbench_citations"
        assert runner.action_kind("/actions/research/workbench-manuscript") == "workbench_manuscript"
        assert runner.action_kind("/actions/research/workbench-manuscript-audit") == "workbench_manuscript_audit"
        assert runner.action_kind("/actions/research/workbench-manuscript-revision-plan") == "workbench_manuscript_revision_plan"
        assert runner.action_kind("/actions/research/workbench-manuscript-revision-task") == "workbench_manuscript_revision_task"
        assert runner.action_kind("/actions/research/workbench-task-board") == "workbench_task_board"
        assert runner.action_kind("/actions/research/workbench-task-board-row") == "workbench_task_board_row"
        assert runner.action_kind("/actions/research/workbench-runbook") == "workbench_runbook"
        assert runner.action_kind("/actions/research/workbench-timeline") == "workbench_timeline"
        assert runner.action_kind("/actions/research/workbench-file-manifest") == "workbench_file_manifest"
        assert runner.action_kind("/actions/research/workbench-file-verify") == "workbench_file_verify"
        assert runner.action_kind("/actions/research/export-workbench") == "workbench_export"
        assert runner.action_kind("/actions/research/operations-report") == "operations_report"
        assert runner.action_kind("/actions/research/operations-advance-next") == "operations_advance_next"
        assert runner.action_kind("/actions/research/operations-execute-next-plan") == "operations_execute_next_plan"
        assert runner.action_kind("/actions/research/workbench-delivery-dashboard") == "workbench_delivery_dashboard"
        assert runner.action_kind("/actions/research/workbench-delivery-execute-next") == "workbench_delivery_execute_next"
        assert runner.action_kind("/actions/research/workbench-quality-gate") == "workbench_quality_gate"
        assert runner.action_kind("/actions/research/workbench-quality-repair-plan") == "workbench_quality_repair_plan"
        assert runner.action_kind("/actions/research/workbench-quality-repair-execute") == "workbench_quality_repair_execute"
        assert runner.action_kind("/actions/research/workbench-plan-queue-row") == "workbench_plan_queue_row"
        assert runner.action_kind("/actions/research/workbench-plan-queue-execute") == "workbench_plan_queue_execute"
        assert runner.action_kind("/actions/research/workbench-action-item") == "workbench_action_item"
        assert runner.action_kind("/actions/research/project-space") == "project_space"
        assert runner.action_kind("/actions/research/project-space-note") == "project_space_note"
        assert runner.action_kind("/actions/research/project-space-action-item") == "project_space_action_item"
        assert runner.action_kind("/actions/research/project-space-answer") == "project_space_answer"
        assert runner.action_kind("/actions/research/project-space-repair-execute") == "project_space_repair_execute"
        assert runner.action_kind("/actions/research/project-handoff-audit") == "project_handoff_audit"
        assert runner.action_kind("/actions/research/project-release-gate") == "project_release_gate"
        assert runner.action_kind("/actions/research/project-snapshot") == "project_snapshot"
        assert runner.action_kind("/actions/research/project-snapshot-comparison") == "project_snapshot_comparison"
        assert runner.action_kind("/actions/research/project-reproducibility-audit") == "project_reproducibility_audit"
        assert runner.action_kind("/actions/research/project-provenance-graph") == "project_provenance_graph"
        assert runner.action_kind("/actions/research/project-delivery") == "project_delivery"
        assert runner.action_kind("/actions/research/package-intake") == "package_intake"
        assert runner.action_kind("/actions/research-search/save") == "research_search_save"
        assert runner.action_kind("/actions/research-search/export") == "research_search_export"
        assert runner.action_kind("/actions/research-search/note") == "research_search_note"
        assert runner.action_kind("/actions/research-search/action-item") == "research_search_action_item"
        assert runner.action_kind("/actions/host-workflow/run") == "host_workflow"
        assert runner.action_kind("/actions/host-workflow/export") == "host_workflow_export"
        assert runner.action_kind("/actions/host-workflow/stage-attempt") == "host_workflow_stage"
        assert runner.action_kind("/actions/host-workflow/cache-decision") == "host_workflow_cache"
        assert runner.action_kind("/actions/host-workflow/retry-decision") == "host_workflow_retry"
        assert runner.action_kind("/actions/host-workflow/artifact-manifest") == "host_workflow_artifact"
        assert runner.action_kind("/actions/host-workflow/report-export") == "host_workflow_report"
        assert runner.action_kind("/actions/research/artifact-input") == "artifact_input"
        assert runner.action_kind("/actions/research/artifact-derive") == "artifact_derive"
        assert runner.action_kind("/actions/research/artifact-log") == "artifact_log"
        assert runner.action_kind("/actions/research/artifact-chart") == "artifact_chart"
        assert runner.action_kind("/actions/research/artifact-package") == "artifact_package"
        assert runner.action_kind("/actions/workflow-portability/run") == "workflow_portability"
        assert runner.action_kind("/actions/workflow-portability/import") == "workflow_portability_import"
        assert runner.action_kind("/actions/workflow-portability/plan") == "workflow_portability_plan"
        assert runner.action_kind("/actions/workflow-portability/bind") == "workflow_portability_bind"
        assert runner.action_kind("/actions/workflow-portability/rehearse") == "workflow_portability_rehearse"
        assert runner.action_kind("/actions/workflow-portability/review") == "workflow_portability_review"
        assert runner.action_kind("/actions/workflow-portability/package") == "workflow_portability_package"
        assert runner.action_kind("/actions/research/llm-relay-request") == "llm_relay_request"
        assert runner.action_kind("/actions/research/llm-relay-response") == "llm_relay_response"
        assert runner.action_kind("/actions/research/llm-relay-fallback") == "llm_relay_fallback"
        assert runner.action_kind("/actions/research/export-notebook") == "notebook_export"
        assert runner.action_kind("/actions/unknown") == "generic"

        records = runner.write_seed_header(next_state, root)
        header = read(root / "user" / "build" / "generated" / "rp_host_action_seed.h")
        seed_file = read(next_state / "rp_host_action_seed")
        assert records == expected_actions
        assert "#define RP_HOST_ACTION_SEED" in header
        assert "kind=research_run" in seed_file
        assert "kind=research_rerun" in seed_file
        assert "parent_run=RUN-999" in seed_file
        assert "kind=dataset" in seed_file
        assert "kind=studio_launch" in seed_file
        assert "title=Studio cytokine evidence" in seed_file
        assert "goal=Determine whether recovery evidence is ready" in seed_file
        assert "workbench_id=W1" in seed_file
        assert "material_notes=Small demonstration table for the studio workflow." not in seed_file
        assert "latest_answer_id=answer1" not in seed_file
        assert "kind=library_source" in seed_file
        assert "kind=template" in seed_file
        assert "kind=workspace_inspect" in seed_file
        assert "kind=workspace_import" in seed_file
        assert "kind=literature_search" in seed_file
        assert "kind=evidence_review" in seed_file
        assert "kind=evidence_protocol" in seed_file
        assert "kind=workbench" in seed_file
        assert "kind=workbench_answer" in seed_file
        assert "kind=workbench_answer_audit" in seed_file
        assert "kind=workbench_evidence_search" in seed_file
        assert "kind=workbench_task" in seed_file
        assert "kind=workbench_note" in seed_file
        assert "kind=workbench_manuscript" in seed_file
        assert "kind=workbench_task_board_row" in seed_file
        assert "kind=workbench_file_verify" in seed_file
        assert "sha_records=9" in seed_file
        assert "verified=9" in seed_file
        assert "missing=0" in seed_file
        assert "kind=operations_report" in seed_file
        assert "kind=workbench_quality_gate" in seed_file
        assert "kind=project_space" in seed_file
        assert "kind=project_release_gate" in seed_file
        assert "kind=project_provenance_graph" in seed_file
        assert "project-provenance.dot" in seed_file
        assert "project-bundle.zip" in seed_file
        assert "kind=research_search_export" in seed_file
        assert "kind=host_workflow" in seed_file
        assert "kind=host_workflow_export" in seed_file
        assert "kind=host_workflow_stage" in seed_file
        assert "kind=host_workflow_cache" in seed_file
        assert "kind=host_workflow_retry" in seed_file
        assert "kind=host_workflow_artifact" in seed_file
        assert "kind=host_workflow_report" in seed_file
        assert "kind=artifact_input" in seed_file
        assert "kind=artifact_derive" in seed_file
        assert "kind=artifact_log" in seed_file
        assert "kind=artifact_chart" in seed_file
        assert "kind=artifact_package" in seed_file
        assert "kind=workflow_portability" in seed_file
        assert "kind=workflow_portability_import" in seed_file
        assert "kind=workflow_portability_plan" in seed_file
        assert "kind=workflow_portability_bind" in seed_file
        assert "kind=workflow_portability_rehearse" in seed_file
        assert "kind=workflow_portability_review" in seed_file
        assert "kind=workflow_portability_package" in seed_file
        assert "kind=llm_relay_request" in seed_file
        assert "kind=llm_relay_response" in seed_file
        assert "kind=llm_relay_fallback" in seed_file
        assert "workflow_id=WF1" in seed_file
        assert "engine=plain-c-runner" in seed_file
        assert "retry_reason=checksum_mismatch" in seed_file
        assert "worker_slots=2" in seed_file
        assert "queue_depth=5" in seed_file
        assert "bundle=wf.zip" in seed_file
        assert "attempt=2" in seed_file
        assert "cache_key=cache:WF1:analyze" in seed_file
        assert "next_attempt=3" in seed_file
        assert "artifact=clean.metrics.json" in seed_file
        assert "report=workflow-report.md" in seed_file
        assert "file=reads_R1.fastq" in seed_file
        assert "output=clean_reads.fastq" in seed_file
        assert "log=clean.log" in seed_file
        assert "chart=qc-chart.json" in seed_file
        assert "package=artifact-bundle.zip" in seed_file
        assert "import_id=workflow-import:WF1:nextflow" in seed_file
        assert "source_format=nextflow" in seed_file
        assert "source=main.wf1.nf" in seed_file
        assert "target_runtime=agentos-ucore" in seed_file
        assert "execution_plan=workflow-migration-execution-plan:WF1:agentcompare" in seed_file
        assert "compare_profile=compare-profile:WF1:migration" in seed_file
        assert "scenario_id=backend-scenario:WF1" in seed_file
        assert "normalized_steps=15" in seed_file
        assert "migration_plan=workflow-migration-plan:WF1" in seed_file
        assert "backend_cases=4" in seed_file
        assert "rehearsal_id=workflow-rehearsal:WF1" in seed_file
        assert "readiness_decision=ready_for_agentos" in seed_file
        assert "export_format=zip" in seed_file
        assert "package=wf-portability.zip" in seed_file
        assert "request_id=llm-q1" in seed_file
        assert "response_id=llm-r1" in seed_file
        assert "summary=Recovered_evidence_ready" in seed_file
        assert "action=template_response" in seed_file
        assert "source_text=" not in seed_file

        runner.write_run_result_state(
            next_state,
            {
                "passed": True,
                "embedded_action_records": expected_actions,
                "log": str(run_dir / "ucore-run.log"),
            },
            f"rp_web_export: host_reader_actions={expected_actions}\nrp_compare_plain: host_actions={expected_actions} verified\nrp_orch: passed\n",
        )
        result_state = read(next_state / "rp_host_run_result")
        assert "passed=1" in result_state
        assert f"embedded_action_records={expected_actions}" in result_state
        assert f"qemu_rp_web_export: host_reader_actions={expected_actions}" in result_state
        assert f"qemu_rp_compare_plain: host_actions={expected_actions} verified" in result_state
        assert "qemu_orch_passed=1" in result_state

        publish_dir = root / "published"
        runner.publish_next_state(next_state, publish_dir)
        assert (publish_dir / "rp_host_run_result").exists()
        assert (publish_dir / "rp_host_action_inbox").exists()

    print("test_plain_ucore_action_runner: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
