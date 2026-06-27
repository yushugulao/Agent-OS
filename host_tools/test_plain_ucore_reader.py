#!/usr/bin/env python3
"""Self-test for the plain uCore host reader."""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request

import plain_ucore_reader


class FakeRunner:
    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def prepare_action_state(actions: list[dict[str, object]], state_dir: Path, run_dir: Path) -> dict[str, object]:
        next_state = run_dir / "state-next"
        next_state.mkdir(parents=True, exist_ok=True)
        for item in state_dir.iterdir():
            if item.is_file() and item.name.startswith("rp_"):
                (next_state / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
        lines = [
            "action={};path={};kind=test;status=accepted".format(action["sequence"], action["path"])
            for action in actions
        ]
        (next_state / "rp_host_action_inbox").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"actions": len(actions), "accepted": len(actions), "status": "ready"}

    @staticmethod
    def run_plain_ucore(repo_dir: Path, run_dir: Path, timeout_seconds: int, wsl_distro: str) -> dict[str, object]:
        next_state = run_dir / "state-next"
        (next_state / "rp_host_run_result").write_text(
            "host_runner=fake\npassed=1\nqemu_orch_passed=1\nstatus=ready\n",
            encoding="utf-8",
        )
        return {"passed": True, "status": "ready", "embedded_action_records": 1, "log": str(run_dir / "fake.log")}

    @staticmethod
    def publish_next_state(next_state: Path, state_dir: Path) -> None:
        for item in next_state.iterdir():
            if item.is_file() and item.name.startswith("rp_"):
                (state_dir / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")


class FakeRelay:
    @staticmethod
    def run_relay(state_dir: Path, out_dir: Path, mode: str, summary_path: Path) -> dict[str, object]:
        (out_dir / "rp_llm_resp").write_text(
            "host_relay_process=fake;mode={};status=ready\n".format(mode),
            encoding="utf-8",
        )
        summary = {"relay": "fake", "mode": mode, "requests": 1, "responses": 1, "status": "ready"}
        summary_path.write_text(json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8")
        return summary


STATE_FILES = {
    "rp_web_bundle": """bundle=host-web-ui
reader_contract=host_plain_ucore_v2
reader_contract_version=2
reader_ready=1
reader_views=21
reader_actions=57
reader_payload_files=rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_bio,rp_api_labres,rp_api_pub,rp_api_know,rp_api_runtime,rp_api_action,rp_web_routes
reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_studio,rp_web_bundle
reader_required_sections=routes,payloads,actions,live_update,downloads,compare
reader_event_stream=rp_web_bundle
reader_fallback=rp_site
reader_state_source=plain_ucore_files
dynamic_inputs=4
project_review=ready;project=lab-gene-x;source=rp_web_bundle;status=ready
release_gate=project-release-gate:lab-gene-x;project=lab-gene-x;decision=release;checks=6;required_actions=0;suggested_actions=2;status=ready
project_snapshot=project-snapshot:lab-gene-x:1;project=lab-gene-x;files=11;present=11;missing=0;hash_records=11;changes=0;status=ready
snapshot_comparison=project-snapshot-comparison:lab-gene-x:latest;project=lab-gene-x;left=old;right=new;changed_files=0;decision=stable;status=ready
reproducibility_audit=project-reproducibility-audit:lab-gene-x;project=lab-gene-x;inputs=2;outputs=8;notebooks=2;claim_audits=1;decision=passed;status=ready
provenance_graph=project-provenance-graph:lab-gene-x;project=lab-gene-x;nodes=9;edges=12;dot=project-provenance.dot;status=ready
project_delivery=project-delivery:lab-gene-x;project=lab-gene-x;decision=ready;bundle=project-bundle.zip;release_gate=release;handoff=ready;status=ready
package_intake=package-intake:external-review;label=External review package;decision=accepted;files=5;sha256=checked;status=ready
package_index=project-package-index;handoff=ready;release_gate=release;snapshot=stable;reproducibility=passed;provenance=ready;status=ready
status=ready
""",
    "rp_web_routes": "routes=74\nget_routes=17\npost_routes=57\nroute=/research-studio;payload=rp_studio;status=ready\nroute=/research/project/{id}/review;payload=rp_web_bundle;status=ready\naction=/actions/research/studio-launch;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-release-gate;method=POST;payload=rp_api_action;status=ready\nstatus=ready\n",
    "rp_api_home": "api=home\nreader_contract=rp_web_bundle\nstatus=ready\n",
    "rp_api_run": "api=run-detail\nreader_contract=rp_web_bundle\nreader_view=run-detail\nstatus=ready\n",
    "rp_api_agents": "api=agent-detail\nagents=7\nstatus=ready\n",
    "rp_api_evidence": "api=evidence-detail\nclaims=8\nstatus=ready\n",
    "rp_api_compare": "api=compare-metrics\nplain_kernel=passed\nfile_scans=128\nstate_convention=1\nuser_permission_only=1\ncontext_trusted=0\nrebuild_steps=6\nstatus=ready\n",
    "rp_api_artifacts": "api=artifacts\nmanifest_records=4\nstatus=ready\n",
    "rp_api_data": "api=data\ndataset_snapshots=2\npreviews=2\nquality_checks=7\ntransforms=2\ncollection_items=4\nhost_action_file_manifest=mf.json\nhost_action_file_verify=passed\nhost_action_file_verified=11\nhost_action_file_missing=0\nstatus=ready\n",
    "rp_api_bio": "api=bio\nsample_registry=rp_sreg\nstatus=ready\n",
    "rp_api_labres": "api=lab-resources\ninstrument_registry=rp_instr\nstatus=ready\n",
    "rp_api_pub": "api=publication\nresult_review=rp_resrev\nstatus=ready\n",
    "rp_api_know": "api=knowledge\nsemantic_index=rp_semindex\nstatus=ready\n",
    "rp_api_runtime": "api=runtime\nruntime_env=rp_runenv\nstatus=ready\n",
    "rp_api_action": "api=actions\nreader_contract=rp_web_bundle\nactions=57\nresearch_studio_launch=/actions/research/studio-launch\nproject_release_gate=/actions/research/project-release-gate\nproject_review_actions=8\nstatus=ready\n",
    "rp_studio": (
        "studio=usable-research-studio\n"
        "sessions=1\n"
        "latest_session=usable-research-studio-session:W1:1\n"
        "studio_session=usable-research-studio-session:W1:1;title=Studio cytokine evidence;goal=Determine whether recovery evidence is ready;direction=evidence review;workbench=W1;run=R1;answer=answer1;decision=studio_completed;status=ready\n"
        "studio_material=host_action;notes=Small demonstration table for the studio workflow.;csv_rows=host;references=host;workspace=host_input;status=ready\n"
        "studio_links=host_action;studio=/research-studio;workbench=/research/workbench/W1;project=/research/project/lab-gene-x;download=/download/research-studio-session/usable-research-studio-session-W1-1;status=ready\n"
        "host_action_studio_title=Studio cytokine evidence\n"
        "host_action_studio_goal=Determine whether recovery evidence is ready\n"
        "host_action_studio_direction=evidence review\n"
        "status=ready\n"
    ),
    "rp_bioop": "ops=7\nop=sample_lookup;records=8;status=ok\nop=access_decision;requests=3;status=ok\n",
    "rp_labresop": "ops=6\nop=schedule_assess;bookings=6;status=ok\nop=training_gate;requirements=4;status=ok\nlab_governance_ops=approvals:2,ethics_protocols:1,protocol_compliance_reports:2,protocol_amendments:2,sop_executions:3,training_records:4,instrument_maintenance:3,inventory_transactions:14,procurement_orders:2,resource_budgets:3,run_queue_items:4,notifications:3,status=ready\n",
    "rp_pubop": "ops=6\nop=result_review;items=10;status=ok\nop=fair_package;checks=8;status=ok\n",
    "rp_knowop": "ops=6\nop=query_answer;answers=4;status=ok\nop=llm_grounding;responses=3;status=ok\n",
    "rp_runop": "ops=7\nop=worker_heartbeat;workers=4;status=ok\nop=host_llm_request;packets=3;status=ok\nplatform_doctor=ready;checks=10;workspace=pass;template=pass;cloud_llm=optional;provider_health=offline:1,cloud:0,ready_cloud:0;downloads=markdown,json\nsource_portfolio=sources:42,citations:4,tags:2,portfolio:ready,status=ready\nresearch_portfolio_scale=sources:42,datasets:3,literature_searches:4,reviews:8,evidence_reviews:4,evidence_extractions:15,screening_decisions:15,exports:66,doctor_reports:10,project_handoff_audits:30,project_run_comparisons:15,project_reproducibility_audits:15,project_snapshot_comparisons:15,status=ready\nruntime_assurance=secret_refs:3,model_registry:2,deployments:1,llm_proxy_audits:2,collab_threads:2,obs_alerts:5,health:1,status=ready\nresearch_ops=semantic_entities:8,semantic_relations:6,prompt_templates:2,prompt_versions:2,prompt_evaluations:1,runbook_steps:7,worker_ops:6,execution_controls:8,status=ready\nregulated_research=annotation_schemas:1,annotation_tasks:3,assay_plates:1,plate_wells:6,cohort_records:2,data_access_requests:1,dataset_cards:1,model_cards:1,research_object_crates:1,research_object_entities:29,sample_custody_events:18,statistical_designs:1,workflow_templates:8,status=ready\n",
    "rp_ui_home": "page=home\nstatus=ready\n",
    "rp_ui_run": "page=run-detail\nstatus=ready\n",
    "rp_ui_agent": "page=agent-detail\ndecision_records=rp_agents,rp_decisions,rp_handoff,rp_deliberation,rp_agent_run\nstatus=ready\n",
    "rp_ui_evidence": "page=evidence-detail\nscreening_decisions=9\nevidence_protocol=usable-evidence-protocol:RUN-900:1\nstatus=ready\n",
    "rp_ui_compare": "page=compare-metrics\npain_file_scans=128\npain_state_convention=1\npain_user_permissions=1\npain_rebuild_steps=6\nstatus=ready\n",
    "rp_runner": (
        "workbench_tasks=9\n"
        "host_action_workbench_id=W1\n"
        "host_action_workbench_title=WB1\n"
        "host_action_workbench_literature_query=prov\n"
        "host_action_workbench_question=Ready?\n"
        "host_action_workbench_evidence_query=rec\n"
        "host_action_workbench_answer=generated\n"
        "host_action_workbench_answer_audit=passed\n"
        "host_action_workbench_readiness=checked\n"
        "host_action_workbench_task=human_review\n"
        "host_action_workbench_task_status=waiting\n"
        "host_action_workbench_note=recorded\n"
        "host_action_workbench_note_title=Scope\n"
        "host_action_workbench_manuscript=exported\n"
        "host_action_workbench_manuscript_format=markdown\n"
        "host_action_workbench_task_board=exported\n"
        "host_action_workbench_board_filter=open\n"
        "workbench_delivery_scale=workbenches:5,templates:5,workspace_imports:5,workspace_inspections:5,answers:5,deliveries:6,studio_sessions:2,project_action_plans:15,project_deliveries:4,project_runbooks:15,project_evidence_audits:15,project_provenance_graphs:3,project_launches:3,project_release_gates:15,project_snapshots:15,status=ready\n"
        "backend_evidence_report=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;status=ready\n"
        "status=ready\n"
    ),
    "rp_report_text": (
        "host_report_run_id=RUN-042\n"
        "host_report_title=Static Study\n"
        "host_report_question=Can static state render the report source map?\n"
        "host_report_provider=template\n"
        "report_source=run_setup;state_file=rp_report_text;source_key=host_report_run_id;linked_sources=rp_input,rp_api_run;status=ready\n"
        "report_source=workflow;state_file=rp_stage_state;source_key=host_workflow_run_id;linked_sources=rp_stage_dag,rp_run_events,rp_retry_plan;status=ready\n"
        "report_source=artifacts;state_file=rp_artifact_manifest;source_key=artifact_review_path;linked_sources=rp_artifact,rp_stage_log,rp_chart_data;status=ready\n"
        "report_source=llm;state_file=rp_llm_resp;source_key=host_relay_response;linked_sources=rp_llm_req,rp_llm_packets,rp_llmeval,rp_llm_guard;status=ready\n"
        "report_source=review;state_file=rp_review_dashboard;source_key=section,gate,decision;linked_sources=rp_review_pack,rp_review2,rp_revision;status=ready\n"
        "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index;status=ready\n"
        "status=ready\n"
    ),
    "rp_stage_state": (
        "run_id=RUN-042\n"
        "stage=ingest;order=1;input=rp_input_fastq;attempts=1;state=done\n"
        "stage=align;order=2;input=rp_artifact:rp_normalized_fastq;attempts=2;state=recovered\n"
        "stage=profile;order=3;input=rp_artifact:rp_align_table;attempts=1;state=cached\n"
        "command=ingest:read_fastq;output=rp_artifact:rp_normalized_fastq\n"
        "command=align:agent-align;output=rp_artifact:rp_align_table;first_status=failed;second_status=recovered\n"
        "command=profile:derive_metrics;output=rp_artifact:rp_metrics_json;cache=hit\n"
        "host_workflow_id=WF1\n"
        "host_workflow_run_id=R1\n"
        "host_workflow_engine=plain-c-runner\n"
        "host_workflow_retry_stage=clean\n"
        "host_workflow_cache_hit_stage=analyze\n"
        "host_workflow_worker_slots=2\n"
        "host_workflow_queue_depth=5\n"
        "status=ready\n"
    ),
    "rp_cache_index": (
        "cache_key=ingest:RUN-042;state=miss;source=rp_input_fastq\n"
        "cache_key=align:RUN-042;state=refreshed;source=rp_artifact\n"
        "cache_key=profile:RUN-042;state=hit;source=rp_compute\n"
        "cache_policy=content_keyed\n"
        "host_workflow_cache_policy=content\n"
        "host_workflow_cache_hit_stage=analyze\n"
        "status=ready\n"
    ),
    "rp_retry_plan": (
        "retry_stage=align\n"
        "attempts=2\n"
        "failure_reason=tool_output_missing\n"
        "rerun_inputs=rp_input_fastq\n"
        "rerun_outputs=rp_artifact\n"
        "skip_stages=ingest,profile,review,package\n"
        "dedupe_key=RUN-042:align\n"
        "minimal_rerun=1\n"
        "host_workflow_retry_stage=clean\n"
        "host_workflow_retry_reason=checksum_mismatch\n"
        "status=ready\n"
    ),
    "rp_run_events": (
        "event=1;stage=ingest;action=read_input;status=done\n"
        "event=4;stage=align;action=rerun;status=recovered\n"
        "event=5;stage=profile;action=cache_lookup;status=hit\n"
    ),
    "rp_worker": (
        "workers=4\n"
        "ready=4\n"
        "busy=0\n"
        "stalled=0\n"
        "heartbeats=4\n"
        "queue_actions=8\n"
        "host_workflow_worker_slots=2\n"
        "host_workflow_queue_depth=5\n"
        "status=ready\n"
    ),
    "rp_execobs": (
        "execution_view=stage_summary;stage=align;order=2;attempts=2;state=recovered;input=rp_artifact:rp_normalized_fastq;output=rp_artifact:rp_align_table;retry=rp_retry_plan;failure=tool_output_missing;worker=worker-2;event=4;status=recovered\n"
        "execution_view=control_summary;dag=rp_stage_dag;cache=rp_cache_index;retry=rp_retry_plan;events=rp_run_events;workers=rp_worker;observer=rp_execobs;status=ready\n"
        "host_execution_view=workflow_run;workflow=WF1;run_id=R1;engine=plain-c-runner;retry_stage=clean;cache_hit=analyze;worker_slots=2;queue_depth=5;observer_events=12;status=ready\n"
    ),
    "rp_artifact": (
        "section=rp_normalized_fastq;reads=2;bases=24;status=ready\n"
        "section=rp_align_table;reference=RUN-042-read-1;variant_count=2;status=ready\n"
        "archive_file=rp_align_table;kind=alignment;status=ready\n"
        "artifact_dossier=rp_input_fastq,rp_normalized_fastq,rp_align_table,rp_metrics_json,rp_gene_counts_csv,rp_chart_data,rp_stage_log\n"
        "artifact_review_link=rp_artifact_manifest->rp_review_pack->rp_package\n"
        "provenance=rp_align_table;stage=align;event=4;retry=rp_retry_plan;review_gate=artifact_manifest;llm_quality=rp_llmeval;status=recovered\n"
        "provenance=rp_metrics_json;stage=profile;event=5;cache=hit;review_gate=artifact_manifest;status=ready\n"
        "status=recovered\n"
    ),
    "rp_agents": "agent=orchestrator;role=control;state=active;msg=4\nagent=recovery;role=repair;state=recovered;msg=3\nagents=7\nmessages=21\n",
    "rp_decisions": "decision=1;actor=orchestrator;choice=start_workflow;basis=rp_plan\ndecision=5;actor=recovery;choice=rerun_align_only;basis=rp_retryq\ndecisions=8\n",
    "rp_handoff": "handoff=planner->retriever;artifact=rp_plan;status=done\nhandoff=recovery->writer;artifact=rp_artifact;status=done\nhandoffs=6\n",
    "rp_deliberation": "item=1;topic=failed_align;vote=recoverable;source=rp_stage_log\nitems=5\n",
    "rp_agent_run": "agent_messages=21\nagent_decisions=8\n",
    "rp_evidence": "claims=8\nevidence_links=5\n",
    "rp_lit": "evidence_links=5\nscreening_decisions=9\n",
    "rp_claimrec": "claim=1;kind=result;source=rp_data;evidence=lit-a,calc-a;status=supported\nclaim=3;kind=recovery;source=rp_fix;evidence=retrylog-a;status=supported\n",
    "rp_provpath": "critical_paths=3\npath1=plan>data>review>repair>audit\npath2=plan>lit>evidence>knowledge>package\n",
    "rp_knowledge": "literature_search_id=usable-literature-search:RUN-900:1\nscreening_decisions=9;included=3;excluded=6\nevidence_extractions=3;fields=mechanism,evidence_type,reported_outcome\nevidence_protocol=usable-evidence-protocol:RUN-900:1;status=registered\nprisma_flow=usable-prisma-flow:RUN-900:1;identified=9;included=3\nevidence_synthesis=usable-evidence-synthesis:RUN-900:1;themes=traceability,reproducibility,recovery\n",
    "rp_package": (
        "delivery_files=8\n"
        "delivery_file=report_md;path=rp_report_text;required=1;exists=1;status=ready\n"
        "review_pack_bridge=delivery_manifest,operations_report,project_space,workbench_handoff\n"
        "review_pack_action=sync_operations_next;source=rp_runner;status=ready\n"
        "host_action_export_bundle_name=reviewer-evidence\n"
        "host_action_bundle_contents=12\n"
        "host_action_workbench_manifest=delivery-manifest.json\n"
        "host_action_workbench_verified_files=9\n"
        "host_action_workbench_missing_files=0\n"
        "host_action_project_id=lab-gene-x\n"
        "host_action_project_space=ready\n"
        "host_action_project_note=recorded\n"
        "host_action_project_action_item=created\n"
        "host_action_project_answer=generated\n"
        "host_action_project_repair=executed\n"
        "host_action_research_search=ready\n"
        "host_action_quality_gate=checked\n"
        "host_action_quality_repair_plan=ready\n"
        "host_action_quality_repair_execute=done\n"
        "evidence_bundle_entries=12\n"
    ),
    "rp_nbexec": "host_action_notebook_format=ipynb\nhost_action_notebook_workbench_docs=ready\nstatus=ready\n",
    "rp_uresrun": "host_action_workbench_outputs=rp_runner,rp_revision,rp_package\nhost_action_workbench_manifest=delivery-manifest.json\nhost_action_workbench_bundle=workbench-bundle.zip\nstatus=ready\n",
    "rp_query": (
        "query=workflow,agent,evidence\n"
        "workflow_hits=34\n"
        "agent_hits=26\n"
        "evidence_hits=10\n"
        "knowledge_index=search_documents:1385,provenance_nodes:406,provenance_links:544,events:6816,context_records:348,host_workflow_artifacts:150,usable_artifacts:429,usable_runs:20,usable_stages:168,usable_messages:223,usable_decisions:203,status=ready\n"
        "status=ready\n"
    ),
    "rp_agentcmp": (
        "plain_kernel=passed\n"
        "test_cases=1162\n"
        "tool_events=171\n"
        "handoffs=6\n"
        "review_handoff_checks=13;review_sections=8;review_gates=6;review_decisions=4;review_handoffs=3;review_pack_actions=3;review_pack_bridges=4;backend_review=1;status=ready\n"
        "review_pack=ready;evidence_items=11;actions=5;plain_kernel=ordinary_files;backend_evidence=1\n"
        "runbook_recovery_checks=16;templates=1;steps=7;incident_triages=1;executions=1;exports=1;worker_records=6;agentos_replacements=4;status=ready\n"
        "project_delivery_checks=18;handoff_audits=1;project_runbooks=1;release_gates=1;snapshots=1;snapshot_comparisons=1;reproducibility_audits=1;provenance_graphs=1;package_intakes=1;package_indexes=1;agentos_replacements=4;status=ready\n"
        "study_protocol_checks=20;protocols=2;launches=2;runs=1;compliance_reports=1;bundles=1;reproduction_packages=1;reproduction_reviews=1;action_plans=1;action_executions=1;dataset_portfolios=1;source_portfolios=1;dataset_cards=1;visualizations=1;answers=1;agentos_replacements=4;status=ready\n"
        "operations_board_checks=18;pending_reviews=1;reproduction_actions=1;workbench_actions=4;plan_items=5;action_items=4;handoffs=3;latest_runs=4;exports=2;agentos_replacements=4;status=ready\n"
        "review_board_checks=24;boards=1;requests=1;votes=4;signoffs=4;assignments=4;workloads=4;filters=2;decision=approved;agentos_replacements=4;status=ready\n"
        "control_plane_checks=30;approvals=4;notifications=4;queue_items=4;plugins=3;workspaces=1;permissions=5;agentos_replacements=4;status=ready\n"
        "llm_delivery_checks=16;llm_queue=3;llm_packets=3;llm_responses=3;llm_eval=7;llm_guard=3;llm_hostreq=3;llm_review_links=2;status=ready\n"
        "workflow_portability_checks=14;portability_imports=5;adapter_specs=6;migration_steps=9;rehearsal_cases=4;blocking_items=0;portability_package=workflow-portability;status=ready\n"
        "portability_backend_checks=12;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;passed_cases=2;planned_cases=2;status=ready\n"
        "backend_runner_checks=12;runner_cases=4;runner_passed=2;runner_planned=2;plain_inputs=4;study_metrics=2;backend_runner_detail_checks=24;runner_detail_rows=4;backend_runner_report_checks=20;runner_report_rows=4;backend_report_links=2;status=ready\n"
        "lab_governance_ops_checks=26;approval_checks=2;protocol_governance_checks=4;sop_execution_checks=3;training_record_checks=4;status=ready\n"
        "knowledge_index_checks=22;llm_transcript_checks=3;workbench_delivery_checks=15;research_portfolio_checks=16;execution_scale_checks=14;operations_scale_checks=12;project_revision_incident_checks=12;reserved_research_surface_checks=21;root_state_surface_checks=10;agentos_reserved_surface_checks=21;state_catalog_checks=12;startup_doctor_checks=14;status=ready\n"
        "state_catalog=keys:573;nonzero:70;zero:503;represented:573;checks:12;status=ready\n"
        "startup_doctor=quickstart:ready;doctor:ready;checks:14;commands:startup_guide,platform_doctor,project_launch,open_research_studio;status=ready\n"
        "knowledge_index=search_documents:1385;provenance_nodes:406;provenance_links:544;events:6816;context_records:348;usable_artifacts:429;usable_runs:20;status=ready\n"
        "llm_transcripts=90;llm_bridge_requests=30;llm_bridge_responses=30;workbenches=5;deliveries=6;studio_sessions=2;project_action_plans=15;project_runbooks=15;project_evidence_audits=15;project_provenance_graphs=3;status=ready\n"
        "research_portfolio=sources:42;datasets:3;literature_searches:4;reviews:8;evidence_reviews:4;evidence_extractions:15;screening_decisions:15;exports:66;doctor_reports:10;project_handoff_audits:30;project_run_comparisons:15;project_reproducibility_audits:15;project_snapshot_comparisons:15;status=ready\n"
        "agentcompare_execution_scale=reports:3;results:15;profiles:3;plain_runs:5;indexed_runs:5;real_artifact_runs:5;status=ready\n"
        "host_runtime_scale=workflow_runs:10;stage_runs:70;workflow_artifacts:150;cache_records:6;agent_messages:70;agent_decisions:70;status=ready\n"
        "content_graph_scale=content_objects:145;object_references:145;host_content_objects:129;host_object_references:129;status=ready\n"
        "host_operations_scale=audit_records:5;metrics:13;llm_providers:3;secret_references:3;executed_corr_ids:4;usable_projects:20;artifacts:128;messages:70;status=ready\n"
        "project_revision_incident=revision_tasks:1;project_scaffolds:1;incidents:1;incident:INC-RUN-042-ALIGN-OOM;failed_stage:align;reason:memory_limit;revision_status:completed;scaffold:deepseek-reliability-response-study;status=ready\n"
        "root_state_surface=projects:1;runs:1;reports:1;plans:1;search_records:1;site_exports:1;compare_profiles:1;audit:5;context:348;project:lab-gene-x;run:RUN-042;status=ready\n"
    ),
    "rp_backend_exec": (
        "runner_case=plain-ucore;input=rp_wfio;artifact=rp_artifact_manifest;result=passed;reason=native_programs_ok;input_check=pass;artifact_check=pass;att=1;retry=none;ticks=3\n"
        "runner_case=retry-recovery;input=rp_retry_plan;artifact=rp_stage_state;result=passed;reason=recovered_align;input_check=pass;artifact_check=pass;att=2;retry=tool_output_missing;ticks=5\n"
        "runner_case=agentos-context;input=rp_wfio;artifact=agent_context;result=planned;reason=kernel_context;input_check=planned;artifact_check=planned;retry=kernel_required\n"
        "runner_case=agentos-fsmeta;input=rp_wfio;artifact=agent_file_meta;result=planned;reason=kernel_metadata;input_check=planned;artifact_check=planned;retry=kernel_required\n"
        "runner_detail=plain-ucore;src=rp_wfio;req=execution_plan;obs=pass;act=record;review=baseline\n"
        "runner_detail=retry-recovery;src=rp_retry_plan+rp_stage_state;req=retry_stage+stage;obs=pass;act=rerun_align;review=recovered\n"
        "runner_detail=agentos-context;src=rp_wfio;req=context_path;obs=planned;act=kernel_context;review=target\n"
        "runner_detail=agentos-fsmeta;src=rp_artifact_manifest;req=metadata_index;obs=planned;act=kernel_fsmeta;review=target\n"
        "runner_detail_rows=4\nrunner_detail_schema=src,req,obs,act,review\n"
        "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=passed\n"
        "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=passed\n"
        "runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=planned\n"
        "runner_report=agentos-fsmeta;plain_cost=scan_records_128;agentos_replace=metadata_index;risk=scan_growth;status=planned\n"
        "runner_report_rows=4\nrunner_report_schema=plain_cost,agentos_replace,risk,status\n"
        "runner_cases=4\nrunner_detail_fields=input_check,artifact_check,att,retry,ticks\nrunner_detail_checks=16\nrunner_verified_inputs=4\nrunner_passed=2\nrunner_planned=2\n"
    ),
    "rp_study": (
        "study_metric=plain_ucore;file_scans=128;context_trusted=0;rebuild_steps=6;detail_checks=4;result=passed\n"
        "study_metric=agentos_ucore;context_trusted=1;batch_tools=1;metadata_index=1;detail_checks=kernel;result=planned\n"
        "study_handoff=rp_backend_exec->rp_agentcmp;status=ready\n"
    ),
    "rp_consistency": "checks=420\nstate_catalog_checks=12\nstartup_doctor_checks=14\nhost_state_keys=573\nagentos_reserved_surface_checks=21\nagentos_reserved_surface=profiles:0,skills:0,tasks:0,deliberations:0,handoffs:0,coord:0,abi:0,adapter:0,readiness:0,tool_bindings:0\nstate_relation=passed\nruntime_assurance_checks=24\nsecret_reference_checks=6\nmodel_registry_checks=5\nllm_proxy_replay_audits=2\ncollaboration_threads=2\nobservability_alerts=5\nresearch_ops_checks=28\nsemantic_graph_checks=6\nprompt_ops_checks=5\nrunbook_checks=7\nworker_ops_checks=5\nexecution_control_checks=5\nregulated_research_checks=32\nannotation_checks=5\nassay_plate_checks=4\ncohort_monitoring_checks=3\ndata_access_checks=4\nresearch_card_checks=4\nresearch_object_checks=5\nsample_custody_checks=3\nstatistical_design_checks=2\nworkflow_template_checks=2\nlab_governance_ops_checks=26\napproval_checks=2\nethics_protocol_checks=1\nprotocol_governance_checks=4\nsop_execution_checks=3\ntraining_record_checks=4\ninstrument_maintenance_checks=3\ninventory_transaction_checks=3\nprocurement_order_checks=2\nresource_budget_checks=2\nrun_queue_checks=1\nnotification_checks=1\nresearch_product_checks=18\nproject_scaffold_files=8\ndataset_product_exports=9\nsource_portfolio_exports=1\nresearch_portfolio_checks=16\nusable_research_sources=42\nusable_research_datasets=3\nusable_research_literature_searches=4\nusable_research_reviews=8\nusable_research_evidence_reviews=4\nusable_research_evidence_extractions=15\nusable_research_screening_decisions=15\nusable_research_exports=66\nusable_research_platform_doctor_reports=10\nusable_research_project_handoff_audits=30\nusable_research_project_run_comparisons=15\nusable_research_project_reproducibility_audits=15\nusable_research_project_snapshot_comparisons=15\nexecution_scale_checks=14\nhost_workflow_runs=10\nhost_workflow_stage_runs=70\nhost_workflow_cache=6\nhost_agent_messages=70\nhost_agent_decisions=70\nagentcompare_reports=3\nagentcompare_results=15\nagentcompare_profiles=3\ncontent_objects=145\nobject_references=145\noperations_scale_checks=12\nhost_audit_records=5\nhost_metrics=13\nhost_llm_providers=3\nhost_secret_references=3\nhost_executed_corr_ids=4\nusable_research_projects=20\nhost_artifacts=128\nhost_messages=70\nhost_content_objects=129\nhost_object_references=129\nhost_agentcompare_reports=3\nhost_agentcompare_results=15\nproject_revision_incident_checks=12\nusable_research_revision_tasks=1\nusable_research_project_scaffolds=1\nincidents=1\nincident_id=INC-RUN-042-ALIGN-OOM\nincident_failed_stage=align\nincident_reason=memory_limit\nincident_status=closed\nrevision_task_status=completed\nrevision_task_owner=Wang\nrevision_review_decision=needs_revision\nproject_scaffold=deepseek-reliability-response-study\nproject_scaffold_exports=json,markdown\nreserved_research_surface_checks=21\nusable_research_dataset_answers=0\nusable_research_dataset_cards=0\nusable_research_dataset_portfolios=0\nusable_research_dataset_previews=0\nusable_research_dataset_run_comparisons=0\nusable_research_dataset_runs=0\nusable_research_dataset_visualizations=0\nusable_research_evidence_syntheses=0\nusable_research_package_intakes=0\nusable_research_prisma_flows=0\nusable_research_project_action_executions=0\nusable_research_project_reviews=0\nusable_research_review_protocols=0\nusable_research_source_portfolios=0\nusable_research_study_protocol_bundles=0\nusable_research_study_protocol_compliance_reports=0\nusable_research_study_protocol_launches=0\nusable_research_study_protocol_runs=0\nusable_research_study_protocols=0\nusable_research_workbench_action_items=0\nusable_research_workbench_notes=0\nroot_state_surface_checks=10\nroot_projects=1\nroot_runs=1\nroot_reports=1\nroot_plans=1\nroot_search_records=1\nroot_site_exports=1\nroot_compare_profiles=1\nroot_audit_records=5\nroot_context_records=348\nroot_project_id=lab-gene-x\nroot_run_id=RUN-042\nroot_report_id=RUN-042-recovery-report\nroot_plan_id=PLAN-RUN-042-RECOVER-1\nroot_search_id=search:1\nroot_site_id=site:1\nroot_compare_profile=agentcompare-default\nroot_audit_spoof_denied=1\nstudy_protocol_reproduction_checks=5\nproject_bundle_cache=ready\nartifact_provenance=3\nartifact_dossier_checks=4\nartifact_path_rebuild_files=6\nartifact_path_rebuild_steps=7\nknowledge_index_checks=22\nllm_transcript_checks=3\nllm_bridge_transcripts=90\nllm_bridge_requests=30\nllm_bridge_responses=30\nworkbench_delivery_checks=15\nusable_research_workbenches=5\nusable_research_templates=5\nusable_research_workspace_imports=5\nusable_research_workspace_inspections=5\nusable_research_workbench_answers=5\nusable_research_deliveries=6\nusable_research_studio_sessions=2\nusable_research_project_action_plans=15\nusable_research_project_deliveries=4\nusable_research_project_runbooks=15\nusable_research_project_evidence_audits=15\nusable_research_project_provenance_graphs=3\nusable_research_project_launches=3\nusable_research_project_release_gates=15\nusable_research_project_snapshots=15\nsearch_documents=1385\nprovenance_nodes=406\nprovenance_links=544\nevent_stream_records=6816\ncontext_records=348\nhost_workflow_artifacts=150\nusable_research_artifacts=429\nusable_research_runs=20\nusable_research_stages=168\nusable_research_messages=223\nusable_research_decisions=203\n",
    "rp_state_catalog": "host_state_keys=573\nnonzero_state_categories=70\nzero_state_categories=503\nrepresented_state_categories=573\nstate_catalog_checks=12\ncoverage_model=nonzero_records_preserved;zero_records_reserved;plain_user_space_files;agentos_kernel_target\nstatus=ready\n",
    "rp_startup": "quickstart=ready\nstartup_checks=8\noffline_runs_ready=1\ncloud_llm_ready=0\nprovider_health=offline:1,cloud:0,ready_cloud:0\nplatform_doctor=ready\ndoctor_checks=10\ndoctor_downloads=markdown,json\nworkspace_writable=pass\nstate_load=pass\ntemplate_provider=pass\nproject_launch=sample_ready\nrecommended_commands=startup_guide,platform_doctor,project_launch,open_research_studio\nagentos_adapter_hint=plain_files_now;kernel_context_later\nstatus=ready\n",
    "rp_runbooks": "service=runbooks\nrun_id=RUN-042\nrunbook_service_checks=16\nrunbook_templates=1\nrunbook_steps=7\nincident_triages=1\nrunbook_executions=1\nrunbook_exports=1\nworker_operation_records=6\nexecution_observer=rp_execobs\nworker_health=rp_worker\ntimeline_ref=rp_timeline\ntemplate=runbook-template:align-oom-recovery;steps=7;owner=recovery;status=ready\nincident=INC-RUN-042-ALIGN-OOM;triage=incident-triage:RUN-042:manual;failed_stage=align;reason=memory_limit;affected_artifacts=rp_artifact_manifest;status=closed\nexecution=runbook-execution:RUN-042:manual;template=runbook-template:align-oom-recovery;completed_steps=7;retry_stage=align;result=recovered;status=passed\nexport=runbook-export:RUN-042:manual;format=markdown;package=rp_package;evidence=rp_review_dashboard;status=ready\nworker_handoff=worker-a->recovery;queue_action=resume_after_review;failure_classification=resource_limit;status=ready\nagentos_adaptation=event_context,kernel_timeline,metadata_index,batch_recovery_tool;status=planned\nstatus=ready\n",
    "rp_projectrel": "service=project-delivery-review\nproject=lab-gene-x\nrun_id=RUN-042\nproject_delivery_checks=18\nproject_handoff_audits=1\nproject_runbooks=1\nproject_release_gates=1\nproject_snapshots=1\nproject_snapshot_comparisons=1\nproject_reproducibility_audits=1\nproject_provenance_graphs=1\npackage_intakes=1\npackage_indexes=1\nhandoff_audit=project-handoff-audit:lab-gene-x;decision=ready;required_actions=0;suggested_actions=2;status=ready\nproject_runbook=project-runbook:lab-gene-x;steps=7;browser_links=8;cli_commands=9;status=ready\nrelease_gate=project-release-gate:lab-gene-x;decision=release;checks=6;required_actions=0;suggested_actions=2;status=ready\nproject_snapshot=project-snapshot:lab-gene-x:1;files=11;present=11;missing=0;hash_records=11;changes=0;status=ready\nsnapshot_comparison=project-snapshot-comparison:lab-gene-x:latest;left=project-snapshot:lab-gene-x:0;right=project-snapshot:lab-gene-x:1;changed_files=0;decision=stable;status=ready\nreproducibility_audit=project-reproducibility-audit:lab-gene-x;inputs=2;outputs=8;notebooks=2;claim_audits=1;decision=passed;status=ready\nprovenance_graph=project-provenance-graph:lab-gene-x;nodes=9;edges=12;dot=project-provenance.dot;status=ready\nproject_delivery=project-delivery:lab-gene-x;decision=ready;bundle=project-bundle.zip;release_gate=release;handoff=ready;status=ready\npackage_intake=package-intake:external-review;label=External review package;decision=accepted;files=5;sha256=checked;status=ready\npackage_index=project-package-index;handoff=ready;release_gate=release;snapshot=stable;reproducibility=passed;provenance=ready;status=ready\nsource_files=rp_package,rp_release,rp_dossier,rp_web_bundle,rp_review_dashboard,rp_runbooks\nagentos_adaptation=file_metadata_index,event_delivery,context_release_evidence,capability_guard;status=planned\nstatus=ready\n",
    "rp_studyproto": "service=study-protocols\nproject=lab-gene-x\nrun_id=RUN-042\nstudy_protocol_checks=20\nstudy_protocols=2\nstudy_protocol_launches=2\nstudy_protocol_runs=1\nstudy_protocol_compliance_reports=1\nstudy_protocol_bundles=1\nstudy_protocol_reproduction_packages=1\nreproduction_reviews=1\nreproduction_action_plans=1\nreproduction_action_executions=1\ndataset_portfolios=1\nsource_portfolios=1\ndataset_cards=1\ndataset_visualizations=1\ndataset_answers=1\nlaunch=study-protocol-launch:lab-gene-x:RUN-042;protocol=variant-calling-qc;criteria=6;agents=4;status=ready\nlaunch=study-protocol-launch:lab-gene-x:RUN-042-rerun;protocol=variant-calling-qc;criteria=6;agents=4;status=ready\nrerun=study-protocol-rerun:lab-gene-x:RUN-042;source=RUN-042;result=stable;status=passed\ncomparison=study-protocol-launch-comparison:RUN-042;left=launch:RUN-042;right=launch:RUN-042-rerun;changed_metrics=0;status=passed\nreproduction_package=study-protocol-reproduction-package:RUN-042;files=8;notebooks=2;datasets=2;status=ready\nreview=study-protocol-reproduction-review:RUN-042;decision=approved;required_actions=0;suggested_actions=2;status=ready\naction_plan=study-protocol-reproduction-action-plan:RUN-042;steps=5;owner=recovery;status=ready\naction_execution=study-protocol-reproduction-action-execution:RUN-042;steps_done=5;result=passed;status=ready\ndataset_portfolio=dataset-portfolio:lab-gene-x;datasets=2;cards=1;visualizations=1;answers=1;status=ready\nsource_portfolio=source-portfolio:lab-gene-x;sources=42;reviewed=8;exports=2;status=ready\nagentos_adaptation=file_metadata_index,context_protocol_evidence,event_reproduction_queue,batch_dataset_tool;status=planned\nstatus=ready\n",
    "rp_opsboard": "service=research-operations\nproject=lab-gene-x\nrun_id=RUN-042\noperations_board_checks=18\nprovider_health=offline:1,cloud:0,ready_cloud:0\npending_reviews=1\nreproduction_package_actions=1\nactive_workbench_actions=4\nactive_plan_items=5\nactive_action_items=4\nready_handoffs=3\nlatest_runs=4\nlatest_delivery=ready\noperations_reports=1\nadvance_next_actions=1\nexecute_next_plan_items=1\nexport_formats=2\ndashboard_pages=1\noperation_summary=research-ops:RUN-042;pending_reviews=1;ready_handoffs=3;latest_runs=4;status=ready\nqueue=workbench-queue:RUN-042;items=4;next=delivery_manifest;status=ready\nplan_queue=workbench-plan-queue:RUN-042;items=5;next=build_delivery_manifest;status=ready\naction_item=project-action:RUN-042:review-pack;owner=reviewer;priority=high;status=ready\naction_item=project-action:RUN-042:delivery-manifest;owner=writer;priority=high;status=waiting\naction_item=project-action:RUN-042:protocol-reproduction;owner=recovery;priority=normal;status=ready\naction_item=project-action:RUN-042:release-check;owner=auditor;priority=normal;status=ready\nadvance_result=operations-advance-next:RUN-042;selected=delivery_manifest;effect=rp_package;status=ready\nexecute_result=operations-execute-plan:RUN-042;selected=build_delivery_manifest;effect=rp_runner;status=ready\nreport_export=research-ops-report:RUN-042;formats=markdown,json;source=rp_runner,rp_package,rp_review_dashboard;status=ready\nhandoff=ops->reviewer;artifact=rp_review_dashboard;status=ready\nhandoff=ops->recovery;artifact=rp_runbooks;status=ready\nhandoff=ops->auditor;artifact=rp_projectrel;status=ready\nhandoff=review-board->operations;artifact=rp_reviewboard;status=ready\nhandoff=control-plane->operations;artifact=rp_control;status=ready\nsource_files=rp_startup,rp_runner,rp_package,rp_review_dashboard,rp_runbooks,rp_projectrel,rp_studyproto\nagentos_adaptation=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor;status=planned\nstatus=ready\n",
    "rp_reviewboard": "service=formal-review-board\nproject=lab-gene-x\nrun_id=RUN-042\nreview_board_checks=24\nreview_boards=1\nreview_requests=1\nreview_votes=4\nreview_signoffs=4\nreview_blockers=0\nreview_decisions=1\nreview_assignments=4\nreview_filters=2\nreview_workloads=4\nreview_escalations=0\ndecision=approved\nboard=review-board:final-release;chair=wang;members=4;status=active\nrequest=review-request:RUN-042:release-dossier;target=release-dossier:RUN-042:final-review;roles=4;status=approved\nvote=review-vote:RUN-042:methods;reviewer=auditor;role=methods_reviewer;decision=approve;status=recorded\nvote=review-vote:RUN-042:data;reviewer=data-steward;role=data_reviewer;decision=approve;status=recorded\nvote=review-vote:RUN-042:systems;reviewer=systems-reviewer;role=systems_reviewer;decision=approve;status=recorded\nvote=review-vote:RUN-042:chair;reviewer=wang;role=release_chair;decision=approve;status=recorded\nsignoff=review-signoff:RUN-042:methods;signer=auditor;role=methods_reviewer;decision=signed;status=recorded\nsignoff=review-signoff:RUN-042:data;signer=data-steward;role=data_reviewer;decision=signed;status=recorded\nsignoff=review-signoff:RUN-042:systems;signer=systems-reviewer;role=systems_reviewer;decision=signed;status=recorded\nsignoff=review-signoff:RUN-042:chair;signer=wang;role=release_chair;decision=signed;status=recorded\ndecision_record=review-board-decision:RUN-042:release;approvals=4;rejections=0;blockers_open=0;missing_roles=0;missing_signoffs=0;status=approved\nassignment=review-assignment:RUN-042:methods;reviewer=auditor;role=methods_reviewer;priority=medium;status=done\nassignment=review-assignment:RUN-042:data;reviewer=data-steward;role=data_reviewer;priority=medium;status=done\nassignment=review-assignment:RUN-042:systems;reviewer=systems-reviewer;role=systems_reviewer;priority=medium;status=done\nassignment=review-assignment:RUN-042:chair;reviewer=wang;role=release_chair;priority=high;status=done\nfilter=review-filter:auditor-open;owner=auditor;results=0;status=ready\nfilter=review-filter:wang-overdue;owner=wang;results=0;status=ready\nworkload=review-workload:auditor;open=0;overdue=0;high=0;status=ready\nworkload=review-workload:data-steward;open=0;overdue=0;high=0;status=ready\nworkload=review-workload:systems-reviewer;open=0;overdue=0;high=0;status=ready\nworkload=review-workload:wang;open=0;overdue=0;high=0;status=ready\nreview_package=formal-review-board-package:RUN-042;files=rp_dossier,rp_review_dashboard,rp_package,rp_opsboard;status=ready\nagentos_adaptation=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding;status=planned\nstatus=ready\n",
    "rp_control": "service=platform-control-plane\nproject=lab-gene-x\nrun_id=RUN-042\ncontrol_plane_checks=30\napprovals=4\napproval_transitions=4\nsubscriptions=3\nnotifications=4\nrun_queue_items=4\nleases=2\nplugin_manifests=3\nplugin_runs=3\nworkspaces=1\nusers=3\naccess_grants=3\nsaved_views=2\napi_tokens=1\npermissions=5\ncontrol_actions=8\napproval=approval:release-dossier:1;target=release-dossier:RUN-042;state=draft;actor=writer;status=recorded\napproval=approval:release-dossier:2;target=release-dossier:RUN-042;state=submitted;actor=writer;status=recorded\napproval=approval:release-dossier:3;target=release-dossier:RUN-042;state=approved;actor=wang;status=recorded\napproval=approval:release-dossier:4;target=release-dossier:RUN-042;state=published;actor=wang;status=recorded\nsubscription=sub:review:wang:APPROVAL_STATE;target=wang;event=APPROVAL_STATE;status=active\nsubscription=sub:review:auditor:QUEUE_ITEM_FINISHED;target=auditor;event=QUEUE_ITEM_FINISHED;status=active\nsubscription=sub:ops:writer:*;target=writer;event=*;status=active\nnotification=notif:1;target=wang;event=APPROVAL_STATE;delivered=1;status=ready\nnotification=notif:2;target=auditor;event=QUEUE_ITEM_FINISHED;delivered=1;status=ready\nnotification=notif:3;target=writer;event=RUN_LEASED;delivered=1;status=ready\nnotification=notif:4;target=writer;event=PLUGIN_RUN;delivered=1;status=ready\nqueue=queue:RUN-042:1;run=RUN-042;priority=90;state=done;worker=orchestrator;status=ready\nqueue=queue:RUN-042:2;run=RUN-042-review;priority=80;state=leased;worker=reviewer;status=ready\nqueue=queue:RUN-042:3;run=RUN-042-package;priority=70;state=queued;worker=none;status=ready\nqueue=queue:RUN-042:4;run=RUN-042-audit;priority=60;state=done;worker=auditor;status=ready\nplugin=plugin.artifacts;name=Artifact Analytics;tools=artifact_count_by_status;enabled=1;status=ready\nplugin=plugin.failures;name=Failure Summaries;tools=stage_failure_summary;enabled=1;status=ready\nplugin=plugin.tuning;name=Parameter Tuning;tools=recommend_memory_limit;enabled=1;status=ready\nplugin_run=plugin-run:1;plugin=plugin.artifacts;tool=artifact_count_by_status;result=ok;status=ready\nplugin_run=plugin-run:2;plugin=plugin.failures;tool=stage_failure_summary;result=ok;status=ready\nplugin_run=plugin-run:3;plugin=plugin.tuning;tool=recommend_memory_limit;current=1024;recommended=1536;status=ready\nworkspace=ws:lab-gene-x;owner=wang;projects=1;status=ready\nuser=user:wang;roles=maintainer;status=ready\nuser=user:auditor;roles=auditor;status=ready\nuser=user:guest;roles=viewer;status=ready\ngrant=grant:wang:lab-gene-x:maintainer;subject=wang;object=lab-gene-x;role=maintainer;status=ready\ngrant=grant:auditor:lab-gene-x:auditor;subject=auditor;object=lab-gene-x;role=auditor;status=ready\ngrant=grant:guest:lab-gene-x:viewer;subject=guest;object=lab-gene-x;role=viewer;status=ready\nsaved_view=view:failed-artifacts;kind=artifacts;query=status=failed;owner=wang;status=ready\nsaved_view=view:planned-jobs;kind=jobs;query=status=planned;owner=wang;status=ready\napi_token=token:local-dashboard;owner=wang;scopes=read,dashboard;secret_material=not_written;status=ready\npermission=can:wang:approve;result=allow;status=ready\npermission=can:wang:admin;result=allow;status=ready\npermission=can:auditor:audit;result=allow;status=ready\npermission=can:guest:write;result=deny;status=ready\npermission=can:guest:approve;result=deny;status=ready\ncontrol_report=platform-control-report:RUN-042;approvals=4;notifications=4;queue_items=4;plugin_runs=3;status=ready\nagentos_adaptation=kernel_capability_check,kernel_event_delivery,kernel_plugin_tool_table,kernel_run_queue;status=planned\nstatus=ready\n",
    "rp_artifact_manifest": (
        "record=1;kind=input;path=rp_input_fastq;status=ready\n"
        "record=3;kind=alignment;path=rp_artifact;section=rp_align_table;status=ready\n"
        "dossier=artifact-detail;source=rp_artifact;stage_log=rp_stage_log;chart=rp_chart_data;review_pack=rp_review_pack;status=ready\n"
        "dossier_check=workflow_stage;source=rp_stage_state;stage=align;status=recovered\n"
        "dossier_check=review_gate;source=rp_review_dashboard;gate=artifact_manifest;status=pass\n"
        "dossier_check=llm_quality;source=rp_llmeval;status=host_checked\n"
        "artifact_review_path=raw_to_report;input=rp_input_fastq;prepared=rp_artifact:rp_normalized_fastq;artifact=rp_artifact:rp_align_table;report=rp_report_text;review=rp_review_dashboard;status=ready\n"
        "artifact_review_path=quality_to_package;metrics=rp_artifact:rp_metrics_json;chart=rp_chart_data;llm_quality=rp_llmeval;delivery=rp_package;status=ready\n"
        "artifact_review_path=recovery_to_review;failure=rp_stage_log;retry=rp_retry_plan;event=rp_run_events:4;manifest=rp_artifact_manifest;review_pack=rp_review_pack;status=recovered\n"
        "manifest_records=4\n"
    ),
    "rp_stage_log": "log=align first_attempt status=failed reason=tool_output_missing\nhost_artifact_log=clean.log;stage=clean;level=warn;message=adapter_trimmed\n",
    "rp_chart_data": "chart=stage_attempts\nhost_artifact_chart=qc-chart.json;type=line;data_file=clean.metrics.json;points=12\n",
    "rp_input": "dynamic_submissions=4\n",
    "rp_ingest_files": (
        "run_id=RUN-042\n"
        "files=2\n"
        "file=1;path=rp_input_fastq;kind=fastq;records=2;bytes=72;status=ready\n"
        "file=2;path=rp_samples;kind=sample_sheet;records=4;bytes=128;status=ready\n"
        "derived_items=5\n"
        "host_file_manifest=mf.json\n"
        "host_file_manifest_files=11\n"
        "host_file_manifest_sha_records=11\n"
        "status=ready\n"
    ),
    "rp_dataset_snapshot": (
        "dataset=lab-gene-x-input\n"
        "snapshots=2\n"
        "snapshot=raw;files=2;records=6;status=ready\n"
        "snapshot=normalized;files=2;records=6;transform=normalize_fastq;normalized_fastq=rp_artifact:rp_normalized_fastq;status=ready\n"
        "status=ready\n"
    ),
    "rp_data_preview": (
        "previews=2\n"
        "preview=fastq;rows=2;columns=4;source=rp_artifact:rp_normalized_fastq;status=ready\n"
        "preview=samples;rows=4;columns=4;source=rp_samples;status=ready\n"
        "derived_preview=alignment;rows=2;columns=3;source=rp_artifact:rp_align_table;status=ready\n"
        "status=ready\n"
    ),
    "rp_data_quality": (
        "dataset=lab-gene-x-input\n"
        "rules=7\n"
        "passed=7\n"
        "failed=0\n"
        "metrics_section=rp_artifact:rp_metrics_json\n"
        "decision=accepted\n"
        "host_file_manifest=mf.json\n"
        "host_file_verify=passed\n"
        "host_file_verify_verified=11\n"
        "host_file_verify_missing=0\n"
        "status=ready\n"
    ),
    "rp_data_transform": (
        "transforms=2\n"
        "transform=normalize_fastq;input=rp_input_fastq;output=rp_dataset_snapshot;status=ready\n"
        "transform=join_sample_sheet;input=rp_samples;output=rp_dataset_collection;status=ready\n"
        "derived=alignment;input=rp_artifact:rp_normalized_fastq;output=rp_artifact:rp_align_table;status=ready\n"
        "derived=metrics;input=rp_artifact:rp_align_table;output=rp_artifact:rp_metrics_json,rp_artifact:rp_gene_counts_csv;status=ready\n"
        "status=ready\n"
    ),
    "rp_dataset_collection": (
        "collection=lab-gene-x-run042-analysis\n"
        "items=4\n"
        "item=raw_fastq;source=rp_input_fastq;status=ready\n"
        "item=samples;source=rp_samples;status=ready\n"
        "item=counts;source=rp_artifact:rp_gene_counts_csv;status=ready\n"
        "item=artifact;source=rp_artifact;status=ready\n"
        "host_file_manifest=mf.json\n"
        "host_file_verified_items=11\n"
        "status=ready\n"
    ),
    "rp_review_dashboard": (
        "dashboard=research-review\n"
        "run=RUN-042\n"
        "sections=8\n"
        "section=workflow;source=rp_stage_dag,rp_stage_state,rp_run_events,rp_retry_plan;status=recovered\n"
        "section=artifacts;source=rp_artifact,rp_artifact_manifest,rp_report_text,rp_chart_data;status=ready\n"
        "section=llm;source=rp_llm_req,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;status=ready\n"
        "gate=required_files;status=pass;source=rp_package\n"
        "gate=artifact_manifest;status=pass;source=rp_artifact_manifest\n"
        "gate=llm_packet_guard;status=pass;source=rp_llm_guard\n"
        "handoff=orchestrator->reviewer;artifact=rp_review_dashboard;status=ready\n"
        "subsection=formal_review_board;source=rp_reviewboard;votes=4;signoffs=4;decision=approved;status=ready\n"
        "decision=ready_for_reviewer;basis=required_files,human_review,llm_packet_guard,workflow_recovered\n"
        "backend_review_evidence=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;review_pack=rp_review_pack;status=ready\n"
        "status=ready\n"
    ),
    "rp_review_pack": (
        "pack=review-evidence\n"
        "evidence=artifact_manifest;source=rp_artifact_manifest;records=4;status=pass\n"
        "evidence=llm_quality;source=rp_llmeval;passed=7;status=pass\n"
        "evidence=delivery_ready;source=rp_package;files=8;status=pass\n"
        "evidence=operations_ready;source=rp_runner;status=pass\n"
        "evidence=project_space_ready;source=rp_package;status=pass\n"
        "backend_evidence_review=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;source=rp_review_dashboard;status=ready\n"
        "backend_action_review=plain-ucore;action=record;review=baseline;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;status=passed\n"
        "backend_action_review=retry-recovery;action=rerun_align;review=recovered;plain_cost=retry_file_stage_file;agentos_replace=event_context;status=passed\n"
        "backend_action_review=agentos-context;action=kernel_context;review=target;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;status=planned\n"
        "action=send_to_reviewer;owner=orchestrator;artifact=rp_review_pack;status=ready\n"
        "action=open_operations_report;owner=orchestrator;artifact=rp_runner;status=ready\n"
        "bridge=delivery_to_operations;delivery=rp_package;operations=rp_runner;project=rp_package;status=ready\n"
        "operations_handoff=rp_runner+rp_package;tasks=9;next=delivery_manifest;report=exported;plan=executed;quality=checked;repair=done;backend=rp_backend_exec;status=ready\n"
        "workbench_handoff=rp_runner+rp_package;workbench=W1;task=human_review;task_status=waiting;manifest=mf.json;verified=11;missing=0;bundle=wb.zip;status=ready\n"
        "project_handoff=rp_package;project=lab-gene-x;space=ready;note=recorded;action_item=created;answer=generated;repair=executed;search=ready;status=ready\n"
        "status=ready\n"
    ),
    "rp_llm_req": "host_relay_request=q1;route=review_summary;provider=template;prompt_hash=abc;source=rp_llmq\n",
    "rp_llm_resp": "host_relay_process=plain_ucore_llm_relay;mode=template;requests=1;responses=1;status=ready\nhost_relay_response=relay-q1;request=q1;summary=ready;citations=5;status=ok\n",
    "rp_llmeval": "host_relay_eval_batch=checked:6;passed:6;blocked:0;status=ready\nhost_relay_eval=q1;response=relay-q1;checks=6;passed=6;status=passed\n",
    "rp_llm_guard": "host_relay_guard_batch=checked:1;blocked:0;secret_values_written=0;status=ready\nhost_relay_guard=q1;prompt_hash=abc;secret_ref=host_env;secret_in_packet=0;status=passed\n",
    "rp_llmlog": "transcripts=90\nbridge_requests=30\nbridge_responses=30\nstatus=ready\n",
    "rp_relay": "host_relay_replay_batch=requests:1;responses:1;matched:1;status=ready\nhost_relay_replay=q1;response=relay-q1;prompt_hash=abc;mode=template;status=passed\n",
    "rp_prompt": "host_relay_prompt_batch=routes:1;requests:1;status=ready\nhost_relay_prompt_route=q1;route=review_summary;budget=1024;prompt_hash=abc;status=tracked\n",
    "rp_llm_packets": "host_relay_packet=q1;response=relay-q1;prompt_hash=abc;secret_in_packet=0;status=ok\n",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as state_tmp, tempfile.TemporaryDirectory() as out_tmp:
        state_dir = Path(state_tmp)
        out_dir = Path(out_tmp)
        for name, text in STATE_FILES.items():
            (state_dir / name).write_text(text, encoding="utf-8")

        summary = plain_ucore_reader.render_site(state_dir, out_dir)
        assert summary["status"] == "ready", summary
        assert summary["pages"] == 20, summary
        assert (out_dir / "index.html").exists()
        assert (out_dir / "run.html").exists()
        assert (out_dir / "workflow.html").exists()
        assert (out_dir / "workbench.html").exists()
        assert (out_dir / "project.html").exists()
        assert (out_dir / "project-review.html").exists()
        assert (out_dir / "review.html").exists()
        assert (out_dir / "delivery.html").exists()
        assert (out_dir / "data.html").exists()
        assert (out_dir / "llm.html").exists()
        assert (out_dir / "api" / "rp_api_home.json").exists()
        index_html = (out_dir / "index.html").read_text(encoding="utf-8")
        assert "Plain uCore Research" in index_html
        assert "State Files" in index_html
        assert "Dynamic Inputs" in index_html
        run_html = (out_dir / "run.html").read_text(encoding="utf-8")
        assert "Research Output" in run_html
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
        assert "workflow_run" in run_html
        assert "plain-c-runner" in run_html
        assert "worker-2" in run_html
        assert "Queue Depth" in run_html
        assert "Backend Evidence" in run_html
        assert "Backend Evidence In Report" in run_html
        assert "Backend Evidence In Runner" in run_html
        assert "Backend Case Narratives" in run_html
        assert "Report Source Map" in run_html
        assert "run_setup" in run_html
        assert "host_report_run_id=RUN-042" in run_html
        assert "host_workflow_run_id=R1" in run_html
        assert "host_relay_response=relay-q1" in run_html
        assert "host_workflow_run_id" in run_html
        assert "Linked Sources" in run_html
        assert "rp_llm_req,rp_llm_packets,rp_llmeval,rp_llm_guard" in run_html
        assert "Operations Report Narrative" in run_html
        assert "Operations Source Files" in run_html
        assert "operations_report" in run_html
        assert "rp_review_pack" in run_html
        assert "operations_handoff" in run_html
        assert "Run Action Trace" in run_html
        assert "Run Action Output Links" in run_html
        assert "Run Action Output Details" in run_html
        assert "Run Action Impact" in run_html
        assert "Run Action Delta" in run_html
        assert "batch_tool_context" in run_html
        assert "execution_plan:pass:record:baseline" in run_html
        assert "context_path:planned:kernel_context:target" in run_html
        assert "risks" in run_html
        workflow_html = (out_dir / "workflow.html").read_text(encoding="utf-8")
        assert "Workflow Runner" in workflow_html
        assert "Workflow Execution View" in workflow_html
        assert "Workflow Control View" in workflow_html
        assert "Workflow Evidence Links" in workflow_html
        assert "workflow_run" in workflow_html
        assert "R1" in workflow_html
        assert "plain-c-runner" in workflow_html
        assert "cache_decision" in workflow_html
        assert "retry_decision" in workflow_html
        assert "stage_evidence" in workflow_html
        assert "rp_artifact_manifest" in workflow_html
        workbench_html = (out_dir / "workbench.html").read_text(encoding="utf-8")
        assert "Research Workbench" in workbench_html
        assert "Workbench Task State" in workbench_html
        assert "Workbench Writing Outputs" in workbench_html
        assert "Workbench File Package" in workbench_html
        assert "Workbench Review Board" in workbench_html
        assert "W1" in workbench_html
        assert "WB1" in workbench_html
        assert "Ready?" in workbench_html
        assert "Scope" in workbench_html
        assert "markdown" in workbench_html
        assert "delivery-manifest.json" in workbench_html
        assert "workbench-bundle.zip" in workbench_html
        data_html = (out_dir / "data.html").read_text(encoding="utf-8")
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
        assert "rp_input_fastq" in data_html
        assert "normalize_fastq" in data_html
        assert "rp_artifact:rp_align_table" in data_html
        assert "mf.json" in data_html
        assert "host_file_verify" in data_html
        project_html = (out_dir / "project.html").read_text(encoding="utf-8")
        assert "Project Space" in project_html
        assert "Project Handoff" in project_html
        assert "Project Evidence Package" in project_html
        assert "Project Package Records" in project_html
        assert "Project Quality And Repair" in project_html
        assert "Project Search And Notes" in project_html
        assert "Project Source Files" in project_html
        assert "lab-gene-x" in project_html
        assert "host_action_project_id" in project_html
        assert "host_action_project_space" in project_html
        assert "host_action_project_answer" in project_html
        assert "host_action_quality_gate" in project_html
        assert "host_action_quality_repair_execute" in project_html
        assert "host_action_research_search" in project_html
        assert "project_followup" in project_html
        assert "Project Delivery" in project_html
        assert "rp_projectrel" in project_html
        assert "project_delivery_checks" in project_html
        assert "Study Protocols" in project_html
        assert "rp_studyproto" in project_html
        assert "study_protocol_checks" in project_html
        operations_html = (out_dir / "operations.html").read_text(encoding="utf-8")
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
        review_board_html = (out_dir / "review-board.html").read_text(encoding="utf-8")
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
        control_plane_html = (out_dir / "control-plane.html").read_text(encoding="utf-8")
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
        project_review_html = (out_dir / "project-review.html").read_text(encoding="utf-8")
        assert "Project Delivery Review" in project_review_html
        assert "Project Release Gate" in project_review_html
        assert "Project Snapshots" in project_review_html
        assert "Project Reproducibility Audit" in project_review_html
        assert "Project Provenance Graph" in project_review_html
        assert "Project Delivery Report" in project_review_html
        assert "project-reproducibility-audit:lab-gene-x" in project_review_html
        assert "package-intake:external-review" in project_review_html
        assert "Project Package Index" in project_review_html
        assert "Study Launches" in project_review_html
        assert "study-protocol-reproduction-package:RUN-042" in project_review_html
        assert "release" in project_review_html
        assert "project-provenance.dot" in project_review_html
        assert "project-bundle.zip" in project_review_html
        compare_html = (out_dir / "compare.html").read_text(encoding="utf-8")
        assert "Compare Summary" in compare_html
        assert "Compare Metrics" in compare_html
        assert "Compare Action Trace" in compare_html
        assert "Compare Action Output Details" in compare_html
        assert "Compare Action Impact" in compare_html
        assert "Compare Action Delta" in compare_html
        assert "File Scans" in compare_html
        assert "Portability Checks" in compare_html
        assert "Backend Checks" in compare_html
        assert "Backend Runner" in compare_html
        assert "Study Protocol Checks" in compare_html
        assert "study_protocol_checks" in compare_html
        assert "operations_board_checks" in compare_html
        assert "review_board_checks" in compare_html
        assert "control_plane_checks" in compare_html
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
        assert "plain_ucore" in compare_html
        assert "Backend Scenario Handoff" in compare_html
        assert "rp_backend_exec" in compare_html
        assert "rp_agentcmp" in compare_html
        assert "128" in compare_html
        agents_html = (out_dir / "agents.html").read_text(encoding="utf-8")
        assert "Agent Detail" in agents_html
        assert "Agent Roster" in agents_html
        assert "Decision Flow" in agents_html
        assert "Handoff Flow" in agents_html
        assert "orchestrator" in agents_html
        assert "rerun_align_only" in agents_html
        assert "Handoffs" in agents_html
        assert "rp_handoff" in agents_html
        evidence_html = (out_dir / "evidence.html").read_text(encoding="utf-8")
        assert "Evidence Detail" in evidence_html
        assert "Claim Records" in evidence_html
        assert "Provenance Paths" in evidence_html
        assert "Evidence Protocol Files" in evidence_html
        assert "Artifact Review Path" in evidence_html
        assert "raw_to_report" in evidence_html
        assert "retrylog-a" in evidence_html
        assert "Evidence Protocol" in evidence_html
        assert "usable-evidence-protocol:RUN-900:1" in evidence_html
        review_html = (out_dir / "review.html").read_text(encoding="utf-8")
        assert "Review Dashboard" in review_html
        assert "Review Sections" in review_html
        assert "Review Gates" in review_html
        assert "Review Evidence Pack" in review_html
        assert "Review Source Map" in review_html
        assert "Delivery Source Map" in review_html
        assert "delivery_file=report_md" in review_html
        assert "host_report_run_id=RUN-042" in review_html
        assert "record=1;kind=input;path=rp_input_fastq;status=ready" in review_html
        assert "host_relay_eval_batch=checked:6;passed:6;blocked:0;status=ready" in review_html
        assert "runner_case=plain-ucore" in review_html
        assert "Review Backend Evidence" in review_html
        assert "Review Backend Actions" in review_html
        assert "backend_evidence_review" in review_html
        assert "backend_review_evidence" in review_html
        assert "rerun_align" in review_html
        assert "kernel_context_path" in review_html
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
        assert "Review Action Output Details" in review_html
        assert "Review Action Impact" in review_html
        assert "Review Action Delta" in review_html
        assert "Handoff Checks" in review_html
        assert "send_to_reviewer" in review_html
        assert "delivery_to_operations" in review_html
        assert "delivery_manifest" in review_html
        assert "lab-gene-x" in review_html
        assert "mf.json" in review_html
        assert "ready_for_reviewer" in review_html
        services_html = (out_dir / "services.html").read_text(encoding="utf-8")
        assert "Service Execution" in services_html
        assert "Service Operation Records" in services_html
        assert "sample_lookup" in services_html
        assert "schedule_assess" in services_html
        assert "fair_package" in services_html
        assert "query_answer" in services_html
        assert "worker_heartbeat" in services_html
        assert "Runbook Steps" in services_html
        assert "Operations Checks" in services_html
        assert "runbook-template:align-oom-recovery" in services_html
        assert "Bio Service Files" in services_html
        delivery_html = (out_dir / "delivery.html").read_text(encoding="utf-8")
        assert "Delivery Package" in delivery_html
        assert "Delivery Files" in delivery_html
        assert "Delivery Package Records" in delivery_html
        assert "Delivery Source Map" in delivery_html
        assert "Review Pack Delivery" in delivery_html
        assert "Workbench Delivery" in delivery_html
        assert "delivery_files=8" in delivery_html
        assert "delivery_file=report_md" in delivery_html
        assert "host_report_run_id=RUN-042" in delivery_html
        assert "reviewer-evidence" in delivery_html
        assert "delivery-manifest.json" in delivery_html
        assert "plan&gt;data&gt;review&gt;repair&gt;audit" in evidence_html
        assert "Plain Kernel Signals" in compare_html
        assert "Consistency Signals" in compare_html
        artifacts_html = (out_dir / "artifacts.html").read_text(encoding="utf-8")
        assert "Evidence Package" in artifacts_html
        assert "Artifact Manifest Records" in artifacts_html
        assert "Path Steps" in artifacts_html
        assert "Artifact Dossier" in artifacts_html
        assert "Derived Artifact Sections" in artifacts_html
        assert "Artifact Provenance" in artifacts_html
        assert "Artifact Review Path" in artifacts_html
        assert "Artifact Source Map" in artifacts_html
        assert "Delivery Source Map" in artifacts_html
        assert "delivery_file=report_md" in artifacts_html
        assert "host_report_run_id=RUN-042" in artifacts_html
        assert "quality_to_package" in artifacts_html
        assert "recovery_to_review" in artifacts_html
        assert "section=rp_align_table" in artifacts_html
        assert "align first_attempt status=failed reason=tool_output_missing" in artifacts_html
        assert "host_relay_eval_batch=checked:6;passed:6;blocked:0;status=ready" in artifacts_html
        assert "Dossier Checks" in artifacts_html
        assert "Archive Files" in artifacts_html
        assert "Stage Logs" in artifacts_html
        assert "Review And LLM Signals" in artifacts_html
        assert "Host Artifact Actions" in artifacts_html
        assert "Operations Source Files" in artifacts_html
        assert "rp_align_table" in artifacts_html
        assert "rp_retry_plan" in artifacts_html
        assert "artifact_manifest" in artifacts_html
        assert "host_relay_eval_batch" in artifacts_html
        assert "host_artifact_chart" in artifacts_html
        studio_html = (out_dir / "studio.html").read_text(encoding="utf-8")
        assert "Research Studio" in studio_html
        assert "Studio Sessions" in studio_html
        assert "Studio Materials" in studio_html
        assert "Studio Links" in studio_html
        assert "Studio Host Actions" in studio_html
        assert "Studio cytokine evidence" in studio_html
        assert "Determine whether recovery evidence is ready" in studio_html
        assert "usable-research-studio-session:W1:1" in studio_html
        assert "/download/research-studio-session/usable-research-studio-session-W1-1" in studio_html
        actions_html = (out_dir / "actions.html").read_text(encoding="utf-8")
        assert "Batch Actions" in actions_html
        assert "Action Output Links" in actions_html
        assert "Action Output Details" in actions_html
        assert "Action Impact" in actions_html
        assert "Action Delta" in actions_html
        assert "/actions/research/run" in actions_html
        llm_html = (out_dir / "llm.html").read_text(encoding="utf-8")
        assert "LLM Relay" in llm_html
        assert "Relay Quality" in llm_html
        assert "Quality Checks" in llm_html
        assert "Delivery Checks" in llm_html
        assert "LLM Relay Flow" in llm_html
        assert "rp_llm_req,rp_llm_packets,rp_llm_resp" in llm_html
        assert "LLM Action Trace" in llm_html
        assert "LLM Action Output Links" in llm_html
        assert "LLM Action Output Details" in llm_html
        assert "LLM Action Impact" in llm_html
        assert "LLM Action Delta" in llm_html
        assert "host_relay_eval_batch" in llm_html
        assert "relay-q1" in llm_html

        saved = json.loads((out_dir / "reader-summary.json").read_text(encoding="utf-8"))
        assert saved["contract"]["contract"] == "host_plain_ucore_v2"
        assert saved["contract"]["missing_payload_files"] == []
        assert saved["contract"]["missing_refresh_files"] == []
        assert saved["status"] == "ready"
        assert saved["action_count"] == 0

        handler = plain_ucore_reader.make_service_handler(
            state_dir,
            out_dir,
            write_state=True,
            auto_run_ucore=True,
            repo_dir=Path("."),
            run_root=out_dir / "auto-runs",
            runner_module=FakeRunner,
            auto_llm_relay=True,
            llm_relay_mode="template",
            llm_relay_module=FakeRelay,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with request.urlopen(base + "/api/contract", timeout=5) as response:
                contract = json.loads(response.read().decode("utf-8"))
            assert contract["contract"]["contract"] == "host_plain_ucore_v2"

            with request.urlopen(base + "/api/state/rp_api_home", timeout=5) as response:
                home = json.loads(response.read().decode("utf-8"))
            assert home["values"]["api"] == "home"

            with request.urlopen(base + "/api/state/rp_studio", timeout=5) as response:
                studio = json.loads(response.read().decode("utf-8"))
            assert any(line == "studio=usable-research-studio" for line in studio["lines"])
            assert any("studio_session=usable-research-studio-session:W1:1" in line for line in studio["lines"])

            action = request.Request(
                base + "/actions/research/run",
                data=json.dumps({"run_id": "RUN-999", "source": "test"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(action, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
            assert result["action"]["status"] == "accepted"
            assert result["action"]["path"] == "/actions/research/run"
            assert result["run"]["status"] == "ready"
            assert result["relay"]["status"] == "ready"
            assert result["relay"]["mode"] == "template"
            assert (out_dir / "host-actions.jsonl").exists()
            assert "path=/actions/research/run" in (state_dir / "rp_host_action_inbox").read_text(encoding="utf-8")
            assert "qemu_orch_passed=1" in (state_dir / "rp_host_run_result").read_text(encoding="utf-8")
            assert "host_relay_process=fake" in (state_dir / "rp_llm_resp").read_text(encoding="utf-8")
            assert (out_dir / "last-run.json").exists()
            assert (out_dir / "llm-relay-summary.json").exists()

            with request.urlopen(base + "/api/live", timeout=5) as response:
                live = json.loads(response.read().decode("utf-8"))
            assert live["action_count"] == 1
            assert live["last_run"]["status"] == "ready"

            batch = request.Request(
                base + "/actions/batch",
                data=json.dumps(
                    {
                        "actions": [
                            {"path": "/actions/research/review", "payload": {"decision": "needs_revision"}},
                            {"path": "/actions/research/export-bundle", "payload": {"bundle": "evidence"}},
                        ]
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(batch, timeout=5) as response:
                batch_result = json.loads(response.read().decode("utf-8"))
            assert len(batch_result["actions"]) == 2, batch_result
            assert batch_result["actions"][0]["sequence"] == 2, batch_result
            assert batch_result["actions"][1]["path"] == "/actions/research/export-bundle", batch_result
            assert batch_result["run"]["status"] == "ready", batch_result

            with request.urlopen(base + "/api/live", timeout=5) as response:
                live = json.loads(response.read().decode("utf-8"))
            assert live["action_count"] == 3, live
            assert "path=/actions/research/export-bundle" in (state_dir / "rp_host_action_inbox").read_text(encoding="utf-8")
            actions_html = (out_dir / "actions.html").read_text(encoding="utf-8")
            assert "Host Actions" in actions_html
            assert "Action Output Links" in actions_html
            assert "Action Output Details" in actions_html
            assert "Action Impact" in actions_html
            assert "Action Delta" in actions_html
            assert "rp_package" in actions_html
            assert "host_action_export_bundle_name=reviewer-evidence" in actions_html
            assert "report_section" in actions_html
            assert "/actions/research/export-bundle" in actions_html
            review_html = (out_dir / "review.html").read_text(encoding="utf-8")
            assert "Review Action Trace" in review_html
            assert "Review Action Output Links" in review_html
            assert "Review Action Output Details" in review_html
            assert "Review Action Impact" in review_html
            assert "Review Action Delta" in review_html
            assert "Operations Report Narrative" in review_html
            assert "Report Source Map" in review_html
            assert "Review Source Map" in review_html
            assert "Delivery Source Map" in review_html
            assert "delivery_file=report_md" in review_html
            assert "backend_evidence_report" in review_html
            assert "review_gate" in review_html
            assert "/actions/research/review" in review_html
            assert "/actions/research/export-bundle" in review_html
            run_html = (out_dir / "run.html").read_text(encoding="utf-8")
            assert "Run Action Trace" in run_html
            assert "Run Action Output Links" in run_html
            assert "Run Action Output Details" in run_html
            assert "Run Action Impact" in run_html
            assert "Run Action Delta" in run_html
            assert "Report Source Map" in run_html
            assert "rp_report_text" in run_html
            assert "run_setup" in run_html
            assert "artifact_path" in run_html
            assert "/actions/research/export-bundle" in run_html
            compare_html = (out_dir / "compare.html").read_text(encoding="utf-8")
            assert "Compare Action Trace" in compare_html
            assert "Compare Action Output Links" in compare_html
            assert "Compare Action Output Details" in compare_html
            assert "Compare Action Impact" in compare_html
            assert "Compare Action Delta" in compare_html

            bad_batch = request.Request(
                base + "/actions/batch",
                data=json.dumps({"actions": [{"path": "/not-an-action", "payload": {}}]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                request.urlopen(bad_batch, timeout=5)
                raise AssertionError("bad batch unexpectedly accepted")
            except Exception as exc:
                assert getattr(exc, "code", None) == 400, exc

            with request.urlopen(base + "/index.html", timeout=5) as response:
                index_html = response.read().decode("utf-8")
            assert "Rendered from plain uCore state files" in index_html
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("test_plain_ucore_reader: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
