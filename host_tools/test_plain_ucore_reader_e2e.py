#!/usr/bin/env python3
"""End-to-end check for host POST action -> plain uCore run -> extracted state -> reader API."""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

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
            auto_llm_relay=True,
            llm_relay_mode="template",
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
                {
                    "path": "/actions/research/studio-launch",
                    "payload": {
                        "title": "Studio cytokine evidence",
                        "goal": "Recovery evidence ready",
                        "direction": "evidence review",
                        "material_notes": "Small demonstration table for the studio workflow.",
                        "provider_id": "template",
                        "workbench_id": "W1",
                        "latest_run_id": "R1",
                        "latest_answer_id": "answer1",
                    },
                },
                {"path": "/actions/research/dataset", "payload": {"title": "D1", "dataset_rows": "6", "columns": "a,b,c", "tags": "h"}},
                {"path": "/actions/research/dataset-preview", "payload": {"dataset_id": "usable-dataset:response-table", "rows": "7", "quality": "pass"}},
                {"path": "/actions/research/dataset-visualization", "payload": {"dataset_id": "usable-dataset:response-table", "chart": "response-chart.svg", "x_field": "sample", "y_field": "value", "group_field": "group", "points": "7"}},
                {"path": "/actions/research/dataset-card", "payload": {"dataset_id": "usable-dataset:response-table", "readiness": "ready", "warnings": "0"}},
                {"path": "/actions/research/dataset-answer", "payload": {"dataset_id": "usable-dataset:response-table", "question": "Which group is stronger?", "answer": "treatment"}},
                {"path": "/actions/research/dataset-run", "payload": {"dataset_id": "usable-dataset:response-table", "run_id": "usable-run:dataset:e2e", "provider_id": "template", "question": "Which group is stronger?", "artifacts": "5"}},
                {"path": "/actions/research/dataset-run-comparison", "payload": {"dataset_id": "usable-dataset:response-table", "left_run": "usable-run:dataset:base", "right_run": "usable-run:dataset:e2e", "decision": "stable"}},
                {"path": "/actions/research/dataset-portfolio", "payload": {"dataset_id": "usable-dataset:response-table", "filter": "ready", "datasets": "3", "ready": "3"}},
                {"path": "/actions/research/library-source", "payload": {"citation_key": "c1", "tags": "h", "source_text": "@c"}},
                {"path": "/actions/research/source-portfolio", "payload": {"source_id": "usable-source:library2026:1", "query": "agent provenance", "sources": "42", "reviewed": "8"}},
                {"path": "/actions/research/template", "payload": {"name": "TP1", "question": "TQ", "provider_id": "template", "dataset_tags": "h", "library_tags": "h"}},
                {"path": "/actions/research/sample-workbench", "payload": {"workbench_id": "usable-workbench:sample-e2e", "template_id": "usable-template:workspace-900", "dataset_id": "usable-dataset:response-table", "question": "What is ready for review?"}},
                {"path": "/actions/research/study-protocol", "payload": {"protocol_id": "usable-study-protocol:e2e", "title": "E2E protocol", "question": "Which group is stronger?", "hypothesis": "treatment is stronger", "dataset_tags": "response", "source_tags": "agent"}},
                {"path": "/actions/research/run-study-protocol", "payload": {"protocol_id": "usable-study-protocol:e2e", "run_id": "usable-study-protocol-run:e2e", "provider_id": "template"}},
                {"path": "/actions/research/study-protocol-compliance", "payload": {"run_id": "usable-study-protocol-run:e2e", "decision": "pass", "findings": "0"}},
                {"path": "/actions/research/study-protocol-bundle", "payload": {"run_id": "usable-study-protocol-run:e2e", "bundle": "study-protocol-e2e.zip", "files": "8"}},
                {"path": "/actions/research/study-protocol-launch", "payload": {"launch_id": "study-protocol-launch:e2e", "protocol_id": "usable-study-protocol:e2e", "run_id": "usable-study-protocol-run:e2e", "provider_id": "template"}},
                {"path": "/actions/research/study-protocol-launch-rerun", "payload": {"launch_id": "study-protocol-launch:e2e", "rerun_id": "study-protocol-rerun:e2e", "provider_id": "template"}},
                {"path": "/actions/research/study-protocol-launch-comparison", "payload": {"launch_id": "study-protocol-launch:e2e", "left": "launch:e2e:base", "right": "launch:e2e:rerun", "changed_metrics": "0"}},
                {"path": "/actions/research/study-protocol-reproduction-package", "payload": {"launch_id": "study-protocol-launch:e2e", "package_id": "study-protocol-reproduction-package:e2e", "files": "8", "notebooks": "2", "datasets": "2"}},
                {"path": "/actions/research/study-protocol-reproduction-package-review", "payload": {"package_id": "study-protocol-reproduction-package:e2e", "decision": "approved", "reviewer": "Wang"}},
                {"path": "/actions/research/study-protocol-reproduction-package-action-plan", "payload": {"package_id": "study-protocol-reproduction-package:e2e", "steps": "5", "owner": "recovery"}},
                {"path": "/actions/research/study-protocol-reproduction-package-action-execute", "payload": {"package_id": "study-protocol-reproduction-package:e2e", "steps_done": "5", "result": "passed", "provider_id": "template"}},
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
                {"path": "/actions/research/project-scaffold", "payload": {"template_id": "scaffold-template:dataset-review", "project_id": "reader-project", "title": "Reader project", "dataset_id": "dataset-reader", "library_source_id": "library-reader", "files": "9", "workspace": "workspace/reader-project"}},
                {"path": "/actions/research/project-launch", "payload": {"project_id": "reader-project", "scaffold_id": "scaffold:reader-project:dataset-review", "workbench_id": "usable-workbench:reader-project", "run_id": "usable-run:reader-project", "provider_id": "template", "question": "Is the reader project ready?"}},
                {"path": "/actions/research/project-action-execute", "payload": {"project_id": "reader-project", "action_id": "usable-project-action:reader-project:1", "action_key": "build_reproduction_package", "provider_id": "template", "max_steps": "5", "result": "completed"}},
                {"path": "/actions/research/project-space", "payload": {"workbench_id": "W1", "project_id": "lab-gene-x", "query": "recovery"}},
                {"path": "/actions/research/project-space-note", "payload": {"workbench_id": "W1", "kind": "decision", "title": "Project scope", "body": "Keep recovered evidence first."}},
                {"path": "/actions/research/project-space-action-item", "payload": {"workbench_id": "W1", "title": "Project task", "instruction": "Prepare handoff", "priority": "high"}},
                {"path": "/actions/research/project-space-review", "payload": {"workbench_id": "W1", "project_id": "lab-gene-x", "decision": "needs_revision", "reviewer": "Wang", "required_changes": "1"}},
                {"path": "/actions/research/project-space-answer", "payload": {"workbench_id": "W1", "question": "What is ready?", "limit": "6"}},
                {"path": "/actions/research/project-space-repair-execute", "payload": {"workbench_id": "W1", "repair_id": "repair1", "provider_id": "template", "max_steps": "4"}},
                {"path": "/actions/research/project-space-task-board-row", "payload": {"workbench_id": "W1", "row_id": "project-row-1", "row_status": "done", "row_note": "Updated from reader"}},
                {"path": "/actions/research/project-handoff-audit", "payload": {"project_id": "lab-gene-x", "scope": "full", "decision": "ready"}},
                {"path": "/actions/research/project-release-gate", "payload": {"project_id": "lab-gene-x", "decision": "release", "checks": "6", "required_actions": "0", "suggested_actions": "2"}},
                {"path": "/actions/research/project-snapshot", "payload": {"project_id": "lab-gene-x", "snapshot_id": "project-snapshot:lab-gene-x:1", "files": "11", "hash_records": "11", "changes": "0"}},
                {"path": "/actions/research/project-snapshot-comparison", "payload": {"project_id": "lab-gene-x", "left": "snapshot0", "right": "snapshot1", "changed_files": "0", "decision": "stable"}},
                {"path": "/actions/research/project-reproducibility-audit", "payload": {"project_id": "lab-gene-x", "inputs": "2", "outputs": "8", "notebooks": "2", "claim_audits": "1", "decision": "passed"}},
                {"path": "/actions/research/project-provenance-graph", "payload": {"project_id": "lab-gene-x", "nodes": "9", "edges": "12", "dot": "project-provenance.dot"}},
                {"path": "/actions/research/project-delivery", "payload": {"project_id": "lab-gene-x", "bundle": "project-bundle.zip", "decision": "ready", "release_gate": "release", "handoff": "ready"}},
                {"path": "/actions/research/package-intake", "payload": {"package_id": "external-review", "label": "External review package", "files": "5", "sha256": "checked", "decision": "accepted"}},
                {"path": "/actions/research-search/save", "payload": {"query": "recovery evidence", "name": "Recovery search"}},
                {"path": "/actions/research-search/export", "payload": {"query": "recovery evidence", "limit": "20"}},
                {"path": "/actions/research-search/note", "payload": {"workbench_id": "W1", "query": "recovery evidence", "title": "Search note", "note": "Keep hits."}},
                {"path": "/actions/research-search/action-item", "payload": {"workbench_id": "W1", "query": "recovery evidence", "title": "Review search hits", "instruction": "Promote key hit", "priority": "high"}},
                {"path": "/actions/host-workflow/run", "payload": {"workflow_id": "WF1", "run_id": "R1", "engine": "plain-c-runner", "stages": "6", "dag": "ingest>clean>analyze>review>package", "max_workers": "2", "worker_slots": "2", "queue_depth": "5", "observer_events": "12", "failed_stage": "clean", "retry_stage": "clean", "cache_hit_stage": "analyze", "retry_reason": "checksum_mismatch", "cache": "content"}},
                {"path": "/actions/host-workflow/export", "payload": {"workflow_id": "WF1", "run_id": "R1", "format": "json", "bundle": "wf.zip"}},
                {"path": "/actions/host-workflow/stage-attempt", "payload": {"workflow_id": "WF1", "run_id": "R1", "stage": "clean", "attempt": "2", "status": "failed", "command": "clean_reads", "duration_ms": "1200"}},
                {"path": "/actions/host-workflow/cache-decision", "payload": {"workflow_id": "WF1", "run_id": "R1", "stage": "analyze", "cache_key": "cache:WF1:analyze", "cache_result": "hit", "cache_policy": "content"}},
                {"path": "/actions/host-workflow/retry-decision", "payload": {"workflow_id": "WF1", "run_id": "R1", "stage": "clean", "retry_reason": "checksum_mismatch", "next_attempt": "3", "decision": "rerun_stage"}},
                {"path": "/actions/host-workflow/artifact-manifest", "payload": {"workflow_id": "WF1", "run_id": "R1", "artifact": "clean.metrics.json", "artifact_kind": "metrics", "sha256": "sha-host-wf1", "bytes": "4096"}},
                {"path": "/actions/host-workflow/report-export", "payload": {"workflow_id": "WF1", "run_id": "R1", "report": "workflow-report.md", "format": "markdown", "sections": "5", "status": "ready"}},
                {"path": "/actions/research/artifact-input", "payload": {"run_id": "R1", "file": "reads_R1.fastq", "artifact_kind": "fastq", "sha256": "sha-host-input", "bytes": "2048", "source": "upload"}},
                {"path": "/actions/research/artifact-derive", "payload": {"run_id": "R1", "input": "reads_R1.fastq", "output": "clean_reads.fastq", "operation": "trim", "stage": "clean", "sha256": "sha-host-derived"}},
                {"path": "/actions/research/artifact-log", "payload": {"run_id": "R1", "stage": "clean", "log": "clean.log", "level": "warn", "message": "adapter_trimmed"}},
                {"path": "/actions/research/artifact-chart", "payload": {"run_id": "R1", "chart": "qc-chart.json", "chart_type": "line", "data_file": "clean.metrics.json", "points": "12"}},
                {"path": "/actions/research/artifact-package", "payload": {"run_id": "R1", "package": "artifact-bundle.zip", "manifest": "artifact-manifest.json", "files": "5", "status": "ready"}},
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
            try:
                with request.urlopen(action, timeout=180) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                log_tail = ""
                try:
                    detail = json.loads(body)
                    log_path = Path(str(detail.get("run", {}).get("run", {}).get("log", "")))
                    if log_path.exists():
                        log_tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
                except Exception as tail_error:  # pragma: no cover - diagnostic path
                    log_tail = f"unable to read log tail: {tail_error}"
                raise AssertionError(f"batch action failed status={exc.code} body={body}\nlog_tail={log_tail}") from exc
            assert len(result["actions"]) == len(actions), result
            assert result["actions"][0]["path"] == "/actions/research/run", result
            assert result["actions"][-1]["path"] == "/actions/agentcompare/run", result
            assert result["run"]["status"] == "ready", result
            assert result["relay"]["status"] == "ready", result
            assert result["relay"]["mode"] == "template", result
            assert result["relay"]["requests"] >= 1, result
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
            assert any("host_relay_workbench_answer=review_summary_supported_by_current_evidence" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workflow=WF1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workflow_export=wf.zip" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workflow_export_format=json" in line for line in rp_runner["lines"]), rp_runner
            assert any("backend_evidence_report=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;status=ready" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_studio_session=usable-research-studio-session:W1" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_studio_title=Studio cytokine evidence" in line for line in rp_runner["lines"]), rp_runner
            assert any("workbench_delivery_scale=workbenches:5" in line for line in rp_runner["lines"]), rp_runner
            assert any("templates:5" in line for line in rp_runner["lines"]), rp_runner
            assert any("workspace_imports:5" in line for line in rp_runner["lines"]), rp_runner
            assert any("workspace_inspections:5" in line for line in rp_runner["lines"]), rp_runner
            assert any("answers:5" in line for line in rp_runner["lines"]), rp_runner
            assert any("deliveries:6" in line for line in rp_runner["lines"]), rp_runner
            assert any("project_action_plans:15" in line for line in rp_runner["lines"]), rp_runner
            assert any("project_runbooks:15" in line for line in rp_runner["lines"]), rp_runner
            rp_studio = read_json(base + "/api/state/rp_studio")
            assert any("host_action_studio_launch=accepted" in line for line in rp_studio["lines"]), rp_studio
            assert any("host_action_studio_title=Studio cytokine evidence" in line for line in rp_studio["lines"]), rp_studio
            assert any("host_action_studio_goal=Recovery evidence ready" in line for line in rp_studio["lines"]), rp_studio
            assert any("studio_session=usable-research-studio-session:W1:1" in line for line in rp_studio["lines"]), rp_studio
            assert any("studio_material=host_action;notes=Pasted notes and table rows" in line for line in rp_studio["lines"]), rp_studio
            studio_html = read_text(base + "/studio.html")
            assert "Research Studio" in studio_html
            assert "Studio Sessions" in studio_html
            assert "Studio Materials" in studio_html
            assert "Studio Action Trace" in studio_html
            assert "Studio cytokine evidence" in studio_html
            assert "Recovery evidence ready" in studio_html
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
            assert any("host_workflow_steps=applied" in line for line in rp_stage_state["lines"]), rp_stage_state
            assert any("host_workflow_stage_action=clean;attempt=2;status=failed;command=clean_reads;duration_ms=1200" in line for line in rp_stage_state["lines"]), rp_stage_state
            rp_run_events = read_json(base + "/api/state/rp_run_events")
            assert any("host_workflow_event=retry;stage=clean;reason=checksum_mismatch" in line for line in rp_run_events["lines"]), rp_run_events
            assert any("host_workflow_event=stage_attempt;workflow=WF1;run_id=R1;stage=clean;status=failed" in line for line in rp_run_events["lines"]), rp_run_events
            assert any("host_workflow_event=finished" in line for line in rp_run_events["lines"]), rp_run_events
            rp_cache_index = read_json(base + "/api/state/rp_cache_index")
            assert any("host_workflow_cache_hit_stage=analyze" in line for line in rp_cache_index["lines"]), rp_cache_index
            assert any("host_workflow_cache_action=analyze;key=cache:WF1:analyze;result=hit;policy=content" in line for line in rp_cache_index["lines"]), rp_cache_index
            rp_retry_plan = read_json(base + "/api/state/rp_retry_plan")
            assert any("host_workflow_retry_reason=checksum_mismatch" in line for line in rp_retry_plan["lines"]), rp_retry_plan
            assert any("host_workflow_retry_action=clean;reason=checksum_mismatch;next_attempt=3;decision=rerun_stage" in line for line in rp_retry_plan["lines"]), rp_retry_plan
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
            rp_artifact_manifest = read_json(base + "/api/state/rp_artifact_manifest")
            assert any("host_workflow_artifact_action=clean.metrics.json;kind=metrics;sha256=sha-host-wf1;bytes=4096" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("host_workflow_report_action=workflow-report.md;format=markdown;sections=5;status=ready" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("dossier=artifact-detail;source=rp_artifact;stage_log=rp_stage_log;chart=rp_chart_data;review_pack=rp_review_pack;status=ready" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("dossier_item=alignment;path=rp_artifact;section=rp_align_table;status=ready" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("dossier_check=workflow_stage;source=rp_stage_state;stage=align;status=recovered" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("dossier_check=review_gate;source=rp_review_dashboard;gate=artifact_manifest;status=pass" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("dossier_check=llm_quality;source=rp_llmeval;status=host_checked" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("artifact_review_path=raw_to_report;input=rp_input_fastq;prepared=rp_artifact:rp_normalized_fastq;artifact=rp_artifact:rp_align_table;report=rp_report_text;review=rp_review_dashboard;status=ready" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("artifact_review_path=quality_to_package;metrics=rp_artifact:rp_metrics_json;chart=rp_chart_data;llm_quality=rp_llmeval;delivery=rp_package;status=ready" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            assert any("artifact_review_path=recovery_to_review;failure=rp_stage_log;retry=rp_retry_plan;event=rp_run_events:4;manifest=rp_artifact_manifest;review_pack=rp_review_pack;status=recovered" in line for line in rp_artifact_manifest["lines"]), rp_artifact_manifest
            rp_artifact = read_json(base + "/api/state/rp_artifact")
            assert any("host_artifact_actions=applied" in line for line in rp_artifact["lines"]), rp_artifact
            assert any("host_artifact_input=reads_R1.fastq;kind=fastq;sha256=sha-host-input;bytes=2048;source=upload" in line for line in rp_artifact["lines"]), rp_artifact
            assert any("host_artifact_derive=reads_R1.fastq;output=clean_reads.fastq;operation=trim;stage=clean;sha256=sha-host-derived" in line for line in rp_artifact["lines"]), rp_artifact
            assert any("artifact_dossier=rp_input_fastq,rp_normalized_fastq,rp_align_table,rp_metrics_json,rp_gene_counts_csv,rp_chart_data,rp_stage_log" in line for line in rp_artifact["lines"]), rp_artifact
            assert any("artifact_review_link=rp_artifact_manifest->rp_review_pack->rp_package" in line for line in rp_artifact["lines"]), rp_artifact
            assert any("provenance=rp_align_table;stage=align;event=4;retry=rp_retry_plan;review_gate=artifact_manifest;llm_quality=rp_llmeval;status=recovered" in line for line in rp_artifact["lines"]), rp_artifact
            assert any("provenance=rp_metrics_json;stage=profile;event=5;cache=hit;review_gate=artifact_manifest;status=ready" in line for line in rp_artifact["lines"]), rp_artifact
            rp_stage_log = read_json(base + "/api/state/rp_stage_log")
            assert any("host_artifact_log=clean.log;stage=clean;level=warn;message=adapter_trimmed" in line for line in rp_stage_log["lines"]), rp_stage_log
            rp_chart_data = read_json(base + "/api/state/rp_chart_data")
            assert any("host_artifact_chart=qc-chart.json;type=line;data_file=clean.metrics.json;points=12" in line for line in rp_chart_data["lines"]), rp_chart_data
            rp_report_text = read_json(base + "/api/state/rp_report_text")
            assert any("host_workflow_report_action=workflow-report.md;format=markdown;sections=5;status=ready" in line for line in rp_report_text["lines"]), rp_report_text
            assert any("backend_evidence_report=rp_backend_exec" in line and "batch_tool_context,event_context,kernel_context_path,metadata_index" in line for line in rp_report_text["lines"]), rp_report_text
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
            assert any("host_relay_writer_summary=review_summary_supported_by_current_evidence" in line for line in rp_revision["lines"]), rp_revision
            rp_report = read_json(base + "/api/state/rp_report_text")
            assert any("host_report_run_id=R1" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_reviewer=Wang" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_revision_targets=m,c,s" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_bundle=ev" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_compare_profile=pb" in line for line in rp_report["lines"]), rp_report
            assert any("host_relay_report_summary=review_summary_supported_by_current_evidence" in line for line in rp_report["lines"]), rp_report
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
            assert any("report_source=workflow;state_file=rp_stage_state;source_key=host_workflow_run_id" in line for line in rp_report["lines"]), rp_report
            assert any("report_source=llm;state_file=rp_llm_resp;source_key=host_relay_response" in line for line in rp_report["lines"]), rp_report
            assert any("report_source=backend;state_file=rp_report_text;source_key=backend_evidence_report" in line for line in rp_report["lines"]), rp_report
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
            assert any("host_artifact_manifest_actions=applied" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_artifact_manifest_input=reads_R1.fastq;kind=fastq;sha256=sha-host-input;bytes=2048;source=upload" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_artifact_manifest_derive=reads_R1.fastq;output=clean_reads.fastq;operation=trim;stage=clean;sha256=sha-host-derived" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_artifact_manifest_log=rp_stage_log" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_artifact_manifest_chart=qc-chart.json;type=line;data_file=clean.metrics.json;points=12" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_artifact_manifest_package=artifact-bundle.zip;manifest=artifact-manifest.json;files=5;status=ready" in line for line in rp_manifest["lines"]), rp_manifest
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
            assert any("host_relay_delivery_file=llm_response" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_id=WF1" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_bundle=wf.zip" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_retry_stage=clean" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_cache_hit_stage=analyze" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_worker_slots=2" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_steps=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_artifact=clean.metrics.json" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_report=workflow-report.md" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_workflow_retry_decision=rerun_stage" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_package=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_import=workflow-import:WF1:nextflow" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_target=agentos-ucore" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_profile=compare-profile:WF1:migration" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_bundle=wf-portability.zip" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_steps=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_portability_format=zip" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_artifacts=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package" in line for line in rp_package["lines"]), rp_package
            assert any("host_artifact_package=artifact-bundle.zip;manifest=artifact-manifest.json;files=5;status=ready" in line for line in rp_package["lines"]), rp_package
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
            assert any("host_action_workflow_steps=5" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability_profile=compare-profile:WF1:migration" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_portability_steps=6" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_artifacts=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package" in line for line in rp_actionio["lines"]), rp_actionio
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
            assert any("host_action_workflow_steps=5" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_portability_outputs=rp_wfio,rp_package,rp_agentcmp" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_portability_profile=compare-profile:WF1:migration" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_portability_steps=6" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_artifacts=1" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_artifact_outputs=rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_llm_relay=rp_llm_req,rp_llmq,rp_llm_resp,rp_llm_packets,rp_llm_hostreq,rp_llm_fallback" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_platform_ops=rp_runner,rp_package,rp_api_action" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_search_query=recovery evidence" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_project_review=rp_web_bundle" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("project_review=ready" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("release_gate=project-release-gate" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("project_snapshot=project-snapshot" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("reproducibility_audit=project-reproducibility-audit" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("provenance_graph=project-provenance-graph" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("project_delivery=project-delivery" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_project_release_gate=release" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_project_provenance_graph=exported" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            assert any("host_action_project_delivery=project-bundle.zip" in line for line in rp_web_bundle["lines"]), rp_web_bundle
            rp_agentcmp = read_json(base + "/api/state/rp_agentcmp")
            assert any("host_action_compare_requested=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_compare_profile=pb" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_portability_verified=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_portability_steps_verified=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_artifacts_verified=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("test_cases=2800" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("tool_events=328" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("review_handoff_checks=13" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("review_pack_bridges=4" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("llm_delivery_checks=16" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("llm_review_links=2" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("workflow_portability_checks=14" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("portability_package=workflow-portability" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("portability_backend_checks=12" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("backend_scenario=backend-scenario:RUN-042:agentcompare" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("passed_cases=2" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("planned_cases=2" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("backend_runner_checks=12" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("backend_runner_detail_checks=24" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("runner_detail_rows=4" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("backend_runner_report_checks=20" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("decision_support_checks=80" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("usable_research_checks=100" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("runner_report_rows=4" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("backend_report_links=2" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("runner_cases=4" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("runner_passed=2" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("runner_planned=2" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("lab_governance_ops_checks=26" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("knowledge_index_checks=22" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("llm_transcript_checks=3" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("workbench_delivery_checks=15" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("research_portfolio_checks=16" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("execution_scale_checks=14" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("operations_scale_checks=12" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("project_revision_incident_checks=12" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("project_revision_incident=revision_tasks:1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("reserved_research_surface_checks=21" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("root_state_surface_checks=10" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("root_state_surface=projects:1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("agentos_reserved_surface_checks=21" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("agentos_reserved_surface=profiles:0" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("state_catalog=keys:573" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("startup_doctor=quickstart:ready" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("incident:INC-RUN-042-ALIGN-OOM" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("knowledge_index=search_documents:1385" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("provenance_nodes:406" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("usable_artifacts:429" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("llm_transcripts=90" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("workbenches=5" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("deliveries=6" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("research_portfolio=sources:42" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("project_handoff_audits:30" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("agentcompare_execution_scale=reports:3" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("results:15" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_runtime_scale=workflow_runs:10" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("stage_runs:70" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("content_graph_scale=content_objects:145" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_operations_scale=audit_records:5" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("project_scaffolds:1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("reason:memory_limit" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("usable_projects:20" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            rp_query = read_json(base + "/api/state/rp_query")
            assert any("knowledge_index=search_documents:1385" in line for line in rp_query["lines"]), rp_query
            assert any("provenance_nodes:406" in line for line in rp_query["lines"]), rp_query
            assert any("provenance_links:544" in line for line in rp_query["lines"]), rp_query
            assert any("events:6816" in line for line in rp_query["lines"]), rp_query
            assert any("context_records:348" in line for line in rp_query["lines"]), rp_query
            assert any("usable_artifacts:429" in line for line in rp_query["lines"]), rp_query
            rp_backend_exec = read_json(base + "/api/state/rp_backend_exec")
            assert any("runner_detail_checks=16" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_verified_inputs=4" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_detail_rows=4" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_detail_schema=src,req,obs,act,review" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_report_rows=4" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_report_schema=plain_cost,agentos_replace,risk,status" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_case=plain-ucore" in line and "input_check=pass" in line and "artifact_check=pass" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_case=retry-recovery" in line and "att=2" in line and "retry=tool_output_missing" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_case=agentos-context" in line and "input_check=planned" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_detail=plain-ucore" in line and "act=record" in line and "review=baseline" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_detail=retry-recovery" in line and "act=rerun_align" in line and "review=recovered" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_detail=agentos-context" in line and "act=kernel_context" in line and "review=target" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_detail=agentos-fsmeta" in line and "act=kernel_fsmeta" in line and "review=target" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_report=plain-ucore" in line and "plain_cost=file_scan_manifest" in line and "agentos_replace=batch_tool_context" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_report=retry-recovery" in line and "risk=stale_retry" in line and "status=passed" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_report=agentos-context" in line and "agentos_replace=kernel_context_path" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            assert any("runner_report=agentos-fsmeta" in line and "plain_cost=scan_records_128" in line for line in rp_backend_exec["lines"]), rp_backend_exec
            rp_study = read_json(base + "/api/state/rp_study")
            assert any("detail_checks=4" in line for line in rp_study["lines"]), rp_study
            assert any("detail_checks=kernel" in line for line in rp_study["lines"]), rp_study
            assert any("review_dashboard=ready;sections=8;gates=6;plain_kernel=ordinary_files" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("review_pack=ready;evidence_items=11;actions=5;plain_kernel=ordinary_files;backend_evidence=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("review_handoff_checks=13" in line and "backend_review=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("runbook_recovery_checks=16" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("project_delivery_checks=18" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("study_protocol_checks=20" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("operations_board_checks=18" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("control_plane_checks=30" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("integrity_plane_checks=36" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("coherence_plane_checks=40" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("publication_checks=48" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("calculation_checks=84" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("real_task_checks=96" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("analysis_results_checks=96" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("experiment_campaign_checks=108" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("statistical_design_checks=120" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("model_registry_service_checks=96" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("systematic_review_checks=104" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("experiment_scheduling_checks=88" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("training_compliance_checks=92" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("release_dossier_checks=112" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("mature_capability_checks=72" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("provenance_view_checks=64" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("provenance_query_checks=72" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            rp_stdesign = read_json(base + "/api/state/rp_stdesign")
            assert any("statistical_design_checks=120" in line for line in rp_stdesign["lines"]), rp_stdesign
            assert any("design=stat-design:lab-gene-x:run042-primary" in line for line in rp_stdesign["lines"]), rp_stdesign
            rp_power = read_json(base + "/api/state/rp_power")
            assert any("required_per_group=11" in line for line in rp_power["lines"]), rp_power
            assert any("status=underpowered" in line for line in rp_power["lines"]), rp_power
            rp_random = read_json(base + "/api/state/rp_random")
            assert any("assignments=4" in line for line in rp_random["lines"]), rp_random
            assert any("status=balanced" in line for line in rp_random["lines"]), rp_random
            rp_blind = read_json(base + "/api/state/rp_blind")
            assert any("status=ok" in line for line in rp_blind["lines"]), rp_blind
            rp_streview = read_json(base + "/api/state/rp_streview")
            assert any("stat_result=approved_with_sample_size_note" in line for line in rp_streview["lines"]), rp_streview
            rp_modelreg = read_json(base + "/api/state/rp_modelreg")
            assert any("model_registry_service_checks=96" in line for line in rp_modelreg["lines"]), rp_modelreg
            assert any("model=registered-model:agent-triage-template" in line for line in rp_modelreg["lines"]), rp_modelreg
            rp_modelver = read_json(base + "/api/state/rp_modelver")
            assert any("version=model-version:agent-triage-template:v1" in line for line in rp_modelver["lines"]), rp_modelver
            assert any("metric_artifact_count=52" in line for line in rp_modelver["lines"]), rp_modelver
            rp_modeleval = read_json(base + "/api/state/rp_modeleval")
            assert any("metric_evidence_coverage=1.000" in line for line in rp_modeleval["lines"]), rp_modeleval
            assert any("status=passed" in line for line in rp_modeleval["lines"]), rp_modeleval
            rp_modeldep = read_json(base + "/api/state/rp_modeldep")
            assert any("check_secret_policy=not_required" in line for line in rp_modeldep["lines"]), rp_modeldep
            assert any("status=ready" in line for line in rp_modeldep["lines"]), rp_modeldep
            rp_modelserve = read_json(base + "/api/state/rp_modelserve")
            assert any("latency_ms=12" in line for line in rp_modelserve["lines"]), rp_modelserve
            assert any("message=offline provider ready" in line for line in rp_modelserve["lines"]), rp_modelserve
            rp_sysreview = read_json(base + "/api/state/rp_sysreview")
            assert any("systematic_review_checks=104" in line for line in rp_sysreview["lines"]), rp_sysreview
            assert any("protocol=systematic-review:agent-os-science" in line for line in rp_sysreview["lines"]), rp_sysreview
            rp_expsched = read_json(base + "/api/state/rp_expsched")
            assert any("experiment_scheduling_checks=88" in line for line in rp_expsched["lines"]), rp_expsched
            assert any("schedule=schedule:RUN-042:lab-execution" in line for line in rp_expsched["lines"]), rp_expsched
            rp_schedtask = read_json(base + "/api/state/rp_schedtask")
            assert any("task=schedule-task:RUN-042:library-prep" in line for line in rp_schedtask["lines"]), rp_schedtask
            rp_schedbook = read_json(base + "/api/state/rp_schedbook")
            assert any("booking=schedule-booking:RUN-042:seq-library" in line for line in rp_schedbook["lines"]), rp_schedbook
            rp_schedconf = read_json(base + "/api/state/rp_schedconf")
            assert any("conflict=schedule-conflict:RUN-042:seq-01-overlap" in line for line in rp_schedconf["lines"]), rp_schedconf
            rp_schedexec = read_json(base + "/api/state/rp_schedexec")
            assert any("execution=schedule-exec:RUN-042:library-prep" in line for line in rp_schedexec["lines"]), rp_schedexec
            rp_traincomp = read_json(base + "/api/state/rp_traincomp")
            assert any("training_compliance_checks=92" in line for line in rp_traincomp["lines"]), rp_traincomp
            assert any("open_gaps=0" in line for line in rp_traincomp["lines"]), rp_traincomp
            rp_trainreq = read_json(base + "/api/state/rp_trainreq")
            assert any("requirement=training-req:sop-deviation:qa-lead" in line for line in rp_trainreq["lines"]), rp_trainreq
            rp_trainrec = read_json(base + "/api/state/rp_trainrec")
            assert any("training=training:qa-lead:sop-deviation" in line for line in rp_trainrec["lines"]), rp_trainrec
            rp_trainassess = read_json(base + "/api/state/rp_trainassess")
            assert any("assessment=competency:qa-lead:sop-deviation" in line for line in rp_trainassess["lines"]), rp_trainassess
            rp_trainauth = read_json(base + "/api/state/rp_trainauth")
            assert any("authorization=auth:qa-lead:qa-lead:lab-gene-x" in line for line in rp_trainauth["lines"]), rp_trainauth
            rp_traingap = read_json(base + "/api/state/rp_traingap")
            assert any("status=resolved" in line for line in rp_traingap["lines"]), rp_traingap
            rp_syssearch = read_json(base + "/api/state/rp_syssearch")
            assert any("results=9" in line for line in rp_syssearch["lines"]), rp_syssearch
            rp_sysscreen = read_json(base + "/api/state/rp_sysscreen")
            assert any("screening_decisions=9" in line for line in rp_sysscreen["lines"]), rp_sysscreen
            assert any("full_text_included=3" in line for line in rp_sysscreen["lines"]), rp_sysscreen
            rp_sysextract = read_json(base + "/api/state/rp_sysextract")
            assert any("extractions=3" in line for line in rp_sysextract["lines"]), rp_sysextract
            assert any("risk_of_bias=3" in line for line in rp_sysextract["lines"]), rp_sysextract
            rp_syssynth = read_json(base + "/api/state/rp_syssynth")
            assert any("confidence=moderate" in line for line in rp_syssynth["lines"]), rp_syssynth
            rp_sysprisma = read_json(base + "/api/state/rp_sysprisma")
            assert any("flow=prisma-flow:agent-os-science" in line for line in rp_sysprisma["lines"]), rp_sysprisma
            assert any("included=3" in line for line in rp_sysprisma["lines"]), rp_sysprisma
            rp_reldossier = read_json(base + "/api/state/rp_reldossier")
            assert any("release_dossier_checks=112" in line for line in rp_reldossier["lines"]), rp_reldossier
            assert any("dossier=release-dossier:RUN-042:final-review" in line for line in rp_reldossier["lines"]), rp_reldossier
            assert any("decision=ready_for_review" in line for line in rp_reldossier["lines"]), rp_reldossier
            rp_reldsec = read_json(base + "/api/state/rp_reldsec")
            assert any("section=experiment-campaign;status=ok" in line for line in rp_reldsec["lines"]), rp_reldsec
            assert any("section=agentos-readiness;status=ok" in line for line in rp_reldsec["lines"]), rp_reldsec
            rp_relattest = read_json(base + "/api/state/rp_relattest")
            assert any("attestations=4" in line for line in rp_relattest["lines"]), rp_relattest
            rp_relpack = read_json(base + "/api/state/rp_relpack")
            assert any("package_files=2" in line for line in rp_relpack["lines"]), rp_relpack
            assert any("download=release-dossier-package:RUN-042" in line for line in rp_relpack["lines"]), rp_relpack
            rp_runbooks = read_json(base + "/api/state/rp_runbooks")
            assert any("runbook_service_checks=16" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("runbook_templates=1" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("runbook_steps=7" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("incident_triages=1" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("runbook_executions=1" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("runbook_exports=1" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("worker_operation_records=6" in line for line in rp_runbooks["lines"]), rp_runbooks
            assert any("agentos_adaptation=event_context,kernel_timeline,metadata_index,batch_recovery_tool" in line for line in rp_runbooks["lines"]), rp_runbooks
            rp_projectrel = read_json(base + "/api/state/rp_projectrel")
            assert any("project_delivery_checks=18" in line for line in rp_projectrel["lines"]), rp_projectrel
            assert any("project_handoff_audits=1" in line for line in rp_projectrel["lines"]), rp_projectrel
            assert any("project_release_gates=1" in line for line in rp_projectrel["lines"]), rp_projectrel
            assert any("project_reproducibility_audits=1" in line for line in rp_projectrel["lines"]), rp_projectrel
            assert any("project_provenance_graphs=1" in line for line in rp_projectrel["lines"]), rp_projectrel
            assert any("package_intakes=1" in line for line in rp_projectrel["lines"]), rp_projectrel
            assert any("agentos_adaptation=file_metadata_index,event_delivery,context_release_evidence,capability_guard" in line for line in rp_projectrel["lines"]), rp_projectrel
            rp_studyproto = read_json(base + "/api/state/rp_studyproto")
            assert any("study_protocol_checks=20" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("study_protocols=2" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("study_protocol_launches=2" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("study_protocol_reproduction_packages=1" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("study-protocol-reproduction-package:RUN-042" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("host_action_study_protocol=applied" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("protocol=usable-study-protocol:e2e" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("action_execute_result=passed" in line for line in rp_studyproto["lines"]), rp_studyproto
            assert any("agentos_adaptation=file_metadata_index,context_protocol_evidence,event_reproduction_queue,batch_dataset_tool" in line for line in rp_studyproto["lines"]), rp_studyproto
            rp_opsboard = read_json(base + "/api/state/rp_opsboard")
            assert any("operations_board_checks=18" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("pending_reviews=1" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("active_workbench_actions=4" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("active_plan_items=5" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("ready_handoffs=3" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("research-ops-report:RUN-042" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("agentos_adaptation=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("handoff=review-board->operations;artifact=rp_reviewboard;status=ready" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("handoff=control-plane->operations;artifact=rp_control;status=ready" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("handoff=integrity-plane->operations;artifact=rp_integrity;status=ready" in line for line in rp_opsboard["lines"]), rp_opsboard
            assert any("handoff=coherence-plane->operations;artifact=rp_coherence;status=ready" in line for line in rp_opsboard["lines"]), rp_opsboard
            rp_reviewboard = read_json(base + "/api/state/rp_reviewboard")
            assert any("review_board_checks=24" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("review_votes=4" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("review_signoffs=4" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("review_assignments=4" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("review_workloads=4" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("decision=approved" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("review_package=formal-review-board-package:RUN-042" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            assert any("agentos_adaptation=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding" in line for line in rp_reviewboard["lines"]), rp_reviewboard
            rp_control = read_json(base + "/api/state/rp_control")
            assert any("control_plane_checks=30" in line for line in rp_control["lines"]), rp_control
            assert any("approvals=4" in line for line in rp_control["lines"]), rp_control
            assert any("notifications=4" in line for line in rp_control["lines"]), rp_control
            assert any("run_queue_items=4" in line for line in rp_control["lines"]), rp_control
            assert any("plugin_manifests=3" in line for line in rp_control["lines"]), rp_control
            assert any("plugin_runs=3" in line for line in rp_control["lines"]), rp_control
            assert any("workspaces=1" in line for line in rp_control["lines"]), rp_control
            assert any("permissions=5" in line for line in rp_control["lines"]), rp_control
            assert any("approval=approval:release-dossier:4" in line for line in rp_control["lines"]), rp_control
            assert any("notification=notif:4;target=writer;event=PLUGIN_RUN" in line for line in rp_control["lines"]), rp_control
            assert any("queue=queue:RUN-042:2;run=RUN-042-review" in line for line in rp_control["lines"]), rp_control
            assert any("plugin=plugin.tuning" in line for line in rp_control["lines"]), rp_control
            assert any("api_token=token:local-dashboard" in line for line in rp_control["lines"]), rp_control
            assert any("agentos_adaptation=kernel_capability_check,kernel_event_delivery,kernel_plugin_tool_table,kernel_run_queue" in line for line in rp_control["lines"]), rp_control
            rp_integrity = read_json(base + "/api/state/rp_integrity")
            assert any("integrity_checks=36" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("evidence_contracts=8" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("reference_contracts=8" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("namespace_checks=5" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("status_checks=5" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("review_alignment_checks=4" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("errors=0" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("decision=passed" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("evidence_check=backend_evidence" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("reference_check=stage_artifacts" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("review_alignment=board_to_dashboard" in line for line in rp_integrity["lines"]), rp_integrity
            assert any("integrity_report=integrity-report:RUN-042" in line for line in rp_integrity["lines"]), rp_integrity
            rp_coherence = read_json(base + "/api/state/rp_coherence")
            assert any("coherence_checks=40" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("delivery_contracts=7" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("run_state_contracts=7" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("lifecycle_contracts=6" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("workflow_lint_checks=5" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("tool_protocol_checks=5" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("report_validation_checks=5" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("agent_coordination_checks=3" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("errors=0" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("decision=passed" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("delivery_check=research_package" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("run_state_check=stage_order" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("workflow_lint=retry_minimality" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("tool_validation=backend_runner" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("report_validation=backend_source" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("agent_coordination=recovery_path" in line for line in rp_coherence["lines"]), rp_coherence
            assert any("coherence_report=coherence-report:RUN-042" in line for line in rp_coherence["lines"]), rp_coherence
            rp_publication = read_json(base + "/api/state/rp_publication")
            assert any("publication_checks=48" in line for line in rp_publication["lines"]), rp_publication
            assert any("targets=2" in line for line in rp_publication["lines"]), rp_publication
            assert any("submissions=2" in line for line in rp_publication["lines"]), rp_publication
            assert any("review_rounds=2" in line for line in rp_publication["lines"]), rp_publication
            assert any("response_packages=2" in line for line in rp_publication["lines"]), rp_publication
            assert any("response_items=4" in line for line in rp_publication["lines"]), rp_publication
            assert any("journal_target=journal-target:systems-biology-report" in line for line in rp_publication["lines"]), rp_publication
            assert any("submission=submission:RUN-042:systems-biology-report" in line for line in rp_publication["lines"]), rp_publication
            assert any("review_round=peer-review:RUN-042:round-1" in line for line in rp_publication["lines"]), rp_publication
            assert any("revision_task=revision:RUN-042:methods-reproducibility" in line for line in rp_publication["lines"]), rp_publication
            assert any("response_package=peer-review-response-package:RUN-042:round-1" in line for line in rp_publication["lines"]), rp_publication
            assert any("publication_decision=publication-decision:RUN-042:accept-with-evidence" in line for line in rp_publication["lines"]), rp_publication
            assert any("agentos_adaptation=kernel_submission_metadata,kernel_review_event_queue,kernel_response_context,kernel_release_gate" in line for line in rp_publication["lines"]), rp_publication
            rp_calculation = read_json(base + "/api/state/rp_calculation")
            assert any("calculation_checks=84" in line for line in rp_calculation["lines"]), rp_calculation
            assert any("computer=calculation-computer:local-agentos" in line for line in rp_calculation["lines"]), rp_calculation
            assert any("code=calculation-code:metadata-qc:v1" in line for line in rp_calculation["lines"]), rp_calculation
            assert any("job=calculation-job:lab-gene-x:run042-qc" in line for line in rp_calculation["lines"]), rp_calculation
            rp_realtask = read_json(base + "/api/state/rp_realtask")
            assert any("real_task_checks=96" in line for line in rp_realtask["lines"]), rp_realtask
            assert any("task=palmer-penguins-morphometrics" in line for line in rp_realtask["lines"]), rp_realtask
            assert any("answer_audit=pass" in line for line in rp_realtask["lines"]), rp_realtask
            rp_realdata = read_json(base + "/api/state/rp_realdata")
            assert any("rows=344" in line for line in rp_realdata["lines"]), rp_realdata
            assert any("metric_group_summaries=5" in line for line in rp_realdata["lines"]), rp_realdata
            assert any("metric_dimension_group_summaries=10" in line for line in rp_realdata["lines"]), rp_realdata
            rp_realreport = read_json(base + "/api/state/rp_realreport")
            assert any("answer_source=report_md" in line for line in rp_realreport["lines"]), rp_realreport
            assert any("claim_audit=pass" in line for line in rp_realreport["lines"]), rp_realreport
            rp_realbundle = read_json(base + "/api/state/rp_realbundle")
            assert any("duplicate_zip_entries=0" in line for line in rp_realbundle["lines"]), rp_realbundle
            assert any("offline_review=ready" in line for line in rp_realbundle["lines"]), rp_realbundle
            rp_analysisres = read_json(base + "/api/state/rp_analysisres")
            assert any("analysis_results_checks=96" in line for line in rp_analysisres["lines"]), rp_analysisres
            assert any("analysis_runs=2" in line for line in rp_analysisres["lines"]), rp_analysisres
            assert any("result_tables=2" in line for line in rp_analysisres["lines"]), rp_analysisres
            rp_anplan = read_json(base + "/api/state/rp_anplan")
            assert any("plan=analysis-plan:RUN-042:treatment-response" in line for line in rp_anplan["lines"]), rp_anplan
            rp_anrun = read_json(base + "/api/state/rp_anrun")
            assert any("run=analysis-run:RUN-042:manual" in line for line in rp_anrun["lines"]), rp_anrun
            rp_resulttbl = read_json(base + "/api/state/rp_resulttbl")
            assert any("table=result-table:manual" in line for line in rp_resulttbl["lines"]), rp_resulttbl
            rp_statres = read_json(base + "/api/state/rp_statres")
            assert any("stat=stat-result:manual" in line for line in rp_statres["lines"]), rp_statres
            rp_anfig = read_json(base + "/api/state/rp_anfig")
            assert any("figure=figure:manual" in line for line in rp_anfig["lines"]), rp_anfig
            rp_interp = read_json(base + "/api/state/rp_interp")
            assert any("interpretation=interpretation:manual" in line for line in rp_interp["lines"]), rp_interp
            rp_decsupport = read_json(base + "/api/state/rp_decsupport")
            assert any("decision_support_checks=80" in line for line in rp_decsupport["lines"]), rp_decsupport
            assert any("recommended_option=agentos_ucore_hybrid" in line for line in rp_decsupport["lines"]), rp_decsupport
            rp_decopt = read_json(base + "/api/state/rp_decopt")
            assert any("option=agentos_ucore_hybrid" in line for line in rp_decopt["lines"]), rp_decopt
            rp_deccrit = read_json(base + "/api/state/rp_deccrit")
            assert any("criterion=agentos_value" in line for line in rp_deccrit["lines"]), rp_deccrit
            rp_decscore = read_json(base + "/api/state/rp_decscore")
            assert any("score=agentos_ucore_hybrid:agentos_value" in line for line in rp_decscore["lines"]), rp_decscore
            rp_decpacket = read_json(base + "/api/state/rp_decpacket")
            assert any("decision-review-packet:agentos-final-demo-backend" in line for line in rp_decpacket["lines"]), rp_decpacket
            rp_usable = read_json(base + "/api/state/rp_usable")
            assert any("usable_research_checks=100" in line for line in rp_usable["lines"]), rp_usable
            assert any("entry=research-question-to-review-package" in line for line in rp_usable["lines"]), rp_usable
            assert any("host_action_sample_workbench=created" in line for line in rp_usable["lines"]), rp_usable
            rp_usabletpl = read_json(base + "/api/state/rp_usabletpl")
            assert any("template=usable-template:workspace-900" in line for line in rp_usabletpl["lines"]), rp_usabletpl
            rp_usableds = read_json(base + "/api/state/rp_usableds")
            assert any("dataset=usable-dataset:penguins" in line for line in rp_usableds["lines"]), rp_usableds
            assert any("host_action_dataset_ops=applied" in line for line in rp_usableds["lines"]), rp_usableds
            assert any("preview_dataset=usable-dataset:response-table" in line for line in rp_usableds["lines"]), rp_usableds
            assert any("dataset_run=usable-run:dataset:e2e" in line for line in rp_usableds["lines"]), rp_usableds
            rp_usablelib = read_json(base + "/api/state/rp_usablelib")
            assert any("source=usable-source:library2026:1" in line for line in rp_usablelib["lines"]), rp_usablelib
            assert any("host_action_source_portfolio=reviewed" in line for line in rp_usablelib["lines"]), rp_usablelib
            rp_usabledag = read_json(base + "/api/state/rp_usabledag")
            assert any("stage=package;order=9" in line for line in rp_usabledag["lines"]), rp_usabledag
            rp_usableops = read_json(base + "/api/state/rp_usableops")
            assert any("handoff=usable-handoff:RUN-900:reviewer" in line for line in rp_usableops["lines"]), rp_usableops
            rp_usableproj = read_json(base + "/api/state/rp_usableproj")
            assert any("usable_project_checks=120" in line for line in rp_usableproj["lines"]), rp_usableproj
            assert any("project_launches=2" in line for line in rp_usableproj["lines"]), rp_usableproj
            assert any("host_action_project_scaffold=reader-project;template=scaffold-template:dataset-review" in line for line in rp_usableproj["lines"]), rp_usableproj
            assert any("host_action_project_launch=reader-project;scaffold=scaffold:reader-project:dataset-review" in line for line in rp_usableproj["lines"]), rp_usableproj
            assert any("host_action_project_action_execute=reader-project;action=usable-project-action:reader-project:1" in line for line in rp_usableproj["lines"]), rp_usableproj
            rp_usableboot = read_json(base + "/api/state/rp_usableboot")
            assert any("platform-doctor" in line or "platform_doctor" in line for line in rp_usableboot["lines"]), rp_usableboot
            rp_usablescaf = read_json(base + "/api/state/rp_usablescaf")
            assert any("scaffold-template:protocol-reproduction" in line for line in rp_usablescaf["lines"]), rp_usablescaf
            assert any("host_action_project_scaffold=reader-project" in line for line in rp_usablescaf["lines"]), rp_usablescaf
            rp_usablelaunch = read_json(base + "/api/state/rp_usablelaunch")
            assert any("usable-project-launch:lab-gene-x:1" in line for line in rp_usablelaunch["lines"]), rp_usablelaunch
            assert any("host_action_project_launch=reader-project" in line for line in rp_usablelaunch["lines"]), rp_usablelaunch
            rp_usablepack = read_json(base + "/api/state/rp_usablepack")
            assert any("usable-study-protocol-reproduction-package:RUN-042" in line for line in rp_usablepack["lines"]), rp_usablepack
            assert any("host_action_project_action_execute=reader-project" in line for line in rp_usablepack["lines"]), rp_usablepack
            assert any("host_action_study_protocol=applied" in line for line in rp_usablepack["lines"]), rp_usablepack
            rp_campaign = read_json(base + "/api/state/rp_campaign")
            assert any("campaign_checks=108" in line for line in rp_campaign["lines"]), rp_campaign
            assert any("campaign=experiment-campaign:RUN-042:align-memory-grid" in line for line in rp_campaign["lines"]), rp_campaign
            rp_trials = read_json(base + "/api/state/rp_trials")
            assert any("trial_count=4" in line for line in rp_trials["lines"]), rp_trials
            assert any("trial=experiment-trial:RUN-042:align-memory-grid:04" in line for line in rp_trials["lines"]), rp_trials
            rp_camp_rank = read_json(base + "/api/state/rp_camp_rank")
            assert any("decision=select_trial_04" in line for line in rp_camp_rank["lines"]), rp_camp_rank
            assert any("metric_delta=3" in line for line in rp_camp_rank["lines"]), rp_camp_rank
            rp_resreview = read_json(base + "/api/state/rp_resreview")
            assert any("review=experiment-result-review:RUN-042:baseline-vs-candidate" in line for line in rp_resreview["lines"]), rp_resreview
            assert any("decision=accept_candidate" in line for line in rp_resreview["lines"]), rp_resreview
            rp_calc_files = read_json(base + "/api/state/rp_calc_files")
            assert any("retrieved_files=3" in line for line in rp_calc_files["lines"]), rp_calc_files
            assert any("retrieved=calculation-retrieved:run042-qc:provenance-json" in line for line in rp_calc_files["lines"]), rp_calc_files
            rp_calc_parse = read_json(base + "/api/state/rp_calc_parse")
            assert any("parser_result=calculation-parser-result:run042-qc" in line for line in rp_calc_parse["lines"]), rp_calc_parse
            assert any("metric=ready_ratio;value=1.00" in line for line in rp_calc_parse["lines"]), rp_calc_parse
            rp_calc_export = read_json(base + "/api/state/rp_calc_export")
            assert any("export=calculation-export:lab-gene-x:run042-qc" in line for line in rp_calc_export["lines"]), rp_calc_export
            rp_mature = read_json(base + "/api/state/rp_mature")
            assert any("reference_platforms=6" in line for line in rp_mature["lines"]), rp_mature
            assert any("capability_mappings=6" in line for line in rp_mature["lines"]), rp_mature
            assert any("capability_checks=72" in line for line in rp_mature["lines"]), rp_mature
            assert any("reference_platform=galaxy;name=Galaxy" in line for line in rp_mature["lines"]), rp_mature
            assert any("reference_platform=snakemake;name=Snakemake" in line for line in rp_mature["lines"]), rp_mature
            rp_mature_map = read_json(base + "/api/state/rp_mature_map")
            assert any("mapping=galaxy-workflow-history" in line for line in rp_mature_map["lines"]), rp_mature_map
            assert any("mapping=snakemake-rule-dag" in line for line in rp_mature_map["lines"]), rp_mature_map
            assert any("agentos_targets=kernel_context_path,kernel_metadata_index,kernel_event_queue,batch_tool_runner,capability_contract_table" in line for line in rp_mature_map["lines"]), rp_mature_map
            rp_mature_checks = read_json(base + "/api/state/rp_mature_checks")
            assert any("checks=72" in line for line in rp_mature_checks["lines"]), rp_mature_checks
            assert any("check=surface.site;target=mature.html;result=pass;status=ready" in line for line in rp_mature_checks["lines"]), rp_mature_checks
            rp_prov_view = read_json(base + "/api/state/rp_prov_view")
            assert any("provenance_view_checks=64" in line for line in rp_prov_view["lines"]), rp_prov_view
            assert any("timeline_views=4" in line for line in rp_prov_view["lines"]), rp_prov_view
            assert any("agentos_mapping=kernel_timeline,kernel_provenance_edges,kernel_ledger,context_detail" in line for line in rp_prov_view["lines"]), rp_prov_view
            rp_prov_edges = read_json(base + "/api/state/rp_prov_edges")
            assert any("edge=12;source=rp_agent_run;target=rp_prov_view;kind=agent_to_trace;status=ready" in line for line in rp_prov_edges["lines"]), rp_prov_edges
            rp_evidence_packet = read_json(base + "/api/state/rp_evidence_packet")
            assert any("packet=agentos-readiness;run=RUN-042" in line for line in rp_evidence_packet["lines"]), rp_evidence_packet
            rp_timeline_view = read_json(base + "/api/state/rp_timeline_view")
            assert any("view=agent_decision_flow;events=6;source=rp_agent_run;status=ready" in line for line in rp_timeline_view["lines"]), rp_timeline_view
            rp_prov_query = read_json(base + "/api/state/rp_prov_query")
            assert any("provenance_query_checks=72" in line for line in rp_prov_query["lines"]), rp_prov_query
            assert any("specs=3" in line for line in rp_prov_query["lines"]), rp_prov_query
            assert any("reader_page=provenance-queries.html" in line for line in rp_prov_query["lines"]), rp_prov_query
            rp_prov_specs = read_json(base + "/api/state/rp_prov_specs")
            assert any("template=provenance-query-template:calculation-root-neighborhood" in line for line in rp_prov_specs["lines"]), rp_prov_specs
            assert any("spec=provenance-query:RUN-042:calculation-lineage" in line for line in rp_prov_specs["lines"]), rp_prov_specs
            rp_prov_exec = read_json(base + "/api/state/rp_prov_exec")
            assert any("execution=provenance-query-execution:calculation-lineage" in line for line in rp_prov_exec["lines"]), rp_prov_exec
            rp_prov_query_pkg = read_json(base + "/api/state/rp_prov_query_pkg")
            assert any("comparison=provenance-query-comparison:RUN-042:rendered-vs-direct" in line for line in rp_prov_query_pkg["lines"]), rp_prov_query_pkg
            assert any("packet=provenance-query-packet:RUN-042:lineage-review" in line for line in rp_prov_query_pkg["lines"]), rp_prov_query_pkg
            rp_peerresp = read_json(base + "/api/state/rp_peerresp")
            assert any("addressed=4" in line for line in rp_peerresp["lines"]), rp_peerresp
            assert any("needs_revision=0" in line for line in rp_peerresp["lines"]), rp_peerresp
            rp_consistency = read_json(base + "/api/state/rp_consistency")
            assert any("checks=420" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("state_catalog_checks=12" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("startup_doctor_checks=14" in line for line in rp_consistency["lines"]), rp_consistency
            rp_state_catalog = read_json(base + "/api/state/rp_state_catalog")
            assert any("host_state_keys=573" in line for line in rp_state_catalog["lines"]), rp_state_catalog
            assert any("represented_state_categories=573" in line for line in rp_state_catalog["lines"]), rp_state_catalog
            rp_startup = read_json(base + "/api/state/rp_startup")
            assert any("quickstart=ready" in line for line in rp_startup["lines"]), rp_startup
            assert any("doctor_checks=10" in line for line in rp_startup["lines"]), rp_startup
            assert any("recommended_commands=startup_guide" in line for line in rp_startup["lines"]), rp_startup
            assert any("runtime_assurance_checks=24" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("research_ops_checks=28" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("semantic_graph_checks=6" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("prompt_ops_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("runbook_checks=7" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("worker_ops_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("execution_control_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("regulated_research_checks=32" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("lab_governance_ops_checks=26" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("knowledge_index_checks=22" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("llm_transcript_checks=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("llm_bridge_transcripts=90" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("llm_bridge_requests=30" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("llm_bridge_responses=30" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("workbench_delivery_checks=15" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("research_portfolio_checks=16" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("execution_scale_checks=14" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("operations_scale_checks=12" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("project_revision_incident_checks=12" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_revision_tasks=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_project_scaffolds=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("incidents=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("incident_reason=memory_limit" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("reserved_research_surface_checks=21" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_dataset_answers=0" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_study_protocols=0" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_workbench_notes=0" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_state_surface_checks=10" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_projects=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_runs=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_reports=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_plans=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_compare_profiles=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_audit_records=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_context_records=348" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_project_id=lab-gene-x" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("root_plan_id=PLAN-RUN-042-RECOVER-1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("agentos_reserved_surface_checks=21" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("agentos_reserved_surface=profiles:0" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("tool_bindings:0" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_workbenches=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_deliveries=6" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_sources=42" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_datasets=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_platform_doctor_reports=10" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_project_handoff_audits=30" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_workflow_runs=10" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_workflow_stage_runs=70" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_workflow_cache=6" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_agent_messages=70" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("agentcompare_reports=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("agentcompare_results=15" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("content_objects=145" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_audit_records=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_metrics=13" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_llm_providers=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_secret_references=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_executed_corr_ids=4" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_projects=20" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("host_artifacts=128" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_project_action_plans=15" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("usable_research_project_runbooks=15" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("search_documents=1385" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("provenance_nodes=406" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("provenance_links=544" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("event_stream_records=6816" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("context_records=348" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("approval_checks=2" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("protocol_governance_checks=4" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("sop_execution_checks=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("training_record_checks=4" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("notification_checks=1" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("annotation_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("assay_plate_checks=4" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("cohort_monitoring_checks=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("data_access_checks=4" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("research_object_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("workflow_template_checks=2" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("secret_reference_checks=6" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("model_registry_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("llm_proxy_replay_audits=2" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("collaboration_threads=2" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("observability_alerts=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("research_product_checks=18" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("project_scaffold_files=8" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("dataset_product_exports=9" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("study_protocol_reproduction_checks=5" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("project_bundle_cache=ready" in line for line in rp_consistency["lines"]), rp_consistency
            rp_runop = read_json(base + "/api/state/rp_runop")
            assert any("platform_doctor=ready;checks=10" in line for line in rp_runop["lines"]), rp_runop
            assert any("source_portfolio=sources:42" in line for line in rp_runop["lines"]), rp_runop
            assert any("research_portfolio_scale=sources:42" in line for line in rp_runop["lines"]), rp_runop
            assert any("doctor_reports:10" in line for line in rp_runop["lines"]), rp_runop
            assert any("research_ops=semantic_entities:8" in line for line in rp_runop["lines"]), rp_runop
            assert any("prompt_templates:2" in line for line in rp_runop["lines"]), rp_runop
            assert any("runbook_steps:7" in line for line in rp_runop["lines"]), rp_runop
            assert any("worker_ops:6" in line for line in rp_runop["lines"]), rp_runop
            assert any("execution_controls:8" in line for line in rp_runop["lines"]), rp_runop
            assert any("regulated_research=annotation_schemas:1" in line for line in rp_runop["lines"]), rp_runop
            assert any("assay_plates:1" in line for line in rp_runop["lines"]), rp_runop
            assert any("dataset_cards:1" in line for line in rp_runop["lines"]), rp_runop
            assert any("research_object_crates:1" in line for line in rp_runop["lines"]), rp_runop
            assert any("workflow_templates:8" in line for line in rp_runop["lines"]), rp_runop
            assert any("artifact_provenance=3" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("artifact_dossier_checks=4" in line for line in rp_consistency["lines"]), rp_consistency
            assert any("artifact_path_rebuild_steps=7" in line for line in rp_consistency["lines"]), rp_consistency
            rp_review_dashboard = read_json(base + "/api/state/rp_review_dashboard")
            assert any("dashboard=research-review" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("section=workflow;source=rp_stage_dag,rp_stage_state,rp_run_events,rp_retry_plan;status=recovered" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("section=llm;source=rp_llm_req,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;status=ready" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("gate=llm_packet_guard;status=pass;source=rp_llm_guard" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("decision=ready_for_reviewer" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("decision=review_pack_ready;basis=delivery_manifest,operations_next,project_action_items,workbench_handoff" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("backend_review_evidence=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;review_pack=rp_review_pack;status=ready" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            assert any("host_relay_quality=passed:24/24;blocked:0;source=rp_llmeval;status=ready" in line for line in rp_review_dashboard["lines"]), rp_review_dashboard
            rp_review_pack = read_json(base + "/api/state/rp_review_pack")
            assert any("pack=review-evidence" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("evidence=llm_quality;source=rp_llmeval;passed=7;status=pass" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("evidence=operations_ready;source=rp_runner;status=pass" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("evidence=project_space_ready;source=rp_package;status=pass" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("action=send_to_reviewer;owner=orchestrator;artifact=rp_review_pack;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("action=open_operations_report;owner=orchestrator;artifact=rp_runner;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("bridge=delivery_to_operations;delivery=rp_package;operations=rp_runner;project=rp_package;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("host_relay_quality=passed:24/24;blocked:0;source=rp_llmeval;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("backend_evidence_review=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;source=rp_review_dashboard;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("backend_action_review=retry-recovery;action=rerun_align;review=recovered;plain_cost=retry_file_stage_file;agentos_replace=event_context;status=passed" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("backend_action_review=agentos-context;action=kernel_context;review=target;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;status=planned" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("operations_handoff=rp_runner+rp_package;tasks=9;next=delivery_manifest;report=exported;plan=executed;quality=checked;repair=done;backend=rp_backend_exec;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("workbench_handoff=rp_runner+rp_package;workbench=W1;task=hr;task_status=waiting;manifest=mf.json;verified=11;missing=0;bundle=wb.zip;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            assert any("project_handoff=rp_package;project=lab-gene-x;space=ready;note=recorded;action_item=created;answer=generated;repair=executed;search=ready;status=ready" in line for line in rp_review_pack["lines"]), rp_review_pack
            rp_package = read_json(base + "/api/state/rp_package")
            assert any("review_pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff" in line for line in rp_package["lines"]), rp_package
            assert any("review_pack_action=sync_operations_next;source=rp_runner;status=ready" in line for line in rp_package["lines"]), rp_package
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
            assert any("actions=123" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("host_workflow_stage=/actions/host-workflow/stage-attempt" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("host_workflow_report=/actions/host-workflow/report-export" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("artifact_input=/actions/research/artifact-input" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("artifact_package=/actions/research/artifact-package" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("operations_report=/actions/research/operations-report" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workflow_portability_run=/actions/workflow-portability/run" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workflow_portability_import=/actions/workflow-portability/import" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workflow_portability_package=/actions/workflow-portability/package" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("workbench_quality_gate=/actions/research/workbench-quality-gate" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_space=/actions/research/project-space" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_space_review=/actions/research/project-space-review" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_space_task_board_row=/actions/research/project-space-task-board-row" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_scaffold=/actions/research/project-scaffold" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_launch=/actions/research/project-launch" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_action_execute=/actions/research/project-action-execute" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("dataset_preview=/actions/research/dataset-preview" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("dataset_run=/actions/research/dataset-run" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("study_protocol_launch=/actions/research/study-protocol-launch" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("study_protocol_reproduction_package_action_execute=/actions/research/study-protocol-reproduction-package-action-execute" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_release_gate=/actions/research/project-release-gate" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_provenance_graph=/actions/research/project-provenance-graph" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_lifecycle_actions=3" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("dataset_actions=8" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("study_protocol_actions=11" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_space_actions=7" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("project_review_actions=8" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("research_search_export=/actions/research-search/export" in line for line in rp_api_action["lines"]), rp_api_action
            assert any("llm_relay_request=/actions/research/llm-relay-request" in line for line in rp_api_action["lines"]), rp_api_action
            rp_llm_req = read_json(base + "/api/state/rp_llm_req")
            assert any("host_llm_request_id=llm-q1" in line for line in rp_llm_req["lines"]), rp_llm_req
            assert any("host_llm_provider=host-relay" in line for line in rp_llm_req["lines"]), rp_llm_req
            rp_llm_resp = read_json(base + "/api/state/rp_llm_resp")
            assert any("host_llm_response_id=llm-r1" in line for line in rp_llm_resp["lines"]), rp_llm_resp
            assert any("host_llm_response_summary=Recovered_evidence_ready" in line for line in rp_llm_resp["lines"]), rp_llm_resp
            assert any("host_relay_process=plain_ucore_llm_relay" in line for line in rp_llm_resp["lines"]), rp_llm_resp
            assert any("host_relay_response=relay-llm-q1" in line for line in rp_llm_resp["lines"]), rp_llm_resp
            rp_llm_packets = read_json(base + "/api/state/rp_llm_packets")
            assert any("host_llm_packet_request=llm-q1" in line for line in rp_llm_packets["lines"]), rp_llm_packets
            assert any("secret_in_packet=0" in line for line in rp_llm_packets["lines"]), rp_llm_packets
            rp_llmeval = read_json(base + "/api/state/rp_llmeval")
            assert any("host_relay_eval_batch=checked:24;passed:24;blocked:0;status=ready" in line for line in rp_llmeval["lines"]), rp_llmeval
            assert any("host_relay_eval=llm-q1;response=relay-llm-q1;checks=6;passed=6" in line for line in rp_llmeval["lines"]), rp_llmeval
            rp_llm_guard = read_json(base + "/api/state/rp_llm_guard")
            assert any("host_relay_guard_batch=checked:4;blocked:0;secret_values_written=0;status=ready" in line for line in rp_llm_guard["lines"]), rp_llm_guard
            assert any("host_relay_guard=llm-q1" in line for line in rp_llm_guard["lines"]), rp_llm_guard
            rp_llmlog = read_json(base + "/api/state/rp_llmlog")
            assert any("transcripts=90" in line for line in rp_llmlog["lines"]), rp_llmlog
            assert any("bridge_requests=30" in line for line in rp_llmlog["lines"]), rp_llmlog
            assert any("bridge_responses=30" in line for line in rp_llmlog["lines"]), rp_llmlog
            rp_relay = read_json(base + "/api/state/rp_relay")
            assert any("host_relay_replay_batch=requests:4;responses:4;matched:4;status=ready" in line for line in rp_relay["lines"]), rp_relay
            assert any("host_relay_replay=llm-q1;response=relay-llm-q1" in line for line in rp_relay["lines"]), rp_relay
            rp_prompt = read_json(base + "/api/state/rp_prompt")
            assert any("host_relay_prompt_batch=routes:3;requests:4;status=ready" in line for line in rp_prompt["lines"]), rp_prompt
            assert any("host_relay_prompt_route=llm-q1;route=review_summary" in line for line in rp_prompt["lines"]), rp_prompt
            rp_llm_hostreq = read_json(base + "/api/state/rp_llm_hostreq")
            assert any("host_llm_host_response=llm-r1" in line for line in rp_llm_hostreq["lines"]), rp_llm_hostreq
            assert any("secret_material=not_written" in line for line in rp_llm_hostreq["lines"]), rp_llm_hostreq
            rp_llm_fallback = read_json(base + "/api/state/rp_llm_fallback")
            assert any("host_llm_fallback_case=missing_cloud_key" in line for line in rp_llm_fallback["lines"]), rp_llm_fallback
            rp_api_runtime = read_json(base + "/api/state/rp_api_runtime")
            assert any("host_llm_request_id=llm-q1" in line for line in rp_api_runtime["lines"]), rp_api_runtime
            assert any("host_llm_relay_quality=passed:24/24;blocked:0;source=rp_llmeval;status=ready" in line for line in rp_api_runtime["lines"]), rp_api_runtime
            rp_bioop = read_json(base + "/api/state/rp_bioop")
            assert any("op=sample_lookup" in line for line in rp_bioop["lines"]), rp_bioop
            rp_labresop = read_json(base + "/api/state/rp_labresop")
            assert any("op=schedule_assess" in line for line in rp_labresop["lines"]), rp_labresop
            assert any("lab_governance_ops=approvals:2" in line for line in rp_labresop["lines"]), rp_labresop
            assert any("protocol_compliance_reports:2" in line for line in rp_labresop["lines"]), rp_labresop
            assert any("sop_executions:3" in line for line in rp_labresop["lines"]), rp_labresop
            assert any("training_records:4" in line for line in rp_labresop["lines"]), rp_labresop
            assert any("notifications:3" in line for line in rp_labresop["lines"]), rp_labresop
            rp_pubop = read_json(base + "/api/state/rp_pubop")
            assert any("op=fair_package" in line for line in rp_pubop["lines"]), rp_pubop
            rp_knowop = read_json(base + "/api/state/rp_knowop")
            assert any("op=query_answer" in line for line in rp_knowop["lines"]), rp_knowop
            rp_runop = read_json(base + "/api/state/rp_runop")
            assert any("op=worker_heartbeat" in line for line in rp_runop["lines"]), rp_runop
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
            assert any("host_relay_report_summary=review_summary_supported_by_current_evidence" in line for line in rp_api_run["lines"]), rp_api_run
            rp_api_evidence = read_json(base + "/api/state/rp_api_evidence")
            assert any("host_action_evidence_inputs=ready" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_action_literature_query=prov" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_action_evidence_included=4" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_action_protocol_title=P1" in line for line in rp_api_evidence["lines"]), rp_api_evidence
            assert any("host_relay_grounding=citations:5" in line for line in rp_api_evidence["lines"]), rp_api_evidence
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
            assert "Workflow Execution View" in run_html
            assert "Workflow Control View" in run_html
            assert "Workflow Evidence Links" in run_html
            assert "stage_summary" in run_html
            assert "stage_assignment" in run_html
            assert "stage_evidence" in run_html
            assert "artifact_provenance" in run_html
            assert "artifact_review_path" in run_html
            assert "review_delivery" in run_html
            assert "worker_pool" in run_html
            assert "cache_decision" in run_html
            assert "retry_decision" in run_html
            assert "rerun_selected_stage" in run_html
            assert "reuse_cached_artifact" in run_html
            assert "rp_stage_log" in run_html
            assert "rp_artifact_manifest" in run_html
            assert "rp_review_dashboard" in run_html
            assert "control_summary" in run_html
            assert "workflow_run" in run_html
            assert "plain-c-runner" in run_html
            assert "Queue Depth" in run_html
            assert "Backend Evidence In Report" in run_html
            assert "Backend Evidence In Runner" in run_html
            assert "Backend Case Narratives" in run_html
            assert "Report Source Map" in run_html
            assert "run_setup" in run_html
            assert "host_workflow_run_id=R1" in run_html
            assert "host_relay_response=relay-llm-q1" in run_html
            assert "backend_evidence_report=rp_backend_exec" in run_html
            assert "rp_llm_req,rp_llm_packets,rp_llmeval,rp_llm_guard" in run_html
            assert "Operations Report Narrative" in run_html
            assert "Operations Source Files" in run_html
            assert "operations_report" in run_html
            assert "host_action_operations_report" in run_html
            assert "workbench_delivery" in run_html
            assert "project_followup" in run_html
            assert "Run Action Trace" in run_html
            assert "Run Action Output Links" in run_html
            assert "Run Action Output Details" in run_html
            assert "Run Action Impact" in run_html
            assert "Run Action Delta" in run_html
            assert "rp_stage_state,rp_run_events" in run_html or "rp_stage_dag,rp_stage_state" in run_html
            assert "rp_artifact,rp_artifact_manifest" in run_html
            assert "rp_llm_req,rp_llmq" in run_html
            assert "host_workflow_stage_action" in run_html
            assert "host_llm_request_id" in run_html
            assert "report_section" in run_html
            assert "artifact_path" in run_html
            assert "llm_packet" in run_html
            assert "host_report_title" in run_html
            assert "matched" in run_html
            assert "/actions/research/run" in run_html
            assert "/actions/host-workflow/run" in run_html
            assert "/actions/research/artifact-package" in run_html
            assert "/actions/research/llm-relay-request" in run_html
            assert "batch_tool_context" in run_html
            assert "execution_plan:pass:record:baseline" in run_html
            assert "context_path:planned:kernel_context:target" in run_html
            assert "risks" in run_html
            workflow_html = read_text(base + "/workflow.html")
            assert "Workflow Runner" in workflow_html
            assert "Workflow Execution View" in workflow_html
            assert "Workflow Control View" in workflow_html
            assert "Workflow Evidence Links" in workflow_html
            assert "workflow_run" in workflow_html
            assert "R1" in workflow_html
            assert "plain-c-runner" in workflow_html
            assert "clean" in workflow_html
            assert "checksum_mismatch" in workflow_html
            assert "cache_decision" in workflow_html
            assert "retry_decision" in workflow_html
            assert "stage_evidence" in workflow_html
            assert "rp_artifact_manifest" in workflow_html
            assert "Workflow Action Trace" in workflow_html
            workbench_html = read_text(base + "/workbench.html")
            assert "Research Workbench" in workbench_html
            assert "Workbench Task State" in workbench_html
            assert "Workbench Writing Outputs" in workbench_html
            assert "Workbench File Package" in workbench_html
            assert "Workbench Review Board" in workbench_html
            assert "Workbench Action Trace" in workbench_html
            assert "W1" in workbench_html
            assert "WB1" in workbench_html
            assert "Ready?" in workbench_html
            assert "Scope" in workbench_html
            assert "host_action_workbench_manuscript_format" in workbench_html
            assert "markdown" in workbench_html
            assert "host_action_workbench_board_filter" in workbench_html
            assert "open" in workbench_html
            assert "mf.json" in workbench_html
            assert "wb.zip" in workbench_html
            assert "host_action_notebook_format" in workbench_html
            data_html = read_text(base + "/data.html")
            assert "Data Pipeline" in data_html
            assert "Ingested Input Files" in data_html
            assert "Dataset Snapshots" in data_html
            assert "Data Preview Records" in data_html
            assert "Derived Data Preview" in data_html
            assert "Data Quality State" in data_html
            assert "Data Transform Records" in data_html
            assert "Derived Data Products" in data_html
            assert "Dataset Collection" in data_html
            assert "Data Manifest Verification" in data_html
            assert "Data Action Trace" in data_html
            assert "rp_input_fastq" in data_html
            assert "rp_samples" in data_html
            assert "normalize_fastq" in data_html
            assert "rp_artifact:rp_align_table" in data_html
            assert "host_input_dataset_rows" in data_html
            assert "host_file_manifest" in data_html
            assert "mf.json" in data_html
            assert "host_file_verify" in data_html
            assert "passed" in data_html
            assert "host_action_file_verified" in data_html
            assert "11" in data_html
            project_html = read_text(base + "/project.html")
            assert "Project Space" in project_html
            assert "Project Handoff" in project_html
            assert "Project Evidence Package" in project_html
            assert "Project Package Records" in project_html
            assert "Project Quality And Repair" in project_html
            assert "Project Search And Notes" in project_html
            assert "Project Source Files" in project_html
            assert "Project Action Trace" in project_html
            assert "Project Action Output Details" in project_html
            assert "Project Action Impact" in project_html
            assert "Project Action Delta" in project_html
            assert "lab-gene-x" in project_html
            assert "host_action_project_id" in project_html
            assert "host_action_project_space" in project_html
            assert "host_action_project_answer" in project_html
            assert "host_action_quality_gate" in project_html
            assert "host_action_quality_repair_execute" in project_html
            assert "host_action_search_query" in project_html
            assert "recovery evidence" in project_html
            assert "rp_projectrel" in project_html
            assert "project_delivery_checks" in project_html
            assert "rp_studyproto" in project_html
            assert "Study Protocols" in project_html
            assert "study_protocol_checks" in project_html
            assert "/actions/research/project-space" in project_html
            assert "/actions/research-search/export" in project_html
            operations_html = read_text(base + "/operations.html")
            assert "Research Operations" in operations_html
            assert "Operations Queue" in operations_html
            assert "Plan Queue" in operations_html
            assert "Action Items" in operations_html
            assert "Operation Results" in operations_html
            assert "Operations Handoff" in operations_html
            assert "rp_opsboard" in operations_html
            assert "operations_board_checks" in operations_html
            assert "workbench-queue:RUN-042" in operations_html
            assert "research-ops-report:RUN-042" in operations_html
            assert "review-board-&gt;operations" in operations_html
            assert "control-plane-&gt;operations" in operations_html
            review_board_html = read_text(base + "/review-board.html")
            assert "Formal Review Board" in review_board_html
            assert "Board Requests" in review_board_html
            assert "Votes" in review_board_html
            assert "Signoffs" in review_board_html
            assert "Board Decision" in review_board_html
            assert "Assignments" in review_board_html
            assert "Filters And Workloads" in review_board_html
            assert "Review Package" in review_board_html
            assert "rp_reviewboard" in review_board_html
            assert "review-board:final-release" in review_board_html
            assert "review-vote:RUN-042:systems" in review_board_html
            assert "review-signoff:RUN-042:chair" in review_board_html
            assert "formal-review-board-package:RUN-042" in review_board_html
            control_plane_html = read_text(base + "/control-plane.html")
            assert "Platform Control Plane" in control_plane_html
            assert "Approval Flow" in control_plane_html
            assert "Notification Delivery" in control_plane_html
            assert "Run Queue" in control_plane_html
            assert "Plugin Tools" in control_plane_html
            assert "Workspace Access" in control_plane_html
            assert "Saved Views And API Token" in control_plane_html
            assert "Permission Checks" in control_plane_html
            assert "Control Report" in control_plane_html
            assert "rp_control" in control_plane_html
            assert "approval:release-dossier:4" in control_plane_html
            assert "PLUGIN_RUN" in control_plane_html
            assert "queue:RUN-042:2" in control_plane_html
            assert "plugin.tuning" in control_plane_html
            assert "token:local-dashboard" in control_plane_html
            integrity_html = read_text(base + "/integrity.html")
            assert "Integrity Plane" in integrity_html
            assert "Integrity Detail" in integrity_html
            assert "Evidence Traceability" in integrity_html
            assert "Reference Integrity" in integrity_html
            assert "Namespace Checks" in integrity_html
            assert "Status Semantics" in integrity_html
            assert "Review Alignment" in integrity_html
            assert "Report Sources" in integrity_html
            assert "Package Trace" in integrity_html
            assert "Integrity Report" in integrity_html
            assert "backend_evidence" in integrity_html
            assert "stage_artifacts" in integrity_html
            assert "integrity-report:RUN-042" in integrity_html
            coherence_html = read_text(base + "/coherence.html")
            assert "Coherence Plane" in coherence_html
            assert "Coherence Detail" in coherence_html
            assert "Delivery Contracts" in coherence_html
            assert "Run State Checks" in coherence_html
            assert "Lifecycle Checks" in coherence_html
            assert "Workflow Lint" in coherence_html
            assert "Tool Protocol" in coherence_html
            assert "Report Validation" in coherence_html
            assert "Agent Coordination" in coherence_html
            assert "coherence-report:RUN-042" in coherence_html
            publication_html = read_text(base + "/publication.html")
            assert "Publication Workflow" in publication_html
            assert "Publication Detail" in publication_html
            assert "Journal Targets" in publication_html
            assert "Submission Packages" in publication_html
            assert "Peer Review Rounds" in publication_html
            assert "Revision Tasks" in publication_html
            assert "Peer Review Response Packages" in publication_html
            assert "Publication Decisions" in publication_html
            assert "peer-review-response-package:RUN-042:round-1" in publication_html
            assert "publication-decision:RUN-042:accept-with-evidence" in publication_html
            calculations_html = read_text(base + "/calculations.html")
            assert "Calculations" in calculations_html
            assert "calculation-computer:local-agentos" in calculations_html
            assert "calculation-code:metadata-qc:v1" in calculations_html
            assert "calculation-job:lab-gene-x:run042-qc" in calculations_html
            assert "calculation-parser-result:run042-qc" in calculations_html
            assert "calculation-export:lab-gene-x:run042-qc" in calculations_html
            real_task_html = read_text(base + "/real-task.html")
            assert "Real Task" in real_task_html
            assert "palmer-penguins" in real_task_html
            assert "rows" in real_task_html
            assert "344" in real_task_html
            assert "answer_source" in real_task_html
            assert "report_md" in real_task_html
            assert "duplicate_zip_entries" in real_task_html
            analysis_results_html = read_text(base + "/analysis-results.html")
            assert "Analysis Results" in analysis_results_html
            assert "Analysis Plans" in analysis_results_html
            assert "Analysis Runs" in analysis_results_html
            assert "Result Tables" in analysis_results_html
            assert "Statistical Results" in analysis_results_html
            assert "Analysis Figures" in analysis_results_html
            assert "Interpretations" in analysis_results_html
            assert "analysis-plan:RUN-042:treatment-response" in analysis_results_html
            assert "analysis-run:RUN-042:manual" in analysis_results_html
            assert "result-table:manual" in analysis_results_html
            assert "stat-result:manual" in analysis_results_html
            assert "figure:manual" in analysis_results_html
            assert "interpretation:manual" in analysis_results_html
            assert "Manual QC analysis is ready for review." in analysis_results_html
            decision_support_html = read_text(base + "/decision-support.html")
            assert "Decision Support" in decision_support_html
            assert "Decision Options" in decision_support_html
            assert "Decision Criteria" in decision_support_html
            assert "Decision Scores" in decision_support_html
            assert "Review Packet" in decision_support_html
            assert "agentos_ucore_hybrid" in decision_support_html
            assert "decision-review-packet:agentos-final-demo-backend" in decision_support_html
            usable_research_html = read_text(base + "/usable-research.html")
            assert "Usable Research" in usable_research_html
            assert "Research Templates" in usable_research_html
            assert "Reusable Datasets" in usable_research_html
            assert "Library Sources" in usable_research_html
            assert "Research DAG" in usable_research_html
            assert "Workbench Queues" in usable_research_html
            assert "usable-template:workspace-900" in usable_research_html
            assert "usable-dataset:penguins" in usable_research_html
            assert "usable-source:library2026:1" in usable_research_html
            assert "usable-handoff:RUN-900:reviewer" in usable_research_html
            usable_project_html = read_text(base + "/usable-project.html")
            assert "Usable Project Lifecycle" in usable_project_html
            assert "Project Scaffold" in usable_project_html
            assert "Project Launches And Operations" in usable_project_html
            assert "Bundles And Package Actions" in usable_project_html
            assert "scaffold-template:protocol-reproduction" in usable_project_html
            assert "usable-project-launch:lab-gene-x:1" in usable_project_html
            assert "usable-study-protocol-reproduction-package:RUN-042" in usable_project_html
            campaign_html = read_text(base + "/experiment-campaigns.html")
            assert "Experiment Campaigns" in campaign_html
            assert "align-memory-grid" in campaign_html
            assert "trial_count" in campaign_html
            assert "select_trial_04" in campaign_html
            assert "accept_candidate" in campaign_html
            statistical_design_html = read_text(base + "/statistical-design.html")
            assert "Statistical Design" in statistical_design_html
            assert "stat-design:lab-gene-x:run042-primary" in statistical_design_html
            assert "required_per_group" in statistical_design_html
            assert "underpowered" in statistical_design_html
            assert "balanced" in statistical_design_html
            assert "approved_with_sample_size_note" in statistical_design_html
            model_registry_html = read_text(base + "/model-registry.html")
            assert "Model Registry" in model_registry_html
            assert "registered-model:agent-triage-template" in model_registry_html
            assert "model-version:agent-triage-template:v1" in model_registry_html
            assert "model-evaluation:agent-triage-template:v1:RUN-042" in model_registry_html
            assert "model-deployment:agent-triage-template:v1:template" in model_registry_html
            assert "offline provider ready" in model_registry_html
            systematic_review_html = read_text(base + "/systematic-review.html")
            assert "Systematic Review" in systematic_review_html
            assert "systematic-review:agent-os-science" in systematic_review_html
            assert "Screening" in systematic_review_html
            assert "9" in systematic_review_html
            assert "moderate" in systematic_review_html
            assert "prisma-flow:agent-os-science" in systematic_review_html
            experiment_schedule_html = read_text(base + "/experiment-schedule.html")
            assert "Experiment Schedule" in experiment_schedule_html
            assert "schedule:RUN-042:lab-execution" in experiment_schedule_html
            assert "schedule-task:RUN-042:library-prep" in experiment_schedule_html
            assert "schedule-booking:RUN-042:seq-library" in experiment_schedule_html
            assert "schedule-conflict:RUN-042:seq-01-overlap" in experiment_schedule_html
            assert "schedule-exec:RUN-042:library-prep" in experiment_schedule_html
            training_html = read_text(base + "/training-compliance.html")
            assert "Training Compliance" in training_html
            assert "training-req:sop-deviation:qa-lead" in training_html
            assert "training:qa-lead:sop-deviation" in training_html
            assert "competency:qa-lead:sop-deviation" in training_html
            assert "auth:qa-lead:qa-lead:lab-gene-x" in training_html
            assert "training-gap:schedule:RUN-042:lab-execution" in training_html
            release_dossier_html = read_text(base + "/release-dossier.html")
            assert "Release Dossier" in release_dossier_html
            assert "release-dossier:RUN-042:final-review" in release_dossier_html
            assert "experiment-campaign" in release_dossier_html
            assert "agentos-readiness" in release_dossier_html
            assert "release-dossier-package:RUN-042" in release_dossier_html
            mature_html = read_text(base + "/mature.html")
            assert "Mature Platform Mapping" in mature_html
            assert "Mature Capability Detail" in mature_html
            assert "Reference Platforms" in mature_html
            assert "Capability Mappings" in mature_html
            assert "Mature Checks" in mature_html
            assert "Galaxy" in mature_html
            assert "AiiDA" in mature_html
            assert "Snakemake" in mature_html
            assert "kernel_context_path" in mature_html
            assert "batch_tool_runner" in mature_html
            provenance_html = read_text(base + "/provenance.html")
            assert "Provenance Timeline" in provenance_html
            assert "Provenance Detail" in provenance_html
            assert "Timeline Views" in provenance_html
            assert "Timeline Events" in provenance_html
            assert "Provenance Edges" in provenance_html
            assert "Evidence Packets" in provenance_html
            assert "agent_decision_flow" in provenance_html
            assert "agent_to_trace" in provenance_html
            assert "kernel_timeline" in provenance_html
            provenance_query_html = read_text(base + "/provenance-queries.html")
            assert "Provenance Queries" in provenance_query_html
            assert "provenance-query-template:calculation-root-neighborhood" in provenance_query_html
            assert "provenance-query-execution:calculation-lineage" in provenance_query_html
            assert "provenance-query-comparison:RUN-042:rendered-vs-direct" in provenance_query_html
            assert "provenance-query-packet:RUN-042:lineage-review" in provenance_query_html
            project_review_html = read_text(base + "/project-review.html")
            assert "Project Delivery Review" in project_review_html
            assert "Project Release Gate" in project_review_html
            assert "Project Snapshots" in project_review_html
            assert "Snapshot Comparison" in project_review_html
            assert "Project Reproducibility Audit" in project_review_html
            assert "Project Provenance Graph" in project_review_html
            assert "Project Delivery Report" in project_review_html
            assert "project-reproducibility-audit:lab-gene-x" in project_review_html
            assert "package-intake:external-review" in project_review_html
            assert "Project Package Index" in project_review_html
            assert "Study Launches" in project_review_html
            assert "study-protocol-reproduction-package:RUN-042" in project_review_html
            assert "Project Review Action Trace" in project_review_html
            assert "Project Review Action Output Details" in project_review_html
            assert "release" in project_review_html
            assert "project-provenance.dot" in project_review_html
            assert "project-bundle.zip" in project_review_html
            assert "/actions/research/project-release-gate" in project_review_html
            assert "/actions/research/project-provenance-graph" in project_review_html
            artifacts_html = read_text(base + "/artifacts.html")
            assert "Evidence Package" in artifacts_html
            assert "ev" in artifacts_html
            assert "Artifact Manifest Records" in artifacts_html
            assert "Path Steps" in artifacts_html
            assert "Artifact Dossier" in artifacts_html
            assert "Derived Artifact Sections" in artifacts_html
            assert "Artifact Provenance" in artifacts_html
            assert "Artifact Review Path" in artifacts_html
            assert "Artifact Source Map" in artifacts_html
            assert "Delivery Source Map" in artifacts_html
            assert "delivery_file=report_md" in artifacts_html
            assert "rp_report_text" in artifacts_html
            assert "quality_to_package" in artifacts_html
            assert "recovery_to_review" in artifacts_html
            assert "section=rp_align_table" in artifacts_html
            assert "align first_attempt status=failed reason=tool_output_missing" in artifacts_html
            assert "Dossier Checks" in artifacts_html
            assert "Archive Files" in artifacts_html
            assert "Review And LLM Signals" in artifacts_html
            assert "Host Artifact Actions" in artifacts_html
            assert "Operations Source Files" in artifacts_html
            assert "artifact-detail" in artifacts_html
            assert "rp_retry_plan" in artifacts_html
            assert "artifact_manifest" in artifacts_html
            assert "host_relay_eval_batch" in artifacts_html
            assert "host_artifact_chart" in artifacts_html
            assert "Artifact Action Output Details" in artifacts_html
            assert "Artifact Action Impact" in artifacts_html
            assert "Artifact Action Delta" in artifacts_html
            assert "host_artifact_manifest_package" in artifacts_html
            assert "artifact_review_path=raw_to_report" in artifacts_html
            assert "artifact_package" in artifacts_html
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
            assert "Artifact Review Path" in evidence_html
            assert "raw_to_report" in evidence_html
            assert "retrylog-a" in evidence_html
            assert "plan&gt;data&gt;review&gt;repair&gt;audit" in evidence_html
            assert "Evidence Protocol" in evidence_html
            assert "usable-evidence-protocol:RUN-900:1" in evidence_html
            assert "host_action_protocol_title" in evidence_html
            services_html = read_text(base + "/services.html")
            assert "Service Execution" in services_html
            assert "Service Operation Records" in services_html
            assert "sample_lookup" in services_html
            assert "schedule_assess" in services_html
            assert "fair_package" in services_html
            assert "query_answer" in services_html
            assert "worker_heartbeat" in services_html
            assert "Runbook Steps" in services_html
            assert "runbook-template:align-oom-recovery" in services_html
            assert "Operations Checks" in services_html
            delivery_html = read_text(base + "/delivery.html")
            assert "Delivery Package" in delivery_html
            assert "Delivery Files" in delivery_html
            assert "Delivery Package Records" in delivery_html
            assert "Delivery Source Map" in delivery_html
            assert "Review Pack Delivery" in delivery_html
            assert "Workbench Delivery" in delivery_html
            assert "delivery_file=report_md" in delivery_html
            assert "rp_report_text" in delivery_html
            assert "delivery-manifest.json" in delivery_html
            assert "Delivery Action Trace" in delivery_html
            review_html = read_text(base + "/review.html")
            assert "Review Dashboard" in review_html
            assert "Review Sections" in review_html
            assert "Review Gates" in review_html
            assert "Review Evidence Pack" in review_html
            assert "Review Source Map" in review_html
            assert "Delivery Source Map" in review_html
            assert "delivery_file=report_md" in review_html
            assert "rp_report_text" in review_html
            assert "rp_artifact_manifest" in review_html
            assert "host_relay_eval_batch" in review_html
            assert "executions=1" in review_html
            assert "Review Backend Evidence" in review_html
            assert "Review Backend Actions" in review_html
            assert "Review Pack Bridges" in review_html
            assert "Review Operations Summary" in review_html
            assert "Review Workbench Summary" in review_html
            assert "Review Project Summary" in review_html
            assert "Report Source Map" in review_html
            assert "Operations Report Narrative" in review_html
            assert "Operations Source Files" in review_html
            assert "project_followup" in review_html
            assert "backend_evidence" in review_html
            assert "Review Action Trace" in review_html
            assert "Review Action Output Links" in review_html
            assert "Review Action Output Details" in review_html
            assert "Review Action Impact" in review_html
            assert "Review Action Delta" in review_html
            assert "rp_review2,rp_revision" in review_html
            assert "rp_runner,rp_revision,rp_package" in review_html
            assert "host_action_revision" in review_html
            assert "llm_packet_guard" in review_html
            assert "Handoff Checks" in review_html
            assert "send_to_reviewer" in review_html
            assert "delivery_to_operations" in review_html
            assert "lab-gene-x" in review_html
            assert "mf.json" in review_html
            assert "delivery_manifest" in review_html
            assert "backend_evidence_review" in review_html
            assert "backend_review_evidence" in review_html
            assert "rerun_align" in review_html
            assert "kernel_context_path" in review_html
            assert "ready_for_reviewer" in review_html
            assert "host_relay_quality" in review_html
            assert "/actions/research/operations-report" in review_html
            assert "/actions/research/workbench-file-verify" in review_html
            assert "/actions/research/project-space" in review_html
            assert "/actions/research/llm-relay-request" in review_html
            assert "/actions/agentcompare/run" in review_html
            compare_html = read_text(base + "/compare.html")
            assert "Compare Summary" in compare_html
            assert "Compare Metrics" in compare_html
            assert "control_plane_checks" in compare_html
            assert "integrity_plane_checks" in compare_html
            assert "Integrity Plane" in compare_html
            assert "coherence_plane_checks" in compare_html
            assert "Coherence Plane" in compare_html
            assert "publication_checks" in compare_html
            assert "Publication Workflow" in compare_html
            assert "calculation_checks" in compare_html
            assert "Calculation Checks" in compare_html
            assert "real_task_checks" in compare_html
            assert "Real Task Checks" in compare_html
            assert "analysis_results_checks" in compare_html
            assert "Analysis Results Checks" in compare_html
            assert "experiment_campaign_checks" in compare_html
            assert "Experiment Campaign Checks" in compare_html
            assert "statistical_design_checks" in compare_html
            assert "Statistical Design Checks" in compare_html
            assert "model_registry_service_checks" in compare_html
            assert "Model Registry Checks" in compare_html
            assert "release_dossier_checks" in compare_html
            assert "Release Dossier Checks" in compare_html
            assert "mature_capability_checks" in compare_html
            assert "Mature Capability" in compare_html
            assert "provenance_view_checks" in compare_html
            assert "Provenance View" in compare_html
            assert "provenance_query_checks" in compare_html
            assert "Provenance Queries" in compare_html
            assert "Compare Action Trace" in compare_html
            assert "Compare Action Output Links" in compare_html
            assert "Compare Action Output Details" in compare_html
            assert "Compare Action Impact" in compare_html
            assert "Compare Action Delta" in compare_html
            assert "rp_wfio,rp_package,rp_agentcmp" in compare_html
            assert "rp_agentcmp,rp_api_compare" in compare_html
            assert "host_portability_payload" in compare_html
            assert "portability_package" in compare_html
            assert "Plain Kernel Signals" in compare_html
            assert "Consistency Signals" in compare_html
            assert "File Scans" in compare_html
            assert "Rebuild Steps" in compare_html
            assert "Portability Checks" in compare_html
            assert "Backend Checks" in compare_html
            assert "Backend Runner" in compare_html
            assert "Backend Runner Cases" in compare_html
            assert "Backend Case Details" in compare_html
            assert "Backend Evidence Report" in compare_html
            assert "retry-recovery" in compare_html
            assert "rerun_align" in compare_html
            assert "kernel_fsmeta" in compare_html
            assert "scan_records_128" in compare_html
            assert "kernel_context_path" in compare_html
            assert "Input Check" in compare_html
            assert "tool_output_missing" in compare_html
            assert "kernel_required" in compare_html
            assert "Backend Study Metrics" in compare_html
            assert "Detail Checks" in compare_html
            assert "operations_board_checks" in compare_html
            assert "review_board_checks" in compare_html
            assert "agentos_ucore" in compare_html
            assert "Backend Scenario Handoff" in compare_html
            assert "rp_backend_exec" in compare_html
            assert "rp_agentcmp" in compare_html
            assert "pb" in compare_html
            assert "/actions/workflow-portability/run" in compare_html
            assert "/actions/agentcompare/run" in compare_html
            assert "/actions/host-workflow/retry-decision" in compare_html
            actions_html = read_text(base + "/actions.html")
            assert "Batch Actions" in actions_html
            assert "Action Output Links" in actions_html
            assert "Action Output Details" in actions_html
            assert "Action Impact" in actions_html
            assert "Action Delta" in actions_html
            assert "Host Actions" in actions_html
            assert "rp_input,rp_runner,rp_report_text" in actions_html
            assert "rp_package,rp_artifact_manifest" in actions_html
            assert "qemu_orch_passed" in actions_html
            assert "host_action_revision" in actions_html
            assert "host_action_run_id" in actions_html
            assert "host_report_run_id" in actions_html
            assert "host_llm_packet_request" in actions_html
            llm_html = read_text(base + "/llm.html")
            assert "LLM Relay" in llm_html
            assert "Relay Quality" in llm_html
            assert "Quality Checks" in llm_html
            assert "Delivery Checks" in llm_html
            assert "LLM Relay Flow" in llm_html
            assert "LLM Action Trace" in llm_html
            assert "LLM Action Output Links" in llm_html
            assert "LLM Action Output Details" in llm_html
            assert "LLM Action Impact" in llm_html
            assert "LLM Action Delta" in llm_html
            assert "rp_llm_req,rp_llm_packets,rp_llm_resp" in llm_html
            assert "/actions/research/llm-relay-request" in llm_html
            assert "host_llm_packet_request" in llm_html
            assert "host_llm_response_summary" in llm_html
            assert "matched" in llm_html
            assert "host_relay_eval_batch" in llm_html
            assert "relay-llm-q1" in llm_html
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    print("test_plain_ucore_reader_e2e: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
