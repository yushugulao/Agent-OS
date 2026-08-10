#!/usr/bin/env python3
"""Static and mutation checks for Context-backed snapshot readers."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "os" / "agent_observe_timeline.c"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


class ContractError(AssertionError):
    pass


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise ContractError(f"missing function {name}")
    brace = match.end() - 1
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise ContractError(f"unterminated function {name}")


def validate_reader(source: str, name: str, out_label: str) -> None:
    body = function_body(source, name)
    enter = "if (agent_lifecycle_context_lane_enter(p) < 0)\n\t\treturn -1;"
    leave = "agent_lifecycle_context_lane_leave(p);"
    label = f"{out_label}:"

    if source.count('#include "agent_lifecycle.h"') != 1:
        raise ContractError("snapshot readers must import the Context lane API")
    if body.count(enter) != 1 or body.count(leave) != 1:
        raise ContractError(f"{name} must acquire and release one Context lane hold")

    enter_at = body.index(enter)
    first_context_at = min(
        body.index("p->context_path_count"),
        body.index("agent_context_read_record("),
    )
    if enter_at > first_context_at:
        raise ContractError(f"{name} samples Context before acquiring its lane")

    acquired_at = enter_at + len(enter)
    leave_at = body.index(leave)
    if leave_at < body.rindex("agent_context_read_record("):
        raise ContractError(f"{name} releases its lane before the final Context read")
    if label not in body or body.index(label) > leave_at:
        raise ContractError(f"{name} lacks a shared release label")
    if f"goto {out_label};" not in body:
        raise ContractError(f"{name} failures do not converge on the release path")
    if re.search(r"\breturn\b", body[acquired_at:leave_at]):
        raise ContractError(f"{name} can return while retaining the Context lane")
    if body[leave_at:].count("return result;") != 1:
        raise ContractError(f"{name} must return only after releasing the Context lane")


def validate_source(source: str) -> None:
    validate_reader(source, "sys_agent_provenance_snapshot", "provenance_out")
    validate_reader(source, "sys_agent_trace_snapshot", "trace_out")


class SnapshotReaderAtomicityTests(unittest.TestCase):
    def test_current_sources(self) -> None:
        validate_source(SOURCE)

    def test_mutation_rejects_missing_lane_hold(self) -> None:
        mutated = SOURCE.replace(
            "\tif (agent_lifecycle_context_lane_enter(p) < 0)\n\t\treturn -1;\n",
            "",
            1,
        )
        with self.assertRaises(ContractError):
            validate_source(mutated)

    def test_mutation_rejects_late_lane_hold(self) -> None:
        enter = (
            "\tif (agent_lifecycle_context_lane_enter(p) < 0)\n"
            "\t\treturn -1;\n"
        )
        body = function_body(SOURCE, "sys_agent_trace_snapshot")
        mutated_body = body.replace(enter, "", 1).replace(
            "\tcontext_visible = p->context_path_count;\n",
            "\tcontext_visible = p->context_path_count;\n" + enter,
            1,
        )
        mutated = SOURCE.replace(body, mutated_body, 1)
        with self.assertRaises(ContractError):
            validate_source(mutated)

    def test_mutation_rejects_early_return_with_lane_held(self) -> None:
        body = function_body(SOURCE, "sys_agent_provenance_snapshot")
        mutated_body = body.replace(
            "\t\t\tresult = AGENT_STATUS_NO_SPACE;\n"
            "\t\t\tgoto provenance_out;",
            "\t\t\treturn AGENT_STATUS_NO_SPACE;",
            1,
        )
        mutated = SOURCE.replace(body, mutated_body, 1)
        with self.assertRaises(ContractError):
            validate_source(mutated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
