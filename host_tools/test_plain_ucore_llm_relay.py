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
            summary_json = json.loads((out_dir / "llm-relay-summary.json").read_text(encoding="utf-8"))
            assert summary_json["response_status"]["relay-host-q1"] == "ok", summary_json

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
