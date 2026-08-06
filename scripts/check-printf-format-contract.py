#!/usr/bin/env python3
"""Fail closed when kernel or user printf drifts from the integer ABI."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path


CALL_NAMES = ("printf", "panic", "errorf", "warnf", "infof", "debugf", "tracef")
CALL_RE = re.compile(r"\b(" + "|".join(CALL_NAMES) + r")\s*\(\s*")
STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)
SUPPORTED = {
    "%d", "%u", "%x", "%ld", "%lu", "%lx",
    "%lld", "%llu", "%llx", "%p", "%c", "%s", "%%",
}


class ContractError(RuntimeError):
    pass


def require(source: str, tokens: tuple[str, ...], owner: str) -> None:
    for token in tokens:
        if token not in source:
            raise ContractError(f"{owner}: missing {token}")


def validate_implementation(source: str, owner: str) -> None:
    require(
        source,
        (
            "static void printint(unsigned long long value, int base, int negative)",
            "char buf[20];",
            "static int integer_conversion(int c)",
            "return c == 'd' || c == 'u' || c == 'x';",
            "case 'd':",
            "case 'u':",
            "case 'x':",
            "value = va_arg(ap, int);",
            "value = va_arg(ap, unsigned int);",
            "value = va_arg(ap, long);",
            "value = va_arg(ap, unsigned long);",
            "value = va_arg(ap, long long);",
            "value = va_arg(ap, unsigned long long);",
            "0ULL - (unsigned long long)value",
            "printint(value,",
            "va_end(ap);",
        ),
        owner,
    )
    if "snprintf" in source or "sprintf" in source or "char buf[base]" in source:
        raise ContractError(f"{owner}: integer formatting gained an unsafe dependency")
    if owner == "kernel":
        require(
            source,
            (
                "fmt[i + 1] == 'l' &&",
                "integer_conversion(fmt[i + 2] & 0xff)",
                "integer_conversion(fmt[i + 1] & 0xff)",
                "printptr(va_arg(ap, uint64));",
                "case 'c':\n\t\t\tconsputc(va_arg(ap, int));",
                "case '%':\n\t\t\tconsputc('%');",
            ),
            owner,
        )
    else:
        require(
            source,
            (
                "s[1] == 'l' && integer_conversion(s[2])",
                "integer_conversion(s[1])",
                "printptr(va_arg(ap, uint64));",
                "char byte = (char)va_arg(ap, int);",
                "out(f, &byte, 1);",
                "case '%':\n\t\t\tout(f, percent, 1);",
                "while (buffer_len > 0)",
                "if (r <= 0)",
                "buffer_len -= r;",
                "buffer[i] = buffer[i + r];",
            ),
            owner,
        )
        flush_start = source.index("int __fflush()")
        flush_end = source.index("int fflush(", flush_start)
        if "__clear_buffer();" in source[flush_start:flush_end]:
            raise ContractError("user: fflush discards an unwritten suffix")
        output_start = source.index("static int out_unlocked(")
        output_end = source.index("static int out(", output_start)
        output = source[output_start:output_end]
        predrain = output.find("if (buffer_len >= __LINE_WIDTH)")
        append = output.find("buffer[buffer_len++] = c;")
        if predrain < 0 or append < 0 or predrain > append or output.count(
            "__write_buffer()"
        ) < 2:
            raise ContractError("user: a retained full buffer can overflow")


def adjacent_string(source: str, offset: int) -> tuple[str, int]:
    parts: list[str] = []
    cursor = offset
    while True:
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        match = STRING_RE.match(source, cursor)
        if match is None:
            break
        parts.append(match.group()[1:-1])
        cursor = match.end()
    return "".join(parts), cursor


def parse_format(fmt: str, location: str, inventory: Counter[str]) -> None:
    cursor = 0
    while cursor < len(fmt):
        if fmt[cursor] != "%":
            cursor += 1
            continue
        if cursor + 1 >= len(fmt):
            raise ContractError(f"{location}: trailing percent in format literal")
        if fmt.startswith("%ll", cursor) and cursor + 3 < len(fmt):
            token = fmt[cursor : cursor + 4]
        elif fmt.startswith("%l", cursor) and cursor + 2 < len(fmt):
            token = fmt[cursor : cursor + 3]
        else:
            token = fmt[cursor : cursor + 2]
        if token not in SUPPORTED:
            raise ContractError(f"{location}: unsupported printf token {token!r}")
        inventory[token] += 1
        cursor += len(token)


def audit_calls(root: Path) -> Counter[str]:
    inventory: Counter[str] = Counter()
    for tree in (root / "os", root / "user"):
        paths = set(tree.rglob("*.c")) | set(tree.rglob("*.h"))
        for path in sorted(paths):
            if path in {
                root / "os/log.h",
                root / "os/printf.h",
                root / "user/include/stdio.h",
                root / "user/include/stdlib.h",
            }:
                continue
            source = path.read_text(encoding="utf-8")
            for match in CALL_RE.finditer(source):
                prefix = source[max(0, match.start() - 16) : match.start()]
                if path.name in {"printf.c", "stdio.c"} and re.search(
                    r"\bvoid\s*$", prefix
                ):
                    continue
                fmt, _ = adjacent_string(source, match.end())
                if not fmt:
                    line = source.count("\n", 0, match.start()) + 1
                    raise ContractError(
                        f"{path.relative_to(root)}:{line}: non-literal {match.group(1)} format"
                    )
                line = source.count("\n", 0, match.start()) + 1
                parse_format(fmt, f"{path.relative_to(root)}:{line}", inventory)
    return inventory


def validate_sources(kernel: str, user: str, root: Path) -> Counter[str]:
    validate_implementation(kernel, "kernel")
    validate_implementation(user, "user")
    return audit_calls(root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = validate_sources(
        (root / "os/printf.c").read_text(encoding="utf-8"),
        (root / "user/lib/stdio.c").read_text(encoding="utf-8"),
        root,
    )
    rendered = " ".join(f"{key}={inventory[key]}" for key in sorted(inventory))
    print(f"[printf-format] static contract passed: {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
