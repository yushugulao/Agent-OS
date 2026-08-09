#!/usr/bin/env python3
"""Focused contracts for the fence-sealed evidence ring and SHA-256 core."""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADER = (ROOT / "os" / "agent_evidence_ring.h").read_text(encoding="utf-8")
RING = (ROOT / "os" / "agent_evidence_ring.c").read_text(encoding="utf-8")
OBSERVE = (ROOT / "os" / "agent_observe.c").read_text(encoding="utf-8")
LEDGER = (ROOT / "os" / "agent_observe_ledger.c").read_text(encoding="utf-8")


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


class EvidenceRingContract(unittest.TestCase):
    def test_fence_api_binds_caller_sequence(self) -> None:
        declaration = re.sub(r"\s+", " ", HEADER)
        self.assertRegex(
            declaration,
            r"int agent_evidence_seal\(struct workflow_lifecycle_key, uint64, "
            r"const uchar\[AGENT_SHA256_DIGEST_SIZE\], uint64, uint64, "
            r"const uchar\[AGENT_SHA256_DIGEST_SIZE\], "
            r"struct agent_evidence_seal_result \*\);",
        )
        prepare = function_body(RING, "agent_evidence_prepare_seal_stable")
        self.assertIn(
            "plan->result.fence_sequence = workflow_fence_sequence", prepare
        )
        self.assertIn(
            "agent_evidence_hash_u64(&hash, workflow_fence_sequence)", prepare
        )
        self.assertIn(
            "agent_sha256_update(&hash, credit_digest, AGENT_SHA256_DIGEST_SIZE)",
            prepare,
        )

    def test_empty_segment_has_an_explicit_merkle_root(self) -> None:
        merkle = function_body(RING, "agent_evidence_merkle_stable")
        self.assertRegex(merkle, r"if\s*\(count\s*==\s*0\)")
        self.assertIn("empty_domain", merkle)

    def test_hashing_runs_after_irq_restore_under_gate(self) -> None:
        seal = function_body(RING, "agent_evidence_seal_stable")
        gate = seal.index("state->sealing = 1")
        restore = seal.index("intr_restore(enabled)", gate)
        prepare = seal.index("agent_evidence_prepare_seal_stable", restore)
        reacquire = seal.index("enabled = intr_save()", prepare)
        self.assertLess(gate, restore)
        self.assertLess(restore, prepare)
        self.assertLess(prepare, reacquire)
        self.assertNotIn(
            "agent_evidence_prepare_seal_stable",
            seal[:restore],
        )

    def test_reserve_fill_commit_publication_order(self) -> None:
        reserve = function_body(RING, "agent_evidence_reserve_locked")
        append = function_body(RING, "agent_evidence_append_context")
        commit = function_body(RING, "agent_evidence_commit_locked")
        discard = function_body(RING, "agent_evidence_discard_locked")
        self.assertIn("AGENT_EVIDENCE_SLOT_BUSY", reserve)
        self.assertIn("AGENT_EVIDENCE_RESERVE_ROLLOVER", reserve)
        fill = append.index("memmove(&reservation.slot->event")
        publish = append.index("agent_evidence_commit_locked", fill)
        self.assertLess(fill, publish)
        self.assertIn("AGENT_EVIDENCE_SLOT_COMMITTED", commit)
        self.assertIn("__ATOMIC_RELEASE", commit)
        self.assertIn("AGENT_EVIDENCE_SLOT_DISCARDED", discard)
        self.assertIn("agent_evidence_note_ticket_locked", discard)
        self.assertNotIn("workflow_lifecycle_scope", append)

    def test_full_partitions_roll_before_reuse(self) -> None:
        reserve = function_body(RING, "agent_evidence_reserve_locked")
        append = function_body(RING, "agent_evidence_append_context")
        self.assertNotIn("oldest", reserve)
        self.assertIn("AGENT_EVIDENCE_RESERVE_ROLLOVER", reserve)
        self.assertIn("agent_evidence_rollover(key)", append)

    def test_rollovers_preserve_workflow_fence_totals(self) -> None:
        prepare = function_body(RING, "agent_evidence_prepare_seal_stable")
        seal = function_body(RING, "agent_evidence_seal_stable")
        note = function_body(RING, "agent_evidence_note_ticket_locked")
        self.assertIn("state->fence_events++", note)
        self.assertIn("state->fence_gaps++", note)
        self.assertIn(
            "workflow_fence ?\n\t\tstate->fence_events : state->segment_events",
            prepare,
        )
        self.assertIn("if (workflow_fence) {", seal)
        self.assertIn("state->fence_events = 0", seal)
        self.assertIn(
            "plan->result.previous_root, state->fence_root", prepare
        )
        self.assertIn("state->fence_root, plan.result.root", seal)
        self.assertIn("memset(state->sealed_root, 0", seal)

    def test_only_critical_context_uses_immediate_legacy_seal(self) -> None:
        record = function_body(OBSERVE, "agent_observe_record_context")
        append = record.index("agent_evidence_append_context")
        first_legacy = record.index("agent_observe_ledger_record_context", append)
        success_return = record.index("return;", first_legacy)
        fallback_legacy = record.index(
            "agent_observe_ledger_record_context", success_return
        )
        critical_guard = record.index(
            "if (authority_effect || record->status != AGENT_STATUS_OK)", append
        )
        self.assertLess(critical_guard, first_legacy)
        self.assertLess(first_legacy, success_return)
        self.assertLess(success_return, fallback_legacy)
        self.assertEqual(record.count("agent_observe_ledger_record_context"), 2)
        self.assertIn("&evidence_ticket", record)
        self.assertNotIn("agent_obsstore", RING)
        note = function_body(RING, "agent_evidence_note_ticket_locked")
        view = function_body(LEDGER, "agent_observe_audit_view_open_locked")
        shadow = function_body(
            LEDGER, "agent_observe_legacy_shadowed_by_evidence"
        )
        self.assertIn("state->total_critical_events++", note)
        self.assertIn("state->evidence_linked_records", view)
        self.assertNotIn("view->evidence.critical_records", view)
        self.assertIn("agent_audit_evidence_tickets[slot]", shadow)
        self.assertIn("view->entries[i].ticket == evidence_ticket", shadow)
        self.assertNotIn("strncmp", shadow)

    def test_dynamic_pages_are_exactly_accounted_and_reaped(self) -> None:
        ensure = function_body(RING, "agent_evidence_pages_ensure")
        release = function_body(RING, "agent_evidence_pages_release")
        domain = re.search(
            r"struct agent_evidence_domain\s*\{(?P<body>.*?)\n\};", RING, re.S
        )
        self.assertIsNotNone(domain)
        body = domain.group("body")
        self.assertIn("void *slot_pages[AGENT_EVIDENCE_PAGE_COUNT]", body)
        self.assertNotIn("ordinary[", body)
        self.assertNotIn("critical[", body)
        self.assertIn(
            "#define AGENT_EVIDENCE_PAGE_COUNT WORKFLOW_EVIDENCE_PAGE_COUNT",
            RING,
        )
        self.assertIn("kalloc_account_page(account, charge_class)", ensure)
        self.assertIn("RESOURCE_AGENT_STATE_PAGE", ensure)
        self.assertIn("state->allocating = 1", ensure)
        self.assertIn("state->slot_pages[page] = pages[page]", ensure)
        self.assertIn("kfree_account_page", release)
        physical = release.index("kfree_account_page")
        logical = release.index("resource_release_many", physical)
        reap = release.index("proc_resource_account_reap", logical)
        self.assertLess(physical, logical)
        self.assertLess(logical, reap)

    def test_audit_projection_uses_global_sequence_not_ring_ticket(self) -> None:
        append = function_body(RING, "agent_evidence_append_context")
        projection = function_body(RING, "agent_evidence_event_to_audit")
        record = function_body(OBSERVE, "agent_observe_record_context")
        event_hash = function_body(RING, "agent_evidence_hash_event")
        self.assertIn("event.audit_sequence = audit_sequence", append)
        self.assertIn("record->sequence = event->audit_sequence", projection)
        self.assertNotIn("record->sequence = event->ticket", projection)
        self.assertIn("agent_observe_alloc_audit_sequence()", record)
        self.assertIn("event->audit_sequence", event_hash)

    def test_ledger_hash_covers_sealed_root_and_published_leaves(self) -> None:
        digest = function_body(RING, "agent_evidence_view_digest")
        tag = function_body(LEDGER, "agent_observe_evidence_hash_tag")
        self.assertIn("AgentOS evidence view v1", digest)
        self.assertIn("view->sealed_root", digest)
        self.assertIn("agent_evidence_hash_event(&event, leaf)", digest)
        self.assertIn("agent_evidence_view_digest(view, digest)", tag)
        self.assertNotIn("latest.record_hash", tag)

    def test_reclaim_seals_unfenced_evidence_before_release(self) -> None:
        reclaim = function_body(RING, "agent_evidence_reclaim")
        retirement = function_body(
            RING, "agent_evidence_prepare_retirement_stable"
        )
        gate = reclaim.index("state->sealing = 1")
        restore = reclaim.index("intr_restore(enabled)", gate)
        prepare = reclaim.index(
            "agent_evidence_prepare_retirement_stable", restore
        )
        reacquire = reclaim.index("enabled = intr_save()", prepare)
        publish = reclaim.index(
            "AGENT_EVIDENCE_RETAINED_F_INTERNAL_RETIRE", reacquire
        )
        release = reclaim.index("agent_evidence_domain_release_locked", publish)
        irq_restore = reclaim.index("intr_restore(enabled)", release)
        page_release = reclaim.index("agent_evidence_pages_release", irq_restore)
        self.assertLess(gate, restore)
        self.assertLess(restore, prepare)
        self.assertLess(prepare, reacquire)
        self.assertLess(reacquire, publish)
        self.assertLess(publish, release)
        self.assertLess(release, irq_restore)
        self.assertLess(irq_restore, page_release)
        self.assertIn("workflow_lifecycle_retiring(key)", reclaim)
        self.assertIn("state->fence_events != 0", reclaim)
        self.assertIn("state->last_fence_sequence == 0", reclaim)
        self.assertIn("AgentOS evidence retirement v1", retirement)
        self.assertNotIn("challenge", retirement)

    def test_retained_root_is_full_key_bound_and_not_a_receipt(self) -> None:
        declaration = re.sub(r"\s+", " ", HEADER)
        self.assertIn(
            "struct workflow_lifecycle_key key; uint flags; uint reserved;",
            declaration,
        )
        self.assertIn("AGENT_EVIDENCE_RETAINED_F_INTERNAL_RETIRE", HEADER)
        getter = function_body(RING, "agent_evidence_retained_get")
        publish = function_body(RING, "agent_evidence_retained_publish_locked")
        seal = function_body(RING, "agent_evidence_seal_stable")
        self.assertIn("workflow_lifecycle_key_equal(retained->key, key)", getter)
        self.assertIn("retained->key = key", publish)
        self.assertIn(
            "AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE", seal
        )

    def test_fence_sealed_ticket_highwater_is_active_and_retained(self) -> None:
        declaration = re.sub(r"\s+", " ", HEADER)
        seal = function_body(RING, "agent_evidence_seal_stable")
        publish = function_body(
            RING, "agent_evidence_retained_publish_locked"
        )
        lookup = function_body(RING, "agent_evidence_ticket_fence_sealed")
        receipt_status = function_body(
            LEDGER, "agent_observe_receipt_status"
        )
        scope_reclaim = function_body(LEDGER, "agent_observe_scope_reclaim")

        self.assertIn("uint64 sealed_ticket_highwater;", declaration)
        self.assertRegex(
            declaration,
            r"int agent_evidence_ticket_fence_sealed\("
            r"struct workflow_lifecycle_key, uint64, uint64 \*\);",
        )

        advance = seal.index(
            "if (plan.result.last_ticket > state->sealed_ticket_highwater)"
        )
        assign = seal.index(
            "state->sealed_ticket_highwater = plan.result.last_ticket",
            advance,
        )
        retain = seal.index(
            "agent_evidence_retained_publish_locked", assign
        )
        self.assertLess(advance, assign)
        self.assertLess(assign, retain)

        self.assertIn(
            "flags & AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE", publish
        )
        self.assertIn("retained->sealed_ticket_highwater", publish)
        self.assertIn("result->last_ticket", publish)

        active = lookup.index(
            "state->used && workflow_lifecycle_key_equal(state->key, key)"
        )
        active_highwater = lookup.index(
            "highwater = state->sealed_ticket_highwater", active
        )
        retained = lookup.index("retained = &agent_evidence_retained", active)
        retained_key = lookup.index(
            "workflow_lifecycle_key_equal(retained->key, key)", retained
        )
        retained_flag = lookup.index(
            "AGENT_EVIDENCE_RETAINED_F_WORKFLOW_FENCE", retained_key
        )
        retained_highwater = lookup.index(
            "highwater = retained->sealed_ticket_highwater", retained_flag
        )
        bound = lookup.index("ticket > highwater || sequence == 0")
        self.assertLess(active, active_highwater)
        self.assertLess(active_highwater, retained)
        self.assertLess(retained, retained_key)
        self.assertLess(retained_key, retained_flag)
        self.assertLess(retained_flag, retained_highwater)
        self.assertLess(retained_highwater, bound)
        self.assertIn("*fence_sequence = sequence", lookup[bound:])

        self.assertIn(
            "agent_evidence_ticket_fence_sealed(", receipt_status
        )
        self.assertIn(
            "AGENT_AUDIT_DURABILITY_FENCE_SEALED", receipt_status
        )
        self.assertNotIn("agent_obsstore", receipt_status)
        self.assertIn("agent_evidence_reclaim(lifecycle)", scope_reclaim)
        self.assertNotIn("agent_obsstore", scope_reclaim)
        self.assertNotIn("agent_observe_capacity", scope_reclaim)

    def test_sha256_known_vectors(self) -> None:
        if ctypes.sizeof(ctypes.c_ulong) != 8:
            self.skipTest("kernel uint64 maps to unsigned long; host is not LP64")
        compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
        if not compiler:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory(prefix="agent-evidence-sha-") as tmp:
            library = Path(tmp) / "libagent_sha256.so"
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-shared",
                    "-fPIC",
                    f"-I{ROOT / 'os'}",
                    str(ROOT / "os" / "agent_sha256.c"),
                    "-o",
                    str(library),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            module = ctypes.CDLL(str(library))
            module.agent_sha256.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_ubyte),
            ]
            vectors = [b"", b"abc", bytes(range(256)), b"a" * 4097]
            for message in vectors:
                source = ctypes.create_string_buffer(message or b"\x00")
                digest = (ctypes.c_ubyte * 32)()
                module.agent_sha256(source, len(message), digest)
                self.assertEqual(bytes(digest), hashlib.sha256(message).digest())

    def test_ring_lifecycle_probe(self) -> None:
        if ctypes.sizeof(ctypes.c_ulong) != 8:
            self.skipTest("kernel uint64 maps to unsigned long; host is not LP64")
        compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
        if not compiler:
            self.skipTest("no host C compiler")
        with tempfile.TemporaryDirectory(prefix="agent-evidence-ring-") as tmp:
            executable = Path(tmp) / "agent-evidence-ring"
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "scripts" / "probes" / "agent-evidence-ring.c"),
                    str(ROOT / "os" / "agent_sha256.c"),
                    "-o",
                    str(executable),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            completed = subprocess.run(
                [str(executable)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIn(
                "retirement=1 external=1 rollover=1", completed.stdout
            )


if __name__ == "__main__":
    unittest.main()
