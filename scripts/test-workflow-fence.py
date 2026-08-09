#!/usr/bin/env python3
"""Mutation tests for the unified Agent workflow-fence source contract."""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-workflow-fence.py"
FILES = (
    "agent_workflow_fence_abi.h",
    "os/agent.h",
    "user/include/agent.h",
    "os/agent_core.c",
    "os/agent_workflow_fence.c",
    "os/workflow_lifecycle.c",
    "os/workflow_credit_domain.c",
    "os/agent_evidence_ring.c",
    "os/agent_metadata_objects.c",
    "os/agent_metadata_catalog.c",
    "os/agent_live_query_events.c",
    "user/lib/syscall.c",
)


class WorkflowFenceMutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="workflow-fence-")
        self.root = Path(self.temporary.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def mutate(self, relative: str, old: str, new: str) -> None:
        path = self.root / relative
        source = path.read_text(encoding="utf-8")
        self.assertIn(old, source, f"mutation anchor drifted in {relative}")
        path.write_text(source.replace(old, new, 1), encoding="utf-8")

    def assert_rejected(self, message: str) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_current_tree_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workflow fence check passed", result.stdout)

    def test_rejects_legacy_256_byte_receipt(self) -> None:
        self.mutate(
            "agent_workflow_fence_abi.h",
            "sizeof(struct agent_workflow_fence_receipt) == 320",
            "sizeof(struct agent_workflow_fence_receipt) == 256",
        )
        self.assert_rejected("receipt size is not frozen")

    def test_rejects_uninitialized_receipt_reserved_fields(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "memset(receipt, 0, sizeof(*receipt));",
            "receipt->flags = 0;",
        )
        self.assert_rejected("reserved account fields")

    def test_rejects_non_exact_fence_dispatch(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "if (count == 0 && flags == AGENT_RUN_F_FENCE) {",
            "if (count >= 0 && (flags & AGENT_RUN_F_FENCE)) {",
        )
        self.assert_rejected("exact count/flag pair")

    def test_rejects_unknown_flag_passthrough(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "if (flags != 0)\n\t\treturn -1;",
            "if (flags == AGENT_RUN_F_FENCE)\n\t\treturn -1;",
        )
        self.assert_rejected("unknown agent_run flags are not rejected")

    def test_rejects_unchecked_receipt_range(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "sizeof(receipt), PTE_W) < 0",
            "sizeof(receipt), PTE_R) < 0",
        )
        self.assert_rejected("receipt is not prevalidated for write")

    def test_rejects_receipt_for_anonymous_fence(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "if (opsaddr == 0 && resultsaddr != 0)",
            "if (opsaddr == 0 && resultsaddr == 0)",
        )
        self.assert_rejected("anonymous workflow fence can request")

    def test_rejects_delivery_ack_before_copyout(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "\t\tif (receipt_ptr != 0 &&\n"
            "\t\t    copyout(p->pagetable, resultsaddr, (char *)&receipt,\n"
            "\t\t\t    sizeof(receipt)) < 0)\n"
            "\t\t\treturn -1;\n"
            "\t\t/* A request without a receipt has no fallible delivery step. */\n"
            "\t\tif (status == AGENT_STATUS_OK && request_ptr != 0)\n"
            "\t\t\tagent_workflow_fence_receipt_delivered(\n"
            "\t\t\t\tvfs_proc_lifecycle(p), request.request_id);",
            "\t\tif (status == AGENT_STATUS_OK && request_ptr != 0)\n"
            "\t\t\tagent_workflow_fence_receipt_delivered(\n"
            "\t\t\t\tvfs_proc_lifecycle(p), request.request_id);\n"
            "\t\tif (receipt_ptr != 0 &&\n"
            "\t\t    copyout(p->pagetable, resultsaddr, (char *)&receipt,\n"
            "\t\t\t    sizeof(receipt)) < 0)\n"
            "\t\t\treturn -1;",
        )
        self.assert_rejected("acknowledged before successful copyout")

    def test_rejects_missing_ack_for_named_request_without_receipt(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "if (status == AGENT_STATUS_OK && request_ptr != 0)",
            "if (status == AGENT_STATUS_OK && request_ptr != 0 &&\n"
            "\t\t    receipt_ptr != 0)",
        )
        self.assert_rejected("without a receipt is not acknowledged")

    def test_rejects_delivery_ack_after_failed_fence(self) -> None:
        self.mutate(
            "os/agent_core.c",
            "if (status == AGENT_STATUS_OK && request_ptr != 0)",
            "if (request_ptr != 0)",
        )
        self.assert_rejected("success and named-request guarded")

    def test_rejects_higher_request_evicting_undelivered_receipt(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "} else if (!cache->delivered) {",
            "} else if (cache->delivered) {",
        )
        self.assert_rejected("can evict an undelivered receipt")

    def test_rejects_new_receipt_published_as_delivered(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "cache->delivered = 0;",
            "cache->delivered = 1;",
        )
        self.assert_rejected("cache publication is not atomic and complete")

    def test_rejects_delivery_ack_for_wrong_request_id(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "cache->request_id == request_id)\n\t\tcache->delivered = 1;",
            "cache->request_id != request_id)\n\t\tcache->delivered = 1;",
        )
        self.assert_rejected("full-key/request-id serialized")

    def test_rejects_request_version_inversion(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "request->version != AGENT_WORKFLOW_FENCE_VERSION",
            "request->version == AGENT_WORKFLOW_FENCE_VERSION",
        )
        self.assert_rejected("request version is not rejected precisely")

    def test_rejects_request_size_inversion(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "request->struct_size != sizeof(*request)",
            "request->struct_size == sizeof(*request)",
        )
        self.assert_rejected("request size is not rejected precisely")

    def test_rejects_nonzero_reserved_acceptance(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "request->reserved != 0",
            "request->reserved == 0",
        )
        self.assert_rejected("flags/reserved/request_id are not fail closed")

    def test_rejects_zero_request_id_acceptance(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "request->request_id == 0",
            "request->request_id != 0",
        )
        self.assert_rejected("flags/reserved/request_id are not fail closed")

    def test_rejects_partial_challenge_compare(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "i < AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE",
            "i + 1 < AGENT_WORKFLOW_FENCE_CHALLENGE_SIZE",
        )
        self.assert_rejected("challenge comparison does not cover every byte")

    def test_rejects_challenge_rewrite_before_seal(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "memmove(challenge, request->challenge, sizeof(challenge));",
            "memmove(challenge, completed.challenge, sizeof(challenge));",
        )
        self.assert_rejected("challenge is not copied byte-for-byte")

    def test_rejects_wrong_authority_capability(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)",
            "agent_identity_has_cap(p, AGENT_CAP_META_READ)",
        )
        self.assert_rejected("gate/flush/seal/commit/publication order changed")

    def test_rejects_noncontroller_fence_caller(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "key, p->vfs_scope_id, p->agent_control_id",
            "key, p->vfs_scope_id, p->agent_id",
        )
        self.assert_rejected("lifecycle controller and scope")

    def test_rejects_inverted_controller_identity_match(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "record->controller_control_id == control_id &&",
            "record->controller_control_id != control_id &&",
        )
        self.assert_rejected("full-key/scope/identity bound")

    def test_rejects_metadata_work_before_lifecycle_gate(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "if (workflow_lifecycle_fence_begin(key, &fence_sequence) < 0) {",
            "fs_deferred_reclaim_drain_current();\n"
            "\tif (workflow_lifecycle_fence_begin(key, &fence_sequence) < 0) {",
        )
        self.assert_rejected("gate/flush/seal/commit/publication order changed")

    def test_rejects_gate_with_active_operations(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "if (record->active_operations == 0 &&\n"
            "\t\t    record->departing_operations == 0) {",
            "if (record->active_operations != 0 &&\n"
            "\t\t    record->departing_operations == 0) {",
        )
        self.assert_rejected("close, drain, and roll back atomically")

    def test_rejects_fence_with_departing_operations(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "record->active_operations == 0 &&\n"
            "\t\t    record->departing_operations == 0",
            "record->active_operations == 0 &&\n"
            "\t\t    record->departing_operations != 0",
        )
        self.assert_rejected("close, drain, and roll back atomically")

    def test_rejects_departure_crossing_fence_gate(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "record->members > 0 && !record->fence_gate &&\n"
            "\t    record->departing_operations != (uint)-1",
            "record->members > 0 &&\n"
            "\t    record->departing_operations != (uint)-1",
        )
        self.assert_rejected("departure can cross a fence")

    def test_rejects_close_crossing_active_fence(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "if (record->fence_gate) {\n"
            "\t\t\tresult = 1;\n"
            "\t\t\tbreak;\n"
            "\t\t}",
            "if (0) {\n"
            "\t\t\tresult = 1;\n"
            "\t\t\tbreak;\n"
            "\t\t}",
        )
        self.assert_rejected("close can race an active fence")

    def test_rejects_fence_after_workflow_close(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "if (record != 0 && !record->closing && record->members > 0 &&\n"
            "\t    !record->fence_gate && record->fence_sequence != ~0ULL)",
            "if (record != 0 && record->members > 0 &&\n"
            "\t    !record->fence_gate && record->fence_sequence != ~0ULL)",
        )
        self.assert_rejected("close, drain, and roll back atomically")

    def test_rejects_join_across_fence_gate(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "!record->closing && !record->fence_gate &&",
            "!record->closing &&",
        )
        self.assert_rejected("join can cross a closed fence gate")

    def test_rejects_pending_credit_at_fence(self) -> None:
        self.mutate(
            "os/workflow_credit_domain.c",
            "if (out->pending[kind] != 0)",
            "if (out->pending[kind] == 0)",
        )
        self.assert_rejected("exact pending-free snapshot")

    def test_rejects_credit_snapshot_before_metadata_cut(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "\tmetadata_generation = 0;",
            "\tworkflow_credit_domain_fence(key, p->resource_account,\n"
            "\t\tstorage_account, &credit);\n"
            "\tmetadata_generation = 0;",
        )
        self.mutate(
            "os/agent_workflow_fence.c",
            "workflow_credit_domain_fence(key, p->resource_account,\n"
            "\t\t\t\t\t storage_account, &credit) < 0",
            "credit.epoch == 0",
        )
        self.assert_rejected("gate/flush/seal/commit/publication order changed")

    def test_rejects_mixed_evidence_binding(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "metadata_generation, credit.epoch, credit_digest,",
            "credit.epoch, metadata_generation, credit_digest,",
        )
        self.assert_rejected("gate/flush/seal/commit/publication order changed")

    def test_rejects_metadata_cut_bypass(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "if (agent_metadata_quiescence_fence_snapshot_current(\n"
            "\t\t    &metadata_generation) < 0 || metadata_generation == 0) {",
            "if (agent_metadata_catalog_generation_snapshot(\n"
            "\t\t    &metadata_generation) < 0 || metadata_generation == 0) {",
        )
        self.assert_rejected("unavailable metadata cut")

    def test_rejects_metadata_cut_without_transaction_gate(self) -> None:
        self.mutate(
            "os/agent_metadata_objects.c",
            "!agent_metadata_txn_lock(1)",
            "agent_metadata_txn_lock(1)",
        )
        self.assert_rejected("one volatile lifecycle transaction")

    def test_rejects_metadata_generation_outside_final_transaction(self) -> None:
        self.mutate(
            "os/agent_metadata_objects.c",
            "\tif (result == AGENT_STATUS_OK)\n"
            "\t\tresult = agent_metadata_catalog_fence_generation(\n"
            "\t\t\tscope_id, lifecycle, metadata_generation);\n"
            "\tagent_metadata_txn_unlock();",
            "\tagent_metadata_txn_unlock();\n"
            "\tif (result == AGENT_STATUS_OK)\n"
            "\t\tresult = agent_metadata_catalog_fence_generation(\n"
            "\t\t\tscope_id, lifecycle, metadata_generation);",
        )
        self.assert_rejected("one volatile lifecycle transaction")

    def test_rejects_partial_tombstone_drain_at_metadata_cut(self) -> None:
        self.mutate(
            "os/agent_live_query_events.c",
            "\t    !agent_metadata_txn_owned(0))\n"
            "\t\treturn AGENT_STATUS_RETRY;\n"
            "\tfor (uint slot = 0; slot < AGENT_LIVE_QUERY_TOMBSTONE_CAP; slot++) {",
            "\t    !agent_metadata_txn_owned(0))\n"
            "\t\treturn AGENT_STATUS_RETRY;\n"
            "\tfor (uint slot = 0; slot < 8; slot++) {",
        )
        self.assert_rejected("fully drain lifecycle live-query work")

    def test_rejects_partial_content_event_drain_at_metadata_cut(self) -> None:
        self.mutate(
            "os/agent_live_query_events.c",
            "slot < AGENT_FILE_META_MAX",
            "slot < 8",
        )
        self.assert_rejected("fully drain lifecycle live-query work")

    def test_rejects_pending_system_live_query_omission(self) -> None:
        self.mutate(
            "os/agent_live_query_events.c",
            "state->scope_id == VFS_SCOPE_SYSTEM ||",
            "state->scope_id != VFS_SCOPE_SYSTEM &&",
        )
        self.assert_rejected("pending SYSTEM or lifecycle live-query state")

    def test_rejects_system_generation_omission(self) -> None:
        self.mutate(
            "os/agent_metadata_catalog.c",
            "system_generation = agent_file_state_scope_generation(VFS_SCOPE_SYSTEM);",
            "system_generation = agent_file_state_scope_generation(scope_id);",
        )
        self.assert_rejected("lifecycle, scope, catalog, and SYSTEM bound")

    def test_rejects_generation_during_catalog_mutation(self) -> None:
        self.mutate(
            "os/agent_metadata_catalog.c",
            "agent_catalog_mutation_owner != 0 || agent_catalog_active_edit != 0 ||\n"
            "\t    vfs_scope_lifecycle(scope_id, &current) < 0",
            "agent_catalog_mutation_owner == 0 || agent_catalog_active_edit == 0 ||\n"
            "\t    vfs_scope_lifecycle(scope_id, &current) < 0",
        )
        self.assert_rejected("lifecycle, scope, catalog, and SYSTEM bound")

    def test_rejects_unhashed_credit_epoch(self) -> None:
        self.mutate(
            "os/agent_evidence_ring.c",
            "agent_evidence_hash_u64(&hash, credit_epoch);",
            "agent_evidence_hash_u64(&hash, 0);",
        )
        self.assert_rejected("evidence root omits the credit epoch")

    def test_rejects_unhashed_resource_used(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "credit->used[kind]);",
            "credit->pending[kind]);",
        )
        self.assert_rejected("canonically binds key/epoch/handles/used")

    def test_rejects_unhashed_account_generation(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "credit->account[role].handle.generation);",
            "credit->account[role].handle.slot);",
        )
        self.assert_rejected("canonically binds key/epoch/handles/used")

    def test_rejects_credit_digest_omitted_from_evidence_root(self) -> None:
        self.mutate(
            "os/agent_evidence_ring.c",
            "agent_sha256_update(&hash, credit_digest, AGENT_SHA256_DIGEST_SIZE);",
            "agent_sha256_update(&hash, merkle_root, AGENT_SHA256_DIGEST_SIZE);",
        )
        self.assert_rejected("evidence root omits the exact credit digest")

    def test_rejects_receipt_with_unbound_credit_digest(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "memmove(completed.credit_digest, credit_digest,",
            "memmove(completed.credit_digest, challenge,",
        )
        self.assert_rejected("receipt omits the evidence-bound credit digest")

    def test_credit_bit_flips_change_digest_and_root(self) -> None:
        def digest(
            key: tuple[int, int],
            epoch: int,
            handles: tuple[tuple[int, int], tuple[int, int]],
            used: tuple[int, ...],
        ) -> bytes:
            hasher = hashlib.sha256()
            hasher.update(b"AgentOS workflow credit exact v1")
            hasher.update(struct.pack("<I", key[0]))
            hasher.update(struct.pack("<Q", key[1]))
            hasher.update(struct.pack("<Q", epoch))
            for slot, generation in handles:
                hasher.update(struct.pack("<I", slot))
                hasher.update(struct.pack("<Q", generation))
            for value in used:
                hasher.update(struct.pack("<Q", value))
            return hasher.digest()

        def root(credit_digest: bytes) -> bytes:
            return hashlib.sha256(b"fence-root-binding" + credit_digest).digest()

        baseline = digest((3, 9), 17, ((2, 5), (7, 11)), (1,) * 8)
        used_flip = digest((3, 9), 17, ((2, 5), (7, 11)), (0,) + (1,) * 7)
        handle_flip = digest((3, 9), 17, ((2, 4), (7, 11)), (1,) * 8)
        self.assertNotEqual(baseline, used_flip)
        self.assertNotEqual(baseline, handle_flip)
        self.assertNotEqual(root(baseline), root(used_flip))
        self.assertNotEqual(root(baseline), root(handle_flip))

    def test_rejects_sequence_advance_on_abort(self) -> None:
        self.mutate(
            "os/workflow_lifecycle.c",
            "if (committed)\n\t\t\trecord->fence_sequence = fence_sequence;",
            "if (!committed)\n\t\t\trecord->fence_sequence = fence_sequence;",
        )
        self.assert_rejected("abort advances the sequence")

    def test_rejects_failure_using_commit_end(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "workflow_lifecycle_fence_end(key, fence_sequence, 0)",
            "workflow_lifecycle_fence_end(key, fence_sequence, 1)",
        )
        self.assert_rejected("failures do not abort without advancing")

    def test_rejects_cache_publication_after_irq_restore(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "agent_workflow_fence_cache_publish(key, request_id, receipt);\n"
            "\tintr_restore(enabled);",
            "intr_restore(enabled);\n"
            "\tagent_workflow_fence_cache_publish(key, request_id, receipt);",
        )
        self.assert_rejected("sequence and retry cache are not atomically published")

    def test_rejects_non_exact_cache_request_id(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "request->request_id == cache->request_id",
            "request->request_id != cache->request_id",
        )
        self.assert_rejected("does not match an exact request_id")

    def test_rejects_same_id_challenge_as_stale(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "result = AGENT_STATUS_CONFLICT;",
            "result = AGENT_STATUS_STALE;",
        )
        self.assert_rejected("different challenge is not a conflict")

    def test_rejects_higher_request_id_as_stale(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "request->request_id < cache->request_id",
            "request->request_id > cache->request_id",
        )
        self.assert_rejected("lacks monotonic stale detection")

    def test_rejects_missing_retry_cache_publication(self) -> None:
        self.mutate(
            "os/agent_workflow_fence.c",
            "agent_workflow_fence_commit_cached(\n"
            "\t\tkey, fence_sequence, request_id, &completed);",
            "agent_workflow_fence_commit_cached_late(\n"
            "\t\tkey, fence_sequence, request_id, &completed);",
        )
        self.assert_rejected("gate/flush/seal/commit/publication order changed")

    def test_rejects_non_exact_receipt_flags(self) -> None:
        replacements = {
            "AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE":
                "AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE",
            "AGENT_WORKFLOW_FENCE_RECEIPT_F_CREDIT_EXACT":
                "AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE",
            "AGENT_WORKFLOW_FENCE_RECEIPT_F_EVIDENCE_SEALED":
                "AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE",
            "AGENT_WORKFLOW_FENCE_RECEIPT_F_METADATA_VOLATILE":
                "AGENT_WORKFLOW_FENCE_RECEIPT_F_PARTIAL_COVERAGE",
        }
        for flag, replacement in replacements.items():
            with self.subTest(flag=flag):
                path = self.root / "os/agent_workflow_fence.c"
                original = path.read_text(encoding="utf-8")
                self.assertIn(flag, original)
                path.write_text(
                    original.replace(
                        flag,
                        replacement,
                        1,
                    ),
                    encoding="utf-8",
                )
                self.assert_rejected(f"receipt omits {flag}")
                path.write_text(original, encoding="utf-8")

    def test_rejects_wrapper_using_a_normal_batch(self) -> None:
        self.mutate(
            "user/lib/syscall.c",
            "return syscall(SYS_agent_run, request, receipt, 0,",
            "return syscall(SYS_agent_run, request, receipt, 1,",
        )
        self.assert_rejected("does not exactly reuse SYS_agent_run")


if __name__ == "__main__":
    unittest.main()
