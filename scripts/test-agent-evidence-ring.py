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
AUDITOR = (ROOT / "user" / "src" / "rp_auditor.c").read_text(encoding="utf-8")
PROBE_PATH = ROOT / "scripts" / "probes" / "agent-evidence-ring.c"
KERNEL_AGENT = (ROOT / "os" / "agent.h").read_text(encoding="utf-8")


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise AssertionError(f"probe compatibility anchor drift: {old!r}")
    return source.replace(old, new, 1)


def lifecycle_probe_source() -> str:
    source = PROBE_PATH.read_text(encoding="utf-8")
    timeline_filter = re.search(
        r"struct agent_timeline_filter\s*\{.*?\n\};", KERNEL_AGENT, re.S
    )
    if timeline_filter is None:
        raise AssertionError("missing production agent_timeline_filter")
    source = replace_once(
        source,
        "\tuint vfs_scope_id;\n};",
        """\tuint vfs_scope_id;
\tint workflow_lifecycle_charged;
\tint vfs_scope_controller;
\tuint64 vfs_effective_caps;
\tuint64 vfs_inheritable_caps;
\tint resource_domain_id;
\tint teardown_live;
};""",
    )
    source = replace_once(
        source,
        "\tint agent_loop_state;\n};",
        """\tint agent_loop_state;
\tint state;
\tuint64 identity_generation;
\tint resource_slot_charged;
\tstruct resource_account_handle resource_account;
\tint resource_slot_reserved;
};""",
    )
    compatibility = r"""
#define NTHREAD 16
#define RUNNING 4
#define VFS_SCOPE_NONE 0U
#define AGENT_CAP_ORCHESTRATE (1ULL << 9)
#define AGENT_STATUS_DENIED -8

struct agent_sched_record;
struct agent_event;

__AGENT_TIMELINE_FILTER__

static int
proc_teardown_live(const struct proc *p)
{
	return p != 0 && p->teardown_live;
}

static struct workflow_lifecycle_key
vfs_proc_lifecycle(const struct proc *p)
{
	struct workflow_lifecycle_key key = {0};

	if (p != 0) {
		key.id = p->workflow_lifecycle_id;
		key.generation = p->workflow_lifecycle_generation;
	}
	return key;
}

static int
vfs_scope_active(uint scope_id)
{
	return !current_retiring && scope_id != VFS_SCOPE_NONE &&
	       scope_id == current_scope;
}

static int
vfs_proc_scope_publishable(const struct proc *p)
{
	return p != 0 && p->vfs_scope_id != VFS_SCOPE_NONE &&
	       vfs_scope_active(p->vfs_scope_id) &&
	       workflow_lifecycle_key_equal(vfs_proc_lifecycle(p), current_key);
}

static int
agent_identity_has_cap(struct proc *p, uint64 capability)
{
	return p != 0 && capability != 0 &&
	       (p->vfs_effective_caps & capability) == capability;
}

static uint64 probe_audit_sequence;
static uint64 probe_cycle;

uint64
agent_observe_alloc_audit_sequence(void)
{
	return ++probe_audit_sequence;
}

uint64
get_cycle(void)
{
	return ++probe_cycle;
}

int
workflow_lifecycle_controller_matches(struct workflow_lifecycle_key key,
				      uint scope_id, uint64 control_id)
{
	return current_thread.process != 0 &&
	       workflow_lifecycle_key_equal(key, current_key) &&
	       scope_id == current_scope && control_id != 0 &&
	       control_id == current_thread.process->agent_control_id;
}
"""
    compatibility = compatibility.replace(
        "__AGENT_TIMELINE_FILTER__", timeline_filter.group(0)
    )
    source = replace_once(
        source,
        '#include "../../os/agent_evidence_ring.c"',
        compatibility + '\n#include "../../os/agent_evidence_ring.c"',
    )
    source = replace_once(
        source,
        "\tp->resource_slot_reserved = 1;\n\tp->vfs_scope_id = scope;",
        """\tp->resource_slot_reserved = 1;
\tp->workflow_lifecycle_charged = 1;
\tp->vfs_scope_controller = 1;
\tp->vfs_effective_caps = AGENT_CAP_ORCHESTRATE;
\tp->vfs_inheritable_caps = AGENT_CAP_ORCHESTRATE;
\tp->resource_domain_id = (int)id;
\tp->teardown_live = 1;
\tp->vfs_scope_id = scope;""",
    )
    source = replace_once(
        source,
        "\tcurrent_thread.agent_loop_state = 2;",
        """\tcurrent_thread.agent_loop_state = 2;
\tcurrent_thread.state = RUNNING;
\tcurrent_thread.identity_generation = generation;
\tcurrent_thread.resource_slot_charged = 1;
\tcurrent_thread.resource_account = p->resource_account;
\tcurrent_thread.resource_slot_reserved = p->resource_slot_reserved;""",
    )
    return source


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
        record = function_body(OBSERVE, "agent_observe_record_context_ticket")
        append = record.index("agent_evidence_append_context")
        first_legacy = record.index("agent_observe_ledger_record_context", append)
        success_return = record.index(
            "return evidence_ticket != 0 ? 0 : -1", first_legacy
        )
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
        commit = function_body(RING, "agent_evidence_context_commit")
        event_init = function_body(RING, "agent_evidence_context_event_init")
        projection = function_body(RING, "agent_evidence_event_to_audit")
        record = function_body(OBSERVE, "agent_observe_record_context_ticket")
        event_hash = function_body(RING, "agent_evidence_hash_event")
        self.assertIn("event->audit_sequence = audit_sequence", event_init)
        self.assertIn("agent_evidence_context_event_init", append)
        self.assertIn("agent_evidence_context_event_init", commit)
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

    def test_rp_auditor_checks_projection_identity_not_legacy_chain(self) -> None:
        audit = re.sub(r"\s+", " ", function_body(AUDITOR, "run_kernel_audit"))
        self.assertNotIn("auditor_audit_hash(record)", audit)
        self.assertNotRegex(
            audit,
            r"kernel_audit_records\[auditor_audit_count - 1\]\.record_hash "
            r"!= kernel_ledger\.ledger_hash",
        )
        self.assertIn(
            "first_audit->record_hash != first->record_hash", audit
        )
        self.assertIn(
            "second_audit->record_hash != second->record_hash", audit
        )
        self.assertIn(
            "first_audit->prev_hash != first->prev_hash", audit
        )
        self.assertIn(
            "second_audit->prev_hash != second->prev_hash", audit
        )
        self.assertIn(
            "auditor_context_hash(record) != record->record_hash", audit
        )
        self.assertIn(
            "edge->source_record_hash == first->record_hash", audit
        )
        self.assertIn(
            "edge->target_record_hash == second->record_hash", audit
        )

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
        self.assertIn("agent_evidence_reclaim(lifecycle)", scope_reclaim)

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
            probe = Path(tmp) / "agent-evidence-ring.c"
            executable = Path(tmp) / "agent-evidence-ring"
            probe.write_text(lifecycle_probe_source(), encoding="utf-8")
            subprocess.run(
                [
                    compiler,
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{PROBE_PATH.parent}",
                    str(probe),
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
