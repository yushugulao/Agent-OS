#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "metadata_reprobe_log",
    ROOT / "scripts/validate-metadata-reprobe-log.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MetadataReprobeLogTests(unittest.TestCase):
    @staticmethod
    def valid_lines(
        kind: str = "busy",
        fault_bank: int | None = None,
        fault_count: int = 3,
        progress: bool = False,
        grouped_faults: bool = False,
    ) -> list[str]:
        banks = (0, 1) if fault_bank is None else (fault_bank,)
        if grouped_faults:
            fault_events = [
                f"agentmeta_boot_fault: kind={kind} bank={bank} remaining={remaining}"
                for bank in banks
                for remaining in range(fault_count - 1, -1, -1)
            ]
        else:
            fault_events = [
                f"agentmeta_boot_fault: kind={kind} bank={bank} remaining={remaining}"
                for remaining in range(fault_count - 1, -1, -1)
                for bank in banks
            ]
        retries = len(fault_events) - 1
        lines = [
            fault_events[0],
            "agentmeta_boot_reprobe: admission_rejected status=-17",
        ]

        if progress:
            primary = banks[-1]
            secondary = banks[0]
            lines.extend(
                (
                    "agentmeta_boot_reprobe: progress "
                    f"sequence=0x0000000000000001 bank={primary} phase=1 offset=64",
                    "agentmeta_boot_reprobe: progress "
                    f"sequence=0x0000000000000002 bank={primary} phase=1 offset=128",
                    "agentmeta_boot_reprobe: progress "
                    f"sequence=0x0000000000000003 bank={secondary} phase=4 offset=16",
                )
            )

        now = 0x10
        for attempt, fault in enumerate(fault_events[1:], start=1):
            lines.append(fault)
            delay = 1 << min(attempt, 6)
            deadline = now + delay
            lines.extend(
                (
                    "agentmeta_boot_reprobe: deferred "
                    f"attempt={attempt} now=0x{now:016x} deadline=0x{deadline:016x}",
                    f"agentmetatransient_ucore: admission_retry={attempt} status=-17",
                    "agentmeta_boot_reprobe: admission_rejected status=-17",
                )
            )
            now = deadline

        lines.extend(
            (
                f"agentmeta_boot_reprobe: recovered=1 retries={retries}",
                f"agentmetatransient_ucore: admission_retry={retries + 1} status=-17",
                "agentmetatransient_ucore: create_succeeded=1",
                "agentmetatransient_ucore: query_succeeded=1",
                "agentmetatransient_ucore: unavailable_seen=1 recovered=1",
                "agentmetatransient_ucore: parent passed",
            )
        )
        return lines

    def validate(
        self,
        lines: list[str],
        kind: str = "busy",
        fault_bank: int | None = None,
        require_progress: bool = False,
    ) -> None:
        MODULE.validate_log(
            ("\n".join(lines) + "\n").encode(),
            kind,
            "all" if fault_bank is None else str(fault_bank),
            require_progress,
        )

    def assert_rejected(
        self,
        lines: list[str],
        kind: str = "busy",
        fault_bank: int | None = None,
        require_progress: bool = False,
    ) -> None:
        with self.assertRaises(MODULE.ValidationError):
            self.validate(lines, kind, fault_bank, require_progress)

    @staticmethod
    def line_index(lines: list[str], text: str) -> int:
        return next(index for index, line in enumerate(lines) if text in line)

    def test_accepts_fault_kinds_and_target_banks(self):
        self.validate(self.valid_lines())
        self.validate(self.valid_lines("io"), "io")
        self.validate(self.valid_lines(fault_bank=0), fault_bank=0)
        self.validate(self.valid_lines("io", 1), "io", 1)
        self.validate(self.valid_lines("interrupted", 1), "interrupted", 1)

    def test_cross_bank_faults_need_not_be_interleaved(self):
        self.validate(self.valid_lines(grouped_faults=True))

    def test_dynamic_positive_retry_count(self):
        self.validate(self.valid_lines(fault_bank=0, fault_count=2), fault_bank=0)
        self.validate(self.valid_lines(fault_bank=0, fault_count=5), fault_bank=0)

    def test_fault_countdown_is_complete_strict_and_kind_exact(self):
        lines = self.valid_lines()
        del lines[self.line_index(lines, "bank=0 remaining=1")]
        self.assert_rejected(lines)

        lines = self.valid_lines()
        first = self.line_index(lines, "bank=0 remaining=2")
        second = self.line_index(lines, "bank=0 remaining=1")
        lines[first], lines[second] = lines[second], lines[first]
        self.assert_rejected(lines)

        lines = self.valid_lines()
        lines[self.line_index(lines, "bank=1 remaining=0")] = (
            "agentmeta_boot_fault: kind=busy bank=1 remaining=1"
        )
        self.assert_rejected(lines)

        lines = self.valid_lines()
        index = self.line_index(lines, "agentmeta_boot_fault:")
        lines[index] = lines[index].replace("busy", "io")
        self.assert_rejected(lines)

    def test_fault_total_is_initial_failure_plus_deferred_retries(self):
        lines = self.valid_lines()
        self.assertEqual(sum("agentmeta_boot_fault:" in line for line in lines), 6)
        self.assertEqual(
            sum("agentmeta_boot_reprobe: deferred" in line for line in lines), 5
        )
        self.validate(lines)

        lines = self.valid_lines()
        del lines[self.line_index(lines, "bank=0 remaining=2")]
        self.assert_rejected(lines)

        lines = self.valid_lines()
        lines.insert(
            self.line_index(lines, "agentmeta_boot_fault:"),
            "agentmeta_boot_fault: kind=busy bank=0 remaining=3",
        )
        self.assert_rejected(lines)

        lines = self.valid_lines()
        recovered = self.line_index(lines, "recovered=1 retries=5")
        lines.insert(
            recovered,
            "agentmeta_boot_reprobe: deferred attempt=6 "
            "now=0x000000000000004e deadline=0x000000000000008e",
        )
        recovered = self.line_index(lines, "recovered=1 retries=5")
        lines[recovered] = lines[recovered].replace("retries=5", "retries=6")
        self.assert_rejected(lines)

    def test_single_bank_profile_rejects_an_unexpected_bank(self):
        lines = self.valid_lines(fault_bank=0)
        lines.insert(1, "agentmeta_boot_fault: kind=busy bank=1 remaining=0")
        self.assert_rejected(lines, fault_bank=0)

    def test_progress_is_exact_monotonic_and_not_a_failure_attempt(self):
        lines = self.valid_lines(progress=True)
        self.validate(lines, require_progress=True)
        self.assertEqual(
            sum("agentmeta_boot_reprobe: deferred" in line for line in lines), 5
        )
        self.assertEqual(
            sum("agentmeta_boot_reprobe: progress" in line for line in lines), 3
        )

    def test_catalog_phase_four_progress_is_accepted(self):
        lines = self.valid_lines(progress=True)
        self.assertTrue(any("phase=4" in line for line in lines))
        self.validate(lines, require_progress=True)

    def test_require_progress_rejects_a_log_without_it(self):
        self.assert_rejected(self.valid_lines(), require_progress=True)

    def test_progress_marker_fields_and_whitespace_are_strict(self):
        mutations = (
            (" bank=1 phase=1", " bank=2 phase=1"),
            (" phase=1 offset=64", " phase=5 offset=64"),
            (" offset=64", " offset=0"),
            ("offset=64", "offset=64 "),
        )
        for old, new in mutations:
            with self.subTest(mutation=new):
                lines = self.valid_lines(progress=True)
                index = self.line_index(lines, "progress sequence=0x0000000000000001")
                lines[index] = lines[index].replace(old, new)
                self.assert_rejected(lines, require_progress=True)

    def test_progress_sequence_mutations_are_rejected(self):
        for replacement in (
            "sequence=0x0000000000000001",
            "sequence=0x0000000000000000",
        ):
            with self.subTest(sequence=replacement):
                lines = self.valid_lines(progress=True)
                index = self.line_index(lines, "progress sequence=0x0000000000000002")
                lines[index] = lines[index].replace(
                    "sequence=0x0000000000000002", replacement
                )
                self.assert_rejected(lines, require_progress=True)

    def test_progress_offset_is_monotonic_per_bank_and_phase(self):
        self.validate(self.valid_lines(progress=True), require_progress=True)
        lines = self.valid_lines(progress=True)
        index = self.line_index(lines, "progress sequence=0x0000000000000002")
        lines[index] = lines[index].replace("offset=128", "offset=32")
        self.assert_rejected(lines, require_progress=True)

    def test_retry_count_and_deferred_attempts_must_agree(self):
        lines = self.valid_lines()
        index = self.line_index(lines, "recovered=1 retries=5")
        lines[index] = lines[index].replace("retries=5", "retries=6")
        self.assert_rejected(lines)

        lines = self.valid_lines()
        index = self.line_index(lines, "deferred attempt=2")
        lines[index] = lines[index].replace("attempt=2", "attempt=3")
        self.assert_rejected(lines)

        lines = self.valid_lines()
        index = self.line_index(lines, "deferred attempt=2")
        lines.insert(index, lines[self.line_index(lines, "deferred attempt=1")])
        self.assert_rejected(lines)

    def test_deadlines_are_positive_bounded_and_obeyed(self):
        lines = self.valid_lines()
        index = self.line_index(lines, "deferred attempt=1")
        lines[index] = lines[index].replace(
            "deadline=0x0000000000000012", "deadline=0x0000000000000010"
        )
        self.assert_rejected(lines)

        lines = self.valid_lines()
        index = self.line_index(lines, "deferred attempt=1")
        lines[index] = lines[index].replace(
            "deadline=0x0000000000000012", "deadline=0x0000000000000080"
        )
        self.assert_rejected(lines)

        lines = self.valid_lines()
        index = self.line_index(lines, "deferred attempt=2")
        lines[index] = lines[index].replace(
            "now=0x0000000000000012", "now=0x0000000000000011"
        )
        self.assert_rejected(lines)

    def test_backoff_must_initially_increase_and_never_decrease(self):
        lines = self.valid_lines()
        index = self.line_index(lines, "deferred attempt=2")
        lines[index] = lines[index].replace(
            "deadline=0x0000000000000016", "deadline=0x0000000000000014"
        )
        self.assert_rejected(lines)

        lines = self.valid_lines(fault_bank=0, fault_count=4)
        index = self.line_index(lines, "deferred attempt=3")
        lines[index] = lines[index].replace(
            "deadline=0x000000000000001e", "deadline=0x0000000000000017"
        )
        self.assert_rejected(lines, fault_bank=0)

    def test_terminal_markers_are_unique_and_ordered(self):
        lines = self.valid_lines()
        lines.insert(
            self.line_index(lines, "create_succeeded=1"),
            "agentmeta_boot_reprobe: recovered=1 retries=5",
        )
        self.assert_rejected(lines)

        lines = self.valid_lines()
        recovered = self.line_index(lines, "recovered=1 retries=5")
        query = self.line_index(lines, "query_succeeded=1")
        lines[recovered], lines[query] = lines[query], lines[recovered]
        self.assert_rejected(lines)

    def test_fault_rejection_and_progress_must_precede_recovery(self):
        for marker in (
            "agentmeta_boot_fault:",
            "agentmeta_boot_reprobe: admission_rejected",
            "agentmeta_boot_reprobe: progress",
        ):
            with self.subTest(marker=marker):
                lines = self.valid_lines(progress=True)
                index = self.line_index(lines, marker)
                moved = lines.pop(index)
                lines.insert(self.line_index(lines, "create_succeeded=1"), moved)
                self.assert_rejected(lines)

    def test_failure_text_and_invalid_utf8_are_rejected(self):
        lines = self.valid_lines()
        lines.insert(4, "agentmetatransient_ucore: check failed: injected")
        self.assert_rejected(lines)
        lines = self.valid_lines()
        lines.insert(4, "panic: injected guest failure")
        self.assert_rejected(lines)
        with self.assertRaises(MODULE.ValidationError):
            MODULE.validate_log(b"\xff", "busy")

    def test_path_containing_panic_is_not_a_guest_failure(self):
        lines = self.valid_lines()
        lines.insert(4, "build/riscv64/ch6b_panic/agentmetatransient_ucore")
        self.validate(lines)

    def test_cli_require_progress_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "reprobe.log"
            output = io.StringIO()
            log.write_text("\n".join(self.valid_lines(progress=True)) + "\n")
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(
                    MODULE.main(
                        [
                            "--log-file",
                            str(log),
                            "--fault-kind",
                            "busy",
                            "--require-progress",
                        ]
                    ),
                    0,
                )
            log.write_text("\n".join(self.valid_lines()) + "\n")
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(
                    MODULE.main(
                        [
                            "--log-file",
                            str(log),
                            "--fault-kind",
                            "busy",
                            "--require-progress",
                        ]
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
