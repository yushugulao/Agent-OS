#!/usr/bin/env python3
"""Host behavior and mutation tests for the two freestanding printf owners."""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts/check-printf-format-contract.py"
SPEC = importlib.util.spec_from_file_location("printf_contract", CHECKER_PATH)
if SPEC is None or SPEC.loader is None:
    raise SystemExit("cannot load printf format checker")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def expect_mutation_rejected(kernel: str, user: str, owner: str, old: str, new: str) -> None:
    selected = kernel if owner == "kernel" else user
    if old not in selected:
        raise SystemExit(f"mutation anchor drift: {owner}: {old}")
    mutated = selected.replace(old, new, 1)
    try:
        CHECKER.validate_implementation(mutated, owner)
    except CHECKER.ContractError:
        return
    raise SystemExit(f"mutation survived: {owner}: {old}")


def run_probe(source: Path, output: Path) -> None:
    compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
    command = compiler + [
        "-std=gnu11",
        "-Wall",
        "-Werror",
        "-fno-builtin",
        "-fstack-protector-strong",
        "-fsanitize=address,undefined",
        "-fno-sanitize-recover=all",
        "-I",
        str(ROOT / "user/include"),
        str(source),
        "-o",
        str(output),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "detect_leaks=0"
    subprocess.run([str(output)], cwd=ROOT, env=environment, check=True)


def main() -> int:
    kernel = (ROOT / "os/printf.c").read_text(encoding="utf-8")
    user = (ROOT / "user/lib/stdio.c").read_text(encoding="utf-8")
    inventory = CHECKER.validate_sources(kernel, user, ROOT)
    mutations = (
        ("kernel", "char buf[20];", "char buf[16];"),
        ("kernel", "case 'u':", "case 'o':"),
        ("kernel", "value = va_arg(ap, unsigned long long);",
         "value = va_arg(ap, unsigned int);"),
        ("kernel", "integer_conversion(fmt[i + 2] & 0xff)", "0"),
        ("kernel", "va_end(ap);", ""),
        ("user", "char buf[20];", "char buf[16];"),
        ("user", "case 'u':", "case 'o':"),
        ("user", "value = va_arg(ap, long long);", "value = va_arg(ap, int);"),
        ("user", "s[1] == 'l' && integer_conversion(s[2])", "0"),
        ("user", "case '%':\n\t\t\tout(f, percent, 1);",
         "case '%':\n\t\t\tout(f, percent, 2);"),
        ("user", "buffer_len -= r;", "buffer_len = 0;"),
        ("user", "buffer[i] = buffer[i + r];", "buffer[i] = buffer[i];"),
        ("user", "if (buffer_len >= __LINE_WIDTH) {",
         "if (buffer_len > __LINE_WIDTH) {"),
        ("user", "va_end(ap);", ""),
    )
    for mutation in mutations:
        expect_mutation_rejected(kernel, user, *mutation)
    try:
        CHECKER.parse_format("%zu", "format mutant", {})
    except CHECKER.ContractError:
        pass
    else:
        raise SystemExit("mutation survived: unsupported %zu call site")

    with tempfile.TemporaryDirectory(prefix="agentos-printf-") as directory:
        temp = Path(directory)
        run_probe(ROOT / "scripts/probes/kernel-printf-integer.c", temp / "kernel")
        run_probe(ROOT / "scripts/probes/user-printf-integer.c", temp / "user")

    print(
        "[printf-format] host probes and "
        f"{len(mutations) + 1} mutations passed; audited={sum(inventory.values())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
