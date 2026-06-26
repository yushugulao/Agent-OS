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
            runner_timeout=90,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            actions = [
                {"path": "/actions/research/run", "payload": {"run_id": "RUN-E2E", "source": "reader-e2e"}},
                {"path": "/actions/research/review", "payload": {"run_id": "RUN-E2E", "reviewer": "Wang", "decision": "needs_revision"}},
                {"path": "/actions/research/revision-task", "payload": {"review_id": "usable-review:Wang:1", "targets": "methods,chart_caption,statistics"}},
                {"path": "/actions/research/run-revision-task", "payload": {"run_id": "RUN-E2E", "task_id": "usable-revision-task:RUN-E2E:1"}},
                {"path": "/actions/research/workbench-answer", "payload": {"workbench": "usable-workbench:RUN-E2E", "question": "What is ready for review?"}},
                {"path": "/actions/research/workbench-evidence-search", "payload": {"workbench": "usable-workbench:RUN-E2E", "query": "recovery evidence"}},
                {"path": "/actions/research/export-notebook", "payload": {"run_id": "RUN-E2E", "format": "ipynb"}},
                {"path": "/actions/research/export-bundle", "payload": {"run_id": "RUN-E2E", "bundle": "reviewer-evidence"}},
                {"path": "/actions/agentcompare/run", "payload": {"profile": "plain_ucore_batch"}},
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
            assert any("host_action_run_id=RUN-E2E" in line for line in rp_input["lines"]), rp_input
            rp_runner = read_json(base + "/api/state/rp_runner")
            assert any("host_action_status=completed" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_revision_run=usable-run:RUN-E2E-rev2" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_compare=plain_ucore_batch" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_id=usable-workbench:RUN-E2E" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_question=What is ready for review?" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_evidence_query=recovery evidence" in line for line in rp_runner["lines"]), rp_runner
            assert any("host_action_workbench_answer=generated" in line for line in rp_runner["lines"]), rp_runner
            rp_review = read_json(base + "/api/state/rp_review2")
            assert any("host_action_human_review=usable-review:Wang:1" in line for line in rp_review["lines"]), rp_review
            assert any("host_action_review_decision=needs_revision" in line for line in rp_review["lines"]), rp_review
            rp_revision = read_json(base + "/api/state/rp_revision")
            assert any("host_action_revision_task=created" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_revision_targets=methods,chart_caption,statistics" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_revision_task_id=usable-revision-task:RUN-E2E:1" in line for line in rp_revision["lines"]), rp_revision
            assert any("host_action_revision_run=completed" in line for line in rp_revision["lines"]), rp_revision
            rp_report = read_json(base + "/api/state/rp_report_text")
            assert any("host_report_run_id=RUN-E2E" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_reviewer=Wang" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_revision_targets=methods,chart_caption,statistics" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_bundle=reviewer-evidence" in line for line in rp_report["lines"]), rp_report
            assert any("host_report_compare_profile=plain_ucore_batch" in line for line in rp_report["lines"]), rp_report
            rp_manifest = read_json(base + "/api/state/rp_artifact_manifest")
            assert any("host_manifest_run_id=RUN-E2E" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_revision_targets=methods,chart_caption,statistics" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_bundle=reviewer-evidence" in line for line in rp_manifest["lines"]), rp_manifest
            assert any("host_manifest_compare_profile=plain_ucore_batch" in line for line in rp_manifest["lines"]), rp_manifest
            rp_package = read_json(base + "/api/state/rp_package")
            assert any("host_action_export_bundle=ready" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_export_bundle_name=reviewer-evidence" in line for line in rp_package["lines"]), rp_package
            assert any("host_action_bundle_contents=report,manifest,notebook,compare" in line for line in rp_package["lines"]), rp_package
            rp_nbexec = read_json(base + "/api/state/rp_nbexec")
            assert any("host_action_notebook_export=ready" in line for line in rp_nbexec["lines"]), rp_nbexec
            assert any("host_action_notebook_format=ipynb" in line for line in rp_nbexec["lines"]), rp_nbexec
            rp_actionio = read_json(base + "/api/state/rp_actionio")
            assert any("host_action_research_run=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_human_review=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_revision=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_workbench=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_export=1" in line for line in rp_actionio["lines"]), rp_actionio
            assert any("host_action_agentcompare=1" in line for line in rp_actionio["lines"]), rp_actionio
            rp_agentcmp = read_json(base + "/api/state/rp_agentcmp")
            assert any("host_action_compare_requested=1" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            assert any("host_action_compare_profile=plain_ucore_batch" in line for line in rp_agentcmp["lines"]), rp_agentcmp
            rp_api_compare = read_json(base + "/api/state/rp_api_compare")
            assert any("host_action_payload_applied=1" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_run_id=RUN-E2E" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_bundle=reviewer-evidence" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_compare_profile=plain_ucore_batch" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench=usable-workbench:RUN-E2E" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_question=What is ready for review?" in line for line in rp_api_compare["lines"]), rp_api_compare
            assert any("host_action_workbench_query=recovery evidence" in line for line in rp_api_compare["lines"]), rp_api_compare
            rp_result = read_json(base + "/api/state/rp_host_run_result")
            assert rp_result["values"]["qemu_orch_passed"] == "1", rp_result
            assert int(rp_result["values"]["extracted_state_files"]) >= 100, rp_result
            assert any("qemu_rp_compare_plain: host_actions=9 verified" in line for line in rp_result["lines"]), rp_result

            run_html = read_text(base + "/run.html")
            assert "Plain uCore Research" in run_html
            assert "Workbench Tasks" in run_html
            assert "Research Output" in run_html
            assert "RUN-E2E" in run_html
            assert "reviewer-evidence" in run_html
            assert "host_action_revision_run" in run_html
            assert "host_action_workbench_question" in run_html
            artifacts_html = read_text(base + "/artifacts.html")
            assert "Evidence Package" in artifacts_html
            assert "reviewer-evidence" in artifacts_html
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
            compare_html = read_text(base + "/compare.html")
            assert "Compare Summary" in compare_html
            assert "Compare Metrics" in compare_html
            assert "Plain Kernel Signals" in compare_html
            assert "Consistency Signals" in compare_html
            assert "File Scans" in compare_html
            assert "Rebuild Steps" in compare_html
            assert "plain_ucore_batch" in compare_html
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
