#!/usr/bin/env python3
"""校验元数据重探测的顺序、进度与退避证据。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ValidationError(RuntimeError):
    pass


FAULT = re.compile(
    r"agentmeta_boot_fault: kind=(?P<kind>busy|io|interrupted) "
    r"bank=(?P<bank>[01]) remaining=(?P<remaining>[0-9]+)"
)
DEFERRED = re.compile(
    r"agentmeta_boot_reprobe: deferred attempt=(?P<attempt>[1-9][0-9]*) "
    r"now=(?P<now>0x[0-9a-f]{16}) deadline=(?P<deadline>0x[0-9a-f]{16})"
)
PROGRESS = re.compile(
    r"agentmeta_boot_reprobe: progress sequence=(?P<sequence>0x[0-9a-f]{16}) "
    r"bank=(?P<bank>[01]) phase=(?P<phase>[1-4]) "
    r"offset=(?P<offset>[1-9][0-9]*)"
)
RECOVERED = re.compile(
    r"agentmeta_boot_reprobe: recovered=1 retries=(?P<retries>[1-9][0-9]*)"
)
REJECTED = re.compile(r"agentmeta_boot_reprobe: admission_rejected status=-17")
USER_RETRY = re.compile(
    r"agentmetatransient_ucore: admission_retry=(?P<attempt>[1-9][0-9]*) "
    r"status=-17"
)
TERMINAL = (
    "agentmetatransient_ucore: create_succeeded=1",
    "agentmetatransient_ucore: query_succeeded=1",
    "agentmetatransient_ucore: unavailable_seen=1 recovered=1",
    "agentmetatransient_ucore: parent passed",
)


def _markers(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[int, re.Match[str]]]:
    return [
        (line_number, match)
        for line_number, line in enumerate(lines, start=1)
        if (match := pattern.fullmatch(line)) is not None
    ]


def _validate_exact_markers(lines: list[str]) -> None:
    markers = (
        ("agentmeta_boot_fault:", FAULT),
        ("agentmeta_boot_reprobe: deferred", DEFERRED),
        ("agentmeta_boot_reprobe: progress", PROGRESS),
        ("agentmeta_boot_reprobe: recovered", RECOVERED),
        ("agentmeta_boot_reprobe: admission_rejected", REJECTED),
        ("agentmetatransient_ucore: admission_retry=", USER_RETRY),
    )
    for line_number, line in enumerate(lines, start=1):
        for prefix, pattern in markers:
            if prefix in line and pattern.fullmatch(line) is None:
                raise ValidationError(f"malformed marker at line {line_number}")


def _validate_faults(
    faults: list[tuple[int, re.Match[str]]], fault_kind: str, fault_bank: str
) -> None:
    if fault_bank not in ("all", "0", "1"):
        raise ValidationError(f"invalid fault bank: {fault_bank}")
    expected_banks = {0, 1} if fault_bank == "all" else {int(fault_bank)}
    observed_banks = {int(match["bank"]) for _, match in faults}
    if observed_banks != expected_banks:
        raise ValidationError(
            f"fault banks differ: expected {sorted(expected_banks)!r}, "
            f"observed {sorted(observed_banks)!r}"
        )
    if any(match["kind"] != fault_kind for _, match in faults):
        raise ValidationError("fault kind differs")

    for bank in sorted(expected_banks):
        remaining = [
            int(match["remaining"])
            for _, match in faults
            if int(match["bank"]) == bank
        ]
        if not remaining or remaining[-1] != 0:
            raise ValidationError(f"bank {bank} fault countdown must end at zero")
        if any(current != previous - 1 for previous, current in zip(remaining, remaining[1:])):
            raise ValidationError(
                f"bank {bank} fault countdown is not strictly contiguous: {remaining!r}"
            )


def _validate_progress(
    progress: list[tuple[int, re.Match[str]]], require_progress: bool
) -> None:
    if require_progress and not progress:
        raise ValidationError("at least one progress marker is required")

    previous_sequence: int | None = None
    offsets: dict[tuple[int, int], int] = {}
    for _, match in progress:
        sequence = int(match["sequence"], 16)
        bank = int(match["bank"])
        phase = int(match["phase"])
        offset = int(match["offset"])
        if previous_sequence is not None and sequence <= previous_sequence:
            raise ValidationError("progress sequence must be strictly increasing")
        key = (bank, phase)
        if key in offsets and offset < offsets[key]:
            raise ValidationError(
                f"progress offset regressed for bank {bank} phase {phase}"
            )
        previous_sequence = sequence
        offsets[key] = offset


def _validate_backoff(
    deferred: list[tuple[int, re.Match[str]]], retries: int
) -> None:
    attempts = [int(match["attempt"]) for _, match in deferred]
    if attempts != list(range(1, retries + 1)):
        raise ValidationError(f"deferred attempts must be exactly 1..{retries}")

    previous_deadline: int | None = None
    previous_delay: int | None = None
    for index, (_, match) in enumerate(deferred):
        now = int(match["now"], 16)
        deadline = int(match["deadline"], 16)
        delay = deadline - now
        if delay <= 0 or delay > 100:
            raise ValidationError("retry deadline is not positive and bounded")
        if previous_deadline is not None and now < previous_deadline:
            raise ValidationError("re-probe ran before the preceding deadline")
        if previous_delay is not None and delay < previous_delay:
            raise ValidationError("retry delay decreased")
        if index == 1 and previous_delay is not None and delay <= previous_delay:
            raise ValidationError("initial retry delay did not increase")
        previous_deadline = deadline
        previous_delay = delay


def validate_log(
    raw: bytes,
    fault_kind: str,
    fault_bank: str = "all",
    require_progress: bool = False,
) -> None:
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise ValidationError(f"log is not valid UTF-8: {error}") from error

    _validate_exact_markers(lines)
    guest_failure = re.compile(
        r"(?:^|\s)(?:kernel panic|panic:|assertion failed)(?:\s|$)", re.IGNORECASE
    )
    forbidden = ("check failed", "confirmed corruption", "install_empty")
    for line_number, line in enumerate(lines, start=1):
        if guest_failure.search(line) is not None or any(
            token in line.lower() for token in forbidden
        ):
            raise ValidationError(f"forbidden failure text at line {line_number}")

    faults = _markers(lines, FAULT)
    deferred = _markers(lines, DEFERRED)
    progress = _markers(lines, PROGRESS)
    recovered = _markers(lines, RECOVERED)
    rejected = _markers(lines, REJECTED)
    user_retry = _markers(lines, USER_RETRY)

    _validate_faults(faults, fault_kind, fault_bank)
    _validate_progress(progress, require_progress)

    if len(recovered) != 1:
        raise ValidationError("expected exactly one recovered marker")
    retries = int(recovered[0][1]["retries"])
    if len(faults) != retries + 1:
        raise ValidationError(
            "fault inventory must contain the initial boot failure plus "
            f"one fault for each of {retries} deferred retries"
        )
    _validate_backoff(deferred, retries)

    if not rejected or not user_retry:
        raise ValidationError("fail-closed admission was not observed")
    exact_lines: dict[str, int] = {}
    for marker in TERMINAL:
        found = [index for index, line in enumerate(lines, start=1) if line == marker]
        if len(found) != 1:
            raise ValidationError(f"expected one exact marker: {marker}")
        exact_lines[marker] = found[0]

    recovered_line = recovered[0][0]
    if any(line_number >= recovered_line for line_number, _ in faults):
        raise ValidationError("fault marker appeared after recovery")
    if any(line_number >= recovered_line for line_number, _ in rejected):
        raise ValidationError("admission remained closed after recovery")
    if progress and not (
        rejected[0][0] < progress[0][0] and progress[-1][0] < recovered_line
    ):
        raise ValidationError("progress marker order differs")
    if not (
        rejected[0][0] < deferred[0][0]
        and deferred[-1][0] < recovered_line
        and recovered_line < exact_lines[TERMINAL[0]]
        < exact_lines[TERMINAL[1]]
        < exact_lines[TERMINAL[2]]
        < exact_lines[TERMINAL[3]]
    ):
        raise ValidationError("admission/backoff/recovery marker order differs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument(
        "--fault-kind", required=True, choices=("busy", "io", "interrupted")
    )
    parser.add_argument("--fault-bank", choices=("all", "0", "1"), default="all")
    parser.add_argument(
        "--require-progress",
        action="store_true",
        help="require at least one bounded probe progress marker",
    )
    args = parser.parse_args(argv)
    try:
        validate_log(
            args.log_file.read_bytes(),
            args.fault_kind,
            args.fault_bank,
            args.require_progress,
        )
    except (OSError, ValidationError) as error:
        print(f"metadata reprobe log validation failed: {error}", file=sys.stderr)
        return 1
    print(
        f"metadata reprobe log: ok kind={args.fault_kind} bank={args.fault_bank}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
