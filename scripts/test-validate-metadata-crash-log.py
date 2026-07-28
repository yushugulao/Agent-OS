#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-metadata-crash-log.py"


class MetadataCrashLogValidatorTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def valid_lines(bank: int = 0, phase: int = 3) -> list[str]:
        return [
            "unrelated boot output",
            (
                "agentmetacrash_ucore: baseline_dirty=0x0000000000000029 "
                "baseline_durable=0x0000000000000029 pending=0"
            ),
            "agentmetacrash_ucore: baseline_ready=1 replicated=1",
            (
                "agentmetacrash_ucore: target_armed scope=3 "
                "generation=0x000000000000002a token=0x0000000000000099"
            ),
            (
                "agentmetacrash_ucore: target_bound scope=3 "
                "generation=0x000000000000002a token=0x0000000000000099 "
                "job=0x0000000000000077"
            ),
            (
                "agentmetacrash_ucore: target_fire scope=3 "
                "generation=0x000000000000002a token=0x0000000000000099 "
                f"job=0x0000000000000077 bank={bank} phase={phase}"
            ),
            f"agentmetacrash_ucore: metadata_phase={phase}",
            "unrelated trailing output",
        ]

    def run_validator(
        self, raw: str | bytes, *, bank: str = "primary", phase: int = 3
    ) -> subprocess.CompletedProcess[str]:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="metadata-crash-log-") as directory:
            log = Path(directory) / "guest.log"
            log.write_bytes(raw)
            return subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--log-file",
                    str(log),
                    "--bank",
                    bank,
                    "--phase",
                    str(phase),
                ],
                cwd=ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def assert_rejected(self, lines: list[str] | bytes, message: str = "") -> None:
        raw = lines if isinstance(lines, bytes) else "\n".join(lines) + "\n"
        result = self.run_validator(raw)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        if message:
            self.assertIn(message, result.stderr)

    def test_accepts_primary_and_mirror_attestations(self):
        primary = self.run_validator("\n".join(self.valid_lines()) + "\n")
        self.assertEqual(primary.returncode, 0, primary.stderr)
        mirror = self.run_validator(
            "\r\n".join(self.valid_lines(bank=1, phase=8)) + "\r\n",
            bank="mirror",
            phase=8,
        )
        self.assertEqual(mirror.returncode, 0, mirror.stderr)

    def test_every_required_marker_must_exist(self):
        for index in range(1, 7):
            with self.subTest(index=index):
                lines = self.valid_lines()
                del lines[index]
                self.assert_rejected(lines, "missing marker")

    def test_every_marker_must_be_unique(self):
        for index in range(1, 7):
            with self.subTest(index=index):
                lines = self.valid_lines()
                lines.insert(index + 1, lines[index])
                self.assert_rejected(lines, "duplicate")

    def test_marker_order_is_attested(self):
        lines = self.valid_lines()
        lines[3], lines[4] = lines[4], lines[3]
        self.assert_rejected(lines, "marker order")

    def test_each_marker_prefix_rejects_a_malformed_whole_line(self):
        for index in range(1, 7):
            with self.subTest(index=index):
                lines = self.valid_lines()
                lines[index] += " "
                self.assert_rejected(lines, "malformed")

    def test_prefixed_noise_does_not_turn_a_marker_into_evidence(self):
        lines = self.valid_lines()
        lines[5] = "noise: " + lines[5]
        self.assert_rejected(lines, "malformed fire")

    def test_target_scope_generation_and_token_must_remain_identical(self):
        mutations = (
            (4, "scope=3", "scope=4"),
            (5, "generation=0x000000000000002a", "generation=0x000000000000002b"),
            (4, "token=0x0000000000000099", "token=0x0000000000000098"),
        )
        for index, old, new in mutations:
            with self.subTest(field=old.split("=")[0]):
                lines = self.valid_lines()
                lines[index] = lines[index].replace(old, new)
                self.assert_rejected(lines, "identity differs")

    def test_bound_and_fired_job_must_match(self):
        lines = self.valid_lines()
        lines[5] = lines[5].replace(
            "job=0x0000000000000077", "job=0x0000000000000078"
        )
        self.assert_rejected(lines, "job differs")

    def test_generation_must_follow_quiet_baseline(self):
        lines = [
            line.replace(
                "generation=0x000000000000002a",
                "generation=0x000000000000002b",
            )
            for line in self.valid_lines()
        ]
        self.assert_rejected(lines, "does not follow baseline")

    def test_fired_bank_must_match_cli_bank(self):
        lines = self.valid_lines(bank=1)
        self.assert_rejected(lines, "fired bank")

    def test_fired_phase_must_match_cli_phase(self):
        lines = self.valid_lines(phase=4)
        lines[6] = "agentmetacrash_ucore: metadata_phase=3"
        self.assert_rejected(lines, "fired phase")

    def test_terminal_phase_must_match_cli_phase(self):
        lines = self.valid_lines()
        lines[6] = "agentmetacrash_ucore: metadata_phase=4"
        self.assert_rejected(lines, "terminal metadata phase")

    def test_baseline_must_be_quiet(self):
        mutations = (
            (
                "baseline_durable=0x0000000000000029",
                "baseline_durable=0x0000000000000028",
            ),
            ("pending=0", "pending=1"),
        )
        for old, new in mutations:
            with self.subTest(new=new):
                lines = self.valid_lines()
                lines[1] = lines[1].replace(old, new)
                self.assert_rejected(lines, "baseline is not quiet")

    def test_scope_token_and_job_must_be_nonzero(self):
        mutations = (
            ("scope=3", "scope=0"),
            ("token=0x0000000000000099", "token=0x0000000000000000"),
            ("job=0x0000000000000077", "job=0x0000000000000000"),
        )
        for old, new in mutations:
            with self.subTest(field=old.split("=")[0]):
                lines = [line.replace(old, new) for line in self.valid_lines()]
                self.assert_rejected(lines, "must be non-zero")

    def test_invalid_utf8_fails_closed(self):
        raw = ("\n".join(self.valid_lines()) + "\n").encode("utf-8") + b"\xff"
        self.assert_rejected(raw, "not valid UTF-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
