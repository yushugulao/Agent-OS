#!/usr/bin/env python3
"""Static mutation regressions for user/kernel trap failure separation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAP = ROOT / "os" / "trap.c"

NON_CODE = re.compile(
    r"/\*.*?\*/|//[^\r\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.S,
)


class ContractError(RuntimeError):
    pass


def code_only(source: str) -> str:
    return NON_CODE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        source,
    )


def matching_brace(source: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ContractError("unterminated brace block")


def function(source: str, name: str, declaration: str) -> tuple[str, tuple[int, int]]:
    code = code_only(source)
    match = re.search(rf"(?m)^[ \t]*{declaration}[ \t\r\n]*\{{", code)
    if match is None:
        raise ContractError(f"missing required declaration for {name}")
    opening = code.find("{", match.start())
    closing = matching_brace(code, opening)
    return code[opening + 1 : closing], (match.start(), closing + 1)


def conditional_block(source: str, condition: str, label: str) -> str:
    match = re.search(rf"\bif\s*\(\s*{condition}\s*\)\s*\{{", source)
    if match is None:
        raise ContractError(f"missing {label} branch")
    opening = source.find("{", match.start())
    closing = matching_brace(source, opening)
    return source[opening + 1 : closing]


def validate(source: str) -> None:
    helper, helper_span = function(
        source,
        "unknown_user_trap",
        r"static\s+void\s+unknown_user_trap\s*\(\s*void\s*\)",
    )
    if len(re.findall(r"\bexit\s*\(\s*-1\s*\)\s*;", helper)) != 1:
        raise ContractError("unknown-user helper must own exactly one exit(-1)")
    if re.search(r"\bpanic\s*\(|\bSSTATUS_SPP\b", helper):
        raise ContractError("unknown-user helper regained a supervisor path")

    devintr, _ = function(
        source,
        "devintr",
        r"static\s+int\s+devintr\s*\(\s*uint64\s+cause\s*\)",
    )
    forbidden = re.search(r"\b(?:exit|unknown_user_trap)\s*\(", devintr)
    if forbidden is not None:
        raise ContractError("devintr must classify interrupts without process teardown")
    switch = re.search(r"\bswitch\s*\(\s*cause\s*\)\s*\{", devintr)
    if switch is None:
        raise ContractError("devintr lost its cause classifier")
    opening = devintr.find("{", switch.start())
    switch_body = devintr[opening + 1 : matching_brace(devintr, opening)]
    labels = list(
        re.finditer(r"\bcase\s+([A-Za-z_][A-Za-z0-9_]*)\s*:|\b(default)\s*:", switch_body)
    )
    names = [match.group(1) or match.group(2) for match in labels]
    expected = {
        "SupervisorTimer": "1",
        "SupervisorExternal": "1",
        "default": "0",
    }
    if names != list(expected):
        raise ContractError(f"devintr cause labels drifted: {names}")
    for index, match in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(switch_body)
        returns = re.findall(r"\breturn\s+([01])\s*;", switch_body[match.end() : end])
        if returns != [expected[names[index]]]:
            raise ContractError(f"devintr {names[index]} result must be {expected[names[index]]}")

    usertrap, user_span = function(
        source,
        "usertrap",
        r"void\s+usertrap\s*\(\s*\)",
    )
    user_interrupt = conditional_block(
        usertrap,
        r"cause\s*&\s*\(\s*1ULL\s*<<\s*63\s*\)",
        "user interrupt",
    )
    user_route = re.compile(
        r"\bif\s*\(\s*!\s*devintr\s*\(\s*cause\s*&\s*0xff\s*\)\s*\)"
        r"\s*(?:\{\s*)?unknown_user_trap\s*\(\s*\)\s*;(?:\s*\})?"
    )
    if user_route.search(user_interrupt) is None:
        raise ContractError("usertrap must send an unhandled interrupt to the user exit helper")
    if len(re.findall(r"\bdevintr\s*\(\s*cause\s*&\s*0xff\s*\)", user_interrupt)) != 1:
        raise ContractError("usertrap must classify each interrupt exactly once")

    kerneltrap, _ = function(
        source,
        "kerneltrap",
        r"void\s+kerneltrap\s*\(\s*\)",
    )
    if re.search(r"\b(?:exit|unknown_user_trap)\s*\(", kerneltrap):
        raise ContractError("kerneltrap must not reach process teardown")
    kernel_interrupt = conditional_block(
        kerneltrap,
        r"scause\s*&\s*\(\s*1ULL\s*<<\s*63\s*\)",
        "kernel interrupt",
    )
    kernel_unhandled = conditional_block(
        kernel_interrupt,
        r"!\s*devintr\s*\(\s*scause\s*&\s*0xff\s*\)",
        "kernel unhandled-interrupt",
    )
    if len(re.findall(r"\bpanic\s*\(", kernel_unhandled)) != 1:
        raise ContractError("kernel unhandled interrupt must panic exactly once")
    if len(re.findall(r"\bdevintr\s*\(\s*scause\s*&\s*0xff\s*\)", kernel_interrupt)) != 1:
        raise ContractError("kerneltrap must classify each interrupt exactly once")

    outside_user = code_only(source)
    for start, end in sorted((helper_span, user_span), reverse=True):
        outside_user = outside_user[:start] + " " * (end - start) + outside_user[end:]
    if re.search(r"\bunknown_user_trap\s*\(", outside_user):
        raise ContractError("unknown-user helper has a non-usertrap caller")


class TrapCallgraphSeparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = TRAP.read_text(encoding="utf-8")

    def mutate_function(self, name: str, declaration: str, old: str, new: str) -> str:
        _, (start, end) = function(self.source, name, declaration)
        text = self.source[start:end]
        self.assertIn(old, text, f"{name} mutation anchor drifted: {old!r}")
        return self.source[:start] + text.replace(old, new, 1) + self.source[end:]

    def assert_rejected(self, source: str, message: str) -> None:
        with self.assertRaisesRegex(ContractError, message):
            validate(source)

    def test_current_tree_passes(self) -> None:
        validate(self.source)

    def test_rejects_devintr_noninteger_contract(self) -> None:
        old = "static int\ndevintr(uint64 cause)"
        self.assertIn(old, self.source, "devintr declaration anchor drifted")
        self.assert_rejected(
            self.source.replace(old, "static void\ndevintr(uint64 cause)", 1),
            "required declaration",
        )

    def test_rejects_devintr_result_corruption(self) -> None:
        declaration = r"static\s+int\s+devintr\s*\(\s*uint64\s+cause\s*\)"
        for old, new, label in (
            ("\t\treturn 1;", "\t\treturn 0;", "SupervisorTimer"),
            ("default:\n\t\treturn 0;", "default:\n\t\treturn 1;", "default"),
        ):
            with self.subTest(label=label):
                mutated = self.mutate_function("devintr", declaration, old, new)
                self.assert_rejected(mutated, rf"devintr {label} result")

    def test_rejects_devintr_teardown_paths(self) -> None:
        declaration = r"static\s+int\s+devintr\s*\(\s*uint64\s+cause\s*\)"
        anchor = "default:\n\t\treturn 0;"
        for call in ("unknown_user_trap();", "exit(-1);"):
            with self.subTest(call=call):
                replacement = f"default:\n\t\t{call}\n\t\treturn 0;"
                mutated = self.mutate_function("devintr", declaration, anchor, replacement)
                self.assert_rejected(mutated, "without process teardown")

    def test_rejects_missing_user_unhandled_route(self) -> None:
        mutated = self.mutate_function(
            "usertrap",
            r"void\s+usertrap\s*\(\s*\)",
            "if (!devintr(cause & 0xff))\n\t\t\tunknown_user_trap();",
            "if (devintr(cause & 0xff))\n\t\t\tunknown_user_trap();",
        )
        self.assert_rejected(mutated, "unhandled interrupt")

    def test_rejects_kernel_unhandled_teardown_or_return(self) -> None:
        declaration = r"void\s+kerneltrap\s*\(\s*\)"
        anchor = 'panic("unknown supervisor trap");'
        for replacement, message in (
            ("unknown_user_trap();", "must not reach process teardown"),
            ("exit(-1);", "must not reach process teardown"),
            ("return;", "must panic exactly once"),
        ):
            with self.subTest(replacement=replacement):
                mutated = self.mutate_function(
                    "kerneltrap", declaration, anchor, replacement
                )
                self.assert_rejected(mutated, message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
