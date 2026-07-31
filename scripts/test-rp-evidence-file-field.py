#!/usr/bin/env python3
"""Host regression probe for streaming runtime-evidence field matching."""

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
PROBE = ROOT / "scripts/probes/rp-evidence-file-field.c"


def main() -> int:
    compiler = host_compiler()
    with tempfile.TemporaryDirectory(prefix="agentos-evidence-field-") as directory:
        temporary = Path(directory)
        sanitizer_flags = required_sanitizer_flags(compiler, temporary)
        binary = temporary / "rp-evidence-file-field"
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
                str(PROBE),
                "-o",
                str(binary),
            ],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            [str(binary)],
            cwd=temporary,
            env=probe_environment(sanitizer_flags),
            check=True,
        )

    print(
        "[rp-evidence-field] streaming and malformed-input probes passed; "
        f"mode={probe_mode(sanitizer_flags)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
