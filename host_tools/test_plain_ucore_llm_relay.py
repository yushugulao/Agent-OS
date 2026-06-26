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
    "rp_runner": "runner=ready\n",
    "rp_revision": "revision=ready\n",
    "rp_package": "package=ready\n",
    "rp_api_run": "api=run\n",
    "rp_api_evidence": "api=evidence\n",
    "rp_agent_run": "agent_run=ready\n",
    "rp_agentcmp": "plain_kernel=passed\n",
    "rp_review_dashboard": "dashboard=research-review\nstatus=ready\n",
    "rp_review_pack": "pack=review-evidence\nstatus=ready\n",
}


def write_state(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name, text in STATE.items():
        (path / name).write_text(text, encoding="utf-8")


def main() -> int:
    previous_endpoint = os.environ.pop("AGENT_PLATFORM_LLM_ENDPOINT", None)
    previous_key = os.environ.pop("AGENT_PLATFORM_LLM_API_KEY", None)
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
            assert "host_relay_quality=passed:24/24;blocked:0;source=rp_llmeval;status=ready" in reviewpack
            assert "host_relay_pack_input=rp_report_text,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_review_dashboard,rp_package;status=ready" in reviewpack
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
            assert "key_present=0" in hostreq
            assert "secret_material=not_written" in hostreq
    finally:
        if previous_endpoint is not None:
            os.environ["AGENT_PLATFORM_LLM_ENDPOINT"] = previous_endpoint
        if previous_key is not None:
            os.environ["AGENT_PLATFORM_LLM_API_KEY"] = previous_key
    print("test_plain_ucore_llm_relay: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
