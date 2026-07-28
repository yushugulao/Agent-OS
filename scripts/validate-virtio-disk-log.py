#!/usr/bin/env python3
"""Validate complete, ordered VirtIO fault-matrix evidence."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class ValidationError(RuntimeError):
    pass


MARKERS = (
    "virtiodisk_ucore: lost-irq passed",
    "virtiodisk_ucore: delayed-progress passed",
    "virtiodisk_ucore: descriptor-pressure passed",
    "virtiodisk_ucore: full-ring-reclaim passed",
    "virtiodisk_ucore: status-errors passed",
    "virtiodisk_ucore: range-rejection passed",
    "virtiodisk_ucore: flush-accounting passed",
    "virtiodisk_ucore: flush-disabled passed",
    "virtiodisk_ucore: timeout-reset passed",
    "virtiodisk_ucore: forged-used-index passed",
    "virtiodisk_ucore: duplicate-used passed",
    "virtiodisk_ucore: stuck-reset passed",
    "virtiodisk_ucore: parent passed",
)
REQUEST_CASES = (
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
)
REQUEST_PREFIX = "virtiodisk_ucore: request case="
REQUEST_RE = re.compile(
    r"^virtiodisk_ucore: request case=([a-z-]+) id=(0x[0-9a-f]{16}) "
    r"type=(\d+) submit=(0x[0-9a-f]{16}) "
    r"complete=(0x[0-9a-f]{16}) result=(-?\d+)$"
)
RANGE_PREFIX = "virtiodisk_ucore: range-rejection id="
RANGE_RE = re.compile(
    r"^virtiodisk_ucore: range-rejection id=(0x[0-9a-f]{16}) "
    r"rejected=(0x[0-9a-f]{16}) submits=(0x[0-9a-f]{16}) "
    r"result=(-?\d+)$"
)
FORBIDDEN = (
    "virtiodisk_ucore: FAIL",
    "virtio_disk_intr status",
    "unknown syscall 549",
    "panic:",
    "kernel panic",
)


def _exact_marker_positions(lines: list[str]) -> list[int]:
    positions = []
    for marker in MARKERS:
        matches = [index for index, line in enumerate(lines) if line == marker]
        if len(matches) != 1:
            raise ValidationError(
                f"marker must occur once as a complete line {marker!r}: "
                f"hits={matches}"
            )
        positions.append(matches[0])
    if positions != sorted(positions):
        raise ValidationError(f"markers out of order: {positions}")
    return positions


def _validate_range_evidence(lines: list[str], positions: list[int],
                             requests: list[tuple[int, str, int]]) -> None:
    candidates = [
        (index, line) for index, line in enumerate(lines)
        if RANGE_PREFIX in line
    ]
    if len(candidates) != 1:
        raise ValidationError(
            f"range provenance must occur once as a complete line: {candidates}"
        )
    line_index, line = candidates[0]
    match = RANGE_RE.fullmatch(line)
    if match is None:
        raise ValidationError(f"malformed range provenance at line {line_index}")
    request_id, rejected, submits, result = match.groups()
    values = (int(request_id, 16), int(rejected, 16),
              int(submits, 16), int(result))
    if values[0] == 0 or values[1:] != (1, 0, -6):
        raise ValidationError(
            "range rejection must attest a non-zero identity, rejected=1, "
            f"submits=0, result=-6; got {values}"
        )
    status_position = positions[MARKERS.index(
        "virtiodisk_ucore: status-errors passed"
    )]
    passed_position = positions[MARKERS.index(
        "virtiodisk_ucore: range-rejection passed"
    )]
    flush_position = positions[MARKERS.index(
        "virtiodisk_ucore: flush-accounting passed"
    )]
    request_by_case = {
        case: (request_line, request_id)
        for request_line, case, request_id in requests
    }
    status_line, status_id = request_by_case["status-errors"]
    flush_line, flush_id = request_by_case["flush-accounting"]
    if not (status_line < status_position < line_index < passed_position <
            flush_line < flush_position):
        raise ValidationError(
            "range provenance order must be status request < status pass < "
            "evidence < range pass < flush request < flush pass"
        )
    if not status_id < values[0] < flush_id:
        raise ValidationError(
            "range request identity must follow status and precede flush"
        )


def _validate_request_traces(lines: list[str]) -> list[tuple[int, str, int]]:
    requests = []
    for index, line in enumerate(lines):
        if REQUEST_PREFIX not in line:
            continue
        match = REQUEST_RE.fullmatch(line)
        if match is None:
            raise ValidationError(f"malformed request trace at line {index}")
        requests.append((index, match.groups()))
    if len(requests) != len(REQUEST_CASES):
        raise ValidationError(f"request trace count mismatch: {len(requests)}")
    previous_id = 0
    validated = []
    for (line_index, groups), expected in zip(requests, REQUEST_CASES):
        case, request_id, request_type, submit, complete, result = groups
        request_id = int(request_id, 16)
        request_type = int(request_type)
        submit = int(submit, 16)
        complete = int(complete, 16)
        result = int(result)
        if (case, request_type, result) != expected:
            raise ValidationError(
                f"request trace mismatch at line {line_index}: {groups}"
            )
        if request_id <= previous_id or complete < submit:
            raise ValidationError(
                f"request identity/tick invariant at line {line_index}: {groups}"
            )
        previous_id = request_id
        validated.append((line_index, case, request_id))
    return validated


def validate_text(text: str) -> list[int]:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    lines = text.splitlines()
    positions = _exact_marker_positions(lines)
    requests = _validate_request_traces(lines)
    _validate_range_evidence(lines, positions, requests)
    for forbidden in FORBIDDEN:
        if forbidden.lower() in text.lower():
            raise ValidationError(f"forbidden guest output: {forbidden!r}")
    return positions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        text = args.log_file.read_bytes().decode("utf-8", errors="strict")
        positions = validate_text(text)
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        print(f"[virtio-disk] log validation failed: {error}", file=sys.stderr)
        return 1
    print(f"[virtio-disk] validated markers={positions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
