#!/usr/bin/env python3
"""校验测得的 fs_epoch Guest 收据与断电标记顺序。"""

import argparse
import re
import sys
from pathlib import Path


CASES = ("dirty", "inflight", "durable")
BATCH_BLOCKS = 8
MIN_COMMIT_WRITES = BATCH_BLOCKS + 3
MAX_COMMIT_WRITES = BATCH_BLOCKS + 8
RECEIPT_RE = re.compile(
    r"^fsepoch_ucore: (?P<kind>commit_receipt|retry_receipt) "
    r"case=(?P<case>dirty|inflight|durable) payload_blocks=(?P<payload>[0-9]+) "
    r"writes=(?P<writes>[0-9]+) flushes=(?P<flushes>[0-9]+) "
    r"failed=(?P<failed>[0-9]+)$"
)
INFLIGHT_RE = re.compile(
    r"^fsepoch_ucore: inflight_recovery old_blocks=(?P<old>[0-9]+) "
    r"new_blocks=(?P<new>[0-9]+)$"
)


class LogError(ValueError):
    pass


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as error:
        raise LogError(f"cannot read {path}: {error}") from error


def require_once(lines: list[str], record: str, label: str) -> None:
    if lines.count(record) != 1:
        raise LogError(f"{label}: expected exactly one {record!r}")


def index_once(lines: list[str], record: str, label: str) -> int:
    require_once(lines, record, label)
    return lines.index(record)


def receipts(
    lines: list[str], case: str, kind: str
) -> list[tuple[int, int, int, int]]:
    found: list[tuple[int, int, int, int]] = []
    for index, line in enumerate(lines):
        match = RECEIPT_RE.fullmatch(line)
        if match is None or match.group("case") != case or match.group("kind") != kind:
            continue
        payload = int(match.group("payload"))
        writes = int(match.group("writes"))
        flushes = int(match.group("flushes"))
        failed = int(match.group("failed"))
        if (
            payload != BATCH_BLOCKS + 1
            or not MIN_COMMIT_WRITES <= writes <= MAX_COMMIT_WRITES
            or flushes != 3
            or failed != 0
        ):
            raise LogError(f"invalid measured receipt: {line}")
        found.append((index, writes, flushes, failed))
    return found


def verify(case: str, fault: list[str], retry: list[str], final: list[str]) -> None:
    point = {
        "dirty": "before_fsync",
        "inflight": "fsync_enter",
        "durable": "after_fsync",
    }[case]
    marker_index = index_once(
        fault, f"fsepoch_ucore: powercut_window case={case} point={point}", "fault"
    )
    if any("fsepoch_ucore: parent passed" in line for line in fault):
        raise LogError("fault boot reached the final completion marker")
    commit_receipts = receipts(fault, case, "commit_receipt")
    if case == "durable":
        if len(commit_receipts) != 1:
            raise LogError("durable cut lacks one measured commit receipt")
        enter_index = index_once(
            fault,
            "fsepoch_ucore: commit_fsync_enter case=durable",
            "fault",
        )
        if not enter_index < commit_receipts[0][0] < marker_index:
            raise LogError("durable commit receipt order is invalid")
    elif commit_receipts:
        raise LogError("pre-return cut contains a completed commit receipt")
    if case == "inflight" and any(
        line == "fsepoch_ucore: fsync_returned case=inflight" for line in fault
    ):
        raise LogError("delayed cut happened after fsync returned")

    checkpoint = f"fsepoch_ucore: retry_durable_checkpoint case={case}"
    checkpoint_index = index_once(retry, checkpoint, "retry")
    retry_receipts = receipts(retry, case, "retry_receipt")
    if case in ("dirty", "inflight"):
        if len(retry_receipts) != 1:
            raise LogError("fault recovery lacks one measured retry receipt")
        enter_index = index_once(
            retry,
            f"fsepoch_ucore: retry_fsync_enter case={case}",
            "retry",
        )
        if not enter_index < retry_receipts[0][0] < checkpoint_index:
            raise LogError("retry commit receipt order is invalid")
    elif retry_receipts:
        raise LogError("already durable recovery unexpectedly rewrote the group")
    if case == "durable":
        noop_index = index_once(
            retry,
            "fsepoch_ucore: durable_recovery noop_fsync_io=0",
            "retry",
        )
        noop_enter_index = index_once(
            retry,
            "fsepoch_ucore: durable_noop_fsync_enter case=durable",
            "retry",
        )
        if not noop_enter_index < noop_index < checkpoint_index:
            raise LogError("durable no-op fsync order is invalid")
    if case == "inflight":
        matches = [INFLIGHT_RE.fullmatch(line) for line in retry]
        matches = [match for match in matches if match is not None]
        if len(matches) != 1:
            raise LogError("inflight recovery lacks one block-state receipt")
        old = int(matches[0].group("old"))
        new = int(matches[0].group("new"))
        if old == 0 or new == 0 or old + new != BATCH_BLOCKS:
            raise LogError("inflight recovery did not observe a partial flush")
        recovery_index = next(
            index for index, line in enumerate(retry) if INFLIGHT_RE.fullmatch(line)
        )
        retry_enter_index = retry.index(
            "fsepoch_ucore: retry_fsync_enter case=inflight"
        )
        if recovery_index >= retry_enter_index:
            raise LogError("inflight state receipt must precede the retry")

    parent_index = index_once(
        final,
        f"fsepoch_ucore: parent passed case={case} blocks={BATCH_BLOCKS + 1}",
        "final",
    )
    if parent_index != len(final) - 1:
        trailing = [line for line in final[parent_index + 1 :] if line.strip()]
        if any(line.startswith("fsepoch_ucore:") for line in trailing):
            raise LogError("final completion marker is not terminal")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--fault-log", required=True, type=Path)
    parser.add_argument("--retry-log", required=True, type=Path)
    parser.add_argument("--final-log", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        verify(
            args.case,
            read_lines(args.fault_log),
            read_lines(args.retry_log),
            read_lines(args.final_log),
        )
    except LogError as error:
        print(f"fs-epoch-log: {error}", file=sys.stderr)
        return 1
    print(f"fs-epoch-log: case={args.case} measured receipts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
