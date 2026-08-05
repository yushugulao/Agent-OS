#!/usr/bin/env python3
"""No-QEMU recovery and mutation tests for the metadata v8 journal."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHARED_ABI = ROOT / "agent_metadata_disk_abi.h"
STORE_SOURCE = ROOT / "os" / "agent_metadata_store.c"
CATALOG_SOURCE = ROOT / "os" / "agent_metadata_catalog.c"
OBJECTS_SOURCE = ROOT / "os" / "agent_metadata_objects.c"
SCAN_SOURCE = ROOT / "os" / "agent_metadata_scan.c"
FILE_STATE_SOURCE = ROOT / "os" / "agent_file_state.c"
DIRECTORY_SOURCE = ROOT / "os" / "agent_metadata_directory.c"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_tools import agent_metadata_journal as journal


BASE_GENERATION = 41
BASE_PAYLOAD_HASH = 0x9876543210ABCDEF
IDENTITY = journal.JournalIdentity(23, 71, 9)


def record(fid: int, marker: int = 0) -> bytes:
    raw = bytearray(journal.SLOT_PAYLOAD_BYTES)
    struct.pack_into("<iiQ", raw, 0, 1, fid, marker)
    return bytes(raw)


def deltas(count: int, *, start_fid: int = 1) -> tuple[journal.JournalDelta, ...]:
    return tuple(
        journal.JournalDelta(journal.OP_UPSERT, record(start_fid + index, index))
        for index in range(count)
    )


def write_at(raw: bytearray, start_slot: int, transaction: bytes) -> None:
    offset = start_slot * journal.SLOT_BYTES
    raw[offset : offset + len(transaction)] = transaction


def mutate_u64_and_rehash_slot(raw: bytearray, slot: int, offset: int, value: int) -> None:
    start = slot * journal.SLOT_BYTES
    struct.pack_into("<Q", raw, start + offset, value)
    raw[start + 88 : start + 96] = bytes(8)
    digest = journal.fnv1a64(bytes(raw[start : start + journal.SLOT_BYTES]))
    struct.pack_into("<Q", raw, start + 88, digest)


def catalog_receipt_contract(source: str) -> bool:
    return all(
        token in source
        for token in (
            "state->scope_id == scope_id &&",
            "workflow_lifecycle_key_equal(state->lifecycle, lifecycle)",
            "state->sequences[i] != change->sequence",
            "state->overflow_sequence == settle->overflow_sequence",
            "agent_catalog_journal_sequence_next()",
            "agent_catalog_journal_lock()",
            "agent_file_state_snapshot_overlay_receipt(",
            "agent_file_state_content_settle(",
        )
    )


class ContentReceiptLedger:
    """Small executable model of exact slot and overlay settlement."""

    def __init__(self) -> None:
        self.journal_sequence = 0
        self.content_sequence = 0
        self.pending: dict[tuple[int, tuple[int, int], int], int] = {}
        self.overlays: dict[tuple[int, int, int], dict[str, object]] = {}

    def note(self, scope: int, lifecycle: tuple[int, int], slot: int) -> None:
        self.journal_sequence += 1
        self.pending[(scope, lifecycle, slot)] = self.journal_sequence

    def write(self, scope: int, lifecycle: tuple[int, int], slot: int,
              identity: tuple[int, int, int], size: int) -> None:
        self.content_sequence += 1
        self.overlays[identity] = {
            "dirty": True,
            "sequence": self.content_sequence,
            "scope": scope,
            "lifecycle": lifecycle,
            "slot": slot,
            "size": size,
        }
        self.note(scope, lifecycle, slot)

    def capture(self, scope: int, lifecycle: tuple[int, int],
                catalog: dict[int, dict[str, object]]) -> list[dict[str, object]] | None:
        selected = [
            (slot, sequence)
            for (entry_scope, entry_lifecycle, slot), sequence in self.pending.items()
            if entry_scope == scope and entry_lifecycle == lifecycle
        ]
        if len(selected) > 15:
            return None
        receipt: list[dict[str, object]] = []
        for slot, journal_sequence in selected:
            meta = catalog.get(slot)
            present = bool(
                meta
                and meta["scope"] == scope
                and meta["lifecycle"] == lifecycle
                and meta["persist"]
            )
            content = None
            size = None if not present else meta["size"]
            if present:
                overlay = self.overlays.get(tuple(meta["identity"]))
                if (
                    overlay
                    and overlay["dirty"]
                    and overlay["scope"] == scope
                    and overlay["lifecycle"] == lifecycle
                    and overlay["slot"] == slot
                ):
                    size = overlay["size"]
                    content = (
                        tuple(meta["identity"]),
                        overlay["sequence"],
                        scope,
                        lifecycle,
                        slot,
                    )
            receipt.append({
                "slot": slot,
                "journal_sequence": journal_sequence,
                "present": present,
                "size": size,
                "content": content,
            })
        return receipt

    def commit(self, receipt: list[dict[str, object]]) -> None:
        for change in receipt:
            slot = int(change["slot"])
            journal_sequence = int(change["journal_sequence"])
            for key, sequence in list(self.pending.items()):
                if key[2] == slot and sequence == journal_sequence:
                    del self.pending[key]
            content = change["content"]
            if content is None:
                continue
            identity, sequence, scope, lifecycle, content_slot = content
            overlay = self.overlays.get(identity)
            if overlay and (
                overlay["dirty"]
                and overlay["sequence"] == sequence
                and overlay["scope"] == scope
                and overlay["lifecycle"] == lifecycle
                and overlay["slot"] == content_slot
            ):
                overlay["dirty"] = False


class MetadataJournalTests(unittest.TestCase):
    def test_host_layout_matches_shared_c_abi(self) -> None:
        source = SHARED_ABI.read_text(encoding="utf-8")
        for token in (
            "#define AGENT_META_STORE_VERSION 8U",
            "#define AGENT_META_JOURNAL_MAGIC 0x41474d4a4e4c3038ULL",
            "#define AGENT_META_JOURNAL_VERSION 1U",
            "#define AGENT_META_JOURNAL_KIND_DATA 1U",
            "#define AGENT_META_JOURNAL_KIND_COMMIT 2U",
            "#define AGENT_META_JOURNAL_KIND_PAD 3U",
            "#define AGENT_META_JOURNAL_OP_NONE 0U",
            "#define AGENT_META_JOURNAL_OP_UPSERT 1U",
            "#define AGENT_META_JOURNAL_OP_DELETE 2U",
            "#define AGENT_META_JOURNAL_OP_ARENA_PATCH 3U",
            "#define AGENT_META_JOURNAL_PATCH_DATA_BYTES 400U",
            "#define AGENT_META_JOURNAL_BLOCK_BYTES 1024U",
            "#define AGENT_META_JOURNAL_SLOT_BYTES 512U",
            "#define AGENT_META_JOURNAL_HEADER_BYTES 96U",
            "#define AGENT_META_JOURNAL_BLOCKS 32U",
            "#define AGENT_META_JOURNAL_MAX_DATA_RECORDS 15U",
            "#define AGENT_META_JOURNAL_MAX_TXN_BLOCKS 8U",
            "#define AGENT_META_JOURNAL_OFFSET 222208U",
        ):
            self.assertIn(token, source)
        offsets = {
            "magic": 0,
            "version": 8,
            "kind": 12,
            "base_generation": 16,
            "generation": 24,
            "scope_id": 32,
            "lifecycle_id": 36,
            "lifecycle_generation": 40,
            "record_index": 48,
            "record_count": 52,
            "operation": 56,
            "payload_bytes": 60,
            "previous_commit_hash": 64,
            "payload_hash": 72,
            "group_hash": 80,
            "slot_hash": 88,
        }
        for field, offset in offsets.items():
            self.assertIn(
                f"__builtin_offsetof(amd_journal_header, {field}) == {offset}U",
                source,
            )

    def test_normal_metadata_path_consumes_bounded_receipt(self) -> None:
        store = STORE_SOURCE.read_text(encoding="utf-8")
        catalog = CATALOG_SOURCE.read_text(encoding="utf-8")
        objects = OBJECTS_SOURCE.read_text(encoding="utf-8")
        scan = SCAN_SOURCE.read_text(encoding="utf-8")
        file_state = FILE_STATE_SOURCE.read_text(encoding="utf-8")
        directory = DIRECTORY_SOURCE.read_text(encoding="utf-8")

        start = store.index("static int agent_meta_persist_start_locked")
        end = store.index("static void agent_meta_persist_abort_locked", start)
        persist_start = store[start:end]
        fast = persist_start.index("agent_meta_store_prepare_journal(")
        fallback = persist_start.index("if (!use_journal)")
        self.assertLess(fast, fallback)
        self.assertNotIn("agent_meta_journal_plan_diff(", persist_start)
        self.assertIn("agent_meta_store_build_scope(", persist_start[fallback:])
        self.assertIn("agent_meta_format_store_hash(store)", persist_start[fallback:])
        self.assertIn("agent_meta_bank_record_index", store)
        self.assertIn("agent_meta_journal_apply_trusted(", store)
        self.assertIn(
            "state->phase = AGENT_META_PERSIST_JOURNAL_COMMIT;", store
        )
        self.assertIn("agent_meta_store_require_mirror(mirror, 0);", store)
        self.assertIn("agent_meta_persist_release_locked(0);", store)

        self.assertTrue(catalog_receipt_contract(catalog))
        self.assertIn("published_meta_slot = ip->agent_meta_slot - 1;", file_state)
        self.assertIn("receipt->sequence =\n\t\t\t\t\tentry->published_size_sequence;", file_state)
        self.assertLess(
            directory.index("agent_metadata_catalog_journal_note_content(&receipt)"),
            directory.index("agent_metadata_store_mark_dirty(ip->vfs_scope_id)"),
        )
        journal_publish = store[
            store.index("static void\nagent_meta_journal_primary_publish_locked"):
            store.index("static int\nagent_meta_persist_journal_step_locked")
        ]
        self.assertNotIn("agent_file_state_sizes_persisted(", journal_publish)
        self.assertIn("agent_metadata_catalog_journal_commit(", journal_publish)

        finish = objects.index("static int\nagent_file_finish_mutation")
        finish_end = objects.index("static int\nagent_lookup_error_status", finish)
        success = objects[objects.index("if (event_payload)", finish):finish_end]
        self.assertNotIn("agent_file_request_scan();", success)
        self.assertIn("scan_ctl.on = 0;", scan)

    def test_pure_content_is_one_exact_upsert_receipt(self) -> None:
        model = ContentReceiptLedger()
        lifecycle = (4, 11)
        identity = (1, 37, 8)
        catalog = {
            3: {
                "scope": 23,
                "lifecycle": lifecycle,
                "identity": identity,
                "persist": True,
                "size": 16,
            }
        }

        model.write(23, lifecycle, 3, identity, 91)
        receipt = model.capture(23, lifecycle, catalog)

        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual(len(receipt), 1)
        self.assertTrue(receipt[0]["present"])
        self.assertEqual(receipt[0]["size"], 91)
        self.assertIsNotNone(receipt[0]["content"])

    def test_catalog_and_content_slots_share_one_bounded_receipt(self) -> None:
        model = ContentReceiptLedger()
        lifecycle = (5, 12)
        catalog = {
            2: {
                "scope": 29, "lifecycle": lifecycle,
                "identity": (1, 42, 1), "persist": True, "size": 4,
            },
            7: {
                "scope": 29, "lifecycle": lifecycle,
                "identity": (1, 47, 1), "persist": True, "size": 8,
            },
        }
        model.note(29, lifecycle, 2)
        model.write(29, lifecycle, 7, (1, 47, 1), 64)

        receipt = model.capture(29, lifecycle, catalog)

        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual({item["slot"] for item in receipt}, {2, 7})
        self.assertEqual(
            next(item["size"] for item in receipt if item["slot"] == 7), 64
        )

    def test_commit_after_new_write_preserves_new_receipt_and_overlay(self) -> None:
        model = ContentReceiptLedger()
        lifecycle = (6, 13)
        identity = (1, 51, 2)
        catalog = {
            9: {
                "scope": 31, "lifecycle": lifecycle,
                "identity": identity, "persist": True, "size": 1,
            }
        }
        model.write(31, lifecycle, 9, identity, 20)
        captured = model.capture(31, lifecycle, catalog)
        assert captured is not None
        model.write(31, lifecycle, 9, identity, 27)

        model.commit(captured)

        self.assertTrue(model.overlays[identity]["dirty"])
        self.assertEqual(model.overlays[identity]["size"], 27)
        self.assertEqual(len(model.pending), 1)
        latest = model.capture(31, lifecycle, catalog)
        assert latest is not None
        self.assertEqual(latest[0]["size"], 27)

    def test_lifecycle_reuse_and_identity_mismatch_do_not_absorb_overlay(self) -> None:
        model = ContentReceiptLedger()
        old_lifecycle = (7, 14)
        new_lifecycle = (7, 15)
        model.write(37, old_lifecycle, 11, (1, 61, 3), 44)
        replacement = {
            11: {
                "scope": 37, "lifecycle": new_lifecycle,
                "identity": (1, 62, 4), "persist": True, "size": 5,
            }
        }

        self.assertEqual(model.capture(37, new_lifecycle, replacement), [])
        old = model.capture(37, old_lifecycle, replacement)
        assert old is not None
        self.assertFalse(old[0]["present"])
        self.assertIsNone(old[0]["content"])

    def test_more_than_fifteen_content_slots_requires_full_snapshot(self) -> None:
        model = ContentReceiptLedger()
        lifecycle = (8, 16)
        catalog: dict[int, dict[str, object]] = {}
        for slot in range(16):
            identity = (1, 100 + slot, 1)
            catalog[slot] = {
                "scope": 41, "lifecycle": lifecycle,
                "identity": identity, "persist": True, "size": 0,
            }
            model.write(41, lifecycle, slot, identity, slot + 1)

        self.assertIsNone(model.capture(41, lifecycle, catalog))

    def test_receipt_contract_rejects_scope_reuse_and_stale_settle_mutants(self) -> None:
        source = CATALOG_SOURCE.read_text(encoding="utf-8")
        self.assertTrue(catalog_receipt_contract(source))
        mutants = (
            source.replace(
                "workflow_lifecycle_key_equal(state->lifecycle, lifecycle)",
                "1 /* scope-only mutant */",
                1,
            ),
            source.replace(
                "state->sequences[i] != change->sequence",
                "0 /* stale settle mutant */",
                1,
            ),
            source.replace(
                "state->overflow_sequence == settle->overflow_sequence",
                "settle->overflow_sequence != 0 /* overflow mutant */",
                1,
            ),
        )
        for mutant in mutants:
            self.assertFalse(catalog_receipt_contract(mutant))

    def test_fixed_geometry_and_exact_header_offsets(self) -> None:
        self.assertEqual(journal.JOURNAL_OFFSET, 222208)
        self.assertEqual(journal.JOURNAL_BYTES, 32 * 1024)
        self.assertEqual(journal.SLOT_COUNT, 64)
        self.assertEqual(journal.SLOT_HEADER.size, 96)
        self.assertEqual(journal.SLOT_PAYLOAD_BYTES, 416)
        self.assertEqual(journal.MAX_DATA_SLOTS, 15)
        encoded, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        fields = journal.SLOT_HEADER.unpack(encoded[: journal.SLOT_HEADER_BYTES])
        self.assertEqual(
            fields[:12],
            (
                journal.JOURNAL_MAGIC,
                journal.JOURNAL_VERSION,
                journal.KIND_DATA,
                BASE_GENERATION,
                BASE_GENERATION + 1,
                IDENTITY.scope_id,
                IDENTITY.lifecycle_id,
                IDENTITY.lifecycle_generation,
                0,
                1,
                journal.OP_UPSERT,
                416,
            ),
        )

    def test_hash_vectors_are_stable(self) -> None:
        self.assertEqual(journal.fnv1a64(b""), 0x14650FB0739D0383)
        self.assertEqual(journal.fnv1a64(b"AgentOS-v8"), 0x9EBC50EB932DA373)
        self.assertEqual(
            journal.base_chain_hash(BASE_GENERATION, BASE_PAYLOAD_HASH),
            0x88DB658854A9D55B,
        )

    def test_single_data_commit_is_one_block(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        commit_hash = appender.append(IDENTITY, deltas(1))
        recovered = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(len(recovered.transactions), 1)
        self.assertEqual(recovered.transactions[0].blocks, 1)
        self.assertEqual(recovered.transactions[0].commit_hash, commit_hash)
        self.assertEqual(recovered.next_slot, 2)
        self.assertIsNone(recovered.ignored_tail_slot)

    def test_even_data_count_has_authenticated_pad(self) -> None:
        encoded, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(2),
        )
        self.assertEqual(len(encoded), 2 * journal.JOURNAL_BLOCK_BYTES)
        pad = journal.SLOT_HEADER.unpack(
            encoded[3 * journal.SLOT_BYTES : 3 * journal.SLOT_BYTES + 96]
        )
        commit = journal.SLOT_HEADER.unpack(
            encoded[2 * journal.SLOT_BYTES : 2 * journal.SLOT_BYTES + 96]
        )
        self.assertEqual(pad[2], journal.KIND_PAD)
        self.assertEqual(pad[8], 3)
        self.assertEqual(pad[12], commit[15])
        self.assertEqual(pad[14], commit[14])

    def test_max_transaction_is_eight_blocks(self) -> None:
        encoded, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(15),
        )
        self.assertEqual(len(encoded), 8 * journal.JOURNAL_BLOCK_BYTES)
        with self.assertRaisesRegex(journal.JournalFormatError, "1..15"):
            journal.encode_transaction(
                base_generation=BASE_GENERATION,
                generation=BASE_GENERATION + 1,
                identity=IDENTITY,
                previous_commit_hash=journal.base_chain_hash(
                    BASE_GENERATION, BASE_PAYLOAD_HASH
                ),
                deltas=deltas(16),
            )

    def test_generation_and_previous_commit_chain_is_contiguous(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        first_hash = appender.append(IDENTITY, deltas(3))
        second_hash = appender.append(
            journal.JournalIdentity(24, 72, 10), deltas(2, start_fid=20)
        )
        recovered = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(
            [transaction.generation for transaction in recovered.transactions],
            [BASE_GENERATION + 1, BASE_GENERATION + 2],
        )
        self.assertEqual(recovered.transactions[1].previous_commit_hash, first_hash)
        self.assertEqual(recovered.last_commit_hash, second_hash)

    def test_complete_group_with_wrong_generation_fails_closed(self) -> None:
        raw = bytearray(journal.JOURNAL_BYTES)
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 2,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        write_at(raw, 0, transaction)
        with self.assertRaisesRegex(journal.JournalIntegrityError, "discontinuous"):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_complete_group_with_wrong_base_fails_closed(self) -> None:
        raw = bytearray(journal.JOURNAL_BYTES)
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION - 1,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        write_at(raw, 0, transaction)
        with self.assertRaisesRegex(journal.JournalIntegrityError, "discontinuous"):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_complete_group_with_wrong_previous_hash_fails_closed(self) -> None:
        raw = bytearray(journal.JOURNAL_BYTES)
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=0x1234,
            deltas=deltas(1),
        )
        write_at(raw, 0, transaction)
        with self.assertRaisesRegex(journal.JournalIntegrityError, "discontinuous"):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_power_cut_at_every_transaction_block_boundary(self) -> None:
        chain = journal.base_chain_hash(BASE_GENERATION, BASE_PAYLOAD_HASH)
        for count in range(1, journal.MAX_DATA_SLOTS + 1):
            transaction, _ = journal.encode_transaction(
                base_generation=BASE_GENERATION,
                generation=BASE_GENERATION + 1,
                identity=IDENTITY,
                previous_commit_hash=chain,
                deltas=deltas(count),
            )
            blocks = len(transaction) // journal.JOURNAL_BLOCK_BYTES
            for durable_blocks in range(blocks + 1):
                with self.subTest(count=count, durable_blocks=durable_blocks):
                    raw = bytearray(journal.JOURNAL_BYTES)
                    durable = durable_blocks * journal.JOURNAL_BLOCK_BYTES
                    raw[:durable] = transaction[:durable]
                    recovered = journal.recover_journal(
                        bytes(raw),
                        base_generation=BASE_GENERATION,
                        base_payload_hash=BASE_PAYLOAD_HASH,
                    )
                    self.assertEqual(
                        len(recovered.transactions),
                        1 if durable_blocks == blocks else 0,
                    )

    def test_torn_last_commit_is_ignored(self) -> None:
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        # The COMMIT payload is canonical zero media.  Until its 96-byte
        # header is complete the slot hash cannot validate; from byte 608 the
        # complete header intentionally makes the zero-filled slot durable.
        for committed_bytes in (513, 577, 607):
            with self.subTest(committed_bytes=committed_bytes):
                raw = bytearray(journal.JOURNAL_BYTES)
                raw[:committed_bytes] = transaction[:committed_bytes]
                recovered = journal.recover_journal(
                    bytes(raw),
                    base_generation=BASE_GENERATION,
                    base_payload_hash=BASE_PAYLOAD_HASH,
                )
                self.assertEqual(recovered.transactions, ())
                self.assertEqual(recovered.ignored_tail_slot, 0)

    def test_corrupt_last_commit_rolls_back_only_last_transaction(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(IDENTITY, deltas(1))
        first = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        ).transactions[0]
        appender.append(IDENTITY, deltas(1, start_fid=10))
        raw = bytearray(appender.bytes())
        second_commit = first.end_slot + 1
        raw[second_commit * journal.SLOT_BYTES + 100] ^= 0x80
        recovered = journal.recover_journal(
            bytes(raw),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(len(recovered.transactions), 1)
        self.assertEqual(recovered.ignored_tail_slot, first.end_slot)

    def test_valid_transaction_after_corrupt_group_fails_closed(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(IDENTITY, deltas(1))
        appender.append(IDENTITY, deltas(1, start_fid=10))
        raw = bytearray(appender.bytes())
        raw[journal.SLOT_BYTES + 20] ^= 0x40
        with self.assertRaisesRegex(journal.JournalIntegrityError, "follows|after"):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_valid_group_after_empty_block_gap_fails_closed(self) -> None:
        raw = bytearray(journal.JOURNAL_BYTES)
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        write_at(raw, 2, transaction)
        with self.assertRaisesRegex(journal.JournalIntegrityError, "gap"):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_payload_or_group_mutation_before_later_commit_fails_closed(self) -> None:
        for mutation in ("payload", "group"):
            with self.subTest(mutation=mutation):
                appender = journal.JournalAppender(
                    base_generation=BASE_GENERATION,
                    base_payload_hash=BASE_PAYLOAD_HASH,
                )
                appender.append(IDENTITY, deltas(1))
                appender.append(IDENTITY, deltas(1, start_fid=10))
                raw = bytearray(appender.bytes())
                if mutation == "payload":
                    raw[120] ^= 1
                else:
                    mutate_u64_and_rehash_slot(raw, 1, 80, 0xBAD)
                with self.assertRaises(journal.JournalIntegrityError):
                    journal.recover_journal(
                        bytes(raw),
                        base_generation=BASE_GENERATION,
                        base_payload_hash=BASE_PAYLOAD_HASH,
                    )

    def test_scope_and_lifecycle_are_bound_across_group(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(IDENTITY, deltas(2))
        appender.append(IDENTITY, deltas(1, start_fid=10))
        raw = bytearray(appender.bytes())
        # Make DATA[1] independently valid but bind it to another lifecycle.
        mutate_u64_and_rehash_slot(
            raw, 1, 40, IDENTITY.lifecycle_generation + 1
        )
        with self.assertRaises(journal.JournalIntegrityError):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_valid_commit_at_transaction_boundary_fails_closed(self) -> None:
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        raw = bytearray(journal.JOURNAL_BYTES)
        raw[: journal.SLOT_BYTES] = transaction[journal.SLOT_BYTES :]
        with self.assertRaisesRegex(journal.JournalIntegrityError, "non-DATA"):
            journal.recover_journal(
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_any_valid_v8_slot_forbids_v7_downgrade(self) -> None:
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(1),
        )
        raw = bytearray(journal.JOURNAL_BYTES)
        raw[: journal.SLOT_BYTES] = transaction[: journal.SLOT_BYTES]
        self.assertTrue(journal.contains_valid_v8_slot(bytes(raw)))
        with self.assertRaises(journal.JournalDowngradeError):
            journal.recover_versioned_store(
                journal.STORE_VERSION_V7,
                bytes(raw),
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
            )

    def test_clean_v7_migrates_once_and_v8_recovers(self) -> None:
        empty = bytes(journal.JOURNAL_BYTES)
        v7 = journal.recover_versioned_store(
            journal.STORE_VERSION_V7,
            empty,
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(v7.mode, "migrate-v7-to-v8")
        self.assertIsNone(v7.journal)
        v8 = journal.recover_versioned_store(
            journal.STORE_VERSION_V8,
            empty,
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(v8.mode, "recover-v8")
        self.assertEqual(v8.journal.last_generation, BASE_GENERATION)

    def test_immutable_media_refuses_physical_block_rewrite(self) -> None:
        media = journal.ImmutableJournalMedia()
        media.write_block_once(0, bytes([0xA5]) * journal.JOURNAL_BLOCK_BYTES)
        with self.assertRaisesRegex(journal.JournalRewriteError, "cannot be rewritten"):
            media.write_block_once(0, bytes(journal.JOURNAL_BLOCK_BYTES))

    def test_interrupted_tail_requires_compaction_not_rewrite(self) -> None:
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=deltas(3),
        )
        raw = bytearray(journal.JOURNAL_BYTES)
        raw[: journal.JOURNAL_BLOCK_BYTES] = transaction[: journal.JOURNAL_BLOCK_BYTES]
        with self.assertRaisesRegex(journal.JournalRewriteError, "compacted"):
            journal.JournalAppender(
                base_generation=BASE_GENERATION,
                base_payload_hash=BASE_PAYLOAD_HASH,
                raw=bytes(raw),
            )

    def test_capacity_requires_compaction_without_reuse(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        for index in range(4):
            appender.append(
                journal.JournalIdentity(23 + index, 71 + index, 9 + index),
                deltas(15, start_fid=1 + index * 20),
            )
        self.assertEqual(appender.next_slot, journal.SLOT_COUNT)
        with self.assertRaisesRegex(journal.JournalCapacityError, "compaction"):
            appender.append(IDENTITY, deltas(1))

    def test_materialized_records_apply_only_committed_deltas(self) -> None:
        base = [record(1, 10), record(2, 20)]
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(
            IDENTITY,
            (
                journal.JournalDelta(journal.OP_UPSERT, record(2, 21)),
                journal.JournalDelta(journal.OP_DELETE, record(1, 10)),
                journal.JournalDelta(journal.OP_UPSERT, record(3, 30)),
            ),
        )
        recovered = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        materialized = journal.materialize_records(base, recovered)
        self.assertEqual(sorted(materialized), [2, 3])
        self.assertEqual(struct.unpack_from("<Q", materialized[2], 8)[0], 21)

    def test_arena_windows_replay_with_record_delta_in_one_commit(self) -> None:
        base_arena = bytes(journal.ARENA_BYTES)
        next_arena = bytearray(base_arena)
        next_arena[17:29] = b"receipt-v8!\0"
        next_arena[8010:8022] = b"root-chain!\0"
        patch0 = journal.encode_arena_patch(base_arena, bytes(next_arena), 0)
        patch1 = journal.encode_arena_patch(base_arena, bytes(next_arena), 8000)
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(
            IDENTITY,
            (
                journal.JournalDelta(journal.OP_ARENA_PATCH, patch0),
                journal.JournalDelta(journal.OP_UPSERT, record(7, 70)),
                journal.JournalDelta(journal.OP_ARENA_PATCH, patch1),
            ),
        )
        recovered = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(
            journal.materialize_arena(base_arena, recovered), bytes(next_arena)
        )
        self.assertEqual(sorted(journal.materialize_records([], recovered)), [7])

    def test_content_upsert_and_arena_patch_are_atomic_at_every_power_cut(self) -> None:
        base_arena = bytes(journal.ARENA_BYTES)
        next_arena = bytearray(base_arena)
        next_arena[400:416] = b"content-receipt!"
        content = record(19, 4096)
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=(
                journal.JournalDelta(
                    journal.OP_ARENA_PATCH,
                    journal.encode_arena_patch(
                        base_arena, bytes(next_arena), 400
                    ),
                ),
                journal.JournalDelta(journal.OP_UPSERT, content),
            ),
        )
        blocks = len(transaction) // journal.JOURNAL_BLOCK_BYTES
        for durable_blocks in range(blocks + 1):
            with self.subTest(durable_blocks=durable_blocks):
                raw = bytearray(journal.JOURNAL_BYTES)
                durable = durable_blocks * journal.JOURNAL_BLOCK_BYTES
                raw[:durable] = transaction[:durable]
                recovered = journal.recover_journal(
                    bytes(raw),
                    base_generation=BASE_GENERATION,
                    base_payload_hash=BASE_PAYLOAD_HASH,
                )
                committed = durable_blocks == blocks
                self.assertEqual(len(recovered.transactions), int(committed))
                self.assertEqual(
                    journal.materialize_arena(base_arena, recovered),
                    bytes(next_arena) if committed else base_arena,
                )
                records = journal.materialize_records([], recovered)
                self.assertEqual(sorted(records), [19] if committed else [])

    def test_arena_patch_rejects_wrong_baseline_and_duplicate_window(self) -> None:
        base_arena = bytes(journal.ARENA_BYTES)
        next_arena = bytearray(base_arena)
        next_arena[0] = 1
        payload = journal.encode_arena_patch(base_arena, bytes(next_arena), 0)
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(
            IDENTITY,
            (
                journal.JournalDelta(journal.OP_ARENA_PATCH, payload),
                journal.JournalDelta(journal.OP_ARENA_PATCH, payload),
            ),
        )
        recovered = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        with self.assertRaisesRegex(journal.JournalFormatError, "twice"):
            journal.materialize_arena(base_arena, recovered)

        single = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        single.append(
            IDENTITY,
            (journal.JournalDelta(journal.OP_ARENA_PATCH, payload),),
        )
        with self.assertRaisesRegex(journal.JournalIntegrityError, "baseline"):
            journal.materialize_arena(bytes([0xA5]) * journal.ARENA_BYTES,
                                      journal.recover_journal(
                                          single.bytes(),
                                          base_generation=BASE_GENERATION,
                                          base_payload_hash=BASE_PAYLOAD_HASH,
                                      ))

    def test_torn_arena_transaction_cannot_publish_partial_window(self) -> None:
        base_arena = bytes(journal.ARENA_BYTES)
        next_arena = bytearray(base_arena)
        next_arena[400:800] = bytes([0x5A]) * 400
        transaction, _ = journal.encode_transaction(
            base_generation=BASE_GENERATION,
            generation=BASE_GENERATION + 1,
            identity=IDENTITY,
            previous_commit_hash=journal.base_chain_hash(
                BASE_GENERATION, BASE_PAYLOAD_HASH
            ),
            deltas=(journal.JournalDelta(
                journal.OP_ARENA_PATCH,
                journal.encode_arena_patch(base_arena, bytes(next_arena), 400),
            ),),
        )
        raw = bytearray(journal.JOURNAL_BYTES)
        raw[: len(transaction)] = transaction
        raw[journal.SLOT_BYTES + 20] ^= 0xFF
        recovered = journal.recover_journal(
            bytes(raw),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        self.assertEqual(recovered.transactions, ())
        self.assertEqual(journal.materialize_arena(base_arena, recovered), base_arena)

    def test_delete_tombstone_must_match_current_record_byte_for_byte(self) -> None:
        appender = journal.JournalAppender(
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        appender.append(
            IDENTITY,
            (journal.JournalDelta(journal.OP_DELETE, record(1, 99)),),
        )
        recovered = journal.recover_journal(
            appender.bytes(),
            base_generation=BASE_GENERATION,
            base_payload_hash=BASE_PAYLOAD_HASH,
        )
        with self.assertRaisesRegex(journal.JournalIntegrityError, "tombstone"):
            journal.materialize_records([record(1, 10)], recovered)
        with self.assertRaisesRegex(journal.JournalIntegrityError, "tombstone"):
            journal.materialize_records([], recovered)

    def test_extract_journal_uses_fixed_tail_only(self) -> None:
        image = (
            bytes([0xAA]) * journal.JOURNAL_OFFSET
            + bytes([0x55]) * journal.JOURNAL_BYTES
            + bytes([0xCC]) * 17
        )
        self.assertEqual(
            journal.extract_journal(image), bytes([0x55]) * journal.JOURNAL_BYTES
        )
        with self.assertRaisesRegex(journal.JournalFormatError, "shorter"):
            journal.extract_journal(image[: journal.JOURNAL_OFFSET])


if __name__ == "__main__":
    unittest.main()
