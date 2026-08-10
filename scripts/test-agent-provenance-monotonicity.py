#!/usr/bin/env python3
"""Fail-closed regressions for process provenance monotonicity."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "os" / "agent_provenance.c"
CONTEXT = ROOT / "os" / "agent_context.c"

KERNEL_FACT = 1 << 0
TRUSTED_USER_CONTROL = 1 << 1
AGENT_DERIVED = 1 << 2
UNTRUSTED_FILE_DATA = 1 << 3
UNTRUSTED_TOOL_OUTPUT = 1 << 4
CROSS_AGENT_DATA = 1 << 5
ALL_LABELS = (1 << 6) - 1


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise ContractError(f"missing function {name}")
    start = source.find("{", match.start())
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise ContractError(f"unterminated function {name}")


def ordered(body: str, *needles: str) -> None:
    cursor = -1
    for needle in needles:
        position = body.find(needle, cursor + 1)
        if position < 0:
            raise ContractError(f"missing ordered token: {needle}")
        cursor = position


def validate(provenance: str, context: str) -> None:
    flags = function_body(provenance, "agent_provenance_context_flags")
    if "labels |= state->pending_labels;" not in flags:
        raise ContractError("pending output labels can replace concurrent ingress taint")
    if re.search(r"\blabels\s*=\s*state->pending_labels\s*;", flags):
        raise ContractError("pending output labels are not monotonic")

    for name in (
        "agent_provenance_context_committed",
        "agent_provenance_context_restore",
    ):
        body = function_body(provenance, name)
        if "state->current_labels |= labels == 0 ?" not in body:
            raise ContractError(f"{name} may remove provenance labels")
        if re.search(r"state->current_labels\s*=(?!=)", body):
            raise ContractError(f"{name} assigns a less-tainted snapshot")

    clear = function_body(context, "agent_context_clear")
    ordered(
        clear,
        "prior_provenance_labels = agent_provenance_current_labels(p);",
        "memset((void *)p->agent_context_sidecar_kva[page], 0,",
        "agent_provenance_merge_current(p, prior_provenance_labels)",
    )
    if "agent_provenance_context_restore(p, 0)" in clear:
        raise ContractError("Context clear resets provenance to a clean state")


class ProvenanceModel:
    def __init__(self) -> None:
        self.current = AGENT_DERIVED
        self.pending = 0

    def ingest(self, labels: int) -> None:
        assert labels & ~ALL_LABELS == 0
        self.current |= labels | AGENT_DERIVED

    def stage(self, labels: int) -> None:
        assert labels & ~ALL_LABELS == 0
        self.pending = labels | AGENT_DERIVED

    def context_flags(self) -> int:
        return self.current | self.pending

    def commit(self, labels: int) -> None:
        self.current |= labels if labels else AGENT_DERIVED
        self.pending = 0

    def restore(self, labels: int) -> None:
        self.current |= labels if labels else AGENT_DERIVED
        self.pending = 0

    def clear_sidecar(self) -> None:
        preserved = self.current
        self.current = AGENT_DERIVED
        self.pending = 0
        self.ingest(preserved)


class ProvenanceMonotonicityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provenance = PROVENANCE.read_text(encoding="utf-8")
        cls.context = CONTEXT.read_text(encoding="utf-8")

    def test_current_tree_passes(self) -> None:
        validate(self.provenance, self.context)

    def test_file_ingress_survives_rollback_and_clear(self) -> None:
        model = ProvenanceModel()
        model.ingest(UNTRUSTED_FILE_DATA)
        model.restore(TRUSTED_USER_CONTROL | AGENT_DERIVED)
        self.assertTrue(model.current & UNTRUSTED_FILE_DATA)
        model.clear_sidecar()
        self.assertTrue(model.current & UNTRUSTED_FILE_DATA)
        self.assertTrue(model.current & TRUSTED_USER_CONTROL)

    def test_mail_ingress_survives_pending_commit(self) -> None:
        model = ProvenanceModel()
        model.stage(TRUSTED_USER_CONTROL | AGENT_DERIVED)
        model.ingest(CROSS_AGENT_DATA | UNTRUSTED_TOOL_OUTPUT)
        flags = model.context_flags()
        model.commit(flags)
        self.assertEqual(model.pending, 0)
        self.assertTrue(model.current & CROSS_AGENT_DATA)
        self.assertTrue(model.current & UNTRUSTED_TOOL_OUTPUT)

    def test_all_labels_are_monotonic(self) -> None:
        model = ProvenanceModel()
        model.ingest(ALL_LABELS)
        model.restore(KERNEL_FACT)
        model.clear_sidecar()
        self.assertEqual(model.current, ALL_LABELS)

    def test_rejects_rollback_assignment(self) -> None:
        old = "state->current_labels |= labels == 0 ?"
        self.assertGreaterEqual(self.provenance.count(old), 2)
        mutated = self.provenance.rsplit(old, 1)
        with self.assertRaises(ContractError):
            validate("state->current_labels = labels == 0 ?".join(mutated), self.context)

    def test_rejects_commit_assignment(self) -> None:
        old = "state->current_labels |= labels == 0 ?"
        self.assertGreaterEqual(self.provenance.count(old), 2)
        mutated = self.provenance.replace(
            old, "state->current_labels = labels == 0 ?", 1
        )
        with self.assertRaises(ContractError):
            validate(mutated, self.context)

    def test_rejects_pending_label_replacement(self) -> None:
        mutated = self.provenance.replace(
            "labels |= state->pending_labels;",
            "labels = state->pending_labels;",
            1,
        )
        with self.assertRaises(ContractError):
            validate(mutated, self.context)

    def test_rejects_clear_without_preservation(self) -> None:
        mutated = self.context.replace(
            "agent_provenance_merge_current(p, prior_provenance_labels)",
            "agent_provenance_context_restore(p, 0)",
            1,
        )
        with self.assertRaises(ContractError):
            validate(self.provenance, mutated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
