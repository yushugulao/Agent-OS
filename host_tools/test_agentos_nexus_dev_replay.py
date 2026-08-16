#!/usr/bin/env python3
"""Deterministic replay of the Nexus write, debug, build, and Guest-test loop."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import agentos_nexus_task_ledger as ledger


FIXTURE = ROOT / "ci" / "agentos-nexus-dev-replay.jsonl"


class NexusDevelopmentReplayTests(unittest.TestCase):
    def replay(self) -> tuple[str, str, tuple[str, ...]]:
        task_ledger = ledger.NexusTaskLedger(require_kernel_identity=False)
        records = [
            json.loads(line)
            for line in FIXTURE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 8)
        self.assertEqual(records[-1].get("final"), True)

        for index, record in enumerate(records[:-1]):
            self.assertEqual(set(record), {"tool", "workspace_generation", "result"})
            task_ledger._record_development_result(
                record["tool"], record["workspace_generation"], record["result"]
            )
            if index == 1:
                self.assertEqual(task_ledger._development_build_id, "")
            if index == 2:
                self.assertEqual(task_ledger._development_case_kinds, set())

        final = records[-1]
        observed = (
            task_ledger._development_source_revision,
            task_ledger._development_build_id,
            tuple(sorted(task_ledger._development_case_kinds)),
        )
        self.assertEqual(
            observed,
            (
                final["source_revision"],
                final["build_id"],
                tuple(final["case_kinds"]),
            ),
        )
        return observed

    def test_replay_is_exhaustive_and_deterministic(self) -> None:
        first = self.replay()
        second = self.replay()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
