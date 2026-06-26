#!/usr/bin/env python3
"""End-to-end check for host POST action -> plain uCore run -> extracted state -> reader API."""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request

import plain_ucore_reader


def read_json(url: str, timeout: int = 10) -> dict[str, object]:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def read_text(url: str, timeout: int = 10) -> str:
    with request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def main() -> int:
    repo_dir = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="plain-ucore-reader-e2e-") as tmp:
        root = Path(tmp)
        state_dir = root / "state"
        out_dir = root / "reader"
        run_root = root / "runs"
        state_dir.mkdir()

        handler = plain_ucore_reader.make_service_handler(
            state_dir,
            out_dir,
            write_state=False,
            auto_run_ucore=True,
            repo_dir=repo_dir,
            run_root=run_root,
            runner_timeout=180,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            actions = [
                {
                    "path": "/actions/research/run",
                    "payload": {
                        "run_id": "R1",
                        "source": "reader-e2e",
                        "title": "T1",
                        "question": "Q1",
                        "provider": "template",
                        "dataset_rows": "7",
                        "reference_entries": "3",
                        "workspace_files": "5",
                        "csv_file": "d.csv",
                        "reference_file": "r.bib",
                    },
                },
                {"path": "/actions/research/dataset", "payload": {"title": "D1", "dataset_rows": "6", "columns": "a,b,c", "tags": "h"}},
                {"path": "/actions/research/library-source", "payload": {"citation_key": "c1", "tags": "h", "source_text": "@c"}},
                {"path": "/actions/research/template", "payload": {"name": "TP1", "question": "TQ", "provider_id": "template", "dataset_tags": "h", "library_tags": "h"}},
                {"path": "/actions/research/inspect-workspace", "payload": {"root": "ws", "max_files": "9", "tags": "workspace"}},
                {"path": "/actions/research/import-workspace", "payload": {"root": "ws", "title": "IW", "question": "WQ", "max_files": "9", "manifest": "m.json"}},
                {"path": "/actions/research/literature-search", "payload": {"query": "prov", "provider": "local", "max_results": "7"}},
                {"path": "/actions/research/evidence-review", "payload": {"search_id": "s1", "reviewer": "Wang", "include_terms": "wpa", "included": "4"}},
                {"path": "/actions/research/evidence-protocol", "payload": {"search_id": "s1", "title": "P1", "research_question": "PQ", "outcome": "tr"}},
                {"path": "/actions/research/review", "payload": {"run_id": "R1", "reviewer": "Wang", "decision": "needs_revision"}},
                {"path": "/actions/research/revision-task", "payload": {"review_id": "rev1", "targets": "m,c,s"}},
                {"path": "/actions/research/run-revision-task", "payload": {"run_id": "R1", "task_id": "task1"}},
                {"path": "/actions/research/workbench", "payload": {"workbench": "W1", "workbench_title": "WB1", "literature_query": "prov"}},
                {"path": "/actions/research/workbench-advance", "payload": {"workbench": "W1", "task": "dm"}},
                {"path": "/actions/research/workbench-auto-advance", "payload": {"workbench": "W1", "step_limit": "8"}},
                {"path": "/actions/research/workbench-answer", "payload": {"workbench": "W1", "question": "Ready?"}},
                {"path": "/actions/research/workbench-answer-audit", "payload": {"workbench": "W1"}},
                {"path": "/actions/research/workbench-evidence-search", "payload": {"workbench": "W1", "query": "rec"}},
                {"path": "/actions/research/workbench-task", "payload": {"workbench": "W1", "task": "hr", "status": "waiting"}},
                {"path": "/actions/research/workbench-note", "payload": {"workbench": "W1", "note_kind": "decision", "title": "Scope", "body": "Use evidence."}},
                {"path": "/actions/research/workbench-notes", "payload": {"workbench": "W1", "notes_filter": "decision"}},
                {"path": "/actions/research/workbench-handoff-package", "payload": {"workbench": "W1", "handoff_scope": "full"}},
                {"path": "/actions/research/workbench-readiness", "payload": {"workbench": "W1"}},
                {"path": "/actions/research/workbench-brief", "payload": {"workbench": "W1", "brief_format": "html"}},
                {"path": "/actions/research/workbench-evidence-dossier", "payload": {"workbench": "W1", "dossier_format": "markdown"}},
                {"path": "/actions/research/workbench-evidence-graph", "payload": {"workbench": "W1", "graph_format": "dot"}},
                {"path": "/actions/research/workbench-citations", "payload": {"workbench": "W1", "citation_format": "bibtex"}},
                {"path": "/actions/research/workbench-manuscript", "payload": {"workbench": "W1", "manuscript_format": "markdown"}},
                {"path": "/actions/research/workbench-manuscript-audit", "payload": {"workbench": "W1", "audit_scope": "citations"}},
                {"path": "/actions/research/workbench-manuscript-revision-plan", "payload": {"workbench": "W1", "revision_area": "methods"}},
                {"path": "/actions/research/workbench-manuscript-revision-task", "payload": {"workbench": "W1", "revision_task": "1", "revision_status": "done"}},
                {"path": "/actions/research/workbench-task-board", "payload": {"workbench": "W1", "board_filter": "open"}},
                {"path": "/actions/research/workbench-task-board-row", "payload": {"workbench": "W1", "row_id": "row1", "row_status": "done"}},
                {"path": "/actions/research/workbench-runbook", "payload": {"workbench": "W1", "runbook_format": "markdown"}},
                {"path": "/actions/research/workbench-timeline", "payload": {"workbench": "W1", "timeline_format": "html"}},
                {"path": "/actions/research/workbench-file-manifest", "payload": {"workbench": "W1", "manifest": "mf.json", "files": "11", "sha_records": "11"}},
                {"path": "/actions/research/workbench-file-verify", "payload": {"workbench": "W1", "manifest": "mf.json", "files": "11", "sha_records": "11", "verified": "11", "missing": "0"}},
                {"path": "/actions/research/workbench-complete", "payload": {"workbench": "W1", "review_decision": "approved"}},
                {"path": "/actions/research/export-workbench", "payload": {"workbench": "W1", "bundle": "wb.zip"}},
                {"path": "/actions/research/operations-report", "payload": {"format": "markdown"}},
                {"path": "/actions/research/operations-advance-next", "payload": {"provider_id": "template", "max_steps": "5", "review_decision": "approved", "delivery_audience": "reviewer"}},
                {"path": "/actions/research/operations-execute-next-plan", "payload": {"provider_id": "template", "max_steps": "6", "answer_question": "What next?", "delivery_audience": "reviewer"}},
                {"path": "/actions/research/workbench-delivery-dashboard", "payload": {"query": "ready", "include_clean": "1"}},
                {"path": "/actions/research/workbench-delivery-execute-next", "payload": {"query": "ready", "provider_id": "template", "max_steps": "4", "answer_question": "Repair delivery?"}},
                {"path": "/actions/research/workbench-quality-gate", "payload": {"workbench_id": "W1"}},
                {"path": "/actions/research/workbench-quality-repair-plan", "payload": {"workbench_id": "W1"}},
                {"path": "/actions/research/workbench-quality-repair-execute", "payload": {"workbench_id": "W1", "repair_id": "repair1", "action_key": "export_file_manifest_and_verify", "provider_id": "template", "max_steps": "3"}},
                {"path": "/actions/research/workbench-plan-queue-row", "payload": {"workbench_id": "W1", "source_type": "readiness", "source_id": "literature_search", "status": "done"}},
                {"path": "/actions/research/workbench-plan-queue-execute", "payload": {"workbench_id": "W1", "source_type": "readiness", "source_id": "literature_search", "provider_id": "template", "max_steps": "4"}},
                {"path": "/actions/research/workbench-action-item", "payload": {"workbench_id": "W1", "title": "Review search hits", "instruction": "Check citations", "priority": "high", "status": "open"}},
                {"path": "/actions/research/project-space", "payload": {"workbench_id": "W1", "project_id": "lab-gene-x", "query": "recovery"}},
                {"path": "/actions/research/project-space-note", "payload": {"workbench_id": "W1", "kind": "decision", "title": "Project scope", "body": "Keep recovered evidence first."}},
                {"path": "/actions/research/project-space-action-item", "payload": {"workbench_id": "W1", "title": "Project task", "instruction": "Prepare handoff", "priority": "high"}},
                {"path": "/actions/research/project-space-answer", "payload": {"workbench_id": "W1", "question": "What is ready?", "limit": "6"}},
                {"path": "/actions/research/project-space-repair-execute", "payload": {"workbench_id": "W1", "repair_id": "repair1", "provider_id": "template", "max_steps": "4"}},
                {"path": "/actions/research-search/save", "payload": {"query": "recovery evidence", "name": "Recovery search"}},
                {"path": "/actions/research-search/export", "payload": {"query": "recovery evidence", "limit": "20"}},
                {"path": "/actions/research-search/note", "payload": {"workbench_id": "W1", "query": "recovery evidence", "title": "Search note", "note": "Keep hits."}},
                {"path": "/actions/research-search/action-item", "payload": {"workbench_id": "W1", "query": "recovery evidence", "title": "Review search hits", "instruction": "Promote key hit", "priority": "high"}},
                {"path": "/actions/host-workflow/run", "payload": {"workflow_id": "WF1", "run_id": "R1", "engine": "plain-c-runner", "stages": "6", "dag": "ingest>clean>analyze>review>package", "max_workers": "2", "worker_slots": "2", "queue_depth": "5", "observer_events": "12", "failed_stage": "clean", "retry_stage": "clean", "cache_hit_stage": "analyze", "retry_reason": "checksum_mismatch", "cache": "content"}},
                {"path": "/actions/host-workflow/export", "payload": {"workflow_id": "WF1", "run_id": "R1", "format": "json", "bundle": "wf.zip"}},
                {"path": "/actions/workflow-portability/run", "payload": {"import_id": "workflow-import:WF1:nextflow", "source_format": "nextflow", "source": "main.wf1.nf", "target_runtime": "agentos-ucore", "execution_plan": "workflow-migration-execution-plan:WF1:agentcompare", "compare_profile": "compare-profile:WF1:migration", "scenario_id": "backend-scenario:WF1", "rehearsal_status": "passed", "readiness_decision": "ready_for_agentos", "package": "wf-portability.zip"}},
                {"path": "/actions/workflow-portability/import", "payload": {"import_id": "workflow-import:WF1:nextflow", "source_format": "nextflow", "source": "main.wf1.nf", "normalized_steps": "15", "adapter_id": "adapter:WF1:nextflow"}},
                {"path": "/actions/workflow-portability/plan", "payload": {"import_id": "workflow-import:WF1:nextflow", "migration_plan": "workflow-migration-plan:WF1", "target_runtime": "agentos-ucore", "migration_steps": "9", "risk_items": "4"}},
                {"path": "/actions/workflow-portability/bind", "payload": {"execution_plan": "workflow-migration-execution-plan:WF1:agentcompare", "compare_profile": "compare-profile:WF1:migration", "scenario_id": "backend-scenario:WF1", "backend_cases": "4"}},
                {"path": "/actions/workflow-portability/rehearse", "payload": {"rehearsal_id": "workflow-rehearsal:WF1", "binding_id": "workflow-migration-binding:WF1", "rehearsal_status": "passed", "observed_ready": "3", "skipped": "1"}},
                {"path": "/actions/workflow-portability/review", "payload": {"review_id": "workflow-migration-readiness:WF1", "readiness_decision": "ready_for_agentos", "blocking_items": "0", "work_items": "6"}},
                {"path": "/actions/workflow-portability/package", "payload": {"import_id": "workflow-import:WF1:nextflow", "package": "wf-portability.zip", "export_format": "zip", "bundle": "wf-portability.zip"}},
                {"path": "/actions/research/llm-relay-request", "payload": {"request_id": "llm-q1", "run_id": "R1", "route": "review_summary", "provider": "host-relay", "prompt": "summarize_recovery_evidence", "budget": "2048", "secret_ref": "host_env"}},
                {"path": "/actions/research/llm-relay-response", "payload": {"request_id": "llm-q1", "response_id": "llm-r1", "provider": "host-relay", "mode": "template", "summary": "Recovered_evidence_ready", "citations": "5"}},
                {"path": "/actions/research/llm-relay-fallback", "payload": {"case": "missing_cloud_key", "action": "template_response", "reason": "host_env_absent", "fallback_status": "ready"}},
                {"path": "/actions/research/export-notebook", "payload": {"run_id": "R1", "format": "ipynb"}},
                {"path": "/actions/research/export-bundle", "payload": {"run_id": "R1", "bundle": "ev"}},
                {"path": "/actions/agentcompare/run", "payload": {"profile": "pb"}},
            ]
            action = request.Request(
                base + "/actions/batch",
                data=json.dumps({"actions": actions}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(action, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
            assert len(result["actions"]) == len(actions), result
            assert result["actions"][0]["path"] == "/actions/research/run", result
            assert result["actions"][-1]["path"] == "/actions/agentcompare/run", result
            assert result["run"]["status"] == "ready", result
            extracted = int(result["run"]["run"]["extracted_state_files"])
            assert extracted >= 100, result

            live = read_json(base + "/api/live")
            assert live["action_count"] == len(actions), live
            assert live["last_run"]["status"] == "ready", live
            assert int(live["last_run"]["run"]["extracted_state_files"]) >= 100, live

            rp_input = read_json(base + "/api/state/rp_input")
            assert any("host_action_run_id=R1" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_title=T1" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_question=Q1" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_provider=template" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_dataset_rows_value=7" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_reference_entries=3" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_workspace_files=5" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_csv_file=d.csv" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_reference_file=r.bib" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_dataset_title=D1" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_dataset_rows=6" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_dataset_columns=a,b,c" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_library_citation=c1" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_template_name=TP1" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_workspace_root=ws" in line for line in rp_input["lines"]), rp_input
            assert any("host_action_workspace_manifest=m.json" in line for line in rp_input["lines"]), rp_input
            rp_ingest = read_json(base + "/api/state/rp_ingest_files")
            assert any("host_input_dataset_rows=7" in line for line in rp_ingest["lines"]), rp_ingest
            assert any("host_input_reference_entries=3" in line for line in rp_ingest["lines"]), rp_ingest
            assert any("host_input_workspace_files=5" in line for line in rp_ingest["lines"]), rp_ingest
            assert any("host_input_csv_file=d.csv" in line for line in rp_ingest["lines"]), rp_ingest
            assert any("host_file_manifest=mf.json" in line for line in rp_ingest["lines"]), rp_ingest
            assert any("host_file_manifest_files=11" in line for line in rp_ingest["lines"]), rp_ingest
            assert any("host_file_manifest_sha_records=11" in line for line in rp_ingest["lines"]), rp_ingest
            rp_data_preview = read_json(base + "/api/state/rp_data_preview")
            assert any("host_input_dataset_rows=7" in line for line in rp_data_preview["lines"]), rp_data_preview
            assert any("host_input_csv_file=d.csv" in line for line in rp_data_preview["lines"]), rp_data_preview
            rp_data_quality = read_json(base + "/api/state/rp_data_quality")
            assert any("host_file_verify=passed" in line for line in rp_data_quality["lines"]), rp_data_quality
            assert any("host_file_verify_verified=11" in line for line in rp_data_quality["lines"]), rp_data_quality
            assert any("host_file_verify_missing=0" in line for line in rp_data_quality["lines"]), rp_data_quality
            rp_runner = read_json(base + "/api/state/rp_runner")
            assert any("host_action_status=completed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_revision_run=usable-run:R1-rev2" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_compare=pb" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_id=W1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_created=1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_title=WB1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_literature_query=prov" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_question=Ready?" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_evidence_query=rec" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_answer=generated" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_answer_audit=passed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_readiness=checked" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_task=dm" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_step_limit=8" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_task=hr" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_task_status=waiting" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_note=recorded" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_note_kind=decision" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_note_title=Scope" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_notes=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_notes_filter=decision" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_handoff=prepared" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_handoff_scope=full" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_brief=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_brief_format=html" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_evidence_dossier=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_dossier_format=markdown" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_evidence_graph=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_graph_format=dot" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_citations=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_citation_format=bibtex" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_manuscript=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_manuscript_format=markdown" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_manuscript_audit=passed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_audit_scope=citations" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_revision_plan=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_revision_area=methods" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_revision_task=1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_revision_status=done" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_revision_task=updated" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_task_board=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_board_filter=open" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_task_board_row=updated" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_row_id=row1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_row_status=done" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_runbook=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_runbook_format=markdown" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_timeline=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_timeline_format=html" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_file_manifest=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_file_verify=passed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_manifest=mf.json" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_manifest_files=11" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_sha_records=11" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_verified_files=11" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_missing_files=0" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_completion=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_export=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_bundle=wb.zip" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_platform_ops=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_operations_report=exported" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_operations_advance=executed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_operations_plan_execute=executed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_delivery_dashboard=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_delivery_repair_execute=done" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_quality_gate=checked" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_quality_repair_plan=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_quality_repair_execute=done" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_plan_queue_row=updated" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_plan_queue_execute=done" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_action_item=created" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_project_space=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_project_note=recorded" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_project_action_item=created" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_project_answer=generated" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_project_repair=executed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_research_search=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_search_query=recovery evidence" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_research_inputs=applied" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_dataset_title=D1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_template_name=TP1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workspace_root=ws" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workspace_manifest=m.json" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_literature_query=prov" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_protocol_title=P1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workflow=WF1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workflow_export=wf.zip" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workflow_export_format=json" in line for line in rp_runner["lines"]), rp_runner
            rp_stage_dag = read_json(base + "/api/state/rp_stage_dag")
            assert any("host_workflow_id=WF1" in line for line in rp_stage_dag["lines"]), rp_stage_dag
            assert any("host_workflow_engine=plain-c-runner" in line for line in rp_stage_dag["lines"]), rp_stage_dag
            assert any("host_workflow_dag=ingest>clean>analyze>review>package" in line for line in rp_stage_dag["lines"]), rp_stage_dag
            rp_stage_state = read_json(base + "/api/state/rp_stage_state")
            assert any("host_workflow_run_id=R1" in line for line in rp_stage_state["lines"]), rp_stage_state
            assert any("host_workflow_engine=plain-c-runner" in line for line in rp_stage_state["lines"]), rp_stage_state
            assert any("host_workflow_retry_stage=clean" in line for line in rp_stage_state["lines"]), rp_stage_state
            assert any("host_workflow_cache_hit_stage=analyze" in line for line in rp_stage_state["lines"]), rp_stage_state
            assert any("host_workflow_worker_slots=2" in line for line in rp_stage_state["lines"]), rp_stage_state
            rp_run_events = read_json(base + "/api/state/rp_run_events")
            assert any("host_workflow_event=retry;stage=clean;reason=checksum_mismatch" in line for line in rp_run_events["lines"]), rp_run_events
            assert any("host_workflow_event=finished" in line for line in rp_run_events["lines"]), rp_run_events
            rp_cache_index = read_json(base + "/api/state/rp_cache_index")
            assert any("host_workflow_cache_hit_stage=analyze" in line for line in rp_cache_index["lines"]), rp_cache_index
            rp_retry_plan = read_json(base + "/api/state/rp_retry_plan")
            assert any("host_workflow_retry_reason=checksum_mismatch" in line for line in rp_retry_plan["lines"]), rp_retry_plan
            rp_wfio = read_json(base + "/api/state/rp_wfio")
            assert any("host_portability_payload=applied" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_import=workflow-import:WF1:nextflow" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("format=nextflow" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("source=main.wf1.nf" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_target=agentos-ucore" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_execution_plan=workflow-migration-execution-plan:WF1:agentcompare" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_compare_profile=compare-profile:WF1:migration" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_scenario=backend-scenario:WF1" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_rehearsal=passed" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_decision=ready_for_agentos" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_package=wf-portability.zip" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_steps=applied" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_import_action=workflow-import:WF1:nextflow;format=nextflow;source=main.wf1.nf" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_plan_action=workflow-migration-plan:WF1;target=agentos-ucore;steps=9;risks=4" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_bind_action=workflow-migration-execution-plan:WF1:agentcompare;profile=compare-profile:WF1:migration;scenario=backend-scenario:WF1;backend_cases=4" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_rehearse_action=workflow-rehearsal:WF1;binding=workflow-migration-binding:WF1;status=passed;observed_ready=3;skipped=1" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_review_action=workflow-migration-readiness:WF1;decision=ready_for_agentos;blocking_items=0;work_items=6" in line for line in rp_wfio["lines"]), rp_wfio
            assert any("host_portability_package_action=wf-portability.zip;format=zip;import=workflow-import:WF1:nextflow;bundle=wf-portability.zip" in line for line in rp_wfio["lines"]), rp_wfio
            rp_worker = read_json(base + "/api/state/rp_worker")
            assert any("host_workflow_worker_slots=2" in line for line in rp_worker["lines"]), rp_worker
            assert any("host_workflow_queue_depth=5" in line for line in rp_worker["lines"]), rp_worker
            rp_execobs = read_json(base + "/api/state/rp_execobs")
            assert any("host_workflow_observer_events=12" in line for line in rp_execobs["lines"]), rp_execobs
            assert any("host_workflow_retry_reason=checksum_mismatch" in line for line in rp_execobs["lines"]), rp_execobs
            rp_lit = read_json(base + "/api/state/rp_lit")
            assert any("host_action_literature_query=prov" in line for line in rp_lit["lines"]), rp_lit
            assert any("host_action_literature_max_results=7" in line for line in rp_lit["lines"]), rp_lit
            assert any("host_action_evidence_included=4" in line for line in rp_lit["lines"]), rp_lit
            assert any("host_action_protocol_title=P1" in line for line in rp_lit["lines"]), rp_lit
            rp_knowledge = read_json(base + "/api/state/rp_knowledge")
            assert any("host_action_library_citation=c1" in line for line in rp_knowledge["lines"]), rp_knowledge
            assert any("host_action_literature_query=prov" in line for line in rp_knowledge["lines"]), rp_knowledge
            assert any("host_action_evidence_included=4" in line for line in rp_knowledge["lines"]), rp_knowledge
            assert any("host_action_protocol_title=P1" in line for line in rp_knowledge["lines"]), rp_knowledge
            rp_review = read_json(base + "/api/state/rp_review2")
            assert any("host_action_human_review=usable-review:Wang:1" in line for line in rp_review["lines"]), rp_review
            assert any("host_action_review_decision=needs_revision" in line for line in rp_review["lines"]), rp_review
            rp_revision = read_json(base + "/api/state/rp_revision")
            assert any("host_action_revision_task=created" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_revision_targets=m,c,s" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_revision_task_id=task1" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_revision_run=completed" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_workbench_writing=ready" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_workbench_manuscript_format=markdown" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_workbench_audit_scope=citations" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_workbench_revision_area=methods" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_workbench_revision_task=1" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_workbench_revision_status=done" in line for line in rp_revision["lines"]), rp_revision
            rp_report = read_json(base + "/api/state/rp_report_text")
            assert any("host_report_run_id=R1" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_reviewer=Wang" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_revision_targets=m,c,s" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_bundle=ev" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_compare_profile=pb" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_title=T1" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_question=Q1" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_provider=template" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_dataset_rows=7" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_workbench_outputs=rp_runner,rp_revision,rp_package" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_workbench=W1" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_workbench_question=Ready?" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_workbench_note_title=Scope" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_workbench_manifest=mf.json" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_workbench_bundle=wb.zip" in line for line in rp_report["lines"]), rp_report
            rp_manifest = read_json(base + "/api/state/rp_artifact_manifest")
            assert any("host_manifest_run_id=R1" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_revision_targets=m,c,s" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_bundle=ev" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_compare_profile=pb" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workbench_outputs=rp_runner,rp_revision,rp_package" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workbench=W1" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workbench_runbook_format=markdown" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workbench_timeline_format=html" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workbench_manifest=mf.json" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_file_count=11" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_sha_records=11" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_file_verify=passed" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_verified_files=11" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_missing_files=0" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workbench_bundle=wb.zip" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workflow=WF1" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_workflow_export=wf.zip" in line for line in rp_manifest["lines"]), rp_manifest
            rp_package = read_json(base + "/api/state/rp_package")
            assert any("host_action_export_bundle=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_export_bundle_name=ev" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_bundle_contents=report,manifest,notebook,compare" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_package=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_handoff_scope=full" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_bundle=wb.zip" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_manifest=mf.json" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_manifest_files=11" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_sha_records=11" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_verified_files=11" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_missing_files=0" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_brief_format=html" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_dossier_format=markdown" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_graph_format=dot" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_citation_format=bibtex" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_manuscript_format=markdown" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_completion=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_readiness=checked" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_answer_audit=passed" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_notes_filter=decision" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_board_filter=open" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_row_id=row1" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_row_status=done" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_runbook_format=markdown" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workbench_timeline_format=html" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_platform_ops_package=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_quality_gate=checked" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_quality_repair_plan=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_quality_repair_execute=done" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_delivery_dashboard=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_plan_queue=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_project_space=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_research_search=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_package=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_id=WF1" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_bundle=wf.zip" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_retry_stage=clean" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_cache_hit_stage=analyze" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_worker_slots=2" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_package=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_import=workflow-import:WF1:nextflow" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_target=agentos-ucore" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_profile=compare-profile:WF1:migration" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_bundle=wf-portability.zip" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_steps=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_format=zip" in line for line in rp_package["lines"]), rp_package
            rp_nbexec = read_json(base + "/api/state/rp_nbexec")
            assert any("host_action_notebook_export=ready" in line for line in rp_nbexec["lines"]), rp_nbexec
            assert any("host_action_notebook_format=ipynb" in line for line in rp_nbexec["lines"]), rp_nbexec
            assert any("host_action_notebook_workbench=rp_runner" in line for line in rp_nbexec["lines"]), rp_nbexec
            assert any("host_action_notebook_workbench_id=W1" in line for line in rp_nbexec["lines"]), rp_nbexec
            assert any("host_action_notebook_workbench_docs=ready" in line for line in rp_nbexec["lines"]), rp_nbexec
            rp_uresrun = read_json(base + "/api/state/rp_uresrun")
            assert any("host_action_run_outputs=rp_report_text,rp_artifact_manifest,rp_nbexec,rp_package" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_title=T1" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_question=Q1" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_provider=template" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_dataset_rows=7" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_reference_entries=3" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workspace_files=5" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workbench_outputs=rp_runner,rp_revision,rp_package" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workbench=W1" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workbench_manifest=mf.json" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workbench_bundle=wb.zip" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_research_inputs=ready" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_dataset_title=D1" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_library_citation=c1" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_template_name=TP1" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workspace_root=ws" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_workspace_manifest=m.json" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_literature_query=prov" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_evidence_included=4" in line for line in rp_uresrun["lines"]), rp_uresrun
            assert any("host_action_protocol_title=P1" in line for line in rp_uresrun["lines"]), rp_uresrun
            rp_actionio = read_json(base + "/api/state/rp_actionio")
            assert any("host_action_research_run=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_research_inputs=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_evidence_inputs=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_human_review=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_revision=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_workbench=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_workbench_outputs=rp_runner,rp_revision,rp_package" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_platform_ops=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_platform_ops_outputs=rp_runner,rp_package,rp_api_action,rp_web_bundle" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_workflow=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_workflow_outputs=rp_stage_dag,rp_stage_state,rp_run_events,rp_artifact_manifest,rp_package" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability_profile=compare-profile:WF1:migration" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability_steps=6" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_llm_relay=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_llm_outputs=rp_llm_req,rp_llmq,rp_llm_resp,rp_llm_packets,rp_llm_hostreq,rp_llm_fallback" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_export=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_agentcompare=1" in line for line in rp_actionio["lines"]), rp_actionio
            rp_api_artifacts = read_json(base + "/api/state/rp_api_artifacts")
            assert any("host_action_file_manifest=mf.json" in line for line in rp_api_artifacts["lines"]), rp_api_artifacts
            assert any("host_action_file_manifest_files=11" in line for line in rp_api_artifacts["lines"]), rp_api_artifacts
            assert any("host_action_file_sha_records=11" in line for line in rp_api_artifacts["lines"]), rp_api_artifacts
            assert any("host_action_file_verify=passed" in line for line in rp_api_artifacts["lines"]), rp_api_artifacts
            assert any("host_action_file_verified=11" in line for line in rp_api_artifacts["lines"]), rp_api_artifacts
            assert any("host_action_file_missing=0" in line for line in rp_api_artifacts["lines"]), rp_api_artifacts
            rp_api_data = read_json(base + "/api/state/rp_api_data")
            assert any("host_action_file_manifest=mf.json" in line for line in rp_api_data["lines"]), rp_api_data
            assert any("host_action_file_manifest_files=11" in line for line in rp_api_data["lines"]), rp_api_data
            assert any("host_action_file_sha_records=11" in line for line in rp_api_data["lines"]), rp_api_data
            assert any("host_action_file_verify=passed" in line for line in rp_api_data["lines"]), rp_api_data
            assert any("host_action_file_verified=11" in line for line in rp_api_data["lines"]), rp_api_data
            assert any("host_action_file_missing=0" in line for line in rp_api_data["lines"]), rp_api_data
            rp_web_bundle = read_json(base + "/api/state/rp_web_bundle")
            assert any("host_action_research_inputs=rp_input,rp_runner,rp_api_run" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_evidence_inputs=rp_lit,rp_knowledge,rp_api_evidence" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_workbench_outputs=rp_runner,rp_revision,rp_package" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_workflow_outputs=rp_stage_dag,rp_stage_state,rp_run_events,rp_artifact_manifest,rp_package" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_portability_profile=compare-profile:WF1:migration" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_portability_steps=6" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_llm_relay=rp_llm_req,rp_llmq,rp_llm_resp,rp_llm_packets,rp_llm_hostreq,rp_llm_fallback" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_platform_ops=rp_runner,rp_package,rp_api_action" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_search_query=recovery evidence" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            rp_agentcmp = read_json(base + "/api/state/rp_agentcmp")
            assert any("host_action_compare_requested=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_compare_profile=pb" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_portability_verified=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_portability_steps_verified=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            rp_api_compare = read_json(base + "/api/state/rp_api_compare")
            assert any("host_action_payload_applied=1" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_run_id=R1" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_bundle=ev" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_compare_profile=pb" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench=W1" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_title=WB1" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_literature_query=prov" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_question=Ready?" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_query=rec" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_advance_task=dm" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_task=hr" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_note_kind=decision" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_note_title=Scope" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_brief_format=html" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_citation_format=bibtex" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_manuscript_format=markdown" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_revision_area=methods" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_board_filter=open" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_row_status=done" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_handoff_scope=full" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_bundle=wb.zip" in line for line in rp_api_compare["lines"]), rp_api_compare
            rp_api_action = read_json(base + "/api/state/rp_api_action")
            assert any("actions=38" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("operations_report=/actions/research/operations-report" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workflow_portability_run=/actions/workflow-portability/run" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workflow_portability_import=/actions/workflow-portability/import" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workflow_portability_package=/actions/workflow-portability/package" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workbench_quality_gate=/actions/research/workbench-quality-gate" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_space=/actions/research/project-space" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("research_search_export=/actions/research-search/export" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("llm_relay_request=/actions/research/llm-relay-request" in line for line in rp_api_action["lines"]), rp_api_action
            rp_llm_req = read_json(base + "/api/state/rp_llm_req")
            assert any("host_llm_request_id=llm-q1" in line for line in rp_llm_req["lines"]), rp_llm_req
            assert any("host_llm_provider=host-relay" in line for line in rp_llm_req["lines"]), rp_llm_req
            rp_llm_resp = read_json(base + "/api/state/rp_llm_resp")
            assert any("host_llm_response_id=llm-r1" in line for line in rp_llm_resp["lines"]), rp_llm_resp
            assert any("host_llm_response_summary=Recovered_evidence_ready" in line for line in rp_llm_resp["lines"]), rp_llm_resp
            rp_llm_packets = read_json(base + "/api/state/rp_llm_packets")
            assert any("host_llm_packet_request=llm-q1" in line for line in rp_llm_packets["lines"]), rp_llm_packets
            rp_llm_hostreq = read_json(base + "/api/state/rp_llm_hostreq")
            assert any("host_llm_host_response=llm-r1" in line for line in rp_llm_hostreq["lines"]), rp_llm_hostreq
            rp_llm_fallback = read_json(base + "/api/state/rp_llm_fallback")
            assert any("host_llm_fallback_case=missing_cloud_key" in line for line in rp_llm_fallback["lines"]), rp_llm_fallback
            rp_api_runtime = read_json(base + "/api/state/rp_api_runtime")
            assert any("host_llm_request_id=llm-q1" in line for line in rp_api_runtime["lines"]), rp_api_runtime
            rp_api_run = read_json(base + "/api/state/rp_api_run")
            assert any("host_action_title=T1" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_question=Q1" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_provider=template" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_dataset_rows=7" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_reference_entries=3" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_workspace_files=5" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_research_inputs=ready" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_dataset_title=D1" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_library_citation=c1" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_template_name=TP1" in line for line in rp_api_run["lines"]), rp_api_run
            assert any("host_action_workspace_root=ws" in line for line in rp_api_run["lines"]), rp_api_run
            rp_api_evidence = read_json(base + "/api/state/rp_api_evidence")
            assert any("host_action_evidence_inputs=ready" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_action_literature_query=prov" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_action_evidence_included=4" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_action_protocol_title=P1" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            rp_result = read_json(base + "/api/state/rp_host_run_result")
            assert rp_result["values"]["qemu_orch_passed"] == "1", rp_result
            assert int(rp_result["values"]["extracted_state_files"]) >= 100, rp_result
            assert any(f"qemu_rp_compare_plain: host_actions={len(actions)} verified" in line for line in rp_result["lines"]), rp_result

            run_html = read_text(base + "/run.html")
            assert "Plain uCore Research" in run_html
            assert "Workbench Tasks" in run_html
            assert "Research Output" in run_html
            assert "R1" in run_html
            assert "ev" in run_html
            assert "host_action_revision_run" in run_html
            assert "host_action_workbench_question" in run_html
            assert "host_action_workbench_note_title" in run_html
            assert "host_action_workbench_manuscript_format" in run_html
            assert "host_action_dataset_title" in run_html
            artifacts_html = read_text(base + "/artifacts.html")
            assert "Evidence Package" in artifacts_html
            assert "ev" in artifacts_html
            agents_html = read_text(base + "/agents.html")
            assert "Agent Detail" in agents_html
            assert "Agent Roster" in agents_html
            assert "Decision Flow" in agents_html
            assert "Handoff Flow" in agents_html
            assert "orchestrator" in agents_html
            assert "rerun_align_only" in agents_html
            assert "planner-&gt;retriever" in agents_html
            assert "Handoffs" in agents_html
            assert "rp_handoff" in agents_html
            evidence_html = read_text(base + "/evidence.html")
            assert "Evidence Detail" in evidence_html
            assert "Claim Records" in evidence_html
            assert "Provenance Paths" in evidence_html
            assert "Evidence Protocol Files" in evidence_html
            assert "retrylog-a" in evidence_html
            assert "plan&gt;data&gt;review&gt;repair&gt;audit" in evidence_html
            assert "Evidence Protocol" in evidence_html
            assert "usable-evidence-protocol:RUN-900:1" in evidence_html
            assert "host_action_protocol_title" in evidence_html
            compare_html = read_text(base + "/compare.html")
            assert "Compare Summary" in compare_html
            assert "Compare Metrics" in compare_html
            assert "Plain Kernel Signals" in compare_html
            assert "Consistency Signals" in compare_html
            assert "File Scans" in compare_html
            assert "Rebuild Steps" in compare_html
            assert "pb" in compare_html
            actions_html = read_text(base + "/actions.html")
            assert "Batch Actions" in actions_html
            assert "Host Actions" in actions_html
            assert "qemu_orch_passed" in actions_html
            assert "host_action_revision" in actions_html
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    print("test_plain_ucore_reader_e2e: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
