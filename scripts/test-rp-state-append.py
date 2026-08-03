#!/usr/bin/env python3
"""Host regression probe for canonical research-state append boundaries."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from host_probe_toolchain import (
    host_compiler,
    probe_environment,
    probe_mode,
    required_sanitizer_flags,
)


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/probes/rp-state-append.c"
HEADERS = (
    ROOT / "user/include/research_platform_state.h",
    ROOT / "baseline_ucore/user/include/research_platform_state.h",
)
PLANNERS = (
    ROOT / "user/src/rp_planner.c",
    ROOT / "baseline_ucore/user/src/rp_planner.c",
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
    for name in (
        "rp_open_bounded_append",
        "rp_write_append_suffix",
        "rp_bytes_equal",
        "rp_state_buffer_begin_append",
        "rp_state_buffer_commit",
        "rp_append_file",
    ):
        user_helper, baseline_helper = (
            function_text(path, name) for path in HEADERS
        )
        if user_helper != baseline_helper:
            raise AssertionError(f"{name} differs between AgentOS and baseline")
        if name in {"rp_state_buffer_commit", "rp_append_file"}:
            if "O_TRUNC" in user_helper or "rp_write_file" in user_helper:
                raise AssertionError(f"{name} still rewrites through truncate")
            if "memcmp" in user_helper:
                raise AssertionError(f"{name} depends on unavailable user libc memcmp")

    agentos_planner, baseline_planner = (
        path.read_text(encoding="ascii") for path in PLANNERS
    )
    if agentos_planner != baseline_planner:
        raise AssertionError("planner append policy differs between targets")
    for journal in ("rp_ack", "rp_tool"):
        if f'rp_write_file("{journal}"' in agentos_planner:
            raise AssertionError(f"planner truncates shared append journal {journal}")
        if f'rp_append_file("{journal}"' not in agentos_planner:
            raise AssertionError(f"planner no longer appends shared journal {journal}")

    compiler = host_compiler()
    with tempfile.TemporaryDirectory(prefix="rp-state-append-") as directory:
        temporary = Path(directory)
        sanitizer_flags = required_sanitizer_flags(compiler, temporary)
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
                    "-DRP_STATE_BUFFER_SIZE=64",
                    *sanitizer_flags,
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
            subprocess.run(
                [str(binary)],
                cwd=run_dir,
                env=probe_environment(sanitizer_flags),
                check=True,
            )

    print(
        "[rp-state-append] canonical boundary probes passed; "
        f"mode={probe_mode(sanitizer_flags)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
