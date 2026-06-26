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


STATE_FILES = {
    "rp_web_bundle": """bundle=host-web-ui
reader_contract=host_plain_ucore_v2
reader_contract_version=2
reader_ready=1
reader_views=14
reader_actions=31
reader_payload_files=rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_bio,rp_api_labres,rp_api_pub,rp_api_know,rp_api_runtime,rp_api_action,rp_web_routes
reader_refresh_files=rp_web_routes,rp_api_home,rp_api_run,rp_api_agents,rp_api_evidence,rp_api_compare,rp_api_artifacts,rp_api_data,rp_api_action,rp_web_bundle
reader_required_sections=routes,payloads,actions,live_update,downloads,compare
reader_event_stream=rp_web_bundle
reader_fallback=rp_site
reader_state_source=plain_ucore_files
dynamic_inputs=4
status=ready
""",
    "rp_web_routes": "routes=45\nget_routes=14\npost_routes=31\nstatus=ready\n",
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
    "rp_api_action": "api=actions\nreader_contract=rp_web_bundle\nactions=31\nstatus=ready\n",
    "rp_ui_home": "page=home\nstatus=ready\n",
    "rp_ui_run": "page=run-detail\nstatus=ready\n",
    "rp_ui_agent": "page=agent-detail\ndecision_records=rp_agents,rp_decisions,rp_handoff,rp_deliberation,rp_agent_run\nstatus=ready\n",
    "rp_ui_evidence": "page=evidence-detail\nscreening_decisions=9\nevidence_protocol=usable-evidence-protocol:RUN-900:1\nstatus=ready\n",
    "rp_ui_compare": "page=compare-metrics\npain_file_scans=128\npain_state_convention=1\npain_user_permissions=1\npain_rebuild_steps=6\nstatus=ready\n",
    "rp_runner": "workbench_tasks=9\nstatus=ready\n",
    "rp_artifact": "status=recovered\n",
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
    "rp_agentcmp": "plain_kernel=passed\ntest_cases=693\nhandoffs=6\n",
    "rp_consistency": "checks=113\n",
    "rp_artifact_manifest": "manifest_records=4\n",
    "rp_input": "dynamic_submissions=4\n",
    "rp_dataset_snapshot": "snapshots=2\n",
    "rp_data_quality": "passed=7\n",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as state_tmp, tempfile.TemporaryDirectory() as out_tmp:
        state_dir = Path(state_tmp)
        out_dir = Path(out_tmp)
        for name, text in STATE_FILES.items():
            (state_dir / name).write_text(text, encoding="utf-8")

        summary = plain_ucore_reader.render_site(state_dir, out_dir)
        assert summary["status"] == "ready", summary
        assert summary["pages"] == 8, summary
        assert (out_dir / "index.html").exists()
        assert (out_dir / "run.html").exists()
        assert (out_dir / "api" / "rp_api_home.json").exists()
        index_html = (out_dir / "index.html").read_text(encoding="utf-8")
        assert "Plain uCore Research" in index_html
        assert "State Files" in index_html
        assert "Dynamic Inputs" in index_html
        run_html = (out_dir / "run.html").read_text(encoding="utf-8")
        assert "Research Output" in run_html
        compare_html = (out_dir / "compare.html").read_text(encoding="utf-8")
        assert "Compare Summary" in compare_html
        assert "Compare Metrics" in compare_html
        assert "File Scans" in compare_html
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
        assert "retrylog-a" in evidence_html
        assert "Evidence Protocol" in evidence_html
        assert "usable-evidence-protocol:RUN-900:1" in evidence_html
        assert "plan&gt;data&gt;review&gt;repair&gt;audit" in evidence_html
        assert "Plain Kernel Signals" in compare_html
        assert "Consistency Signals" in compare_html
        artifacts_html = (out_dir / "artifacts.html").read_text(encoding="utf-8")
        assert "Evidence Package" in artifacts_html
        actions_html = (out_dir / "actions.html").read_text(encoding="utf-8")
        assert "Batch Actions" in actions_html
        assert "/actions/research/run" in actions_html

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
            assert (out_dir / "host-actions.jsonl").exists()
            assert "path=/actions/research/run" in (state_dir / "rp_host_action_inbox").read_text(encoding="utf-8")
            assert "qemu_orch_passed=1" in (state_dir / "rp_host_run_result").read_text(encoding="utf-8")
            assert (out_dir / "last-run.json").exists()

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
            assert "/actions/research/export-bundle" in actions_html

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
