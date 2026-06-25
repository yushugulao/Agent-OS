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
                "payload": {"profile": "plain_ucore"},
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
                    "path": "/actions/research/workbench-complete",
                    "payload": {"workbench": "usable-workbench:RUN-900"},
                }
            ],
        )
        summary = runner.prepare_action_state(loaded, state_dir, run_dir)

        assert summary["actions"] == 3
        assert summary["accepted"] == 3
        assert "research_run" in summary["kinds"]
        assert "agentcompare" in summary["kinds"]
        assert "workbench_complete" in summary["kinds"]

        next_state = run_dir / "state-next"
        assert (next_state / "rp_input").exists()
        assert (next_state / "rp_web_bundle").exists()
        assert (next_state / "rp_agentcmp").exists()
        assert not (next_state / "ignore.txt").exists()

        queue = read(next_state / "rp_host_action_queue")
        assert "kind=research_run" in queue
        assert "kind=agentcompare" in queue
        assert "kind=workbench_complete" in queue
        assert "run_id=RUN-999" in queue
        assert "workbench=usable-workbench:RUN-900" in queue
        assert "status=ready" in queue

        plan = read(next_state / "rp_host_action_plan")
        assert "collect=rp_web_bundle" in plan
        assert "collect=rp_compare_plain" in plan
        assert "kind=workbench_complete" in plan

        inbox = read(next_state / "rp_host_action_inbox")
        assert "/actions/research/run" in inbox
        assert "/actions/agentcompare/run" in inbox

        assert (run_dir / "actions.json").exists()
        assert (run_dir / "runner-summary.json").exists()

        assert runner.action_kind("/actions/research/run-revision") == "revision_run"
        assert runner.action_kind("/actions/research/export-notebook") == "notebook_export"
        assert runner.action_kind("/actions/unknown") == "generic"

        records = runner.write_seed_header(next_state, root)
        header = read(root / "user" / "build" / "generated" / "rp_host_action_seed.h")
        assert records == 3
        assert "#define RP_HOST_ACTION_SEED" in header
        assert "kind=research_run" in header
        assert "\\n" in header

    print("test_plain_ucore_action_runner: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
