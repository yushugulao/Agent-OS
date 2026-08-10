#!/usr/bin/env python3
"""Static, mutation, and model checks for Context/Evidence publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "os/agent_context.c",
    "os/agent_context.h",
    "os/agent_internal.h",
    "os/agent_observe.c",
    "os/agent_provenance.c",
)
SOURCES = {
    name: (ROOT / name).read_text(encoding="utf-8")
    for name in FILES
}


class ContractError(AssertionError):
    pass


def function_span(source: str, name: str) -> tuple[int, int]:
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
                return match.start(), index + 1
    raise ContractError(f"unterminated function {name}")


def function_body(source: str, name: str) -> str:
    start, end = function_span(source, name)
    return source[start:end]


def require(source: str, needle: str, message: str) -> None:
    if needle not in source:
        raise ContractError(message)


def require_order(source: str, needles: tuple[str, ...], message: str) -> None:
    cursor = -1
    for needle in needles:
        found = source.find(needle, cursor + 1)
        if found < 0:
            raise ContractError(f"{message}: missing {needle!r}")
        cursor = found


def validate_atomic_publication(sources: dict[str, str]) -> None:
    context = sources["os/agent_context.c"]
    context_h = sources["os/agent_context.h"]
    internal = sources["os/agent_internal.h"]
    observe = sources["os/agent_observe.c"]
    provenance = sources["os/agent_provenance.c"]

    append = function_body(context, "agent_context_append_flags")
    require_order(
        append,
        (
            "agent_context_publish_begin(p);",
            "agent_context_write_header_locked(p) < 0",
            "agent_observe_commit_context_reserved_ticket(",
            "agent_observe_commit_security_reserved_ticket(",
            "agent_context_publish_end(p);",
            "agent_observe_publish_context_ticket(",
        ),
        "reserved Context must stage odd, commit Evidence, then release-publish",
    )
    if append.count("agent_context_publish_begin(p);") != 1 or append.count(
        "agent_context_publish_end(p);"
    ) != 1:
        raise ContractError("append must have one balanced publication window")
    for guard in (
        "reservation != 0 && security_reservation != 0",
        "evidence_ticket_out == 0",
        "agent_observe_recording_suppressed(p)",
        "reservation != 0 && !reservation->active",
        "security_reservation != 0 && !security_reservation->active",
    ):
        require(append, guard, f"reserved append preflight lost: {guard}")

    publish_end = function_body(context, "agent_context_publish_end")
    require(
        publish_end,
        "__atomic_fetch_add(sequence, 1, __ATOMIC_RELEASE)",
        "Context visibility must remain a release publication",
    )

    terminal_commit = function_body(
        observe, "agent_observe_commit_context_reserved_ticket"
    )
    require(
        terminal_commit,
        "agent_evidence_context_commit(",
        "terminal publication no longer commits its reserved Evidence slot",
    )
    security_commit = function_body(
        observe, "agent_observe_commit_security_reserved_ticket"
    )
    require(
        security_commit,
        "agent_evidence_security_commit(",
        "security denial no longer commits its critical Evidence slot",
    )
    projection = function_body(observe, "agent_observe_publish_context_ticket")
    if "agent_evidence_" in projection:
        raise ContractError("legacy projection must not own canonical Evidence commit")

    denial = function_body(provenance, "agent_provenance_append_security_denial")
    require_order(
        denial,
        (
            "workflow_lifecycle_operation_enter(evidence_lifecycle)",
            "agent_evidence_security_reserve(p, &reservation)",
            "agent_lifecycle_context_lane_enter(p)",
            "agent_context_append_security_denial_record(",
            "ticket == 0 || reservation.active",
            "*ticket_out = ticket",
            "agent_lifecycle_context_lane_leave(p)",
            "workflow_lifecycle_operation_leave(evidence_lifecycle)",
        ),
        "security denial must use the atomic Context publication path",
    )
    if "agent_evidence_security_commit(" in denial:
        raise ContractError("security Evidence commit escaped the Context odd window")
    failure = denial[
        denial.index("if (agent_context_append_security_denial_record(") :
        denial.index("if (ticket == 0 || reservation.active)")
    ]
    require_order(
        failure,
        (
            "agent_evidence_security_abort(&reservation)",
            "agent_provenance_abort_staged_labels(",
            "result = AGENT_STATUS_NO_SPACE",
            "goto out_context_lane",
        ),
        "failed denial publication must unwind its ticket and staged labels",
    )

    denial_append = function_body(
        context, "agent_context_append_security_denial_record"
    )
    for token in (
        "struct agent_evidence_security_reservation *reservation",
        "uint64 *evidence_ticket_out",
        "reservation, evidence_ticket_out",
    ):
        require(denial_append, token, f"security atomic handoff lost: {token}")

    for declaration in (
        "agent_observe_commit_context_reserved_ticket(",
        "agent_observe_commit_security_reserved_ticket(",
        "agent_observe_publish_context_ticket(",
    ):
        require(internal, declaration, f"internal atomic API missing: {declaration}")
    require(
        context_h,
        "struct agent_evidence_security_reservation *, uint64 *",
        "security denial Context API lost its reserved ticket handoff",
    )
    if "agent_observe_record_context_reserved_ticket(" in context + observe + internal:
        raise ContractError("retired post-publication reserved commit returned")


def move_token_before(source: str, function: str, token: str, before: str) -> str:
    start, end = function_span(source, function)
    body = source[start:end]
    token_at = body.find(token)
    before_at = body.find(before)
    if token_at < 0 or before_at < 0:
        raise AssertionError("mutation anchor missing")
    body = body[:token_at] + body[token_at + len(token) :]
    before_at = body.find(before)
    body = body[:before_at] + token + "\n\t" + body[before_at:]
    return source[:start] + body + source[end:]


@dataclass
class PublicationModel:
    sequence: int = 0
    context_staged: bool = False
    evidence_committed: bool = False

    def stage(self) -> None:
        self.sequence += 1
        self.context_staged = True

    def commit_evidence(self) -> None:
        self.evidence_committed = True

    def publish(self) -> None:
        self.sequence += 1

    def reader_observation(self) -> tuple[bool, bool] | None:
        if self.sequence & 1:
            return None
        return self.context_staged, self.evidence_committed


class AtomicPublicationTests(unittest.TestCase):
    def test_current_sources(self) -> None:
        validate_atomic_publication(SOURCES)

    def test_mutation_rejects_early_context_publication(self) -> None:
        sources = dict(SOURCES)
        sources["os/agent_context.c"] = move_token_before(
            sources["os/agent_context.c"],
            "agent_context_append_flags",
            "agent_context_publish_end(p);",
            "agent_observe_commit_context_reserved_ticket(",
        )
        with self.assertRaises(ContractError):
            validate_atomic_publication(sources)

    def test_mutation_rejects_security_commit_outside_context(self) -> None:
        sources = dict(SOURCES)
        denial = function_body(
            sources["os/agent_provenance.c"],
            "agent_provenance_append_security_denial",
        )
        mutated = denial.replace(
            "*ticket_out = ticket;",
            "agent_evidence_security_commit();\n\t*ticket_out = ticket;",
            1,
        )
        sources["os/agent_provenance.c"] = sources[
            "os/agent_provenance.c"
        ].replace(denial, mutated, 1)
        with self.assertRaises(ContractError):
            validate_atomic_publication(sources)

    def test_model_hides_staged_context_until_evidence_commit(self) -> None:
        model = PublicationModel()
        model.stage()
        self.assertIsNone(model.reader_observation())
        model.commit_evidence()
        self.assertIsNone(model.reader_observation())
        model.publish()
        self.assertEqual(model.reader_observation(), (True, True))

    def test_model_exposes_early_publish_violation(self) -> None:
        model = PublicationModel()
        model.stage()
        model.publish()
        self.assertEqual(model.reader_observation(), (True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
