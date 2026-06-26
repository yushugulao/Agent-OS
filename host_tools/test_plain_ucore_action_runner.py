#!/usr/bin/env python3
"""Unit checks for plain_ucore_action_runner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import plain_ucore_action_runner as runner


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_dir = root / "state"
        run_dir = root / "run"
        state_dir.mkdir()
        (state_dir / "rp_input").write_text("input=ready\n", encoding="utf-8")
        (state_dir / "rp_web_bundle").write_text("bundle=ready\n", encoding="utf-8")
        (state_dir / "rp_agentcmp").write_text("compare=ready\n", encoding="utf-8")
        (state_dir / "ignore.txt").write_text("ignore\n", encoding="utf-8")

        actions_path = root / "host-actions.jsonl"
        actions = [
            {
                "sequence": 1,
                "path": "/actions/research/run",
                "status": "accepted",
                "payload": {"run_id": "RUN-999", "source": "test"},
            },
            {
                "sequence": 2,
                "path": "/actions/agentcompare/run",
                "status": "accepted",
                "payload": {"profile": "plain_ucore_batch"},
            },
        ]
        actions_path.write_text(
            "\n".join(json.dumps(action, ensure_ascii=False) for action in actions) + "\n",
            encoding="utf-8",
        )

        loaded = runner.read_jsonl(actions_path)
        loaded = runner.append_records(
            loaded,
            [
                {
                    "path": "/actions/research/workbench",
                    "payload": {"workbench": "usable-workbench:RUN-900", "workbench_title": "RUN-900 workbench", "literature_query": "agent workflow provenance"},
                },
                {
                    "path": "/actions/research/workbench-complete",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-advance",
                    "payload": {"workbench": "usable-workbench:RUN-900", "task": "delivery_manifest"},
                },
                {
                    "path": "/actions/research/workbench-auto-advance",
                    "payload": {"workbench": "usable-workbench:RUN-900", "step_limit": "8"},
                },
                {
                    "path": "/actions/research/workbench-answer",
                    "payload": {"workbench": "usable-workbench:RUN-900", "question": "What is ready for review?"},
                },
                {
                    "path": "/actions/research/workbench-answer-audit",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-evidence-search",
                    "payload": {"workbench": "usable-workbench:RUN-900", "query": "recovery evidence"},
                },
                {
                    "path": "/actions/research/workbench-task",
                    "payload": {"workbench": "usable-workbench:RUN-900", "task": "human_review", "status": "waiting"},
                },
                {
                    "path": "/actions/research/workbench-note",
                    "payload": {"workbench": "usable-workbench:RUN-900", "note_kind": "decision", "title": "Scope decision", "body": "Use recovered evidence first."},
                },
                {
                    "path": "/actions/research/workbench-notes",
                    "payload": {"workbench": "usable-workbench:RUN-900", "notes_filter": "decision"},
                },
                {
                    "path": "/actions/research/workbench-handoff-package",
                    "payload": {"workbench": "usable-workbench:RUN-900", "handoff_scope": "full"},
                },
                {
                    "path": "/actions/research/workbench-readiness",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                },
                {
                    "path": "/actions/research/workbench-brief",
                    "payload": {"workbench": "usable-workbench:RUN-900", "brief_format": "html"},
                },
                {
                    "path": "/actions/research/workbench-evidence-dossier",
                    "payload": {"workbench": "usable-workbench:RUN-900", "dossier_format": "markdown"},
                },
                {
                    "path": "/actions/research/workbench-evidence-graph",
                    "payload": {"workbench": "usable-workbench:RUN-900", "graph_format": "dot"},
                },
                {
                    "path": "/actions/research/workbench-citations",
                    "payload": {"workbench": "usable-workbench:RUN-900", "citation_format": "bibtex"},
                },
                {
                    "path": "/actions/research/workbench-manuscript",
                    "payload": {"workbench": "usable-workbench:RUN-900", "manuscript_format": "markdown"},
                },
                {
                    "path": "/actions/research/workbench-manuscript-audit",
                    "payload": {"workbench": "usable-workbench:RUN-900", "audit_scope": "citations"},
                },
                {
                    "path": "/actions/research/workbench-manuscript-revision-plan",
                    "payload": {"workbench": "usable-workbench:RUN-900", "revision_area": "methods"},
                },
                {
                    "path": "/actions/research/workbench-manuscript-revision-task",
                    "payload": {"workbench": "usable-workbench:RUN-900", "revision_task": "1", "revision_status": "done"},
                },
                {
                    "path": "/actions/research/workbench-task-board",
                    "payload": {"workbench": "usable-workbench:RUN-900", "board_filter": "open"},
                },
                {
                    "path": "/actions/research/workbench-task-board-row",
                    "payload": {"workbench": "usable-workbench:RUN-900", "row_id": "usable-workbench:RUN-900:board:task:human_review", "row_status": "done"},
                },
                {
                    "path": "/actions/research/workbench-runbook",
                    "payload": {"workbench": "usable-workbench:RUN-900", "runbook_format": "markdown"},
                },
                {
                    "path": "/actions/research/workbench-timeline",
                    "payload": {"workbench": "usable-workbench:RUN-900", "timeline_format": "html"},
                },
                {
                    "path": "/actions/research/workbench-file-manifest",
                    "payload": {"workbench": "usable-workbench:RUN-900", "manifest": "delivery-manifest.json"},
                },
                {
                    "path": "/actions/research/workbench-file-verify",
                    "payload": {"workbench": "usable-workbench:RUN-900", "manifest": "delivery-manifest.json"},
                },
                {
                    "path": "/actions/research/export-workbench",
                    "payload": {"workbench": "usable-workbench:RUN-900", "bundle": "workbench-bundle.zip"},
                },
                {
                    "path": "/actions/research/review",
                    "payload": {"run_id": "RUN-999", "reviewer": "Wang", "decision": "needs_revision"},
                },
                {
                    "path": "/actions/research/revision-task",
                    "payload": {"review_id": "usable-review:Wang:1", "targets": "methods,chart_caption,statistics"},
                },
                {
                    "path": "/actions/research/export-notebook",
                    "payload": {"format": "ipynb"},
                },
                {
                    "path": "/actions/research/export-bundle",
                    "payload": {"run_id": "RUN-999", "bundle": "reviewer-evidence"},
                },
            ],
        )
        summary = runner.prepare_action_state(loaded, state_dir, run_dir)
        expected_actions = 33

        assert summary["actions"] == expected_actions
        assert summary["accepted"] == expected_actions
        assert "research_run" in summary["kinds"]
        assert "agentcompare" in summary["kinds"]
        assert "workbench" in summary["kinds"]
        assert "workbench_complete" in summary["kinds"]
        assert "workbench_advance" in summary["kinds"]
        assert "workbench_auto_advance" in summary["kinds"]
        assert "workbench_answer" in summary["kinds"]
        assert "workbench_answer_audit" in summary["kinds"]
        assert "workbench_evidence_search" in summary["kinds"]
        assert "workbench_task" in summary["kinds"]
        assert "workbench_note" in summary["kinds"]
        assert "workbench_notes" in summary["kinds"]
        assert "workbench_handoff_package" in summary["kinds"]
        assert "workbench_readiness" in summary["kinds"]
        assert "workbench_brief" in summary["kinds"]
        assert "workbench_evidence_dossier" in summary["kinds"]
        assert "workbench_evidence_graph" in summary["kinds"]
        assert "workbench_citations" in summary["kinds"]
        assert "workbench_manuscript" in summary["kinds"]
        assert "workbench_manuscript_audit" in summary["kinds"]
        assert "workbench_manuscript_revision_plan" in summary["kinds"]
        assert "workbench_manuscript_revision_task" in summary["kinds"]
        assert "workbench_task_board" in summary["kinds"]
        assert "workbench_task_board_row" in summary["kinds"]
        assert "workbench_runbook" in summary["kinds"]
        assert "workbench_timeline" in summary["kinds"]
        assert "workbench_file_manifest" in summary["kinds"]
        assert "workbench_file_verify" in summary["kinds"]
        assert "workbench_export" in summary["kinds"]
        assert "human_review" in summary["kinds"]
        assert "revision_task" in summary["kinds"]
        assert "notebook_export" in summary["kinds"]
        assert "bundle_export" in summary["kinds"]

        next_state = run_dir / "state-next"
        assert (next_state / "rp_input").exists()
        assert (next_state / "rp_web_bundle").exists()
        assert (next_state / "rp_agentcmp").exists()
        assert not (next_state / "ignore.txt").exists()

        queue = read(next_state / "rp_host_action_queue")
        assert "kind=research_run" in queue
        assert "kind=agentcompare" in queue
        assert "kind=workbench" in queue
        assert "kind=workbench_complete" in queue
        assert "kind=workbench_advance" in queue
        assert "kind=workbench_auto_advance" in queue
        assert "kind=workbench_answer" in queue
        assert "kind=workbench_answer_audit" in queue
        assert "kind=workbench_evidence_search" in queue
        assert "kind=workbench_task" in queue
        assert "kind=workbench_note" in queue
        assert "kind=workbench_notes" in queue
        assert "kind=workbench_handoff_package" in queue
        assert "kind=workbench_readiness" in queue
        assert "kind=workbench_brief" in queue
        assert "kind=workbench_evidence_dossier" in queue
        assert "kind=workbench_evidence_graph" in queue
        assert "kind=workbench_citations" in queue
        assert "kind=workbench_manuscript" in queue
        assert "kind=workbench_manuscript_audit" in queue
        assert "kind=workbench_manuscript_revision_plan" in queue
        assert "kind=workbench_manuscript_revision_task" in queue
        assert "kind=workbench_task_board" in queue
        assert "kind=workbench_task_board_row" in queue
        assert "kind=workbench_runbook" in queue
        assert "kind=workbench_timeline" in queue
        assert "kind=workbench_file_manifest" in queue
        assert "kind=workbench_file_verify" in queue
        assert "kind=workbench_export" in queue
        assert "kind=human_review" in queue
        assert "kind=revision_task" in queue
        assert "kind=notebook_export" in queue
        assert "kind=bundle_export" in queue
        assert "run_id=RUN-999" in queue
        assert "reviewer=Wang" in queue
        assert "targets=methods,chart_caption,statistics" in queue
        assert "bundle=reviewer-evidence" in queue
        assert "profile=plain_ucore_batch" in queue
        assert "workbench=usable-workbench:RUN-900" in queue
        assert "workbench_title=RUN-900 workbench" in queue
        assert "literature_query=agent workflow provenance" in queue
        assert "question=What is ready for review?" in queue
        assert "query=recovery evidence" in queue
        assert "task=human_review" in queue
        assert "step_limit=8" in queue
        assert "note_kind=decision" in queue
        assert "title=Scope decision" in queue
        assert "body=Use recovered evidence first." in queue
        assert "notes_filter=decision" in queue
        assert "handoff_scope=full" in queue
        assert "brief_format=html" in queue
        assert "dossier_format=markdown" in queue
        assert "graph_format=dot" in queue
        assert "citation_format=bibtex" in queue
        assert "manuscript_format=markdown" in queue
        assert "audit_scope=citations" in queue
        assert "revision_area=methods" in queue
        assert "revision_task=1" in queue
        assert "revision_status=done" in queue
        assert "board_filter=open" in queue
        assert "row_id=usable-workbench:RUN-900:board:task:human_review" in queue
        assert "row_status=done" in queue
        assert "runbook_format=markdown" in queue
        assert "timeline_format=html" in queue
        assert "manifest=delivery-manifest.json" in queue
        assert "bundle=workbench-bundle.zip" in queue
        assert "status=ready" in queue

        plan = read(next_state / "rp_host_action_plan")
        assert "collect=rp_web_bundle" in plan
        assert "collect=rp_compare_plain" in plan
        assert "kind=workbench" in plan
        assert "kind=workbench_complete" in plan
        assert "kind=workbench_advance" in plan
        assert "kind=workbench_auto_advance" in plan
        assert "kind=workbench_answer" in plan
        assert "kind=workbench_answer_audit" in plan
        assert "kind=workbench_evidence_search" in plan
        assert "kind=workbench_task" in plan
        assert "kind=workbench_note" in plan
        assert "kind=workbench_notes" in plan
        assert "kind=workbench_handoff_package" in plan
        assert "kind=workbench_readiness" in plan
        assert "kind=workbench_brief" in plan
        assert "kind=workbench_evidence_dossier" in plan
        assert "kind=workbench_evidence_graph" in plan
        assert "kind=workbench_citations" in plan
        assert "kind=workbench_manuscript" in plan
        assert "kind=workbench_manuscript_audit" in plan
        assert "kind=workbench_manuscript_revision_plan" in plan
        assert "kind=workbench_manuscript_revision_task" in plan
        assert "kind=workbench_task_board" in plan
        assert "kind=workbench_task_board_row" in plan
        assert "kind=workbench_runbook" in plan
        assert "kind=workbench_timeline" in plan
        assert "kind=workbench_file_manifest" in plan
        assert "kind=workbench_file_verify" in plan
        assert "kind=workbench_export" in plan

        inbox = read(next_state / "rp_host_action_inbox")
        assert "/actions/research/run" in inbox
        assert "/actions/agentcompare/run" in inbox
        assert "/actions/research/workbench" in inbox
        assert "/actions/research/workbench-answer" in inbox
        assert "/actions/research/workbench-answer-audit" in inbox
        assert "/actions/research/workbench-evidence-search" in inbox
        assert "/actions/research/workbench-task" in inbox
        assert "/actions/research/workbench-note" in inbox
        assert "/actions/research/workbench-notes" in inbox
        assert "/actions/research/workbench-handoff-package" in inbox
        assert "/actions/research/workbench-readiness" in inbox
        assert "/actions/research/workbench-brief" in inbox
        assert "/actions/research/workbench-evidence-dossier" in inbox
        assert "/actions/research/workbench-evidence-graph" in inbox
        assert "/actions/research/workbench-citations" in inbox
        assert "/actions/research/workbench-manuscript" in inbox
        assert "/actions/research/workbench-manuscript-audit" in inbox
        assert "/actions/research/workbench-manuscript-revision-plan" in inbox
        assert "/actions/research/workbench-manuscript-revision-task" in inbox
        assert "/actions/research/workbench-task-board" in inbox
        assert "/actions/research/workbench-task-board-row" in inbox
        assert "/actions/research/workbench-runbook" in inbox
        assert "/actions/research/workbench-timeline" in inbox
        assert "/actions/research/workbench-file-manifest" in inbox
        assert "/actions/research/workbench-file-verify" in inbox
        assert "/actions/research/export-workbench" in inbox

        assert (run_dir / "actions.json").exists()
        assert (run_dir / "runner-summary.json").exists()

        assert runner.action_kind("/actions/research/run-revision") == "revision_run"
        assert runner.action_kind("/actions/research/workbench") == "workbench"
        assert runner.action_kind("/actions/research/workbench-advance") == "workbench_advance"
        assert runner.action_kind("/actions/research/workbench-auto-advance") == "workbench_auto_advance"
        assert runner.action_kind("/actions/research/workbench-answer") == "workbench_answer"
        assert runner.action_kind("/actions/research/workbench-answer-audit") == "workbench_answer_audit"
        assert runner.action_kind("/actions/research/workbench-evidence-search") == "workbench_evidence_search"
        assert runner.action_kind("/actions/research/workbench-task") == "workbench_task"
        assert runner.action_kind("/actions/research/workbench-note") == "workbench_note"
        assert runner.action_kind("/actions/research/workbench-notes") == "workbench_notes"
        assert runner.action_kind("/actions/research/workbench-handoff-package") == "workbench_handoff_package"
        assert runner.action_kind("/actions/research/workbench-readiness") == "workbench_readiness"
        assert runner.action_kind("/actions/research/workbench-brief") == "workbench_brief"
        assert runner.action_kind("/actions/research/workbench-evidence-dossier") == "workbench_evidence_dossier"
        assert runner.action_kind("/actions/research/workbench-evidence-graph") == "workbench_evidence_graph"
        assert runner.action_kind("/actions/research/workbench-citations") == "workbench_citations"
        assert runner.action_kind("/actions/research/workbench-manuscript") == "workbench_manuscript"
        assert runner.action_kind("/actions/research/workbench-manuscript-audit") == "workbench_manuscript_audit"
        assert runner.action_kind("/actions/research/workbench-manuscript-revision-plan") == "workbench_manuscript_revision_plan"
        assert runner.action_kind("/actions/research/workbench-manuscript-revision-task") == "workbench_manuscript_revision_task"
        assert runner.action_kind("/actions/research/workbench-task-board") == "workbench_task_board"
        assert runner.action_kind("/actions/research/workbench-task-board-row") == "workbench_task_board_row"
        assert runner.action_kind("/actions/research/workbench-runbook") == "workbench_runbook"
        assert runner.action_kind("/actions/research/workbench-timeline") == "workbench_timeline"
        assert runner.action_kind("/actions/research/workbench-file-manifest") == "workbench_file_manifest"
        assert runner.action_kind("/actions/research/workbench-file-verify") == "workbench_file_verify"
        assert runner.action_kind("/actions/research/export-workbench") == "workbench_export"
        assert runner.action_kind("/actions/research/export-notebook") == "notebook_export"
        assert runner.action_kind("/actions/unknown") == "generic"

        records = runner.write_seed_header(next_state, root)
        header = read(root / "user" / "build" / "generated" / "rp_host_action_seed.h")
        assert records == expected_actions
        assert "#define RP_HOST_ACTION_SEED" in header
        assert "kind=research_run" in header
        assert "kind=workbench" in header
        assert "kind=workbench_answer" in header
        assert "kind=workbench_answer_audit" in header
        assert "kind=workbench_evidence_search" in header
        assert "kind=workbench_task" in header
        assert "kind=workbench_note" in header
        assert "kind=workbench_manuscript" in header
        assert "kind=workbench_task_board_row" in header
        assert "kind=workbench_file_verify" in header
        assert "\\n" in header

        runner.write_run_result_state(
            next_state,
            {
                "passed": True,
                "embedded_action_records": expected_actions,
                "log": str(run_dir / "ucore-run.log"),
            },
            f"rp_web_export: host_reader_actions={expected_actions}\nrp_compare_plain: host_actions={expected_actions} verified\nrp_orch: passed\n",
        )
        result_state = read(next_state / "rp_host_run_result")
        assert "passed=1" in result_state
        assert f"embedded_action_records={expected_actions}" in result_state
        assert f"qemu_rp_web_export: host_reader_actions={expected_actions}" in result_state
        assert f"qemu_rp_compare_plain: host_actions={expected_actions} verified" in result_state
        assert "qemu_orch_passed=1" in result_state

        publish_dir = root / "published"
        runner.publish_next_state(next_state, publish_dir)
        assert (publish_dir / "rp_host_run_result").exists()
        assert (publish_dir / "rp_host_action_inbox").exists()

    print("test_plain_ucore_action_runner: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
