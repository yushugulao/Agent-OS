#!/usr/bin/env python3
"""校验审查构建唯一允许的编译器参数 profile。"""
from __future__ import annotations

import re
import sys


LEGACY_PROFILE = (
    "-DAGENT_CONTEXT_SYNC_TEST_PROFILE -DWAIT_ATOMIC_TEST_PROFILE"
)
SHOWCASE_PROFILE = re.compile(
    r"-Werror "
    r"-DLABDEMO_RUN_NONCE=0x([0-9a-f]{16})ULL "
    r"-DLABDEMO_SAMPLE_ID=([1-9]|[1-5][0-9]|6[0-4]) "
    r"-DLABDEMO_NATIVE_FIRST=([01])\Z"
)
SHOWCASE_TESTS = "labdemo_ucore labdemo_execprobe_ucore"


def validate(flags: str, chapter: str, tests: str, init_proc: str) -> bool:
    if flags == "" or flags == LEGACY_PROFILE:
        return True
    match = SHOWCASE_PROFILE.fullmatch(flags)
    if match is None or match.group(1) == "0000000000000000":
        return False
    return (
        chapter == "agent"
        and tests == SHOWCASE_TESTS
        and init_proc == "labdemo_ucore"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        return 2
    if not validate(argv[1], argv[2], argv[3], argv[4]):
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
