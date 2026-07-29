#!/usr/bin/env python3
"""Host regression probe for canonical research-state append boundaries."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/probes/rp-state-append.c"
HEADERS = (
    ROOT / "user/include/research_platform_state.h",
    ROOT / "baseline_ucore/user/include/research_platform_state.h",
)


def function_text(path: Path, name: str) -> str:
    text = path.read_text(encoding="ascii")
    start = text.index(f"static RP_UNUSED int {name}(")
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated function {name} in {path}")


def main() -> int:
    user_helper, baseline_helper = (
        function_text(path, "rp_append_file") for path in HEADERS
    )
    if user_helper != baseline_helper:
        raise AssertionError("rp_append_file differs between AgentOS and baseline")

    compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
    with tempfile.TemporaryDirectory(prefix="rp-state-append-") as directory:
        temporary = Path(directory)
        for label, header in zip(("agentos", "baseline"), HEADERS):
            binary = temporary / f"rp-state-append-{label}"
            subprocess.run(
                compiler
                + [
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fno-builtin",
                    "-fstack-protector-strong",
                    "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all",
                    "-I",
                    str(PROBE.parent / "rp-evidence-host"),
                    f'-DRP_STATE_HEADER="{header.as_posix()}"',
                    str(PROBE),
                    "-o",
                    str(binary),
                ],
                cwd=ROOT,
                check=True,
            )
            run_dir = temporary / label
            run_dir.mkdir()
            environment = dict(os.environ)
            environment["ASAN_OPTIONS"] = "detect_leaks=0"
            subprocess.run([str(binary)], cwd=run_dir, env=environment, check=True)

    print("[rp-state-append] canonical boundary probes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
