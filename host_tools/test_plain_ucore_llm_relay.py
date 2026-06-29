#!/usr/bin/env python3
"""Unit checks for plain_ucore_llm_relay."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import plain_ucore_llm_relay as relay


STATE = {
    "rp_llmq": (
        "queue=host_relay_packets\n"
        "queued=3\n"
        "q1=review_summary;claims=8;evidence_links=5;secret_policy=no_secret_in_ucore\n"
        "q2=method_check;protocol_checks=5;data_schema=17;secret_policy=no_secret_in_ucore\n"
        "q3=recovery_note;failed_stage=align;attempts=2;prov_paths=3;secret_policy=no_secret_in_ucore\n"
        "status=ready\n"
    ),
    "rp_llm_req": (
        "provider=template\n"
        "host_llm_request_id=host-q1\n"
        "host_llm_route=review_summary\n"
        "host_llm_provider=template\n"
        "host_llm_prompt=summarize_recovery_evidence\n"
        "host_llm_budget=2048\n"
        "host_llm_secret_ref=host_env\n"
        "status=queued\n"
    ),
    "rp_llm_resp": "responses=3\nstatus=ready\n",
    "rp_llm_hostreq": "template_mode=ready\nstatus=ready\n",
    "rp_llm_packets": "packets=3\nstatus=ready\n",
    "rp_llmlog": "roundtrip=ready\nstatus=ready\n",
    "rp_llmeval": "passed=7\n",
    "rp_llm_guard": "secrets_in_ucore=0\n",
    "rp_relay": "mode=host_file_relay\n",
    "rp_prompt": "routes=4\n",
    "rp_actionio": "actions=ready\n",
    "rp_web_bundle": "reader_ready=1\n",
    "rp_api_runtime": "api=runtime\nstatus=ready\n",
    "rp_report_text": "report=ready\n",
    "rp_runner": (
        "runner=ready\n"
        "workbench=usable-workbench:RUN-900:plain-ucore\n"
        "workbench_tasks=9\n"
        "workbench_next_task=delivery_manifest\n"
        "host_action_workbench_id=W1\n"
        "host_action_workbench_task=human_review\n"
        "host_action_workbench_task_status=waiting\n"
        "host_action_workbench_manifest=mf.json\n"
        "host_action_workbench_verified_files=11\n"
        "host_action_workbench_missing_files=0\n"
        "host_action_workbench_bundle=wb.zip\n"
        "backend_evidence_report=rp_backend_exec\n"
    ),
    "rp_revision": "revision=ready\n",
    "rp_package": (
        "package=ready\n"
        "delivery_files=8\n"
        "host_action_operations_report=exported\n"
        "host_action_operations_next=executed\n"
        "host_action_quality_gate=checked\n"
        "host_action_quality_repair_execute=done\n"
        "host_action_workbench_completion=ready\n"
        "host_action_workbench_manifest=mf.json\n"
        "host_action_workbench_verified_files=11\n"
        "host_action_workbench_missing_files=0\n"
        "host_action_workbench_bundle=wb.zip\n"
        "host_action_project_space=ready\n"
        "host_action_project_id=lab-gene-x\n"
        "host_action_project_note=recorded\n"
        "host_action_project_action_item=created\n"
        "host_action_project_answer=generated\n"
        "host_action_project_repair=executed\n"
        "host_action_research_search=ready\n"
        "status=ready\n"
    ),
    "rp_api_run": "api=run\n",
    "rp_api_evidence": "api=evidence\n",
    "rp_agent_run": "agent_run=ready\n",
    "rp_agentcmp": "plain_kernel=passed\n",
    "rp_review_dashboard": "dashboard=research-review\nstatus=ready\n",
    "rp_review_pack": "pack=review-evidence\nstatus=ready\n",
    "rp_backend_exec": (
        "runner_detail=plain-ucore;src=rp_wfio;req=execution_plan;obs=pass;act=record;review=baseline\n"
        "runner_detail=retry-recovery;src=rp_retry_plan+rp_stage_state;req=retry_stage+stage;obs=pass;act=rerun_align;review=recovered\n"
        "runner_detail=user-context;src=rp_query+rp_provpath;req=context_path;obs=pass;act=rebuild_from_files;review=userland\n"
        "runner_report=plain-ucore;plain_cost=file_scan_manifest;agentos_replace=batch_tool_context;risk=manual_state;status=passed\n"
        "runner_report=retry-recovery;plain_cost=retry_file_stage_file;agentos_replace=event_context;risk=stale_retry;status=passed\n"
        "runner_report=user-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=passed\n"
    ),
}


def write_state(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name, text in STATE.items():
        (path / name).write_text(text, encoding="utf-8")


def main() -> int:
    previous_endpoint = os.environ.pop("AGENT_PLATFORM_LLM_ENDPOINT", None)
    previous_key = os.environ.pop("AGENT_PLATFORM_LLM_API_KEY", None)
    previous_provider = os.environ.pop("AGENT_PLATFORM_LLM_PROVIDER", None)
    previous_model = os.environ.pop("AGENT_PLATFORM_LLM_MODEL", None)
    previous_key_file = os.environ.pop("AGENT_PLATFORM_LLM_API_KEY_FILE", None)
    try:
        with tempfile.TemporaryDirectory() as state_tmp, tempfile.TemporaryDirectory() as out_tmp:
            state_dir = Path(state_tmp)
            out_dir = Path(out_tmp)
            write_state(state_dir)
            summary = relay.run_relay(state_dir, out_dir, mode="template")
            assert summary["status"] == "ready", summary
            assert summary["requests"] == 4, summary
            assert summary["secret_values_written"] is False, summary
            resp = (out_dir / "rp_llm_resp").read_text(encoding="utf-8")
            assert "host_relay_process=plain_ucore_llm_relay" in resp
            assert "host_relay_response=relay-host-q1" in resp
            assert "review_summary_supported_by_current_evidence" in resp
            packets = (out_dir / "rp_llm_packets").read_text(encoding="utf-8")
            assert "secret_in_packet=0" in packets
            eval_state = (out_dir / "rp_llmeval").read_text(encoding="utf-8")
            assert "host_relay_eval_batch=checked:24;passed:24;blocked:0;status=ready" in eval_state
            assert "host_relay_eval=host-q1;response=relay-host-q1;checks=6;passed=6" in eval_state
            guard = (out_dir / "rp_llm_guard").read_text(encoding="utf-8")
            assert "host_relay_guard_batch=checked:4;blocked:0;secret_values_written=0;status=ready" in guard
            assert "host_relay_guard=host-q1" in guard
            replay = (out_dir / "rp_relay").read_text(encoding="utf-8")
            assert "host_relay_replay_batch=requests:4;responses:4;matched:4;status=ready" in replay
            assert "host_relay_replay=host-q1;response=relay-host-q1" in replay
            prompt = (out_dir / "rp_prompt").read_text(encoding="utf-8")
            assert "host_relay_prompt_batch=routes:3;requests:4;status=ready" in prompt
            assert "host_relay_prompt_route=host-q1;route=review_summary" in prompt
            report = (out_dir / "rp_report_text").read_text(encoding="utf-8")
            assert "host_relay_report_summary=review_summary_supported_by_current_evidence" in report
            runner = (out_dir / "rp_runner").read_text(encoding="utf-8")
            assert "host_relay_workbench_answer=review_summary_supported_by_current_evidence" in runner
            revision = (out_dir / "rp_revision").read_text(encoding="utf-8")
            assert "host_relay_writer_summary=review_summary_supported_by_current_evidence" in revision
            package = (out_dir / "rp_package").read_text(encoding="utf-8")
            assert "host_relay_delivery_file=llm_response" in package
            api_run = (out_dir / "rp_api_run").read_text(encoding="utf-8")
            assert "host_relay_report_summary=review_summary_supported_by_current_evidence" in api_run
            api_evidence = (out_dir / "rp_api_evidence").read_text(encoding="utf-8")
            assert "host_relay_grounding=citations:5" in api_evidence
            agent_run = (out_dir / "rp_agent_run").read_text(encoding="utf-8")
            assert "host_relay_agent_decision=writer_use_relay_response" in agent_run
            agentcmp = (out_dir / "rp_agentcmp").read_text(encoding="utf-8")
            assert "host_llm_relay_replay=ready;checked=24;blocked=0;plain_kernel=ordinary_files" in agentcmp
            reviewdash = (out_dir / "rp_review_dashboard").read_text(encoding="utf-8")
            assert "host_relay_quality=passed:24/24;blocked:0;source=rp_llmeval;status=ready" in reviewdash
            assert "host_relay_review_input=rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt;page=llm.html;status=ready" in reviewdash
            reviewpack = (out_dir / "rp_review_pack").read_text(encoding="utf-8")
            assert "evidence=operations_ready;source=rp_runner;status=pass" in reviewpack
            assert "evidence=project_space_ready;source=rp_package;status=pass" in reviewpack
            assert "action=open_operations_report;owner=orchestrator;artifact=rp_runner;status=ready" in reviewpack
            assert "action=close_project_items;owner=reviewer;artifact=rp_package;status=ready" in reviewpack
            assert "bridge=delivery_to_operations;delivery=rp_package;operations=rp_runner;project=rp_package;status=ready" in reviewpack
            assert "host_relay_quality=passed:24/24;blocked:0;source=rp_llmeval;status=ready" in reviewpack
            assert "host_relay_pack_input=rp_report_text,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_review_dashboard,rp_package;status=ready" in reviewpack
            assert "backend_evidence_review=rp_backend_exec;plain_costs=7;agentos_replacements=7;risks=7;source=rp_review_dashboard;status=ready" in reviewpack
            assert "backend_action_review=retry-recovery;action=rerun_align;review=recovered;plain_cost=retry_file_stage_file;agentos_replace=event_context;status=passed" in reviewpack
            assert "backend_action_review=user-context;action=rebuild_from_files;review=userland;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;status=passed" in reviewpack
            assert "operations_handoff=rp_runner+rp_package;tasks=9;next=delivery_manifest;report=exported;plan=executed;quality=checked;repair=done;backend=rp_backend_exec;status=ready" in reviewpack
            assert "workbench_handoff=rp_runner+rp_package;workbench=W1;task=human_review;task_status=waiting;manifest=mf.json;verified=11;missing=0;bundle=wb.zip;status=ready" in reviewpack
            assert "project_handoff=rp_package;project=lab-gene-x;space=ready;note=recorded;action_item=created;answer=generated;repair=executed;search=ready;status=ready" in reviewpack
            summary_json = json.loads((out_dir / "llm-relay-summary.json").read_text(encoding="utf-8"))
            assert summary_json["response_status"]["relay-host-q1"] == "ok", summary_json
            assert summary_json["quality"] == {"checked": 24, "passed": 24, "blocked": 0}, summary_json

        with tempfile.TemporaryDirectory() as state_tmp, tempfile.TemporaryDirectory() as out_tmp:
            state_dir = Path(state_tmp)
            out_dir = Path(out_tmp)
            write_state(state_dir)
            summary = relay.run_relay(state_dir, out_dir, mode="openai-compatible")
            assert summary["status"] == "ready", summary
            resp = (out_dir / "rp_llm_resp").read_text(encoding="utf-8")
            assert "status=config_missing" in resp
            hostreq = (out_dir / "rp_llm_hostreq").read_text(encoding="utf-8")
            assert "provider=deepseek" in hostreq
            assert "key_present=0" in hostreq
            assert "model=deepseek-v4-pro" in hostreq
            assert "secret_material=not_written" in hostreq

        original_call = relay.call_openai_compatible
        try:
            def fake_cloud_call(request: relay.RelayRequest, config: dict[str, str], mode_label: str = "cloud") -> relay.RelayResponse:
                return relay.RelayResponse(
                    request_id=request.request_id,
                    response_id=f"relay-{request.request_id}",
                    mode=mode_label,
                    provider="deepseek",
                    status="ok",
                    summary=f"cloud_{request.route}_summary",
                    citations="6",
                )

            relay.call_openai_compatible = fake_cloud_call  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as state_tmp, tempfile.TemporaryDirectory() as out_tmp:
                state_dir = Path(state_tmp)
                out_dir = Path(out_tmp)
                write_state(state_dir)
                summary = relay.run_relay(state_dir, out_dir, mode="cloud")
                assert summary["status"] == "ready", summary
                assert summary["requests"] == 10, summary
                conclusions = (out_dir / "rp_llm_conclusions").read_text(encoding="utf-8")
                assert "llm_conclusion=review_summary" in conclusions
                assert "llm_conclusion=method_check" in conclusions
                assert "llm_conclusion=recovery_note" in conclusions
                assert "llm_conclusion=writer_summary" in conclusions
                assert "llm_conclusion=project_review_opinion" in conclusions
                assert "llm_conclusion=final_report_summary" in conclusions
                assert "mode=cloud" in conclusions
                review = (out_dir / "rp_review2").read_text(encoding="utf-8")
                assert "llm_review_summary=cloud_review_summary_summary" in review
                reviewdash = (out_dir / "rp_review_dashboard").read_text(encoding="utf-8")
                assert "llm_method_check=cloud_method_check_summary" in reviewdash
                retry = (out_dir / "rp_retry_plan").read_text(encoding="utf-8")
                assert "llm_recovery_note=cloud_recovery_note_summary" in retry
                revision = (out_dir / "rp_revision").read_text(encoding="utf-8")
                assert "llm_writer_summary=cloud_writer_summary_summary" in revision
                project = (out_dir / "rp_projectrel").read_text(encoding="utf-8")
                assert "llm_project_review_opinion=cloud_project_review_opinion_summary" in project
                report = (out_dir / "rp_report_text").read_text(encoding="utf-8")
                assert "llm_final_report_summary=cloud_final_report_summary_summary" in report
        finally:
            relay.call_openai_compatible = original_call  # type: ignore[assignment]
    finally:
        if previous_endpoint is not None:
            os.environ["AGENT_PLATFORM_LLM_ENDPOINT"] = previous_endpoint
        if previous_key is not None:
            os.environ["AGENT_PLATFORM_LLM_API_KEY"] = previous_key
        if previous_provider is not None:
            os.environ["AGENT_PLATFORM_LLM_PROVIDER"] = previous_provider
        if previous_model is not None:
            os.environ["AGENT_PLATFORM_LLM_MODEL"] = previous_model
        if previous_key_file is not None:
            os.environ["AGENT_PLATFORM_LLM_API_KEY_FILE"] = previous_key_file
    print("test_plain_ucore_llm_relay: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
