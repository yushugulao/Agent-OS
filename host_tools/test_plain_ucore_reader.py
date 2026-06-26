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
reader_views=14
reader_actions=48
reader_payload_files=rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_bio,rp_api_labres,rp_api_pub,rp_api_know,rp_api_runtime,rp_api_action,rp_web_routes
reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_web_bundle
reader_required_sections=routes,payloads,actions,live_update,downloads,compare
reader_event_stream=rp_web_bundle
reader_fallback=rp_site
reader_state_source=plain_ucore_files
dynamic_inputs=4
status=ready
""",
    "rp_web_routes": "routes=62\nget_routes=14\npost_routes=48\nstatus=ready\n",
    "rp_api_home": "api=home\nreader_contract=rp_web_bundle\nstatus=ready\n",
    "rp_api_run": "api=run-detail\nreader_contract=rp_web_bundle\nreader_view=run-detail\nstatus=ready\n",
    "rp_api_agents": "api=agent-detail\nagents=7\nstatus=ready\n",
    "rp_api_evidence": "api=evidence-detail\nclaims=8\nstatus=ready\n",
    "rp_api_compare": "api=compare-metrics\nplain_kernel=passed\nfile_scans=128\nstate_convention=1\nuser_permission_only=1\ncontext_trusted=0\nrebuild_steps=6\nstatus=ready\n",
    "rp_api_artifacts": "api=artifacts\nmanifest_records=4\nstatus=ready\n",
    "rp_api_data": "api=data\ndataset_snapshots=2\nstatus=ready\n",
    "rp_api_bio": "api=bio\nsample_registry=rp_sreg\nstatus=ready\n",
    "rp_api_labres": "api=lab-resources\ninstrument_registry=rp_instr\nstatus=ready\n",
    "rp_api_pub": "api=publication\nresult_review=rp_resrev\nstatus=ready\n",
    "rp_api_know": "api=knowledge\nsemantic_index=rp_semindex\nstatus=ready\n",
    "rp_api_runtime": "api=runtime\nruntime_env=rp_runenv\nstatus=ready\n",
    "rp_api_action": "api=actions\nreader_contract=rp_web_bundle\nactions=48\nstatus=ready\n",
    "rp_ui_home": "page=home\nstatus=ready\n",
    "rp_ui_run": "page=run-detail\nstatus=ready\n",
    "rp_ui_agent": "page=agent-detail\ndecision_records=rp_agents,rp_decisions,rp_handoff,rp_deliberation,rp_agent_run\nstatus=ready\n",
    "rp_ui_evidence": "page=evidence-detail\nscreening_decisions=9\nevidence_protocol=usable-evidence-protocol:RUN-900:1\nstatus=ready\n",
    "rp_ui_compare": "page=compare-metrics\npain_file_scans=128\npain_state_convention=1\npain_user_permissions=1\npain_rebuild_steps=6\nstatus=ready\n",
    "rp_runner": "workbench_tasks=9\nbackend_evidence_report=rp_backend_exec;plain_costs=4;agentos_replacements=4;risks=4;status=ready\nstatus=ready\n",
    "rp_report_text": "backend_evidence_report=rp_backend_exec;plain_costs=file_scan_manifest,retry_file_stage_file,rebuild_steps_6,scan_records_128;agentos_replacements=batch_tool_context,event_context,kernel_context_path,metadata_index;status=ready\nstatus=ready\n",
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
    "rp_package": "delivery_files=8\nevidence_bundle_entries=12\n",
    "rp_agentcmp": "plain_kernel=passed\ntest_cases=838\nhandoffs=6\nreview_handoff_checks=13;review_sections=8;review_gates=6;review_decisions=3;review_handoffs=3;review_pack_actions=3;review_pack_bridges=4;backend_review=1;status=ready\nreview_pack=ready;evidence_items=11;actions=5;plain_kernel=ordinary_files;backend_evidence=1\nllm_delivery_checks=16;llm_queue=3;llm_packets=3;llm_responses=3;llm_eval=7;llm_guard=3;llm_hostreq=3;llm_review_links=2;status=ready\nworkflow_portability_checks=14;portability_imports=5;adapter_specs=6;migration_steps=9;rehearsal_cases=4;blocking_items=0;portability_package=workflow-portability;status=ready\nportability_backend_checks=12;execution_plan=workflow-migration-execution-plan:RUN-042:agentcompare;backend_scenario=backend-scenario:RUN-042:agentcompare;compare_profile=compare-profile:RUN-042:migration;passed_cases=2;planned_cases=2;status=ready\nbackend_runner_checks=12;runner_cases=4;runner_passed=2;runner_planned=2;plain_inputs=4;study_metrics=2;backend_runner_detail_checks=24;runner_detail_rows=4;backend_runner_report_checks=20;runner_report_rows=4;backend_report_links=2;status=ready\n",
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
    "rp_consistency": "checks=120\nartifact_provenance=3\nartifact_dossier_checks=4\nartifact_path_rebuild_files=6\nartifact_path_rebuild_steps=7\n",
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
    "rp_dataset_snapshot": "snapshots=2\n",
    "rp_data_quality": "passed=7\n",
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
        assert summary["pages"] == 10, summary
        assert (out_dir / "index.html").exists()
        assert (out_dir / "run.html").exists()
        assert (out_dir / "review.html").exists()
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
        assert "quality_to_package" in artifacts_html
        assert "recovery_to_review" in artifacts_html
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
            assert "delivery_files=8" in actions_html
            assert "report_section" in actions_html
            assert "/actions/research/export-bundle" in actions_html
            review_html = (out_dir / "review.html").read_text(encoding="utf-8")
            assert "Review Action Trace" in review_html
            assert "Review Action Output Links" in review_html
            assert "Review Action Output Details" in review_html
            assert "Review Action Impact" in review_html
            assert "Review Action Delta" in review_html
            assert "Operations Report Narrative" in review_html
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
            assert "rp_report_text" in run_html
            assert "dynamic_submissions=4" in run_html
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
