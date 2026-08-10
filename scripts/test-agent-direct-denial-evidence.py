#!/usr/bin/env python3
"""Focused source contracts for lifecycle-native direct-denial Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADER = (ROOT / "os" / "agent_evidence_ring.h").read_text(encoding="utf-8")
RING = (ROOT / "os" / "agent_evidence_ring.c").read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\s*\([^;]*?\)\s*\{{", source, re.S)
    if not match:
        raise AssertionError(f"missing function {name}")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise AssertionError(f"unterminated function {name}")


def validate_direct_controller_authority(source: str) -> None:
    controller = function_body(
        source, "agent_evidence_direct_controller_valid"
    )
    if "controller->vfs_scope_controller" in controller:
        raise AssertionError(
            "the VFS scope-controller role flag is not controller authority"
        )
    if (
        "workflow_lifecycle_controller_matches(\n"
        "\t\t       key, scope_id, controller->agent_control_id)"
        not in controller
    ):
        raise AssertionError(
            "the authoritative lifecycle controller binding is missing"
        )


@dataclass(frozen=True)
class Identity:
    lifecycle_id: int
    lifecycle_generation: int
    account_slot: int
    account_generation: int
    thread_generation: int


class DirectReserveModel:
    """Small admission model; rollover is intentionally not an operation."""

    def __init__(self, capacity: int = 2) -> None:
        self.capacity = capacity
        self.prepared: tuple[int, int, int, int] | None = None
        self.used = 0
        self.next_ticket = 1

    def prepare(self, controller: Identity) -> bool:
        if min(
            controller.lifecycle_id,
            controller.lifecycle_generation,
            controller.account_generation,
            controller.thread_generation,
        ) <= 0:
            return False
        self.prepared = (
            controller.lifecycle_id,
            controller.lifecycle_generation,
            controller.account_slot,
            controller.account_generation,
        )
        return True

    def deny(self, actor: Identity) -> int:
        identity = (
            actor.lifecycle_id,
            actor.lifecycle_generation,
            actor.account_slot,
            actor.account_generation,
        )
        if (
            self.prepared is None
            or identity != self.prepared
            or actor.thread_generation <= 0
            or self.used == self.capacity
        ):
            return 0
        ticket = self.next_ticket
        self.next_ticket += 1
        self.used += 1
        return ticket


class DirectDenialEvidenceContract(unittest.TestCase):
    def test_controller_prepare_is_explicit_and_account_charged(self) -> None:
        declaration = re.sub(r"\s+", " ", HEADER)
        self.assertRegex(
            declaration,
            r"int agent_evidence_prepare_direct_denials\( "
            r"struct proc \*, struct workflow_lifecycle_key\);",
        )
        prepare = function_body(RING, "agent_evidence_prepare_direct_denials")
        controller = function_body(
            RING, "agent_evidence_direct_controller_valid"
        )
        validate_direct_controller_authority(RING)
        ensure = prepare.index("agent_evidence_pages_ensure(key, controller)")
        publish = prepare.index("state->direct_denials_prepared = 1", ensure)
        self.assertLess(ensure, publish)
        self.assertIn("agent_identity_has_cap(controller, AGENT_CAP_ORCHESTRATE)", controller)
        self.assertIn(
            "workflow_lifecycle_controller_matches(\n"
            "\t\t       key, scope_id, controller->agent_control_id)",
            controller,
        )
        self.assertNotIn("controller->vfs_scope_controller", controller)
        self.assertIn("state->page_account, account", prepare)
        self.assertIn("resource_account_active(account)", prepare)

    def test_scope_flag_cannot_override_authoritative_controller_binding(self) -> None:
        mutated = RING.replace(
            "controller->is_agent &&",
            "controller->is_agent && controller->vfs_scope_controller &&",
            1,
        )
        self.assertNotEqual(RING, mutated)
        with self.assertRaisesRegex(
            AssertionError, "role flag is not controller authority"
        ):
            validate_direct_controller_authority(mutated)

    def test_append_validates_full_lifecycle_and_actor_generations(self) -> None:
        participant = function_body(
            RING, "agent_evidence_lifecycle_participant_valid"
        )
        actor = function_body(RING, "agent_evidence_direct_actor_valid_locked")
        self.assertIn("p->workflow_lifecycle_charged", participant)
        self.assertIn("p->pid <= 0", participant)
        self.assertIn("vfs_proc_lifecycle(p), key", participant)
        self.assertIn("workflow_lifecycle_active(key)", participant)
        self.assertIn("workflow_lifecycle_scope(key, &scope_id)", participant)
        self.assertIn("vfs_scope_active(scope_id)", participant)
        self.assertIn("vfs_proc_scope_publishable(p)", participant)
        self.assertIn("thread->identity_generation == thread_generation", actor)
        self.assertIn("thread->resource_slot_charged", participant)
        self.assertIn("state->page_account, account", actor)
        self.assertIn("resource_account_active(account)", actor)
        self.assertIn("account.generation != 0", actor)

    def test_worker_path_never_allocates_borrows_or_rolls_over(self) -> None:
        append = function_body(
            RING, "agent_evidence_append_direct_syscall_denial"
        )
        self.assertNotIn("agent_evidence_pages_ensure", append)
        self.assertNotIn("agent_evidence_rollover", append)
        self.assertNotIn("agent_evidence_domain_locked(key, 1)", append)
        self.assertIn("agent_evidence_domain_locked(key, 0)", append)
        self.assertIn("agent_evidence_reserve_locked(\n\t\t\t\tstate, 1", append)
        self.assertIn(
            "if (reserve_status != AGENT_EVIDENCE_RESERVE_OK)", append
        )
        self.assertNotRegex(append, r"for\s*\([^)]*attempt")

    def test_synthetic_event_is_critical_security_evidence(self) -> None:
        append = function_body(
            RING, "agent_evidence_append_direct_syscall_denial"
        )
        event_hash = function_body(RING, "agent_evidence_hash_event")
        record_hash = function_body(
            RING, "agent_evidence_direct_record_hash"
        )
        projection = function_body(RING, "agent_evidence_event_to_audit")
        timeline = function_body(RING, "agent_evidence_view_timeline")
        self.assertIn("AgentOS direct syscall denial evidence v1", event_hash)
        self.assertIn("AGENT_EVIDENCE_F_DIRECT_DENIAL", event_hash)
        self.assertIn("AgentOS direct syscall denial record v1", record_hash)
        self.assertIn("event->context_sequence = 0", append)
        self.assertIn("agent_observe_alloc_audit_sequence()", append)
        self.assertIn("AGENT_EVIDENCE_DIRECT_SYSCALL_NAMESPACE", append)
        self.assertIn("event->value0 = side_effect_mask", append)
        self.assertIn("event->value1 = thread_generation", append)
        self.assertIn(
            "event->value2 = AGENT_PROVENANCE_DENY_MISSING_CONTRACT", append
        )
        self.assertIn("AGENT_CONTEXT_RECORD_F_SECURITY_DENIAL", append)
        self.assertIn("AGENT_EVIDENCE_F_CRITICAL", append)
        self.assertIn("AGENT_EVIDENCE_F_SECURITY_DENIAL", append)
        self.assertIn("AGENT_EVIDENCE_F_DIRECT_DENIAL", append)
        self.assertIn("event->status = AGENT_STATUS_DENIED", append)
        self.assertIn("return record_hash != 0 ? record_hash : 1", record_hash)
        self.assertIn("record->flags = event->flags", projection)
        self.assertIn("timeline->sequence = event.context_sequence", timeline)

    def test_prepared_state_is_generation_local_and_reclaimed(self) -> None:
        create = function_body(RING, "agent_evidence_domain_locked")
        release = function_body(RING, "agent_evidence_domain_release_locked")
        self.assertIn("state->direct_denials_prepared = 0", create)
        self.assertIn("state->direct_denials_prepared = 0", release)

    def test_first_denial_after_prepare_gets_a_nonzero_ticket(self) -> None:
        controller = Identity(2, 7, 3, 11, 19)
        model = DirectReserveModel()
        self.assertTrue(model.prepare(controller))
        self.assertGreater(model.deny(Identity(2, 7, 3, 11, 23)), 0)

    def test_wrong_lifecycle_or_account_generation_fails_closed(self) -> None:
        controller = Identity(2, 7, 3, 11, 19)
        model = DirectReserveModel()
        self.assertTrue(model.prepare(controller))
        self.assertEqual(model.deny(Identity(2, 8, 3, 11, 23)), 0)
        self.assertEqual(model.deny(Identity(2, 7, 3, 12, 23)), 0)
        self.assertEqual(model.deny(Identity(2, 7, 3, 11, 0)), 0)

    def test_unprepared_and_exhausted_reserves_do_not_roll_over(self) -> None:
        controller = Identity(2, 7, 3, 11, 19)
        actor = Identity(2, 7, 3, 11, 23)
        model = DirectReserveModel(capacity=1)
        self.assertEqual(model.deny(actor), 0)
        self.assertTrue(model.prepare(controller))
        first = model.deny(actor)
        self.assertGreater(first, 0)
        self.assertEqual(model.deny(actor), 0)
        self.assertEqual(model.next_ticket, first + 1)


if __name__ == "__main__":
    unittest.main()
