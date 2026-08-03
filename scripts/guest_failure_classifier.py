#!/usr/bin/env python3
"""Stage-aware classification of structured AgentOS Guest failure lines."""

from __future__ import annotations

import re


PHASE_BUILD = "build"
PHASE_GUEST = "guest"

FAILURE_BAD_ADDRESS = "bad_address"
FAILURE_PANIC = "panic"
FAILURE_KERNEL_ERROR = "kernel_error"
FAILURE_USER = "user_failure"
FAILURE_ASSERTION = "assertion_failure"

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PANIC_LINE_RE = re.compile(
    r"^\[PANIC (?:"
    r"-?\d+--?\d+\]\s+\S+:\d+:"
    r"|-?\d+\]\[\S+:\d+\]:"
    r")\s+.+$",
    re.IGNORECASE,
)
ERROR_LINE_RE = re.compile(
    r"^\[ERROR -?\d+--?\d+\](?:"
    r"unknown syscall\s+-?\d+"
    r"|-?\d+ in application, bad addr = .+?, bad instruction = .+?, "
    r"core dumped\."
    r"|IllegalInstruction in application, core dumped\."
    r"|unknown trap:.+"
    r"|invalid trap from kernel:.+"
    r")$",
    re.IGNORECASE,
)
BAD_ADDR_LINE_RE = re.compile(
    r"^\[ERROR -?\d+--?\d+\]-?\d+ in application, bad addr = .+?, "
    r"bad instruction = .+?, core dumped\.$",
    re.IGNORECASE,
)
USER_FAILURE_LINE_RE = re.compile(
    r"^[A-Za-z0-9_.-]+:\s+(?:.*\s)?(?:check failed|child_failed)"
    r"(?::|\s|$).*$",
    re.IGNORECASE,
)
ORCHESTRATOR_FAILURE_LINE_RE = re.compile(
    r"^rp_(?:seed_|agentos_)?orch:\s+(?:child_failed|failed)"
    r"(?:\s|:|$).*$",
    re.IGNORECASE,
)
LEGACY_FAILURE_LINE_RE = re.compile(
    r"(?:^|\s)(?:kernel panic|panic:|assertion failed)(?:\s|$)",
    re.IGNORECASE,
)


def classify_output_line(line: str, *, phase: str) -> str | None:
    """Return a failure category for one output line in its execution phase.

    Build output is deliberately not interpreted as Guest output.  This keeps
    target names such as ``build/riscv64/ch6b_panic`` from becoming failures,
    while every line observed after QEMU starts is checked by one shared rule.
    """

    if not isinstance(line, str):
        raise TypeError("output line must be text")
    if phase == PHASE_BUILD:
        return None
    if phase != PHASE_GUEST:
        raise ValueError(f"unsupported output phase: {phase!r}")

    normalized = ANSI_ESCAPE_RE.sub("", line.rstrip("\r"))
    if BAD_ADDR_LINE_RE.fullmatch(normalized):
        return FAILURE_BAD_ADDRESS
    if PANIC_LINE_RE.fullmatch(normalized):
        return FAILURE_PANIC
    if ERROR_LINE_RE.fullmatch(normalized):
        return FAILURE_KERNEL_ERROR
    if (
        USER_FAILURE_LINE_RE.fullmatch(normalized)
        or ORCHESTRATOR_FAILURE_LINE_RE.fullmatch(normalized)
    ):
        return FAILURE_USER
    if LEGACY_FAILURE_LINE_RE.search(normalized):
        return FAILURE_ASSERTION
    return None


def is_failure_line(line: str, *, phase: str) -> bool:
    return classify_output_line(line, phase=phase) is not None
