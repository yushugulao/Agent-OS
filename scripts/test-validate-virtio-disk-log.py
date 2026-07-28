#!/usr/bin/env python3
"""Mutation tests for the VirtIO fault-matrix log contract."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-virtio-disk-log.py"


class VirtioDiskLogValidatorTests(unittest.TestCase):
    @staticmethod
    def request_line(index: int, case: str, request_type: int, result: int) -> str:
        submit = index * 10
        request_id = index * 10
        return (
            f"virtiodisk_ucore: request case={case} id=0x{request_id:016x} "
            f"type={request_type} submit=0x{submit:016x} "
            f"complete=0x{submit + 1:016x} result={result}"
        )

    @classmethod
    def valid_lines(cls) -> list[str]:
        requests = [
            cls.request_line(index, case, request_type, result)
            for index, (case, request_type, result) in enumerate(
                (
                    ("lost-irq", 0, 0),
                    ("delayed-progress", 0, 0),
                    ("descriptor-pressure", 0, 0),
                    ("status-errors", 0, -2),
                    ("flush-accounting", 4, 0),
                    ("flush-disabled", 4, -2),
                    ("timeout-reset", 0, 0),
                    ("forged-index", 0, -1),
                    ("duplicate-used", 0, 0),
                    ("stuck-reset", 0, -3),
                ),
                start=1,
            )
        ]
        return [
            "unrelated boot output",
            requests[0],
            "virtiodisk_ucore: lost-irq passed",
            requests[1],
            "virtiodisk_ucore: delayed-progress passed",
            requests[2],
            "virtiodisk_ucore: descriptor-pressure passed",
            "virtiodisk_ucore: full-ring-reclaim passed",
            requests[3],
            "virtiodisk_ucore: status-errors passed",
            (
                "virtiodisk_ucore: range-rejection "
                "id=0x000000000000002d rejected=0x0000000000000001 "
                "submits=0x0000000000000000 result=-6"
            ),
            "virtiodisk_ucore: range-rejection passed",
            requests[4],
            "virtiodisk_ucore: flush-accounting passed",
            requests[5],
            "virtiodisk_ucore: flush-disabled passed",
            requests[6],
            "virtiodisk_ucore: timeout-reset passed",
            requests[7],
            "virtiodisk_ucore: forged-used-index passed",
            requests[8],
            "virtiodisk_ucore: duplicate-used passed",
            requests[9],
            "virtiodisk_ucore: stuck-reset passed",
            "virtiodisk_ucore: parent passed",
        ]

    def run_validator(self, lines: list[str] | bytes) -> subprocess.CompletedProcess[str]:
        raw = lines if isinstance(lines, bytes) else ("\n".join(lines) + "\n").encode()
        with tempfile.TemporaryDirectory(prefix="virtio-disk-log-") as directory:
            log = Path(directory) / "guest.log"
            log.write_bytes(raw)
            return subprocess.run(
                [sys.executable, str(VALIDATOR), "--log-file", str(log)],
                cwd=ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def assert_rejected(self, lines: list[str] | bytes, message: str = "") -> None:
        result = self.run_validator(lines)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        if message:
            self.assertIn(message, result.stderr)

    def test_accepts_complete_ordered_range_provenance(self):
        result = self.run_validator(self.valid_lines())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_range_marker_and_provenance_are_both_mandatory(self):
        for index in (10, 11):
            with self.subTest(index=index):
                lines = self.valid_lines()
                del lines[index]
                self.assert_rejected(lines)

    def test_range_counts_and_result_are_measured(self):
        mutations = (
            ("rejected=0x0000000000000001", "rejected=0x0000000000000000"),
            ("submits=0x0000000000000000", "submits=0x0000000000000001"),
            ("result=-6", "result=-1"),
            ("id=0x000000000000002d", "id=0x0000000000000000"),
        )
        for old, new in mutations:
            with self.subTest(new=new):
                lines = self.valid_lines()
                lines[10] = lines[10].replace(old, new)
                self.assert_rejected(lines, "rejected=1")

    def test_duplicate_or_reordered_range_evidence_is_rejected(self):
        lines = self.valid_lines()
        lines.insert(11, lines[10])
        self.assert_rejected(lines, "once")

        lines = self.valid_lines()
        evidence = lines.pop(10)
        lines.insert(12, evidence)
        self.assert_rejected(lines, "range provenance order")

        lines = self.valid_lines()
        lines[10] = lines[10].replace(
            "id=0x000000000000002d", "id=0x0000000000000033"
        )
        self.assert_rejected(lines, "identity must follow status")

    def test_residual_or_prefixed_strings_are_not_evidence(self):
        lines = self.valid_lines()
        lines[11] = "debug: " + lines[11]
        self.assert_rejected(lines, "complete line")

        lines = self.valid_lines()
        lines[10] = "debug: " + lines[10]
        self.assert_rejected(lines, "malformed range provenance")

    def test_malformed_request_trace_does_not_disappear(self):
        lines = self.valid_lines()
        lines[1] += " trailing"
        self.assert_rejected(lines, "malformed request trace")

    def test_invalid_utf8_fails_closed(self):
        raw = ("\n".join(self.valid_lines()) + "\n").encode() + b"\xff"
        self.assert_rejected(raw, "decode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
