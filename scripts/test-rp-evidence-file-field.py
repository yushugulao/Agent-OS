#!/usr/bin/env python3
"""Host regression probe for streaming runtime-evidence field matching."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/probes/rp-evidence-file-field.c"


def main() -> int:
    compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
    with tempfile.TemporaryDirectory(prefix="agentos-evidence-field-") as directory:
        temporary = Path(directory)
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
                "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all",
                "-I",
                str(PROBE.parent / "rp-evidence-host"),
                str(PROBE),
                "-o",
                str(binary),
            ],
            cwd=ROOT,
            check=True,
        )
        environment = dict(os.environ)
        environment["ASAN_OPTIONS"] = "detect_leaks=0"
        subprocess.run([str(binary)], cwd=temporary, env=environment, check=True)

    print("[rp-evidence-field] streaming and malformed-input probes passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
