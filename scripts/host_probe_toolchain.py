#!/usr/bin/env python3
"""Portable host-compiler setup for dynamic C regression probes."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


SANITIZER_FLAGS = (
    "-fsanitize=address,undefined",
    "-fno-sanitize-recover=all",
)
UNSANITIZED_OPT_IN = "AGENTOS_ALLOW_UNSANITIZED_HOST_PROBES"


def host_compiler() -> list[str]:
    configured = (
        os.environ.get("HOST_CC")
        or os.environ.get("HOSTCC")
        or os.environ.get("CC")
        or "cc"
    )
    compiler = shlex.split(configured)
    if not compiler:
        raise RuntimeError("HOST_CC must name a host C compiler")
    return compiler


def required_sanitizer_flags(compiler: list[str], directory: Path) -> list[str]:
    source = directory / "sanitizer-smoke.c"
    binary = directory / "sanitizer-smoke"
    source.write_text("int main(void) { return 0; }\n", encoding="ascii")
    failure_detail = "compiler rejected ASan/UBSan"
    try:
        result = subprocess.run(
            compiler + [*SANITIZER_FLAGS, str(source), "-o", str(binary)],
            capture_output=True,
            cwd=directory,
        )
    except OSError as error:
        result = None
        failure_detail = f"host compiler could not start: {error}"
    if result is not None and result.returncode == 0:
        environment = probe_environment(list(SANITIZER_FLAGS))
        try:
            result = subprocess.run(
                [str(binary)],
                capture_output=True,
                cwd=directory,
                env=environment,
            )
        except OSError as error:
            result = None
            failure_detail = f"sanitizer smoke probe could not start: {error}"
        if result is not None and result.returncode == 0:
            return list(SANITIZER_FLAGS)
        if result is not None:
            failure_detail = "ASan/UBSan runtime smoke probe failed"
    opt_in = os.environ.get(UNSANITIZED_OPT_IN, "0")
    if opt_in not in {"0", "1"}:
        raise RuntimeError(f"{UNSANITIZED_OPT_IN} must be 0 or 1")
    if opt_in == "1":
        return []
    raise RuntimeError(
        "ASan/UBSan is unavailable; refusing an unsanitized host probe "
        f"without explicit local-only {UNSANITIZED_OPT_IN}=1 "
        f"({failure_detail})"
    )


def probe_environment(sanitizer_flags: list[str]) -> dict[str, str]:
    environment = dict(os.environ)
    if sanitizer_flags:
        environment["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1"
    return environment


def probe_mode(sanitizer_flags: list[str]) -> str:
    return "ASan/UBSan" if sanitizer_flags else "functional-only; sanitizer unavailable"


def shell_records(directory: Path) -> list[str]:
    """Return a data-only record consumed by host-probe-toolchain.sh."""
    compiler = host_compiler()
    flags = required_sanitizer_flags(compiler, directory)
    environment = probe_environment(flags)
    records = [*(f"compiler\t{item}" for item in compiler)]
    records.extend(f"flag\t{item}" for item in flags)
    if flags:
        records.extend(
            f"environment\t{name}={environment[name]}"
            for name in ("ASAN_OPTIONS", "UBSAN_OPTIONS")
        )
    records.append(f"mode\t{probe_mode(flags)}")
    for record in records:
        if record.count("\t") != 1 or any(
            character in record for character in ("\0", "\r", "\n")
        ):
            raise RuntimeError("host probe toolchain records contain control bytes")
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Select the mandatory Host C probe compiler and sanitizers"
    )
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        parser.error("--directory must name an existing directory")
    try:
        records = shell_records(args.directory)
    except RuntimeError as error:
        print(f"host-probe-toolchain: {error}", file=sys.stderr)
        return 2
    for record in records:
        print(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
