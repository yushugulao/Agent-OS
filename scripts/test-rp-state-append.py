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
