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
reader_views=40
reader_actions=123
reader_payload_files=rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_bio,rp_api_labres,rp_api_pub,rp_api_know,rp_api_runtime,rp_api_action,rp_api_catalog,rp_web_routes
reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_api_catalog,rp_studio,rp_web_bundle
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
publication_page=rp_publication;peer_response=rp_peerresp;status=ready
calculations_page=rp_calculation;jobs=1;retrieved=3;parser_results=1;status=ready
real_task_page=rp_realtask;dataset=palmer-penguins;rows=344;answer_audit=pass;status=ready
analysis_results_page=rp_analysisres;runs=2;tables=2;statistics=2;figures=2;status=ready
decision_support_page=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=ready
usable_research_page=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;queues=2;status=ready
usable_project_page=rp_usableproj;scaffolds=3;launches=2;bundles=2;doctor=pass;status=ready
experiment_campaigns_page=rp_campaign;campaigns=1;trials=4;best_trial=04;status=ready
statistical_design_page=rp_stdesign;designs=1;power=underpowered;randomization=balanced;status=ready
model_registry_page=rp_modelreg;models=1;versions=1;evaluations=1;deployments=1;status=ready
systematic_review_page=rp_sysreview;protocols=1;screening=9;included=3;status=ready
experiment_schedule_page=rp_expsched;schedules=1;tasks=3;bookings=4;conflicts=1;status=ready
training_compliance_page=rp_traincomp;requirements=4;records=4;gaps=1;auth=3;status=ready
release_dossier_page=rp_reldossier;sections=7;decision=ready_for_review;status=ready
mature_capability_page=rp_mature;profiles=6;mappings=6;checks=72;status=ready
provenance_page=rp_prov_view;timeline_views=4;subgraphs=3;packets=4;status=ready
provenance_queries_page=rp_prov_query;specs=3;executions=3;packets=1;status=ready
status=ready
""",
    "rp_web_routes": "routes=141\nget_routes=18\npost_routes=123\nroute=/research-studio;payload=rp_studio;status=ready\nroute=/research/project/{id}/review;payload=rp_web_bundle;status=ready\nroute=/api-catalog;payload=rp_api_catalog;status=ready\naction=/actions/research/studio-launch;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/dataset-preview;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/study-protocol-launch;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-space-review;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-space-task-board-row;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-scaffold;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-launch;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-action-execute;method=POST;payload=rp_api_action;status=ready\naction=/actions/research/project-release-gate;method=POST;payload=rp_api_action;status=ready\nstatus=ready\n",
    "rp_api_home": "api=home\nreader_contract=rp_web_bundle\nstatus=ready\n",
    "rp_api_run": "api=run-detail\nreader_contract=rp_web_bundle\nreader_view=run-detail\nstatus=ready\n",
    "rp_api_agents": "api=agent-detail\nagents=7\nstatus=ready\n",
    "rp_api_evidence": "api=evidence-detail\nclaims=8\nstatus=ready\n",
    "rp_api_compare": "api=compare-metrics\nplain_kernel=passed\nfile_scans=128\nstate_convention=1\nuser_permission_only=1\ncontext_trusted=0\nrebuild_steps=6\nstatus=ready\n",
    "rp_api_artifacts": "api=artifacts\nmanifest_records=4\nstatus=ready\n",
    "rp_api_data": "api=data\ndataset_snapshots=2\npreviews=2\nquality_checks=7\ntransforms=2\ncollection_items=4\nhost_action_file_manifest=mf.json\nhost_action_file_verify=passed\nhost_action_file_verified=11\nhost_action_file_missing=0\nstatus=ready\n",
    "rp_api_bio": "api=bio\nsample_registry=rp_sreg\nstatus=ready\n",
    "rp_api_labres": "api=lab-resources\ninstrument_registry=rp_instr\nstatus=ready\n",
    "rp_api_pub": "api=publication\nresult_review=rp_resrev\npublication_workflow=rp_publication;targets=2;submissions=2;reviews=2;responses=2;status=ready\nstatus=ready\n",
    "rp_api_know": "api=knowledge\nsemantic_index=rp_semindex\nstatus=ready\n",
    "rp_api_runtime": "api=runtime\nruntime_env=rp_runenv\nstatus=ready\n",
    "rp_api_action": "api=actions\nreader_contract=rp_web_bundle\nactions=123\nresearch_studio_launch=/actions/research/studio-launch\ndataset_preview=/actions/research/dataset-preview\ndataset_run=/actions/research/dataset-run\nstudy_protocol_launch=/actions/research/study-protocol-launch\nstudy_protocol_reproduction_package_action_execute=/actions/research/study-protocol-reproduction-package-action-execute\nproject_space_review=/actions/research/project-space-review\nproject_space_task_board_row=/actions/research/project-space-task-board-row\nproject_scaffold=/actions/research/project-scaffold\nproject_launch=/actions/research/project-launch\nproject_action_execute=/actions/research/project-action-execute\nproject_release_gate=/actions/research/project-release-gate\nproject_lifecycle_actions=3\ndataset_actions=8\nstudy_protocol_actions=11\nproject_space_actions=7\nproject_review_actions=8\nstatus=ready\n",
    "rp_api_catalog": "api=catalog\nhost_api_routes=214\nhost_action_routes=95\nreader_api_payloads=15\nreader_views=40\napi_group_count=14\napi_grouped_routes=214\nusable_research_api_routes=77\ndomain_api_routes=50\nlab_research_api_routes=15\nworkflow_api_routes=12\ndata_api_routes=10\napi_group=usable_research;routes=77;state=rp_usable,rp_usableproj,rp_studyproto;status=ready\napi_group=domain;routes=50;state=rp_analysisres,rp_backend,rp_decsupport;status=ready\napi_group=lab_research;routes=15;state=rp_lab,rp_sysreview,rp_expsched;status=ready\napi_group=workflow;routes=12;state=rp_stage_state,rp_wfio,rp_backend_exec;status=ready\napi_key=/api/analysis-results;group=domain;state=rp_analysisres;status=ready\napi_key=/api/experiment-scheduling;group=lab_research;state=rp_expsched;status=ready\napi_key=/api/workflow-runner;group=workflow;state=rp_workflow_runner;status=ready\napi_key=/api/usable-research-workbench-file-catalog;state=rp_runner,rp_package;status=ready\napi_key=/api/usable-research-study-protocol-reproduction-package-action-plan;state=rp_studyproto,rp_usablepack;status=ready\napi_key=/api/llm-proxy;state=rp_prompt,rp_llm_guard;status=ready\nreader_projection=host_api_catalog_to_plain_ucore_state_files\nstatus=ready\n",
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
    "rp_pubop": "ops=6\nop=result_review;items=10;status=ok\nop=fair_package;checks=8;status=ok\nop=publication_workflow;submissions=2;reviews=2;responses=2;decisions=2;status=ok\n",
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
        "model_registry=rp_modelreg;version=v1;evaluation=passed;deployment=ready;status=ready\n"
        "analysis_results=rp_analysisres;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;status=ready\n"
        "decision_support=rp_decsupport;options=3;criteria=5;scores=15;selected=agentos_ucore_hybrid;status=ready\n"
        "usable_research=rp_usable;templates=3;datasets=3;library_sources=3;dag_stages=9;deliverables=8;status=ready\n"
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
        "test_cases=2800\n"
        "tool_events=328\n"
        "handoffs=6\n"
        "review_handoff_checks=13;review_sections=8;review_gates=6;review_decisions=4;review_handoffs=3;review_pack_actions=3;review_pack_bridges=4;backend_review=1;status=ready\n"
        "review_pack=ready;evidence_items=11;actions=5;plain_kernel=ordinary_files;backend_evidence=1\n"
        "runbook_recovery_checks=16;templates=1;steps=7;incident_triages=1;executions=1;exports=1;worker_records=6;agentos_replacements=4;status=ready\n"
        "project_delivery_checks=18;handoff_audits=1;project_runbooks=1;release_gates=1;snapshots=1;snapshot_comparisons=1;reproducibility_audits=1;provenance_graphs=1;package_intakes=1;package_indexes=1;agentos_replacements=4;status=ready\n"
        "study_protocol_checks=20;protocols=2;launches=2;runs=1;compliance_reports=1;bundles=1;reproduction_packages=1;reproduction_reviews=1;action_plans=1;action_executions=1;dataset_portfolios=1;source_portfolios=1;dataset_cards=1;visualizations=1;answers=1;agentos_replacements=4;status=ready\n"
        "operations_board_checks=18;pending_reviews=1;reproduction_actions=1;workbench_actions=4;plan_items=5;action_items=4;handoffs=3;latest_runs=4;exports=2;agentos_replacements=4;status=ready\n"
        "review_board_checks=24;boards=1;requests=1;votes=4;signoffs=4;assignments=4;workloads=4;filters=2;decision=approved;agentos_replacements=4;status=ready\n"
        "control_plane_checks=30;approvals=4;notifications=4;queue_items=4;plugins=3;workspaces=1;permissions=5;agentos_replacements=4;status=ready\n"
        "integrity_plane_checks=36;evidence_contracts=8;reference_contracts=8;namespace_checks=5;status_checks=5;review_alignment_checks=4;report_source_checks=3;package_trace_checks=3;agentos_replacements=4;status=ready\n"
        "coherence_plane_checks=40;delivery_contracts=7;run_state_contracts=7;lifecycle_contracts=6;workflow_lint=5;tool_protocol=5;report_validation=5;agent_coordination=3;agentos_replacements=4;status=ready\n"
        "publication_checks=48;targets=2;submissions=2;review_rounds=2;revision_tasks=3;response_packages=2;response_items=4;decisions=2;agentos_replacements=4;status=ready\n"
        "calculation_checks=84;computers=1;codes=1;jobs=1;retrieved=3;parser_results=1;exports=1;agentos_replacements=4;status=ready\n"
        "real_task_checks=96;dataset=palmer-penguins;rows=344;numeric_fields=5;answer_audit=pass;bundle=ready;status=ready\n"
        "analysis_results_checks=96;plans=1;runs=2;tables=2;statistics=2;figures=2;interpretations=2;charts=4;agentos_replacements=4;status=ready\n"
        "decision_support_checks=80;options=3;criteria=5;scores=15;review_packets=1;selected=agentos_ucore_hybrid;agentos_replacements=4;status=ready\n"
        "usable_research_checks=100;templates=3;datasets=3;library_sources=3;dag_stages=9;plan_queue=4;action_queue=5;handoffs=3;deliverables=8;status=ready\n"
        "usable_project_checks=120;scaffold_templates=3;project_launches=2;project_bundles=2;doctor_checks=10;status=ready\n"
        "experiment_campaign_checks=108;campaigns=1;trials=4;best_trial=04;result_review=accept_candidate;status=ready\n"
        "statistical_design_checks=120;designs=1;power=underpowered;randomization=balanced;blinding=ok;stat_result=approved_with_sample_size_note;status=ready\n"
        "model_registry_service_checks=96;models=1;versions=1;evaluations=1;deployments=1;serving_checks=1;agentos_replacements=4;status=ready\n"
        "systematic_review_checks=104;protocols=1;searches=1;screening=9;extractions=3;bias=3;prisma=1;agentos_replacements=4;status=ready\n"
        "experiment_scheduling_checks=88;schedules=1;tasks=3;bookings=4;conflicts=1;executions=2;charts=4;status=ready\n"
        "training_compliance_checks=92;requirements=4;training_records=4;competency=4;authorizations=3;gaps=1;open_gaps=0;charts=4;status=ready\n"
        "release_dossier_checks=112;sections=7;evidence_ids=18;decision=ready_for_review;status=ready\n"
        "mature_capability_checks=72;profiles=6;mappings=6;checks=72;platforms=Galaxy,AiiDA,DVC,MLflow,Nextflow,Snakemake;agentos_replacements=5;status=ready\n"
        "provenance_view_checks=64;timeline_views=4;subgraphs=3;packets=4;agentos_replacements=4;status=ready\n"
        "provenance_query_checks=72;specs=3;templates=1;executions=3;comparisons=1;exports=1;packets=1;agentos_replacements=4;status=ready\n"
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
    "rp_calculation": (
        "service=calculation\n"
        "run_id=RUN-042\n"
        "calculation_checks=84\n"
        "computers=1\n"
        "codes=1\n"
        "jobs=1\n"
        "submissions=1\n"
        "retrieved_files=3\n"
        "parser_results=1\n"
        "exports=1\n"
        "computer=calculation-computer:local-agentos;label=local-agentos-workdir;hostname=localhost;scheduler=direct;transport=local;status=active\n"
        "code=calculation-code:metadata-qc:v1;computer=calculation-computer:local-agentos;entry=agent_platform.calculations:metadata_qc;parser=metadata-qc-parser;version=1.0;status=active\n"
        "job=calculation-job:lab-gene-x:run042-qc;process_type=aiida.calculations:agent.metadata_qc;state=finished;exit_status=0;code=calculation-code:metadata-qc:v1;computer=calculation-computer:local-agentos\n"
        "job_inputs=dataset-collection:lab-gene-x:run042-analysis,data-transform-run:lab-gene-x:run042-normalize\n"
        "scheduler_record=calculation-submission:run042-qc;status=finished;attempts=1;command=metadata-qc\n"
        "status=ready\n"
    ),
    "rp_calc_files": (
        "job=calculation-job:lab-gene-x:run042-qc\n"
        "retrieved_files=3\n"
        "retrieved=calculation-retrieved:run042-qc:stdout-txt;path=stdout.txt;kind=retrieved_output;checksum=stdout042;status=available\n"
        "retrieved=calculation-retrieved:run042-qc:results-json;path=results.json;kind=retrieved_output;checksum=results042;status=available\n"
        "retrieved=calculation-retrieved:run042-qc:provenance-json;path=provenance.json;kind=provenance_manifest;checksum=prov042;status=available\n"
        "output_snapshot=dataset-snapshot:calculation:run042-qc;rows=3;files=3;status=ready\n"
        "status=ready\n"
    ),
    "rp_sysreview": (
        "service=systematic-review\n"
        "systematic_review_checks=104\n"
        "protocol=systematic-review:agent-os-science\n"
        "title=Agent-OS support for scientific Agent workflows\n"
        "research_question=Which platform mechanisms improve reliability, provenance, and reproducibility in scientific Agent workflows?\n"
        "population=Scientific computing and AI-for-science workflows\n"
        "intervention=Agent runtime support and kernel-managed context\n"
        "comparator=plain user-space workflow orchestration\n"
        "outcome=reproducibility,provenance_quality,failure_recovery,report_traceability\n"
        "owner=wang\n"
        "status=registered\n"
    ),
    "rp_syssearch": (
        "strategy=literature-search:agent-os-science:local\n"
        "protocol=systematic-review:agent-os-science\n"
        "source=local-literature-library\n"
        "query=agent workflow provenance reproducibility kernel\n"
        "results=9\n"
        "status=ready\n"
    ),
    "rp_sysscreen": (
        "screening_decisions=9\n"
        "title_abstract_included=3\n"
        "full_text_included=3\n"
        "excluded=6\n"
        "decision=paper:agent-kernel-context;stage=full_text;result=include;reason=kernel_context_support\n"
        "status=ready\n"
    ),
    "rp_sysextract": (
        "extractions=3\n"
        "risk_of_bias=3\n"
        "low=2\n"
        "some_concerns=1\n"
        "record=paper:agent-kernel-context;mechanism=context_path;evidence=systems_demo;outcome=traceability\n"
        "status=complete\n"
    ),
    "rp_syssynth": (
        "synthesis=evidence-synthesis:agent-os-science\n"
        "included_papers=3\n"
        "conclusion=kernel-managed context and accountable tool calls improve traceability\n"
        "confidence=moderate\n"
        "status=ready\n"
    ),
    "rp_sysprisma": (
        "flow=prisma-flow:agent-os-science\n"
        "identified=9\n"
        "screened=9\n"
        "excluded=6\n"
        "included=3\n"
        "status=ready\n"
    ),
    "rp_expsched": (
        "service=experiment-scheduling\n"
        "experiment_scheduling_checks=88\n"
        "schedules=1\n"
        "schedule=schedule:RUN-042:lab-execution\n"
        "project=lab-gene-x\n"
        "run_id=RUN-042\n"
        "title=RUN-042 controlled lab execution schedule\n"
        "owner=lab-ops\n"
        "status=approved\n"
    ),
    "rp_schedtask": (
        "tasks=3\n"
        "task=schedule-task:RUN-042:verify-resources;type=resource_check;target=sop-execution:RUN-042:library-prep;assignee=auditor;status=planned;evidence=eln-record:RUN-042:library-prep,sop-execution:RUN-042:library-prep\n"
        "task=schedule-task:RUN-042:library-prep;type=lab_operation;target=lab-op:RUN-042:library-prep;assignee=lab-tech;dependency=schedule-task:RUN-042:verify-resources;status=planned;evidence=lab-op:RUN-042:library-prep,artifact:report.md\n"
        "task=schedule-task:RUN-042:sop-review;type=sop_review;target=sop-deviation:RUN-042:library-prep:03;assignee=qa-lead;dependency=schedule-task:RUN-042:library-prep;status=planned;evidence=sop-deviation:RUN-042:library-prep:03,eln-check:RUN-042:library-prep:seed\n"
        "status=ready\n"
    ),
    "rp_schedbook": (
        "bookings=4\n"
        "booking=schedule-booking:RUN-042:seq-library;schedule=schedule:RUN-042:lab-execution;resource=instrument:seq-01;task=schedule-task:RUN-042:library-prep;start=100;end=160;status=reserved\n"
        "booking=schedule-booking:RUN-042:lab-tech;schedule=schedule:RUN-042:lab-execution;resource=person:lab-tech;task=schedule-task:RUN-042:library-prep;start=90;end=170;status=reserved\n"
        "booking=schedule-booking:RUN-042:qa-reviewer;schedule=schedule:RUN-042:lab-execution;resource=person:qa-lead;task=schedule-task:RUN-042:sop-review;start=170;end=200;status=reserved\n"
        "booking=schedule-booking:RUN-042:seq-overlap-demo;schedule=schedule:RUN-042:lab-execution;resource=instrument:seq-01;task=schedule-task:RUN-042:sop-review;start=130;end=150;status=conflict\n"
        "conflicts=1\n"
        "status=ready\n"
    ),
    "rp_schedconf": (
        "conflicts=1\n"
        "conflict=schedule-conflict:RUN-042:seq-01-overlap;schedule=schedule:RUN-042:lab-execution;booking=schedule-booking:RUN-042:seq-overlap-demo;resource=instrument:seq-01;severity=warning;status=detected;description=overlapping instrument request;resolution=reschedule_or_second_instrument\n"
        "status=ready\n"
    ),
    "rp_schedexec": (
        "execution_records=2\n"
        "execution=schedule-exec:RUN-042:verify-resources;task=schedule-task:RUN-042:verify-resources;status=completed;evidence=rp_ressched,rp_labresop;notes=resources_ready\n"
        "execution=schedule-exec:RUN-042:library-prep;task=schedule-task:RUN-042:library-prep;status=completed;evidence=rp_stage_log,rp_artifact;notes=operation_completed_after_retry\n"
        "status=ready\n"
    ),
    "rp_traincomp": (
        "service=training-compliance\n"
        "training_compliance_checks=92\n"
        "schedule=schedule:RUN-042:lab-execution\n"
        "project=lab-gene-x\n"
        "run_id=RUN-042\n"
        "requirements=4\n"
        "training_records=4\n"
        "competency_assessments=4\n"
        "role_authorizations=3\n"
        "training_gaps=1\n"
        "initial_open_gaps=1\n"
        "open_gaps=0\n"
        "resolved_gaps=1\n"
        "active_authorizations=3\n"
        "status=ready\n"
    ),
    "rp_trainreq": (
        "requirements=4\n"
        "requirement=training-req:sop-library-prep:lab-tech;role=lab-tech;topic=sop-library-prep;required_level=operator;source=schedule-task:RUN-042:library-prep;status=active\n"
        "requirement=training-req:instrument-seq-01:lab-tech;role=lab-tech;topic=seq-01;required_level=operator;source=schedule-booking:RUN-042:seq-library;status=active\n"
        "requirement=training-req:resource-check:auditor;role=auditor;topic=resource-check;required_level=reviewer;source=schedule-task:RUN-042:verify-resources;status=active\n"
        "requirement=training-req:sop-deviation:qa-lead;role=qa-lead;topic=sop-deviation;required_level=approver;source=schedule-task:RUN-042:sop-review;status=active\n"
        "status=ready\n"
    ),
    "rp_trainrec": (
        "records=4\n"
        "training=training:lab-tech:sop-library-prep;person=lab-tech;topic=sop-library-prep;level=operator;expires=210;status=valid;evidence=sop-cert:lab-tech:library-prep\n"
        "training=training:lab-tech:seq-01;person=lab-tech;topic=seq-01;level=operator;expires=190;status=valid;evidence=instrument-cert:seq-01:lab-tech\n"
        "training=training:auditor:resource-check;person=auditor;topic=resource-check;level=reviewer;expires=220;status=valid;evidence=resource-audit-cert:auditor\n"
        "training=training:qa-lead:sop-deviation;person=qa-lead;topic=sop-deviation;level=approver;expires=240;status=valid;evidence=deviation-review-cert:qa-lead\n"
        "status=ready\n"
    ),
    "rp_trainassess": (
        "assessments=4\n"
        "assessment=competency:lab-tech:sop-library-prep;person=lab-tech;topic=sop-library-prep;score=96;decision=pass;status=ready\n"
        "assessment=competency:lab-tech:seq-01;person=lab-tech;topic=seq-01;score=94;decision=pass;status=ready\n"
        "assessment=competency:auditor:resource-check;person=auditor;topic=resource-check;score=91;decision=pass;status=ready\n"
        "assessment=competency:qa-lead:sop-deviation;person=qa-lead;topic=sop-deviation;score=93;decision=pass;status=ready\n"
        "status=ready\n"
    ),
    "rp_trainauth": (
        "active_authorizations=3\n"
        "authorization=auth:lab-tech:lab-tech:lab-gene-x;person=lab-tech;role=lab-tech;project=lab-gene-x;requirements=2;decision=authorized;status=active\n"
        "authorization=auth:auditor:auditor:lab-gene-x;person=auditor;role=auditor;project=lab-gene-x;requirements=1;decision=authorized;status=active\n"
        "authorization=auth:qa-lead:qa-lead:lab-gene-x;person=qa-lead;role=qa-lead;project=lab-gene-x;requirements=1;decision=authorized;status=active\n"
        "status=ready\n"
    ),
    "rp_traingap": (
        "training_gaps=1\n"
        "gap=training-gap:schedule:RUN-042:lab-execution:schedule-task:RUN-042:sop-review:role_authorization:role-authorization:qa-lead;schedule=schedule:RUN-042:lab-execution;task=schedule-task:RUN-042:sop-review;person=qa-lead;type=role_authorization;severity=blocking;initial_status=open;status=resolved;resolution=qa-lead training, competency, and authorization completed\n"
        "open_gaps=0\n"
        "resolved_gaps=1\n"
        "status=ready\n"
    ),
    "rp_calc_parse": (
        "job=calculation-job:lab-gene-x:run042-qc\n"
        "parser_result=calculation-parser-result:run042-qc;parser=metadata-qc-parser;status=ok;output_snapshot=dataset-snapshot:calculation:run042-qc\n"
        "metric=input_count;value=2;status=ready\n"
        "metric=collection_items;value=4;status=ready\n"
        "metric=ready_ratio;value=1.00;status=ready\n"
        "warnings=0\n"
        "status=ready\n"
    ),
    "rp_calc_export": (
        "job=calculation-job:lab-gene-x:run042-qc\n"
        "export=calculation-export:lab-gene-x:run042-qc;type=markdown;path=calculation-export-run042-qc.md;checksum=calcexport042;status=ready\n"
        "package=calculation-package:lab-gene-x:run042-qc;files=3;parser_results=1;exports=1;status=ready\n"
        "reader_page=calculations.html\n"
        "status=ready\n"
    ),
    "rp_realtask": (
        "service=real-task-validation\n"
        "task=palmer-penguins-morphometrics\n"
        "question=bill-and-body-size-patterns-by-species-and-sex\n"
        "dataset=palmer-penguins\n"
        "run_id=RUN-PENGUINS-001\n"
        "real_task_checks=96\n"
        "input_files=3\n"
        "references=3\n"
        "provider=deepseek\n"
        "provider_secret_persisted=0\n"
        "workbench_status=delivered\n"
        "readiness=ready\n"
        "answer_audit=pass\n"
        "project_bundle=ready\n"
        "status=ready\n"
    ),
    "rp_realdata": (
        "dataset=palmer-penguins\n"
        "rows=344\n"
        "columns=8\n"
        "numeric_fields=5\n"
        "metric_group_summaries=5\n"
        "metric_dimension_group_summaries=10\n"
        "categorical_fields=island,sex\n"
        "missing_sex_labels=present\n"
        "source_files=penguins.csv,references.bib,notes.md\n"
        "data_quality=accepted\n"
        "status=ready\n"
    ),
    "rp_realreport": (
        "report=palmer-penguins-report\n"
        "llm_provider=deepseek\n"
        "answer_source=report_md\n"
        "raw_llm_packet=trace_only\n"
        "claim_audit=pass\n"
        "answer_audit=pass\n"
        "limitations=missing_sex_labels,observational_data,causal_caution\n"
        "citations=3\n"
        "status=ready\n"
    ),
    "rp_realbundle": (
        "bundle=palmer-penguins-project-bundle\n"
        "duplicate_zip_entries=0\n"
        "package_files=project_bundle,report,analysis,claim_audit,answer_audit\n"
        "offline_review=ready\n"
        "http_checks=4\n"
        "status=ready\n"
    ),
    "rp_analysisres": (
        "service=analysis-results\n"
        "analysis_results_checks=96\n"
        "project=lab-gene-x\n"
        "run_id=RUN-042\n"
        "analysis_plans=1\n"
        "analysis_runs=2\n"
        "result_tables=2\n"
        "statistical_results=2\n"
        "figures=2\n"
        "interpretations=2\n"
        "manual_run=analysis-run:RUN-042:manual\n"
        "status=ready\n"
    ),
    "rp_anplan": (
        "plans=1\n"
        "plan=analysis-plan:RUN-042:treatment-response;project=lab-gene-x;run_id=RUN-042;dataset=expression-qc;question=treatment-response;status=ready\n"
        "status=ready\n"
    ),
    "rp_anrun": (
        "runs=2\n"
        "run=analysis-run:RUN-042:treatment-response;plan=analysis-plan:RUN-042:treatment-response;mode=workflow;input=rp_calculation;output=rp_resulttbl;status=complete\n"
        "run=analysis-run:RUN-042:manual;plan=analysis-plan:RUN-042:treatment-response;mode=manual-qc;input=rp_resulttbl;output=rp_interp;status=complete\n"
        "status=ready\n"
    ),
    "rp_resulttbl": (
        "tables=2\n"
        "table=result-table:RUN-042:gene-summary;run=analysis-run:RUN-042:treatment-response;rows=12;columns=6;export=gene-summary.csv;status=ready\n"
        "table=result-table:manual;run=analysis-run:RUN-042:manual;rows=4;columns=5;export=manual-qc.csv;status=ready\n"
        "status=ready\n"
    ),
    "rp_statres": (
        "statistics=2\n"
        "stat=stat-result:RUN-042:treatment-vs-control;run=analysis-run:RUN-042:treatment-response;method=welch-t-test;p_value=0.031;effect=moderate;status=ready\n"
        "stat=stat-result:manual;run=analysis-run:RUN-042:manual;method=effect-size-check;p_value=na;effect=consistent;status=ready\n"
        "status=ready\n"
    ),
    "rp_anfig": (
        "figures=2\n"
        "figure=figure:RUN-042:treatment-response;run=analysis-run:RUN-042:treatment-response;kind=bar-with-ci;path=figures/treatment-response.svg;status=ready\n"
        "figure=figure:manual;run=analysis-run:RUN-042:manual;kind=qc-table-snapshot;path=figures/manual-qc.svg;status=ready\n"
        "status=ready\n"
    ),
    "rp_interp": (
        "interpretations=2\n"
        "interpretation=interpretation:RUN-042:treatment-response;run=analysis-run:RUN-042:treatment-response;conclusion=treatment response has moderate support;reviewer=Analyst;status=ready\n"
        "interpretation=interpretation:manual;run=analysis-run:RUN-042:manual;conclusion=Manual QC analysis is ready for review.;reviewer=Reviewer;status=ready\n"
        "status=ready\n"
    ),
    "rp_decsupport": (
        "service=decision-support\n"
        "decision_support_checks=80\n"
        "decision=decision:agentos-final-demo-backend\n"
        "target=comparative-study:RUN-042:agentos-readiness\n"
        "options=3\n"
        "criteria=5\n"
        "scores=15\n"
        "review_packets=1\n"
        "recommended_option=agentos_ucore_hybrid\n"
        "weighted_score_agentos_ucore_hybrid=8.15\n"
        "status=ready\n"
    ),
    "rp_decopt": (
        "options=3\n"
        "option=userland_only;benefit=replayable_baseline;cost=weak_os_argument;recommendation=baseline_arm;status=ready\n"
        "option=agentos_ucore_hybrid;benefit=direct_os_value;cost=syscall_adapter;recommendation=final_target;status=ready\n"
        "option=full_kernel_llm_path;benefit=max_kernel_ownership;cost=tls_dns_secret_risk;recommendation=reject_for_final_delivery;status=ready\n"
    ),
    "rp_deccrit": (
        "criteria=5\n"
        "criterion=agentos_value;weight=0.30;description=How directly the option proves OS-level Agent support.;status=ready\n"
        "criterion=reproducibility;weight=0.25;description=Replay without unstable cloud or host state.;status=ready\n"
    ),
    "rp_decscore": (
        "scores=15\n"
        "score=agentos_ucore_hybrid:agentos_value;option=agentos_ucore_hybrid;criterion=agentos_value;value=9;rationale=Kernel services carry Agent state.;status=ready\n"
        "score=userland_only:agentos_value;option=userland_only;criterion=agentos_value;value=2;rationale=Baseline only.;status=ready\n"
    ),
    "rp_decpacket": (
        "packet=decision-review-packet:agentos-final-demo-backend\n"
        "decision=decision:agentos-final-demo-backend\n"
        "recommended_option=agentos_ucore_hybrid\n"
        "option_scores=userland_only:5.35,agentos_ucore_hybrid:8.15,full_kernel_llm_path:4.55\n"
        "evidence=rp_backend_exec,rp_study,rp_llm_packets,rp_package,rp_reldossier\n"
        "status=ready\n"
    ),
    "rp_usable": (
        "service=usable-research\n"
        "usable_research_checks=100\n"
        "entry=research-question-to-review-package\n"
        "project=usable-project:lab-gene-x-final-demo\n"
        "run_id=usable-run:RUN-900\n"
        "templates=3\n"
        "datasets=3\n"
        "library_sources=3\n"
        "dag_stages=9\n"
        "plan_queue_rows=4\n"
        "action_queue_rows=5\n"
        "handoff_packages=3\n"
        "status=ready\n"
    ),
    "rp_usabletpl": (
        "templates=3\n"
        "template=usable-template:workspace-900;name=Reusable response comparison;question=Compare recovered workflow evidence and prepare a reviewer package.;tags=reusable,workflow,agent;status=ready\n"
        "selected_template=usable-template:workspace-900\n"
    ),
    "rp_usableds": (
        "datasets=3\n"
        "dataset=usable-dataset:penguins;rows=344;columns=8;tags=real-task,morphometrics;quality=accepted;preview=ready;status=ready\n"
    ),
    "rp_usablelib": (
        "library_sources=3\n"
        "source=usable-source:library2026:1;title=Agent workflow provenance;kind=reference;tags=agent,provenance;status=ready\n"
    ),
    "rp_usabledag": (
        "dag=usable-research-dag\n"
        "stage=package;order=9;depends=review;artifact=rp_package;agent=orchestrator;status=ready\n"
    ),
    "rp_usableops": (
        "operations=usable-research-workbench\n"
        "queue=workbench_action;rows=5;ready=2;needs_action=2;optional=1;status=ready\n"
        "handoff=usable-handoff:RUN-900:reviewer;files=8;required_missing=0;decision=ready;status=ready\n"
    ),
    "rp_usableproj": (
        "service=usable-project-lifecycle\n"
        "usable_project_checks=120\n"
        "configuration=usable-research-config:offline-template\n"
        "scaffold_templates=3\n"
        "scaffold_files=8\n"
        "project_launches=2\n"
        "project_bundles=2\n"
        "platform_doctor_checks=10\n"
        "operations_digest_sections=6\n"
        "status=ready\n"
    ),
    "rp_usableboot": (
        "config=usable-research-config:offline-template;provider=template;cloud_key_required=0;status=ready\n"
        "doctor=usable-platform-doctor:1;checks=10;passed=10;failed=0;warnings=0;status=ready\n"
    ),
    "rp_usablescaf": (
        "templates=3\n"
        "template=scaffold-template:protocol-reproduction;files=8;includes=protocol,launch,runs,comparison,review,actions,bundle,manifest;status=ready\n"
        "scaffold=scaffold:lab-gene-x:starter;project=lab-gene-x;files=8;importable=1;status=ready\n"
        "file=inputs/dataset.csv;kind=data;rows=4;status=ready\n"
    ),
    "rp_usablelaunch": (
        "launches=2\n"
        "launch=usable-project-launch:lab-gene-x:1;scaffold=scaffold:lab-gene-x:starter;workbench=usable-workbench:RUN-900;run=usable-run:RUN-900;status=ready\n"
        "operation=operations_digest;sections=6;pending_reviews=1;active_actions=5;handoffs=3;status=ready\n"
    ),
    "rp_usablepack": (
        "bundles=2\n"
        "bundle=usable-project-bundle:lab-gene-x;project=lab-gene-x;files=14;manifest=project-package-index;download=ready;status=ready\n"
        "bundle=usable-study-protocol-reproduction-package:RUN-042;files=8;notebooks=2;datasets=2;review=approved;status=ready\n"
        "intake=usable-package-intake:external-review;files=5;sha256=checked;decision=accepted;status=ready\n"
    ),
    "rp_campaign": (
        "service=experiment-campaigns\n"
        "run_id=RUN-042\n"
        "campaign_checks=108\n"
        "campaign=experiment-campaign:RUN-042:align-memory-grid\n"
        "parameter_space=memory_mb:1024,1536;threads:2,4\n"
        "trials=4\n"
        "best_trial=experiment-trial:RUN-042:align-memory-grid:04\n"
        "selection_metric=qc_pass_rate\n"
        "result_review=accept_candidate\n"
        "status=ready\n"
    ),
    "rp_trials": (
        "trial_count=4\n"
        "trial=experiment-trial:RUN-042:align-memory-grid:01;memory_mb=1024;threads=2;qc_pass_rate=94;runtime=38;status=complete\n"
        "trial=experiment-trial:RUN-042:align-memory-grid:02;memory_mb=1024;threads=4;qc_pass_rate=95;runtime=34;status=complete\n"
        "trial=experiment-trial:RUN-042:align-memory-grid:03;memory_mb=1536;threads=2;qc_pass_rate=96;runtime=37;status=complete\n"
        "trial=experiment-trial:RUN-042:align-memory-grid:04;memory_mb=1536;threads=4;qc_pass_rate=97;runtime=31;status=selected\n"
        "status=ready\n"
    ),
    "rp_camp_rank": (
        "comparison=experiment-campaign-comparison:RUN-042:align-memory-grid\n"
        "ranked_trials=4\n"
        "best_trial=experiment-trial:RUN-042:align-memory-grid:04\n"
        "metric_delta=3\n"
        "decision=select_trial_04\n"
        "status=ready\n"
    ),
    "rp_resreview": (
        "review=experiment-result-review:RUN-042:baseline-vs-candidate\n"
        "baseline=experiment-trial:RUN-042:align-memory-grid:01\n"
        "candidate=experiment-trial:RUN-042:align-memory-grid:04\n"
        "metric_deltas=qc_pass_rate:+3,runtime:-7\n"
        "parameter_changes=memory_mb:+512,threads:+2\n"
        "artifact_changes=rp_trials,rp_camp_rank\n"
        "decision=accept_candidate\n"
        "status=ready\n"
    ),
    "rp_stdesign": (
        "service=statistical-design\n"
        "statistical_design_checks=120\n"
        "design=stat-design:lab-gene-x:run042-primary\n"
        "project=lab-gene-x\n"
        "run_id=RUN-042\n"
        "study=study:lab-gene-x-treatment-response\n"
        "hypothesis=hypothesis:RUN-042:align-memory\n"
        "primary_endpoint=candidate_genes_mean\n"
        "comparison=treatment_vs_control\n"
        "effect_size=1.25\n"
        "alpha=0.05\n"
        "target_power=0.80\n"
        "allocation_ratio=1.00\n"
        "status=ready\n"
    ),
    "rp_power": (
        "analysis=power-analysis:lab-gene-x:run042-primary\n"
        "design=stat-design:lab-gene-x:run042-primary\n"
        "method=two_sample_normal_approximation\n"
        "required_per_group=11\n"
        "required_total=22\n"
        "actual_min_group_size=2\n"
        "achieved_power=0.239\n"
        "status=underpowered\n"
    ),
    "rp_random": (
        "randomization=randomization:lab-gene-x:run042-primary\n"
        "design=stat-design:lab-gene-x:run042-primary\n"
        "arms=control,treatment\n"
        "strata=batch\n"
        "seed=RUN-042-deterministic\n"
        "assignments=4\n"
        "assignment=S-001:control\n"
        "assignment=S-002:treatment\n"
        "assignment=S-003:control\n"
        "assignment=S-004:treatment\n"
        "balance=arms:control:2,treatment:2\n"
        "status=balanced\n"
    ),
    "rp_blind": (
        "blinding=blinding-check:lab-gene-x:run042-primary\n"
        "design=stat-design:lab-gene-x:run042-primary\n"
        "blinded_roles=reporter,auditor,statistician\n"
        "unblinded_roles=lab-operator\n"
        "leaks=0\n"
        "status=ok\n"
    ),
    "rp_streview": (
        "review=stat-design-review:lab-gene-x:run042-primary\n"
        "design=stat-design:lab-gene-x:run042-primary\n"
        "reviewer=methodologist\n"
        "stat_result=approved_with_sample_size_note\n"
        "finding=current sample count is below planned power target\n"
        "evidence_ids=5\n"
        "export=stat-design-export:lab-gene-x:run042-primary\n"
        "export_type=markdown\n"
        "checksum=stdesign-md-042\n"
        "status=ready\n"
    ),
    "rp_modelreg": (
        "service=model-registry\n"
        "model_registry_service_checks=96\n"
        "registered_models=1\n"
        "model=registered-model:agent-triage-template\n"
        "name=agent-triage-template\n"
        "model_type=template-agent\n"
        "task=scientific workflow triage and report drafting\n"
        "owner=wang\n"
        "tags=agent,triage,research\n"
        "status=ready\n"
    ),
    "rp_modelver": (
        "version=model-version:agent-triage-template:v1\n"
        "model=registered-model:agent-triage-template\n"
        "source=rp_llm_packets\n"
        "model_card=rp_modelcard\n"
        "training_run=RUN-042\n"
        "artifacts=rp_report_text,rp_package,rp_llm_packets\n"
        "metric_artifact_count=52\n"
        "metric_prompt_eval_score=0.875\n"
        "status=staged\n"
    ),
    "rp_modeleval": (
        "evaluation=model-evaluation:agent-triage-template:v1:RUN-042\n"
        "version=model-version:agent-triage-template:v1\n"
        "dataset=dataset-snapshot:RUN-042:quality\n"
        "metric_evidence_coverage=1.000\n"
        "metric_report_status_ok=1.000\n"
        "metric_prompt_eval_score=0.875\n"
        "outputs=report:run-042-recovery-report:v1\n"
        "status=passed\n"
    ),
    "rp_modeldep": (
        "deployment=model-deployment:agent-triage-template:v1:template\n"
        "version=model-version:agent-triage-template:v1\n"
        "target_environment=template\n"
        "policy=offline_review_candidate\n"
        "check_model_card=ok\n"
        "check_evaluation=ok\n"
        "check_provider=ok\n"
        "check_secret_policy=not_required\n"
        "status=ready\n"
    ),
    "rp_modelserve": (
        "serving_check=model-serving-check:agent-triage-template:v1:template\n"
        "deployment=model-deployment:agent-triage-template:v1:template\n"
        "provider=template\n"
        "latency_ms=12\n"
        "message=offline provider ready\n"
        "status=ok\n"
    ),
    "rp_reldossier": (
        "service=release-dossier\n"
        "release_dossier_checks=112\n"
        "dossier=release-dossier:RUN-042:final-review\n"
        "run_id=RUN-042\n"
        "candidate=release-candidate:RUN-042:final\n"
        "research_package=rp_package\n"
        "sections=7\n"
        "evidence_ids=18\n"
        "decision=ready_for_review\n"
        "checksum=rel-dossier-042\n"
        "status=ready\n"
    ),
    "rp_reldsec": (
        "dossier=release-dossier:RUN-042:final-review\n"
        "section=research-package;status=ok;summary=52_artifacts_75_checks;evidence=rp_package\n"
        "section=governance;status=ok;summary=release_gate_and_review_board_passed;evidence=rp_projectrel,rp_reviewboard\n"
        "section=publication;status=ok;summary=2_submissions_2_decisions;evidence=rp_publication\n"
        "section=data-release;status=ok;summary=fair_validation_and_data_version_ready;evidence=rp_datarel,rp_dataver\n"
        "section=experiment-campaign;status=ok;summary=1_campaign_4_trials_best_04;evidence=rp_campaign,rp_trials,rp_camp_rank,rp_resreview\n"
        "section=execution-evidence;status=ok;summary=4_packets_and_provenance_ready;evidence=rp_execobs,rp_prov_view,rp_prov_query\n"
        "section=agentos-readiness;status=ok;summary=backend_runner_reports_ready;evidence=rp_backend,rp_backend_exec,rp_study\n"
        "status=ready\n"
    ),
    "rp_relattest": (
        "dossier=release-dossier:RUN-042:final-review\n"
        "attestations=4\n"
        "attestation=review-board;status=accepted;source=rp_reviewboard\n"
        "attestation=integrity-plane;status=passed;source=rp_integrity\n"
        "attestation=coherence-plane;status=passed;source=rp_coherence\n"
        "attestation=publication;status=accepted;source=rp_publication\n"
        "status=ready\n"
    ),
    "rp_relpack": (
        "dossier=release-dossier:RUN-042:final-review\n"
        "package_files=2\n"
        "file=release-dossier.json;kind=json;checksum=reljson042;status=ready\n"
        "file=release-dossier.md;kind=markdown;checksum=relmd042;status=ready\n"
        "download=release-dossier-package:RUN-042\n"
        "status=ready\n"
    ),
    "rp_mature": (
        "service=mature-capability-map\n"
        "reference_platforms=6\n"
        "capability_mappings=6\n"
        "capability_checks=72\n"
        "profile_checks=6\n"
        "store_checks=24\n"
        "surface_checks=24\n"
        "ratio_checks=6\n"
        "errors=0\n"
        "warnings=0\n"
        "decision=passed\n"
        "coverage=workflow_history,process_graph,data_versioning,experiment_tracking,portable_workflows,rule_dag\n"
        "reference_platform=galaxy;name=Galaxy;concepts=history,dataset_collection,workflow_invocation;status=ready\n"
        "reference_platform=aiida;name=AiiDA;concepts=process_node,provenance_graph,calcjob;status=ready\n"
        "reference_platform=dvc;name=DVC;concepts=stage,dataset_hash,remote_cache;status=ready\n"
        "reference_platform=mlflow;name=MLflow;concepts=experiment,run,artifact_registry;status=ready\n"
        "reference_platform=nextflow;name=Nextflow;concepts=process,channel,resume_cache;status=ready\n"
        "reference_platform=snakemake;name=Snakemake;concepts=rule,input_output,dry_run;status=ready\n"
        "agentos_adaptation=kernel_reference_profile_index,kernel_capability_contracts,kernel_tool_binding_checks,kernel_evidence_projection;status=planned\n"
        "status=ready\n"
    ),
    "rp_mature_refs": (
        "profiles=6\n"
        "profile=reference-platform:galaxy;name=Galaxy;concepts=history,dataset_collection,workflow_invocation;status=ready\n"
        "profile=reference-platform:aiida;name=AiiDA;concepts=process_node,provenance_graph,calcjob;status=ready\n"
        "profile=reference-platform:dvc;name=DVC;concepts=stage,dataset_hash,remote_cache;status=ready\n"
        "profile=reference-platform:mlflow;name=MLflow;concepts=experiment,run,artifact_registry;status=ready\n"
        "profile=reference-platform:nextflow;name=Nextflow;concepts=process,channel,resume_cache;status=ready\n"
        "profile=reference-platform:snakemake;name=Snakemake;concepts=rule,input_output,dry_run;status=ready\n"
        "status=ready\n"
    ),
    "rp_mature_map": (
        "mappings=6\n"
        "agentos_targets=kernel_context_path,kernel_metadata_index,kernel_event_queue,batch_tool_runner,capability_contract_table\n"
        "mapping=galaxy-workflow-history;profile=galaxy;concept=history;services=run,artifact,review;state=rp_stage_state,rp_artifact_manifest,rp_review_dashboard;status=ready\n"
        "mapping=aiida-process-graph;profile=aiida;concept=provenance_graph;services=lineage,integrity,package;state=rp_lineage,rp_integrity,rp_package;status=ready\n"
        "mapping=dvc-dataflow;profile=dvc;concept=stage_cache;services=data_pipeline,manifest,cache;state=rp_data_pipeline,rp_artifact_manifest,rp_cache_index;status=ready\n"
        "mapping=mlflow-experiment-registry;profile=mlflow;concept=experiment_run;services=report,metrics,publication;state=rp_report_text,rp_metrics,rp_publication;status=ready\n"
        "mapping=nextflow-portable-workflow;profile=nextflow;concept=portable_workflow;services=wfio,backend,execution;state=rp_wfio,rp_backend_exec,rp_execobs;status=ready\n"
        "mapping=snakemake-rule-dag;profile=snakemake;concept=rule_dag;services=stage_dag,retry,coherence;state=rp_stage_dag,rp_retry_plan,rp_coherence;status=ready\n"
        "status=ready\n"
    ),
    "rp_mature_checks": (
        "checks=72\n"
        "ok=72\n"
        "warnings=0\n"
        "errors=0\n"
        "check=profile.galaxy;target=Galaxy;result=pass;status=ready\n"
        "check=profile.aiida;target=AiiDA;result=pass;status=ready\n"
        "check=profile.dvc;target=DVC;result=pass;status=ready\n"
        "check=profile.mlflow;target=MLflow;result=pass;status=ready\n"
        "check=profile.nextflow;target=Nextflow;result=pass;status=ready\n"
        "check=profile.snakemake;target=Snakemake;result=pass;status=ready\n"
        "check=surface.site;target=mature.html;result=pass;status=ready\n"
        "check=agentos.batch_runner;target=batch_tool_runner;result=planned;status=ready\n"
        "status=ready\n"
    ),
    "rp_prov_view": (
        "run_id=RUN-042\n"
        "provenance_view_checks=64\n"
        "timeline_views=4\n"
        "timeline_events=9\n"
        "subgraphs=3\n"
        "subgraph_edges=12\n"
        "evidence_packets=4\n"
        "decision_packets=3\n"
        "reader_page=provenance.html\n"
        "agentos_mapping=kernel_timeline,kernel_provenance_edges,kernel_ledger,context_detail\n"
        "status=ready\n"
    ),
    "rp_prov_edges": (
        "edges=12\n"
        "edge=1;source=rp_input;target=rp_stage_dag;kind=input_to_workflow;status=ready\n"
        "edge=6;source=rp_artifact_manifest;target=rp_report_text;kind=evidence_to_report;status=ready\n"
        "edge=12;source=rp_agent_run;target=rp_prov_view;kind=agent_to_trace;status=ready\n"
    ),
    "rp_evidence_packet": (
        "packets=4\n"
        "packet=workflow-recovery;run=RUN-042;sources=rp_stage_state,rp_retry_plan,rp_artifact_manifest;checks=16;status=ready\n"
        "packet=agentos-readiness;run=RUN-042;sources=rp_mature,rp_agentcmp,rp_backend_exec;checks=16;status=ready\n"
    ),
    "rp_timeline_view": (
        "views=4\n"
        "view=run_timeline;events=9;source=rp_timeline;status=ready\n"
        "view=agent_decision_flow;events=6;source=rp_agent_run;status=ready\n"
        "timeline_event=dossier;tick=42;actor=orchestrator;artifact=rp_review_pack;status=ready\n"
    ),
    "rp_prov_query": (
        "run_id=RUN-042\n"
        "provenance_query_checks=72\n"
        "specs=3\n"
        "templates=1\n"
        "executions=3\n"
        "comparisons=1\n"
        "exports=1\n"
        "evidence_packets=1\n"
        "projected_rows=14\n"
        "reader_page=provenance-queries.html\n"
        "agentos_mapping=timeline_query,provenance_snapshot,ledger_snapshot,context_detail\n"
        "status=ready\n"
    ),
    "rp_prov_specs": (
        "specs=3\n"
        "template=provenance-query-template:calculation-root-neighborhood;owner=auditor;direction=both;depth=2;params=root_id,query_name;status=ready\n"
        "spec=provenance-query:RUN-042:calculation-lineage;owner=auditor;root=calculation-job:lab-gene-x:run042-qc;direction=both;depth=2;status=ready\n"
        "spec=provenance-query:RUN-042:template-rendered-lineage;owner=auditor;root=calculation-job:lab-gene-x:run042-qc;template=calculation-root-neighborhood;direction=both;depth=2;status=ready\n"
    ),
    "rp_prov_exec": (
        "executions=3\n"
        "execution=provenance-query-execution:calculation-lineage;query=provenance-query:RUN-042:calculation-lineage;nodes=8;links=7;rows=8;status=ok\n"
        "execution=provenance-query-execution:template-rendered-lineage;query=provenance-query:RUN-042:template-rendered-lineage;nodes=8;links=7;rows=8;status=ok\n"
        "row=calculation-job:lab-gene-x:run042-qc;node_type=calculation_job;title=RUN-042 QC calculation;status=finished\n"
    ),
    "rp_prov_query_pkg": (
        "comparisons=1\n"
        "comparison=provenance-query-comparison:RUN-042:rendered-vs-direct;base=provenance-query-execution:calculation-lineage;candidate=provenance-query-execution:template-rendered-lineage;added=0;removed=0;row_delta=0;status=ok\n"
        "export=provenance-query-export:RUN-042:calculation-lineage;execution=provenance-query-execution:calculation-lineage;type=markdown;checksum=provquery042;status=ready\n"
        "packet=provenance-query-packet:RUN-042:lineage-review;comparison=provenance-query-comparison:RUN-042:rendered-vs-direct;executions=2;nodes=8;links=7;checksum=packet042;status=ready\n"
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
    "rp_opsboard": "service=research-operations\nproject=lab-gene-x\nrun_id=RUN-042\noperations_board_checks=18\nprovider_health=offline:1,cloud:0,ready_cloud:0\npending_reviews=1\nreproduction_package_actions=1\nactive_workbench_actions=4\nactive_plan_items=5\nactive_action_items=4\nready_handoffs=3\nlatest_runs=4\nlatest_delivery=ready\noperations_reports=1\nadvance_next_actions=1\nexecute_next_plan_items=1\nexport_formats=2\ndashboard_pages=1\noperation_summary=research-ops:RUN-042;pending_reviews=1;ready_handoffs=3;latest_runs=4;status=ready\nqueue=workbench-queue:RUN-042;items=4;next=delivery_manifest;status=ready\nplan_queue=workbench-plan-queue:RUN-042;items=5;next=build_delivery_manifest;status=ready\naction_item=project-action:RUN-042:review-pack;owner=reviewer;priority=high;status=ready\naction_item=project-action:RUN-042:delivery-manifest;owner=writer;priority=high;status=waiting\naction_item=project-action:RUN-042:protocol-reproduction;owner=recovery;priority=normal;status=ready\naction_item=project-action:RUN-042:release-check;owner=auditor;priority=normal;status=ready\nadvance_result=operations-advance-next:RUN-042;selected=delivery_manifest;effect=rp_package;status=ready\nexecute_result=operations-execute-plan:RUN-042;selected=build_delivery_manifest;effect=rp_runner;status=ready\nreport_export=research-ops-report:RUN-042;formats=markdown,json;source=rp_runner,rp_package,rp_review_dashboard;status=ready\nhandoff=ops->reviewer;artifact=rp_review_dashboard;status=ready\nhandoff=ops->recovery;artifact=rp_runbooks;status=ready\nhandoff=ops->auditor;artifact=rp_projectrel;status=ready\nhandoff=review-board->operations;artifact=rp_reviewboard;status=ready\nhandoff=control-plane->operations;artifact=rp_control;status=ready\nhandoff=integrity-plane->operations;artifact=rp_integrity;status=ready\nhandoff=coherence-plane->operations;artifact=rp_coherence;status=ready\nsource_files=rp_startup,rp_runner,rp_package,rp_review_dashboard,rp_runbooks,rp_projectrel,rp_studyproto\nagentos_adaptation=event_queue,context_ops_trace,capability_action_guard,batch_plan_executor;status=planned\nstatus=ready\n",
    "rp_reviewboard": "service=formal-review-board\nproject=lab-gene-x\nrun_id=RUN-042\nreview_board_checks=24\nreview_boards=1\nreview_requests=1\nreview_votes=4\nreview_signoffs=4\nreview_blockers=0\nreview_decisions=1\nreview_assignments=4\nreview_filters=2\nreview_workloads=4\nreview_escalations=0\ndecision=approved\nboard=review-board:final-release;chair=wang;members=4;status=active\nrequest=review-request:RUN-042:release-dossier;target=release-dossier:RUN-042:final-review;roles=4;status=approved\nvote=review-vote:RUN-042:methods;reviewer=auditor;role=methods_reviewer;decision=approve;status=recorded\nvote=review-vote:RUN-042:data;reviewer=data-steward;role=data_reviewer;decision=approve;status=recorded\nvote=review-vote:RUN-042:systems;reviewer=systems-reviewer;role=systems_reviewer;decision=approve;status=recorded\nvote=review-vote:RUN-042:chair;reviewer=wang;role=release_chair;decision=approve;status=recorded\nsignoff=review-signoff:RUN-042:methods;signer=auditor;role=methods_reviewer;decision=signed;status=recorded\nsignoff=review-signoff:RUN-042:data;signer=data-steward;role=data_reviewer;decision=signed;status=recorded\nsignoff=review-signoff:RUN-042:systems;signer=systems-reviewer;role=systems_reviewer;decision=signed;status=recorded\nsignoff=review-signoff:RUN-042:chair;signer=wang;role=release_chair;decision=signed;status=recorded\ndecision_record=review-board-decision:RUN-042:release;approvals=4;rejections=0;blockers_open=0;missing_roles=0;missing_signoffs=0;status=approved\nassignment=review-assignment:RUN-042:methods;reviewer=auditor;role=methods_reviewer;priority=medium;status=done\nassignment=review-assignment:RUN-042:data;reviewer=data-steward;role=data_reviewer;priority=medium;status=done\nassignment=review-assignment:RUN-042:systems;reviewer=systems-reviewer;role=systems_reviewer;priority=medium;status=done\nassignment=review-assignment:RUN-042:chair;reviewer=wang;role=release_chair;priority=high;status=done\nfilter=review-filter:auditor-open;owner=auditor;results=0;status=ready\nfilter=review-filter:wang-overdue;owner=wang;results=0;status=ready\nworkload=review-workload:auditor;open=0;overdue=0;high=0;status=ready\nworkload=review-workload:data-steward;open=0;overdue=0;high=0;status=ready\nworkload=review-workload:systems-reviewer;open=0;overdue=0;high=0;status=ready\nworkload=review-workload:wang;open=0;overdue=0;high=0;status=ready\nreview_package=formal-review-board-package:RUN-042;files=rp_dossier,rp_review_dashboard,rp_package,rp_opsboard;status=ready\nagentos_adaptation=capability_review_roles,context_signoff_trace,event_review_queue,metadata_dossier_binding;status=planned\nstatus=ready\n",
    "rp_control": "service=platform-control-plane\nproject=lab-gene-x\nrun_id=RUN-042\ncontrol_plane_checks=30\napprovals=4\napproval_transitions=4\nsubscriptions=3\nnotifications=4\nrun_queue_items=4\nleases=2\nplugin_manifests=3\nplugin_runs=3\nworkspaces=1\nusers=3\naccess_grants=3\nsaved_views=2\napi_tokens=1\npermissions=5\ncontrol_actions=8\napproval=approval:release-dossier:1;target=release-dossier:RUN-042;state=draft;actor=writer;status=recorded\napproval=approval:release-dossier:2;target=release-dossier:RUN-042;state=submitted;actor=writer;status=recorded\napproval=approval:release-dossier:3;target=release-dossier:RUN-042;state=approved;actor=wang;status=recorded\napproval=approval:release-dossier:4;target=release-dossier:RUN-042;state=published;actor=wang;status=recorded\nsubscription=sub:review:wang:APPROVAL_STATE;target=wang;event=APPROVAL_STATE;status=active\nsubscription=sub:review:auditor:QUEUE_ITEM_FINISHED;target=auditor;event=QUEUE_ITEM_FINISHED;status=active\nsubscription=sub:ops:writer:*;target=writer;event=*;status=active\nnotification=notif:1;target=wang;event=APPROVAL_STATE;delivered=1;status=ready\nnotification=notif:2;target=auditor;event=QUEUE_ITEM_FINISHED;delivered=1;status=ready\nnotification=notif:3;target=writer;event=RUN_LEASED;delivered=1;status=ready\nnotification=notif:4;target=writer;event=PLUGIN_RUN;delivered=1;status=ready\nqueue=queue:RUN-042:1;run=RUN-042;priority=90;state=done;worker=orchestrator;status=ready\nqueue=queue:RUN-042:2;run=RUN-042-review;priority=80;state=leased;worker=reviewer;status=ready\nqueue=queue:RUN-042:3;run=RUN-042-package;priority=70;state=queued;worker=none;status=ready\nqueue=queue:RUN-042:4;run=RUN-042-audit;priority=60;state=done;worker=auditor;status=ready\nplugin=plugin.artifacts;name=Artifact Analytics;tools=artifact_count_by_status;enabled=1;status=ready\nplugin=plugin.failures;name=Failure Summaries;tools=stage_failure_summary;enabled=1;status=ready\nplugin=plugin.tuning;name=Parameter Tuning;tools=recommend_memory_limit;enabled=1;status=ready\nplugin_run=plugin-run:1;plugin=plugin.artifacts;tool=artifact_count_by_status;result=ok;status=ready\nplugin_run=plugin-run:2;plugin=plugin.failures;tool=stage_failure_summary;result=ok;status=ready\nplugin_run=plugin-run:3;plugin=plugin.tuning;tool=recommend_memory_limit;current=1024;recommended=1536;status=ready\nworkspace=ws:lab-gene-x;owner=wang;projects=1;status=ready\nuser=user:wang;roles=maintainer;status=ready\nuser=user:auditor;roles=auditor;status=ready\nuser=user:guest;roles=viewer;status=ready\ngrant=grant:wang:lab-gene-x:maintainer;subject=wang;object=lab-gene-x;role=maintainer;status=ready\ngrant=grant:auditor:lab-gene-x:auditor;subject=auditor;object=lab-gene-x;role=auditor;status=ready\ngrant=grant:guest:lab-gene-x:viewer;subject=guest;object=lab-gene-x;role=viewer;status=ready\nsaved_view=view:failed-artifacts;kind=artifacts;query=status=failed;owner=wang;status=ready\nsaved_view=view:planned-jobs;kind=jobs;query=status=planned;owner=wang;status=ready\napi_token=token:local-dashboard;owner=wang;scopes=read,dashboard;secret_material=not_written;status=ready\npermission=can:wang:approve;result=allow;status=ready\npermission=can:wang:admin;result=allow;status=ready\npermission=can:auditor:audit;result=allow;status=ready\npermission=can:guest:write;result=deny;status=ready\npermission=can:guest:approve;result=deny;status=ready\ncontrol_report=platform-control-report:RUN-042;approvals=4;notifications=4;queue_items=4;plugin_runs=3;status=ready\nagentos_adaptation=kernel_capability_check,kernel_event_delivery,kernel_plugin_tool_table,kernel_run_queue;status=planned\nstatus=ready\n",
    "rp_integrity": (
        "service=integrity-plane\n"
        "project=lab-gene-x\n"
        "run_id=RUN-042\n"
        "integrity_checks=36\n"
        "evidence_contracts=8\n"
        "evidence_checks=8\n"
        "reference_contracts=8\n"
        "reference_checks=8\n"
        "namespace_checks=5\n"
        "status_checks=5\n"
        "review_alignment_checks=4\n"
        "report_source_checks=3\n"
        "package_trace_checks=3\n"
        "errors=0\n"
        "warnings=0\n"
        "decision=passed\n"
        "evidence_contract=research_finding->rp_evidence;required=claim,evidence,source;status=ready\n"
        "evidence_check=backend_evidence;source=rp_backend_exec;target=rp_report_text;result=pass;status=ready\n"
        "reference_contract=stage_artifacts;source=rp_stage_state;target=rp_artifact;field=output;status=ready\n"
        "reference_check=stage_artifacts;source=rp_stage_state;target=rp_artifact;result=pass;status=ready\n"
        "namespace_check=run_id;value=RUN-042;scope=project;result=pass;status=ready\n"
        "status_check=package;source=rp_package;allowed=draft,ready,approved,released;result=pass\n"
        "review_alignment=board_to_dashboard;source=rp_reviewboard;target=rp_review_dashboard;decision=aligned;status=ready\n"
        "report_source_check=workflow;source=rp_report_text;target=rp_stage_state;source_key=host_workflow_run_id;status=ready\n"
        "package_trace=delivery;source=rp_package;target=rp_web_bundle;result=pass;status=ready\n"
        "integrity_report=integrity-report:RUN-042;checks=36;errors=0;warnings=0;status=ready\n"
        "agentos_adaptation=kernel_context_attestation,kernel_metadata_reference_index,kernel_event_trace,kernel_namespace_registry;status=planned\n"
        "status=ready\n"
    ),
    "rp_coherence": (
        "service=coherence-plane\n"
        "project=lab-gene-x\n"
        "run_id=RUN-042\n"
        "coherence_checks=40\n"
        "delivery_contracts=7\n"
        "delivery_checks=7\n"
        "run_state_contracts=7\n"
        "run_state_checks=7\n"
        "lifecycle_contracts=6\n"
        "lifecycle_checks=6\n"
        "workflow_lint_checks=5\n"
        "tool_protocol_checks=5\n"
        "report_validation_checks=5\n"
        "agent_coordination_checks=3\n"
        "errors=0\n"
        "warnings=0\n"
        "decision=passed\n"
        "delivery_contract=research_package;primary=rp_package;related=rp_report_text,rp_artifact_manifest,rp_review_pack;status=ready\n"
        "delivery_check=llm_delivery;source=rp_llm_resp;result=pass;status=ready\n"
        "run_state_contract=stage_state;source=rp_stage_state;expected=done,recovered,cached,accepted,ready;status=ready\n"
        "run_state_check=cache_reuse;source=rp_cache_index;result=pass;status=ready\n"
        "lifecycle_contract=llm_relay;order=request>route>guard>response>quality;status=ready\n"
        "lifecycle_check=backend_case;source=rp_backend_exec;result=pass;status=ready\n"
        "workflow_lint=manifest_links;source=rp_artifact_manifest;expected=raw_to_report;result=pass;status=ready\n"
        "tool_validation=llm_relay;tools=relay_guarded;source=rp_llm_guard;result=pass;status=ready\n"
        "report_validation=llm_source;source=rp_report_text;target=rp_llm_resp;result=pass;status=ready\n"
        "agent_coordination=decision_trace;source=rp_decisions;target=rp_review_dashboard;result=pass;status=ready\n"
        "coherence_report=coherence-report:RUN-042;checks=40;errors=0;warnings=0;status=ready\n"
        "status=ready\n"
    ),
    "rp_publication": (
        "service=publication-workflow\n"
        "run_id=RUN-042\n"
        "targets=2\n"
        "submissions=2\n"
        "review_rounds=2\n"
        "revision_tasks=3\n"
        "response_packages=2\n"
        "response_items=4\n"
        "decisions=2\n"
        "publication_checks=48\n"
        "journal_target=journal-target:systems-biology-report;name=Journal_of_Reproducible_Systems_Biology;article=research_article;requirements=5;status=active\n"
        "journal_target=journal-target:agentos-systems;name=AgentOS_Systems_Letters;article=systems_artifact;requirements=4;status=active\n"
        "submission=submission:RUN-042:systems-biology-report;target=journal-target:systems-biology-report;package=delivery-package:RUN-042;manuscript=manuscript:RUN-042;artifacts=5;checklist=5;status=submitted\n"
        "submission=submission:RUN-042:agentos-artifact;target=journal-target:agentos-systems;package=delivery-package:RUN-042;manuscript=manuscript:RUN-042;artifacts=6;checklist=4;status=accepted\n"
        "review_round=peer-review:RUN-042:round-1;submission=submission:RUN-042:systems-biology-report;reviewer=reviewer-a;decision=minor_revision;points=3;evidence=4;status=response_ready\n"
        "review_round=peer-review:RUN-042:round-2;submission=submission:RUN-042:agentos-artifact;reviewer=reviewer-b;decision=ready;points=1;evidence=3;status=response_ready\n"
        "revision_task=revision:RUN-042:discussion-evidence;review=peer-review:RUN-042:round-1;section=discussion;assignee=reporter;evidence=3;status=done\n"
        "revision_task=revision:RUN-042:methods-reproducibility;review=peer-review:RUN-042:round-1;section=methods;assignee=writer;evidence=4;status=done\n"
        "revision_task=revision:RUN-042:artifact-appendix;review=peer-review:RUN-042:round-2;section=appendix;assignee=auditor;evidence=3;status=done\n"
        "response_package=peer-review-response-package:RUN-042:round-1;review=peer-review:RUN-042:round-1;items=3;addressed=3;needs_revision=0;decision=ready;status=ready\n"
        "response_package=peer-review-response-package:RUN-042:round-2;review=peer-review:RUN-042:round-2;items=1;addressed=1;needs_revision=0;decision=ready;status=ready\n"
        "response_item=1;package=peer-review-response-package:RUN-042:round-1;point=alignment_evidence;revision=revision:RUN-042:discussion-evidence;evidence=rp_stage_state,rp_retry_plan,rp_artifact_manifest;status=addressed\n"
        "response_item=2;package=peer-review-response-package:RUN-042:round-1;point=statistical_method;revision=revision:RUN-042:methods-reproducibility;evidence=rp_report_text,rp_chart_data,rp_evidence;status=addressed\n"
        "response_item=3;package=peer-review-response-package:RUN-042:round-1;point=consent_handling;revision=revision:RUN-042:methods-reproducibility;evidence=rp_governance,rp_privacy,rp_compliance;status=addressed\n"
        "response_item=4;package=peer-review-response-package:RUN-042:round-2;point=artifact_appendix;revision=revision:RUN-042:artifact-appendix;evidence=rp_package,rp_dossier,rp_integrity;status=addressed\n"
        "publication_decision=publication-decision:RUN-042:accept-with-evidence;submission=submission:RUN-042:systems-biology-report;decision=accepted;approved_by=editorial-board;release_candidate=release:RUN-042:plain-ucore;status=ready\n"
        "publication_decision=publication-decision:RUN-042:artifact-accept;submission=submission:RUN-042:agentos-artifact;decision=accepted;approved_by=systems-board;release_candidate=release:RUN-042:plain-ucore;status=ready\n"
        "search_index=publication,peer_review,response,revision,submission;records=15;status=ready\n"
        "provenance=rp_package->rp_publication->rp_peerresp->rp_dossier;status=ready\n"
        "agentos_adaptation=kernel_submission_metadata,kernel_review_event_queue,kernel_response_context,kernel_release_gate;status=planned\n"
        "decision=accepted\n"
        "status=ready\n"
    ),
    "rp_pubplan": (
        "publication_plan=RUN-042\n"
        "targets=2\n"
        "journal_targets=2\n"
        "checklist_items=9\n"
        "submission_material=rp_package,rp_dossier,rp_report_text,rp_artifact_manifest,rp_review_pack\n"
        "journal_requirement=structured_abstract;source=rp_report_text;status=ready\n"
        "journal_requirement=methods_reproducibility;source=rp_repro;status=ready\n"
        "journal_requirement=ethics_statement;source=rp_governance;status=ready\n"
        "journal_requirement=data_availability;source=rp_datarel;status=ready\n"
        "journal_requirement=artifact_appendix;source=rp_dossier;status=ready\n"
        "agentos_showcase=plain_userland_vs_kernel_assisted;status=planned\n"
        "status=ready\n"
    ),
    "rp_peerresp": (
        "peer_review_response=RUN-042\n"
        "packages=2\n"
        "responses=6\n"
        "items=4\n"
        "addressed=4\n"
        "needs_revision=0\n"
        "response_letter=peer-review-response:RUN-042;sections=4;evidence_links=13;status=ready\n"
        "response_package=peer-review-response-package:RUN-042:round-1;decision=ready;items=3;status=ready\n"
        "response_package=peer-review-response-package:RUN-042:round-2;decision=ready;items=1;status=ready\n"
        "response_item=alignment_evidence;reply=updated_discussion;status=addressed\n"
        "response_item=statistical_method;reply=methods_named;status=addressed\n"
        "response_item=consent_handling;reply=governance_linked;status=addressed\n"
        "response_item=artifact_appendix;reply=appendix_linked;status=addressed\n"
        "status=ready\n"
    ),
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
        "decision=review_pack_ready;basis=delivery_manifest,operations_next,project_action_items,workbench_handoff\n"
        "subsection=integrity_plane;source=rp_integrity;checks=36;errors=0;result=passed;status=ready\n"
        "subsection=real_task;source=rp_realtask;dataset=palmer-penguins;checks=96;outcome=passed;status=ready\n"
        "subsection=analysis_results;source=rp_analysisres;checks=96;runs=2;statistics=2;status=ready\n"
        "subsection=experiment_campaigns;source=rp_campaign;campaigns=1;trials=4;checks=108;outcome=passed;status=ready\n"
        "subsection=model_registry;source=rp_modelreg;checks=96;evaluation=passed;deployment=ready;status=ready\n"
        "subsection=release_dossier;source=rp_reldossier;sections=7;checks=112;outcome=passed;status=ready\n"
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
        assert summary["pages"] == 40, summary
        assert (out_dir / "index.html").exists()
        assert (out_dir / "run.html").exists()
        assert (out_dir / "workflow.html").exists()
        assert (out_dir / "workbench.html").exists()
        assert (out_dir / "project.html").exists()
        assert (out_dir / "project-review.html").exists()
        assert (out_dir / "review.html").exists()
        assert (out_dir / "delivery.html").exists()
        assert (out_dir / "data.html").exists()
        assert (out_dir / "api-catalog.html").exists()
        assert (out_dir / "llm.html").exists()
        assert (out_dir / "integrity.html").exists()
        assert (out_dir / "coherence.html").exists()
        assert (out_dir / "training-compliance.html").exists()
        assert (out_dir / "publication.html").exists()
        assert (out_dir / "calculations.html").exists()
        assert (out_dir / "real-task.html").exists()
        assert (out_dir / "analysis-results.html").exists()
        assert (out_dir / "decision-support.html").exists()
        assert (out_dir / "usable-research.html").exists()
        assert (out_dir / "usable-project.html").exists()
        assert (out_dir / "experiment-campaigns.html").exists()
        assert (out_dir / "statistical-design.html").exists()
        assert (out_dir / "model-registry.html").exists()
        assert (out_dir / "systematic-review.html").exists()
        assert (out_dir / "experiment-schedule.html").exists()
        assert (out_dir / "release-dossier.html").exists()
        assert (out_dir / "mature.html").exists()
        assert (out_dir / "provenance.html").exists()
        assert (out_dir / "provenance-queries.html").exists()
        assert (out_dir / "api" / "rp_api_home.json").exists()
        assert (out_dir / "api" / "rp_api_catalog.json").exists()
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
        api_catalog_html = (out_dir / "api-catalog.html").read_text(encoding="utf-8")
        assert "API Catalog" in api_catalog_html
        assert "Host API Routes" in api_catalog_html
        assert "Grouped Routes" in api_catalog_html
        assert "API Groups" in api_catalog_html
        assert "214" in api_catalog_html
        assert "50" in api_catalog_html
        assert "/api/analysis-results" in api_catalog_html
        assert "/api/experiment-scheduling" in api_catalog_html
        assert "/api/workflow-runner" in api_catalog_html
        assert "/api/usable-research-workbench-file-catalog" in api_catalog_html
        assert "/api/llm-proxy" in api_catalog_html
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
        integrity_html = (out_dir / "integrity.html").read_text(encoding="utf-8")
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
        assert "rp_integrity" in integrity_html
        assert "backend_evidence" in integrity_html
        assert "stage_artifacts" in integrity_html
        assert "integrity-report:RUN-042" in integrity_html
        coherence_html = (out_dir / "coherence.html").read_text(encoding="utf-8")
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
        publication_html = (out_dir / "publication.html").read_text(encoding="utf-8")
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
        calculations_html = (out_dir / "calculations.html").read_text(encoding="utf-8")
        assert "Calculations" in calculations_html
        assert "calculation-computer:local-agentos" in calculations_html
        assert "calculation-code:metadata-qc:v1" in calculations_html
        assert "calculation-job:lab-gene-x:run042-qc" in calculations_html
        assert "calculation-parser-result:run042-qc" in calculations_html
        assert "calculation-export:lab-gene-x:run042-qc" in calculations_html
        real_task_html = (out_dir / "real-task.html").read_text(encoding="utf-8")
        assert "Real Task" in real_task_html
        assert "palmer-penguins" in real_task_html
        assert "rows" in real_task_html
        assert "344" in real_task_html
        assert "answer_source" in real_task_html
        assert "report_md" in real_task_html
        assert "duplicate_zip_entries" in real_task_html
        analysis_results_html = (out_dir / "analysis-results.html").read_text(encoding="utf-8")
        assert "Analysis Results" in analysis_results_html
        assert "Analysis Result Detail" in analysis_results_html
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
        decision_support_html = (out_dir / "decision-support.html").read_text(encoding="utf-8")
        assert "Decision Support" in decision_support_html
        assert "Decision Options" in decision_support_html
        assert "Decision Criteria" in decision_support_html
        assert "Decision Scores" in decision_support_html
        assert "Review Packet" in decision_support_html
        assert "agentos_ucore_hybrid" in decision_support_html
        assert "decision-review-packet:agentos-final-demo-backend" in decision_support_html
        usable_research_html = (out_dir / "usable-research.html").read_text(encoding="utf-8")
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
        usable_project_html = (out_dir / "usable-project.html").read_text(encoding="utf-8")
        assert "Usable Project Lifecycle" in usable_project_html
        assert "Project Scaffold" in usable_project_html
        assert "Project Launches And Operations" in usable_project_html
        assert "Bundles And Package Actions" in usable_project_html
        assert "scaffold-template:protocol-reproduction" in usable_project_html
        assert "usable-project-launch:lab-gene-x:1" in usable_project_html
        assert "usable-study-protocol-reproduction-package:RUN-042" in usable_project_html
        campaign_html = (out_dir / "experiment-campaigns.html").read_text(encoding="utf-8")
        assert "Experiment Campaigns" in campaign_html
        assert "align-memory-grid" in campaign_html
        assert "trial_count" in campaign_html
        assert "4" in campaign_html
        assert "select_trial_04" in campaign_html
        assert "accept_candidate" in campaign_html
        statistical_design_html = (out_dir / "statistical-design.html").read_text(encoding="utf-8")
        assert "Statistical Design" in statistical_design_html
        assert "stat-design:lab-gene-x:run042-primary" in statistical_design_html
        assert "required_per_group" in statistical_design_html
        assert "underpowered" in statistical_design_html
        assert "balanced" in statistical_design_html
        assert "approved_with_sample_size_note" in statistical_design_html
        model_registry_html = (out_dir / "model-registry.html").read_text(encoding="utf-8")
        assert "Model Registry" in model_registry_html
        assert "registered-model:agent-triage-template" in model_registry_html
        assert "model-version:agent-triage-template:v1" in model_registry_html
        assert "model-evaluation:agent-triage-template:v1:RUN-042" in model_registry_html
        assert "model-deployment:agent-triage-template:v1:template" in model_registry_html
        assert "offline provider ready" in model_registry_html
        systematic_review_html = (out_dir / "systematic-review.html").read_text(encoding="utf-8")
        assert "Systematic Review" in systematic_review_html
        assert "systematic-review:agent-os-science" in systematic_review_html
        assert "Screening" in systematic_review_html
        assert "9" in systematic_review_html
        assert "moderate" in systematic_review_html
        assert "prisma-flow:agent-os-science" in systematic_review_html
        experiment_schedule_html = (out_dir / "experiment-schedule.html").read_text(encoding="utf-8")
        assert "Experiment Schedule" in experiment_schedule_html
        assert "schedule:RUN-042:lab-execution" in experiment_schedule_html
        assert "schedule-task:RUN-042:library-prep" in experiment_schedule_html
        assert "schedule-booking:RUN-042:seq-library" in experiment_schedule_html
        assert "schedule-conflict:RUN-042:seq-01-overlap" in experiment_schedule_html
        assert "schedule-exec:RUN-042:library-prep" in experiment_schedule_html
        release_dossier_html = (out_dir / "release-dossier.html").read_text(encoding="utf-8")
        assert "Release Dossier" in release_dossier_html
        assert "release-dossier:RUN-042:final-review" in release_dossier_html
        assert "experiment-campaign" in release_dossier_html
        assert "agentos-readiness" in release_dossier_html
        assert "attestations" in release_dossier_html
        assert "release-dossier-package:RUN-042" in release_dossier_html
        mature_html = (out_dir / "mature.html").read_text(encoding="utf-8")
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
        provenance_html = (out_dir / "provenance.html").read_text(encoding="utf-8")
        assert "Provenance Timeline" in provenance_html
        assert "Provenance Detail" in provenance_html
        assert "Timeline Views" in provenance_html
        assert "Timeline Events" in provenance_html
        assert "Provenance Edges" in provenance_html
        assert "Evidence Packets" in provenance_html
        assert "agent_decision_flow" in provenance_html
        assert "agent_to_trace" in provenance_html
        assert "kernel_timeline" in provenance_html
        provenance_query_html = (out_dir / "provenance-queries.html").read_text(encoding="utf-8")
        assert "Provenance Queries" in provenance_query_html
        assert "provenance-query-template:calculation-root-neighborhood" in provenance_query_html
        assert "provenance-query-execution:calculation-lineage" in provenance_query_html
        assert "provenance-query-comparison:RUN-042:rendered-vs-direct" in provenance_query_html
        assert "provenance-query-packet:RUN-042:lineage-review" in provenance_query_html
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
        assert "integrity_plane_checks" in compare_html
        assert "Integrity Plane" in compare_html
        assert "coherence_plane_checks" in compare_html
        assert "publication_checks" in compare_html
        assert "calculation_checks" in compare_html
        assert "Calculation Checks" in compare_html
        assert "real_task_checks" in compare_html
        assert "Real Task Checks" in compare_html
        assert "analysis_results_checks" in compare_html
        assert "Analysis Results Checks" in compare_html
        assert "experiment_campaign_checks" in compare_html
        assert "Experiment Campaign Checks" in compare_html
        assert "training_compliance_checks" in compare_html
        assert "Training Compliance Checks" in compare_html
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
        assert "Coherence Plane" in compare_html
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
        training_html = (out_dir / "training-compliance.html").read_text(encoding="utf-8")
        assert "Training Compliance" in training_html
        assert "training_compliance_checks" in training_html
        assert "training-req:sop-deviation:qa-lead" in training_html
        assert "training:qa-lead:sop-deviation" in training_html
        assert "competency:qa-lead:sop-deviation" in training_html
        assert "auth:qa-lead:qa-lead:lab-gene-x" in training_html
        assert "training-gap:schedule:RUN-042:lab-execution" in training_html
        assert "Resolved Gaps" in training_html
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
