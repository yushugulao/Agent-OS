#!/usr/bin/env python3
"""Keep deferred maintenance behind a cheap syscall-return pending edge."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def compact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def require(text: str, fragment: str, message: str) -> None:
    if fragment not in text:
        raise ValueError(message)


def check(root: Path) -> None:
    syscall = compact(root / "os/syscall.c")
    trap = compact(root / "os/trap.c")
    proc = compact(root / "os/proc.c")

    if syscall.count("agent_background_checkpoint();") != 1:
        raise ValueError("syscall path has an unbounded maintenance trigger")
    require(
        syscall,
        "if(agent_background_work_pending()||id==SYS_sched_yield)"
        "agent_background_checkpoint();",
        "syscall return lost its pending-only maintenance safe point",
    )
    if "id!=SYS_agent_performance_snapshot" in syscall:
        raise ValueError("observer syscall policy bypasses the pending edge")
    if "!transaction.fs_epoch_admitted&&fs_epoch_should_commit()" in syscall:
        raise ValueError("unrelated syscalls can still inherit an aged commit")

    require(
        trap,
        "kernel_work_begin_background();agent_background_checkpoint();"
        "kernel_work_end_background();",
        "timer-driven maintenance progress is not bounded as background work",
    )
    require(
        proc,
        "if(t==NULL&&fs_epoch_should_commit()&&fs_epoch_request_begin()==0)",
        "idle writeback fallback is missing",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"background dispatch fast-path check failed: {error}", file=sys.stderr)
        return 1
    print("background dispatch fast-path check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
