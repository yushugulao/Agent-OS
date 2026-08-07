#!/usr/bin/env python3
"""可独立重放的观测磁盘证据变异测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import plain_ucore_fs_extract as fs
from agent_metadata_disk_format import load_contract as load_metadata_contract
from agent_metadata_disk_format import payload_hash as metadata_payload_hash
from agent_observe_disk_evidence import (
    DEFAULT_OBSERVE_CONTRACT,
    ObservationEvidenceError,
    _fnv,
    _record_hash,
    load_observation_contract,
    validate_observation_payload,
    verify_observation_acceptance,
    verify_observation_image,
)
from agent_observe_disk_fixture import build_fixture


def kernel_record_hash(raw: bytes, layout) -> int:
    names = (
        "prev_hash", "sequence", "tick", "cause_sequence", "span_id",
        "workflow_lifecycle_generation", "branch_generation",
        "cause_branch_generation", "actor_control_id", "cause_control_id",
        "cause_record_hash", "value0", "value1", "value2", "flags", "kind",
        "workflow_lifecycle_id", "pid", "tid", "source_pid", "target_pid",
        "agent_id", "role", "loop_state", "tool_id", "event_type", "status",
    )
    value = layout.hash_initial
    for name in names:
        size = (
            layout.uint_bytes
            if name == "workflow_lifecycle_id"
            else layout.int_bytes
            if name in {
                "kind", "pid", "tid", "source_pid", "target_pid", "agent_id",
                "role", "loop_state", "tool_id", "event_type", "status",
            }
            else layout.uint64_bytes
        )
        offset = layout.record_fields[name]
        number = int.from_bytes(raw[offset : offset + size], "little", signed=False)
        for byte in number.to_bytes(8, "little"):
            value ^= byte
            value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    start = layout.record_fields["text"]
    for byte in raw[start : start + layout.text_bytes]:
        value ^= byte
        value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    return value or 1


class ObservationDiskEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = load_metadata_contract()
        cls.layout = load_observation_contract()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        raw, self.marker = build_fixture()
        self.image = self.root / "observe.img"
        self.image.write_bytes(raw)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self, marker: str | None = None) -> dict:
        return verify_observation_image(
            self.image, self.marker if marker is None else marker
        )

    def test_generic_provider_validation_does_not_require_boot_identity(self) -> None:
        image = self.image.read_bytes()
        _, inodes = self._bank_inodes(image)
        bank = self._read_inode_bytes(image, inodes[0])
        start = self.metadata.header_bytes + self.layout.arena_fields["payload"]
        payload = bytes(bank[start : start + self.layout.observe_bytes])
        result = validate_observation_payload(payload, self.layout)
        self.assertGreater(result["generation"], 0)
        self.assertNotIn("identity", result)

    def _bank_inodes(self, image: bytes):
        sb = fs.read_superblock(image)
        entries = fs.root_entries(image, sb)
        by_name = {name: inum for inum, name in entries}
        return sb, [fs.read_inode(image, sb, by_name[name]) for name in self.metadata.bank_names]

    @staticmethod
    def _read_inode_bytes(image: bytes, inode) -> bytearray:
        return bytearray(fs.read_file(image, inode))

    @staticmethod
    def _write_inode_bytes(image: bytearray, inode, payload: bytes) -> None:
        remaining = len(payload)
        offset = 0
        for block in inode.addrs[: fs.NDIRECT]:
            if remaining == 0:
                break
            if block == 0:
                raise AssertionError("fixture bank unexpectedly needs an indirect block")
            take = min(remaining, fs.BSIZE)
            image[block * fs.BSIZE : block * fs.BSIZE + take] = payload[offset : offset + take]
            remaining -= take
            offset += take
        if remaining:
            raise AssertionError("fixture bank write was incomplete")

    def _mutate_banks(self, mutate) -> None:
        image = bytearray(self.image.read_bytes())
        _, inodes = self._bank_inodes(image)
        bank = self._read_inode_bytes(image, inodes[0])
        mutate(bank)
        for inode in inodes:
            self._write_inode_bytes(image, inode, bytes(bank))
        self.image.write_bytes(image)

    def _refresh_outer(
        self,
        bank: bytearray,
        *,
        observation: bool = True,
        section: bool = True,
        arena: bool = True,
        metadata: bool = True,
    ) -> None:
        m, layout = self.metadata, self.layout
        arena_start = m.header_bytes
        af = layout.arena_fields
        observation_start = arena_start + af["payload"]
        observation_end = observation_start + layout.observe_bytes
        if observation:
            of = layout.observe_fields
            value = _fnv(layout, bytes(bank[observation_start : observation_start + of["image_hash"]]))
            bank[observation_start + of["image_hash"] : observation_start + of["image_hash"] + 8] = value.to_bytes(8, "little")
        if section:
            payload = bytes(bank[observation_start:observation_end])
            desc_hash = arena_start + af["sections"] + layout.descriptor_fields["payload_hash"]
            bank[desc_hash : desc_hash + 8] = _fnv(layout, payload).to_bytes(8, "little")
        if arena:
            arena_hash = arena_start + af["image_hash"]
            value = _fnv(layout, bytes(bank[arena_start:arena_hash]))
            bank[arena_hash : arena_hash + 8] = value.to_bytes(8, "little")
        if metadata:
            header = {
                name: int.from_bytes(
                    bank[offset : offset + m.header_integer_bytes], "little"
                )
                for name, offset in m.header_offsets.items()
            }
            payload = bytes(bank[m.header_bytes:])
            value = metadata_payload_hash(m, header, payload)
            offset = m.header_offsets["payload_hash"]
            bank[offset : offset + m.header_integer_bytes] = value.to_bytes(
                m.header_integer_bytes, "little"
            )

    def _first_scope_offset(self) -> int:
        return (
            self.metadata.header_bytes
            + self.layout.arena_fields["payload"]
            + self.layout.observe_fields["scopes"]
        )

    def _first_entry_offset(self) -> int:
        return self._entry_offset(0)

    def _entry_offset(self, index: int) -> int:
        return (
            self._first_scope_offset()
            + self.layout.scope_fields["records"]
            + index * self.layout.entry_bytes
        )

    def _first_record_offset(self) -> int:
        return self._record_offset(0)

    def _record_offset(self, index: int) -> int:
        return self._entry_offset(index) + self.layout.entry_fields["record"]

    def _refresh_first_record_hash(self, bank: bytearray) -> int:
        return self._refresh_record_hash(bank, 0)

    def _refresh_record_hash(self, bank: bytearray, index: int) -> int:
        layout = self.layout
        record_offset = self._record_offset(index)
        record = bytes(bank[record_offset : record_offset + layout.record_bytes])
        record_hash = kernel_record_hash(record, layout)
        field = record_offset + layout.record_fields["record_hash"]
        bank[field : field + 8] = record_hash.to_bytes(8, "little")
        ledger = self._first_scope_offset() + layout.scope_fields["ledger_hash"]
        bank[ledger : ledger + 8] = record_hash.to_bytes(8, "little")
        return record_hash

    def _append_direct_record(self, bank: bytearray) -> int:
        layout = self.layout
        first_entry = self._entry_offset(0)
        second_entry = self._entry_offset(1)
        bank[second_entry : second_entry + layout.entry_bytes] = bank[
            first_entry : first_entry + layout.entry_bytes
        ]
        first_record = self._record_offset(0)
        second_record = self._record_offset(1)
        first_hash = int.from_bytes(
            bank[
                first_record + layout.record_fields["record_hash"] :
                first_record + layout.record_fields["record_hash"] + 8
            ],
            "little",
        )
        for name, value in (("sequence", 101), ("tick", 2), ("prev_hash", first_hash)):
            offset = second_record + layout.record_fields[name]
            bank[offset : offset + 8] = value.to_bytes(8, "little")
        offset = second_entry + layout.entry_fields["link_flags"]
        bank[offset] = (
            layout.link_flags["latest_tail"]
            | layout.link_flags["prev_retained"]
        )
        offset = second_entry + layout.entry_fields["receipt_id"]
        bank[offset : offset + 8] = (9002).to_bytes(8, "little")
        scope = self._first_scope_offset()
        for name in ("record_count", "total_records"):
            size = 4 if name == "record_count" else 8
            offset = scope + layout.scope_fields[name]
            bank[offset : offset + size] = (2).to_bytes(size, "little")
        return self._refresh_record_hash(bank, 1)

    def _replace_marker_fields(self, **values: int) -> None:
        replacements = {name: str(value) for name, value in values.items()}
        parts = []
        for part in self.marker.split():
            name = part.split("=", 1)[0]
            parts.append(
                f"{name}={replacements[name]}" if name in replacements else part
            )
        self.marker = " ".join(parts)

    def _rehash_record_chain(self, bank: bytearray) -> int:
        layout = self.layout
        scope = self._first_scope_offset()
        count_offset = scope + layout.scope_fields["record_count"]
        record_count = int.from_bytes(bank[count_offset : count_offset + 4], "little")
        previous_hash = 0
        for index in range(record_count):
            record = self._record_offset(index)
            if index != 0:
                offset = record + layout.record_fields["prev_hash"]
                bank[offset : offset + 8] = previous_hash.to_bytes(8, "little")
            raw = bytes(bank[record : record + layout.record_bytes])
            previous_hash = kernel_record_hash(raw, layout)
            offset = record + layout.record_fields["record_hash"]
            bank[offset : offset + 8] = previous_hash.to_bytes(8, "little")
        ledger = scope + layout.scope_fields["ledger_hash"]
        bank[ledger : ledger + 8] = previous_hash.to_bytes(8, "little")
        return previous_hash

    def _install_acceptance_scope(self) -> None:
        selected: dict[str, int] = {}

        def mutate(bank: bytearray) -> None:
            layout = self.layout
            template = bytes(
                bank[
                    self._entry_offset(0) :
                    self._entry_offset(0) + layout.entry_bytes
                ]
            )
            classes = (
                layout.identity_classes["causal"],
                layout.identity_classes["authority"],
                layout.identity_classes["telemetry"],
                layout.identity_classes["telemetry"],
            )
            kinds = (2, 3, 4, 5)
            previous_hash = 0
            for index in range(layout.records_per_scope):
                entry = self._entry_offset(index)
                bank[entry : entry + layout.entry_bytes] = template
                record = self._record_offset(index)

                def put_record(name: str, value: int, size: int = 8) -> None:
                    offset = record + layout.record_fields[name]
                    bank[offset : offset + size] = value.to_bytes(
                        size, "little", signed=name in {
                            "kind", "pid", "tid", "source_pid", "target_pid",
                            "agent_id", "role", "loop_state", "tool_id",
                            "event_type", "status",
                        }
                    )

                put_record("sequence", 100 + index)
                put_record("tick", 10 + index)
                put_record("prev_hash", 0xA55A if index == 0 else previous_hash)
                put_record("span_id", 200 + index)
                put_record("actor_control_id", 300 + index)
                put_record("kind", kinds[index % len(kinds)], 4)
                record_raw = bytes(bank[record : record + layout.record_bytes])
                previous_hash = kernel_record_hash(record_raw, layout)
                put_record("record_hash", previous_hash)

                identity_class = classes[index] if index < len(classes) else classes[-1]
                offset = entry + layout.entry_fields["identity_class"]
                bank[offset] = identity_class
                offset = entry + layout.entry_fields["link_flags"]
                bank[offset] = (
                    (layout.link_flags["latest_tail"]
                     if index >= layout.records_per_scope - layout.latest_tail
                     else 0)
                    | (layout.link_flags["prev_retained"] if index != 0 else 0)
                )
                offset = entry + layout.entry_fields["principal"]
                bank[offset : offset + 8] = (300 + index).to_bytes(8, "little")
                offset = entry + layout.entry_fields["receipt_id"]
                bank[offset : offset + 8] = (9001 + index).to_bytes(8, "little")

            scope = self._first_scope_offset()
            offset = scope + layout.scope_fields["record_count"]
            bank[offset : offset + 4] = layout.records_per_scope.to_bytes(4, "little")
            offset = scope + layout.scope_fields["total_records"]
            bank[offset : offset + 8] = (12).to_bytes(8, "little")
            offset = scope + layout.scope_fields["ledger_hash"]
            bank[offset : offset + 8] = previous_hash.to_bytes(8, "little")
            selected.update(
                receipt_sequence=100 + layout.records_per_scope - 1,
                receipt_record_hash=previous_hash,
                receipt_id=9000 + layout.records_per_scope,
            )
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        self._replace_marker_fields(**selected)

    def test_real_uCore_image_round_trip(self) -> None:
        result = self.verify()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["bank_names"], list(self.metadata.bank_names))
        observation = result["arena"]["observation"]
        self.assertEqual(observation["identity"]["receipt_id"], 9001)
        self.assertEqual(
            observation["identity"]["identity_class"],
            self.layout.identity_classes["authority"],
        )
        self.assertEqual(
            observation["identity"]["link_flags"],
            self.layout.link_flags["latest_tail"],
        )

    def test_short_format_fixture_is_not_full_v8_acceptance(self) -> None:
        self.assertEqual(self.verify()["status"], "verified")
        with self.assertRaisesRegex(
            ObservationEvidenceError, "observation v8 acceptance failed"
        ):
            verify_observation_acceptance(self.image, self.marker)

    def test_full_v8_acceptance_reports_decidable_scope_fields(self) -> None:
        self._install_acceptance_scope()
        result = verify_observation_acceptance(self.image, self.marker)
        matched = result["arena"]["observation"]["matched_scope"]
        self.assertEqual(matched["record_count"], 6)
        self.assertEqual(matched["successful_records"], 12)
        self.assertEqual(matched["retained_tail_count"], 4)
        self.assertEqual(matched["retained_anchor_count"], 2)
        self.assertEqual(
            matched["anchor_identity_classes"],
            ["authority", "causal"],
        )
        self.assertEqual(matched["anchor_kinds"], [2, 3])
        self.assertEqual(
            result["acceptance"],
            {
                "profile": "observation-v8-tail-diversity",
                "matched_scope": 3,
                "record_count": 6,
                "successful_records": 12,
                "tail_count": 4,
                "anchor_count": 2,
                "anchor_has_causal": True,
                "anchor_has_authority": True,
                "anchor_kind_count": 2,
                "status": "verified",
            },
        )

    def test_full_v8_acceptance_cli_emits_json(self) -> None:
        self._install_acceptance_scope()
        guest_log = self.root / "boot1.log"
        guest_log.write_text(self.marker + "\n", encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("agent_observe_disk_evidence.py")),
                "--image",
                str(self.image),
                "--guest-log",
                str(guest_log),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        prefix = "observation_disk_evidence: "
        self.assertTrue(completed.stdout.startswith(prefix), completed.stdout)
        payload = json.loads(completed.stdout[len(prefix) :])
        self.assertEqual(payload["acceptance"]["status"], "verified")
        self.assertEqual(payload["acceptance"]["anchor_count"], 2)

    def test_full_v8_acceptance_rejects_missing_anchor_identity_class(self) -> None:
        for name, index, replacement in (
            ("causal", 0, "telemetry"),
            ("authority", 1, "telemetry"),
        ):
            with self.subTest(name=name):
                raw, self.marker = build_fixture()
                self.image.write_bytes(raw)
                self._install_acceptance_scope()

                def mutate(bank: bytearray) -> None:
                    offset = (
                        self._entry_offset(index)
                        + self.layout.entry_fields["identity_class"]
                    )
                    bank[offset] = self.layout.identity_classes[replacement]
                    self._refresh_outer(bank)

                self._mutate_banks(mutate)
                with self.assertRaisesRegex(
                    ObservationEvidenceError, f"anchor lacks {name} identity"
                ):
                    verify_observation_acceptance(self.image, self.marker)

    def test_full_v8_acceptance_rejects_single_anchor_kind(self) -> None:
        self._install_acceptance_scope()
        selected: dict[str, int] = {}

        def mutate(bank: bytearray) -> None:
            for index in range(self.layout.diversity_anchors):
                offset = (
                    self._record_offset(index)
                    + self.layout.record_fields["kind"]
                )
                bank[offset : offset + 4] = (2).to_bytes(4, "little", signed=True)
            selected["receipt_record_hash"] = self._rehash_record_chain(bank)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        self._replace_marker_fields(**selected)
        with self.assertRaisesRegex(
            ObservationEvidenceError, "anchor audit kind is not diverse"
        ):
            verify_observation_acceptance(self.image, self.marker)

    def test_full_v8_acceptance_rejects_no_retention_pressure(self) -> None:
        self._install_acceptance_scope()
        selected: dict[str, int] = {}

        def mutate(bank: bytearray) -> None:
            first = self._first_record_offset()
            offset = first + self.layout.record_fields["prev_hash"]
            bank[offset : offset + 8] = b"\0" * 8
            scope = self._first_scope_offset()
            offset = scope + self.layout.scope_fields["total_records"]
            bank[offset : offset + 8] = (6).to_bytes(8, "little")
            selected["receipt_record_hash"] = self._rehash_record_chain(bank)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        self._replace_marker_fields(**selected)
        with self.assertRaisesRegex(
            ObservationEvidenceError, "successful_records=6"
        ):
            verify_observation_acceptance(self.image, self.marker)

    def test_full_v8_acceptance_rejects_short_retained_set(self) -> None:
        self._install_acceptance_scope()
        selected: dict[str, int] = {}

        def mutate(bank: bytearray) -> None:
            layout = self.layout
            bank[
                self._entry_offset(5) : self._entry_offset(5) + layout.entry_bytes
            ] = b"\0" * layout.entry_bytes
            scope = self._first_scope_offset()
            offset = scope + layout.scope_fields["record_count"]
            bank[offset : offset + 4] = (5).to_bytes(4, "little")
            offset = self._entry_offset(1) + layout.entry_fields["link_flags"]
            bank[offset] |= layout.link_flags["latest_tail"]
            record = self._record_offset(4)
            hash_offset = record + layout.record_fields["record_hash"]
            record_hash = int.from_bytes(
                bank[hash_offset : hash_offset + 8], "little"
            )
            offset = scope + layout.scope_fields["ledger_hash"]
            bank[offset : offset + 8] = record_hash.to_bytes(8, "little")
            selected.update(
                receipt_sequence=104,
                receipt_record_hash=record_hash,
                receipt_id=9005,
            )
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        self._replace_marker_fields(**selected)
        self.assertEqual(self.verify()["status"], "verified")
        with self.assertRaisesRegex(
            ObservationEvidenceError, "record_count=5"
        ):
            verify_observation_acceptance(self.image, self.marker)

    def test_arbitrary_nonempty_fixture_is_rejected(self) -> None:
        self.image.write_bytes(b"fixture-observe-image\0")
        with self.assertRaises(ObservationEvidenceError):
            self.verify()

    def test_metadata_bank_hash_and_replica_mutations_are_rejected(self) -> None:
        image = bytearray(self.image.read_bytes())
        _, inodes = self._bank_inodes(image)
        bank = self._read_inode_bytes(image, inodes[0])
        bank[-1] ^= 0x80
        self._write_inode_bytes(image, inodes[0], bank)
        self.image.write_bytes(image)
        with self.assertRaises(ObservationEvidenceError):
            self.verify()

    def test_replica_directory_inode_alias_is_rejected(self) -> None:
        image = bytearray(self.image.read_bytes())
        sb = fs.read_superblock(image)
        root = fs.read_inode(image, sb, fs.ROOTINO)
        target = self.metadata.bank_names[1]
        first = dict((name, inum) for inum, name in fs.root_entries(image, sb))[
            self.metadata.bank_names[0]
        ]
        for blockno in root.addrs[:fs.NDIRECT]:
            base = blockno * fs.BSIZE
            for offset in range(0, fs.BSIZE, 16):
                if fs.dir_name(image[base + offset + 2 : base + offset + 16]) == target:
                    image[base + offset : base + offset + 2] = first.to_bytes(2, "little")
        self.image.write_bytes(image)
        with self.assertRaisesRegex(ObservationEvidenceError, "share physical storage"):
            self.verify()

    def test_distinct_bank_inodes_sharing_data_blocks_are_rejected(self) -> None:
        image = bytearray(self.image.read_bytes())
        sb = fs.read_superblock(image)
        entries = dict((name, inum) for inum, name in fs.root_entries(image, sb))
        offsets = [
            (entries[name] // sb.ipb + sb.inodestart) * fs.BSIZE
            + (entries[name] % sb.ipb) * sb.dinode_size
            for name in self.metadata.bank_names
        ]
        image[offsets[1] + 12 : offsets[1] + 12 + 4 * (fs.NDIRECT + 1)] = \
            image[offsets[0] + 12 : offsets[0] + 12 + 4 * (fs.NDIRECT + 1)]
        self.image.write_bytes(image)
        with self.assertRaisesRegex(ObservationEvidenceError, "share physical storage"):
            self.verify()

    def test_arena_hash_mutation_survives_bank_hash_but_is_rejected(self) -> None:
        def mutate(bank):
            offset = self.metadata.header_bytes + self.layout.arena_fields["image_hash"]
            bank[offset] ^= 1
            self._refresh_outer(bank, observation=False, section=False, arena=False)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "arena header"):
            self.verify()

    def test_section_hash_mutation_survives_outer_hashes_but_is_rejected(self) -> None:
        def mutate(bank):
            offset = (
                self.metadata.header_bytes
                + self.layout.arena_fields["sections"]
                + self.layout.descriptor_fields["payload_hash"]
            )
            bank[offset] ^= 1
            self._refresh_outer(bank, observation=False, section=False)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "section payload hash"):
            self.verify()

    def test_observation_hash_mutation_survives_outer_hashes_but_is_rejected(self) -> None:
        def mutate(bank):
            offset = (
                self.metadata.header_bytes
                + self.layout.arena_fields["payload"]
                + self.layout.observe_fields["image_hash"]
            )
            bank[offset] ^= 1
            self._refresh_outer(bank, observation=False)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "checkpoint header"):
            self.verify()

    def test_observation_header_reserved_word_is_canonical_zero(self) -> None:
        def mutate(bank):
            offset = self.metadata.header_bytes + self.layout.arena_fields["payload"]
            offset += self.layout.observe_fields["reserved"]
            bank[offset : offset + 4] = (1).to_bytes(4, "little")
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "checkpoint header"):
            self.verify()

    def test_record_hash_mutation_survives_every_outer_hash_but_is_rejected(self) -> None:
        def mutate(bank):
            offset = self.metadata.header_bytes + self.layout.arena_fields["payload"]
            offset += self.layout.observe_fields["scopes"]
            offset += self.layout.scope_fields["records"]
            offset += self.layout.entry_fields["record"]
            offset += self.layout.record_fields["text"]
            bank[offset] ^= 1
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "record 0/0"):
            self.verify()

    def test_negative_status_uses_kernel_unsigned_int_promotion(self) -> None:
        expected: dict[str, int] = {}

        def mutate(bank):
            layout = self.layout
            observation = self.metadata.header_bytes + layout.arena_fields["payload"]
            scope = observation + layout.observe_fields["scopes"]
            record = (
                scope
                + layout.scope_fields["records"]
                + layout.entry_fields["record"]
            )
            status = record + layout.record_fields["status"]
            bank[status : status + layout.int_bytes] = (-1).to_bytes(
                layout.int_bytes, "little", signed=True
            )
            record_raw = bytes(bank[record : record + layout.record_bytes])
            record_hash = kernel_record_hash(record_raw, layout)
            expected["record_hash"] = record_hash
            hash_offset = record + layout.record_fields["record_hash"]
            bank[hash_offset : hash_offset + 8] = record_hash.to_bytes(8, "little")
            ledger = scope + layout.scope_fields["ledger_hash"]
            bank[ledger : ledger + 8] = record_hash.to_bytes(8, "little")
            self.assertEqual(
                _record_hash(
                    bytes(bank[record : record + layout.record_bytes]), layout
                ),
                record_hash,
            )
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        marker = " ".join(
            f"receipt_record_hash={expected['record_hash']}"
            if part.startswith("receipt_record_hash=")
            else part
            for part in self.marker.split()
        )
        self.assertEqual(self.verify(marker)["status"], "verified")

    def test_half_correlated_span_key_is_rejected(self) -> None:
        def mutate(bank):
            offset = self.metadata.header_bytes + self.layout.arena_fields["payload"]
            offset += self.layout.observe_fields["scopes"]
            offset += self.layout.scope_fields["records"]
            offset += self.layout.entry_fields["span_owner"]
            bank[offset : offset + 8] = b"\0" * 8
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "record 0/0"):
            self.verify()

    def test_entry_identity_link_and_reserved_mutations_are_rejected(self) -> None:
        cases = (
            ("identity", self.layout.entry_fields["identity_class"], 3),
            ("unknown_link", self.layout.entry_fields["link_flags"], 4),
            ("missing_tail", self.layout.entry_fields["link_flags"], 0),
            ("first_prev", self.layout.entry_fields["link_flags"], 3),
            ("reserved", self.layout.entry_fields["reserved"], 1),
        )
        for name, relative, value in cases:
            with self.subTest(name=name):
                raw, self.marker = build_fixture()
                self.image.write_bytes(raw)

                def mutate(bank, relative=relative, value=value):
                    offset = self._first_entry_offset() + relative
                    bank[offset] = value
                    self._refresh_outer(bank)

                self._mutate_banks(mutate)
                with self.assertRaisesRegex(
                    ObservationEvidenceError, "record 0/0"
                ):
                    self.verify()

    def test_direct_retained_link_is_verified(self) -> None:
        def mutate(bank):
            self._append_direct_record(bank)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        observation = self.verify()["arena"]["observation"]
        self.assertEqual(observation["total_records"], 2)
        self.assertEqual(observation["record_count"], 2)
        self.assertEqual(observation["dropped_records"], 0)

    def test_direct_link_without_prev_retained_flag_is_rejected(self) -> None:
        def mutate(bank):
            layout = self.layout
            self._append_direct_record(bank)
            offset = self._entry_offset(1) + layout.entry_fields["link_flags"]
            bank[offset] = layout.link_flags["latest_tail"]
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "record 0/1"):
            self.verify()

    def test_retained_gap_is_accepted_when_hashed_record_was_omitted(self) -> None:
        def mutate(bank):
            layout = self.layout
            self._append_direct_record(bank)
            record = self._record_offset(1)
            offset = record + layout.record_fields["prev_hash"]
            bank[offset : offset + 8] = (123).to_bytes(8, "little")
            offset = self._entry_offset(1) + layout.entry_fields["link_flags"]
            bank[offset] = layout.link_flags["latest_tail"]
            scope = self._first_scope_offset()
            offset = scope + layout.scope_fields["total_records"]
            bank[offset : offset + 8] = (3).to_bytes(8, "little")
            self._refresh_record_hash(bank, 1)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        observation = self.verify()["arena"]["observation"]
        self.assertEqual(observation["total_records"], 3)
        self.assertEqual(observation["record_count"], 2)
        self.assertEqual(observation["admission_drops"], 0)
        self.assertEqual(observation["dropped_records"], 1)

    def test_causal_and_authority_identity_invariants_are_enforced(self) -> None:
        for name in ("authority_principal", "causal_span"):
            with self.subTest(name=name):
                raw, self.marker = build_fixture()
                self.image.write_bytes(raw)

                def mutate(bank, name=name):
                    layout = self.layout
                    entry = self._first_entry_offset()
                    record = self._first_record_offset()
                    if name == "authority_principal":
                        offset = entry + layout.entry_fields["principal"]
                        bank[offset : offset + 8] = (301).to_bytes(8, "little")
                    else:
                        offset = entry + layout.entry_fields["identity_class"]
                        bank[offset] = layout.identity_classes["causal"]
                        offset = entry + layout.entry_fields["span_owner"]
                        bank[offset : offset + 8] = b"\0" * 8
                        offset = record + layout.record_fields["span_id"]
                        bank[offset : offset + 8] = b"\0" * 8
                        self._refresh_first_record_hash(bank)
                    self._refresh_outer(bank)

                self._mutate_banks(mutate)
                with self.assertRaisesRegex(
                    ObservationEvidenceError, "record 0/0"
                ):
                    self.verify()

    def test_sidecar_control_identity_must_fit_persisted_lease(self) -> None:
        def mutate(bank):
            entry = self._first_entry_offset()
            offset = entry + self.layout.entry_fields["span_owner"]
            bank[offset : offset + 8] = (1000).to_bytes(8, "little")
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(
            ObservationEvidenceError, "allocator lease does not exceed"
        ):
            self.verify()

    def test_audit_sequence_is_globally_unique_across_scopes(self) -> None:
        def mutate(bank):
            layout = self.layout
            observation = self.metadata.header_bytes + layout.arena_fields["payload"]
            of = layout.observe_fields
            sf = layout.scope_fields
            ef = layout.entry_fields
            rf = layout.record_fields
            first_entry = self._first_entry_offset()
            second_scope = observation + of["scopes"] + layout.scope_bytes
            second_entry = second_scope + sf["records"]
            second_record = second_entry + ef["record"]

            def put(offset, size, value):
                bank[offset : offset + size] = value.to_bytes(size, "little")

            put(observation + of["scope_count"], 4, 2)
            put(observation + of["lifecycle_lease_ends"] + 8, 8, 100)
            put(second_scope + sf["used"], 4, layout.scope_flags["used"])
            put(second_scope + sf["scope_id"], 4, 4)
            put(second_scope + sf["lifecycle_id"], 4, 2)
            put(second_scope + sf["record_count"], 4, 1)
            put(second_scope + sf["lifecycle_generation"], 8, 11)
            put(second_scope + sf["total_records"], 8, 1)
            bank[second_entry : second_entry + layout.entry_bytes] = bank[
                first_entry : first_entry + layout.entry_bytes
            ]
            put(second_entry + ef["scope_id"], 4, 4)
            put(second_entry + ef["receipt_id"], 8, 9003)
            put(second_record + rf["workflow_lifecycle_id"], 4, 2)
            put(second_record + rf["workflow_lifecycle_generation"], 8, 11)
            record = bytes(
                bank[second_record : second_record + layout.record_bytes]
            )
            record_hash = kernel_record_hash(record, layout)
            put(second_record + rf["record_hash"], 8, record_hash)
            put(second_scope + sf["ledger_hash"], 8, record_hash)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "record 1/0"):
            self.verify()

    def test_kind_and_agent_id_ranges_are_enforced_after_rehash(self) -> None:
        for name in ("kind", "agent_id"):
            with self.subTest(name=name):
                raw, self.marker = build_fixture()
                self.image.write_bytes(raw)

                def mutate(bank, name=name):
                    layout = self.layout
                    record = self._first_record_offset()
                    field = layout.record_fields[name]
                    value = layout.audit_kind_max + 1 if name == "kind" else -1
                    bank[
                        record + field : record + field + layout.int_bytes
                    ] = value.to_bytes(layout.int_bytes, "little", signed=True)
                    self._refresh_first_record_hash(bank)
                    self._refresh_outer(bank)

                self._mutate_banks(mutate)
                with self.assertRaisesRegex(
                    ObservationEvidenceError, "record 0/0"
                ):
                    self.verify()

    def test_ledger_mutation_survives_every_outer_hash_but_is_rejected(self) -> None:
        def mutate(bank):
            offset = self.metadata.header_bytes + self.layout.arena_fields["payload"]
            offset += self.layout.observe_fields["scopes"]
            offset += self.layout.scope_fields["ledger_hash"]
            bank[offset] ^= 1
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "ledger hash"):
            self.verify()

    def test_drop_only_scope_is_valid_and_counted(self) -> None:
        def mutate(bank):
            layout = self.layout
            observation = (
                self.metadata.header_bytes + layout.arena_fields["payload"]
            )
            fields = layout.observe_fields
            scope = (
                observation
                + fields["scopes"]
                + layout.scope_bytes
            )
            scope_fields = layout.scope_fields

            def put(offset, size, value):
                bank[offset : offset + size] = value.to_bytes(size, "little")

            put(observation + fields["scope_count"], 4, 2)
            put(observation + fields["lifecycle_lease_ends"] + 8, 8, 100)
            put(scope + scope_fields["used"], 4, layout.scope_flags["used"])
            put(scope + scope_fields["scope_id"], 4, 4)
            put(scope + scope_fields["lifecycle_id"], 4, 2)
            put(scope + scope_fields["record_count"], 4, 0)
            put(scope + scope_fields["lifecycle_generation"], 8, 11)
            put(scope + scope_fields["total_records"], 8, 3)
            put(scope + scope_fields["admission_drops"], 8, 3)
            put(scope + scope_fields["ledger_hash"], 8, 0)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        observation = self.verify()["arena"]["observation"]
        self.assertEqual(observation["scope_count"], 2)
        self.assertEqual(observation["total_records"], 4)
        self.assertEqual(observation["record_count"], 1)
        self.assertEqual(observation["admission_drops"], 3)
        self.assertEqual(observation["dropped_records"], 3)

    def test_admission_drops_do_not_imply_retention_gap(self) -> None:
        def mutate(bank):
            scope = self._first_scope_offset()
            fields = self.layout.scope_fields
            offset = scope + fields["total_records"]
            bank[offset : offset + 8] = (2).to_bytes(8, "little")
            offset = scope + fields["admission_drops"]
            bank[offset : offset + 8] = (1).to_bytes(8, "little")
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        observation = self.verify()["arena"]["observation"]
        self.assertEqual(observation["admission_drops"], 1)
        self.assertEqual(observation["dropped_records"], 1)

    def test_admission_drop_overflow_is_rejected(self) -> None:
        def mutate(bank):
            offset = self._first_scope_offset()
            offset += self.layout.scope_fields["admission_drops"]
            bank[offset : offset + 8] = (1).to_bytes(8, "little")
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "scope 0"):
            self.verify()

    def test_retention_omission_requires_an_explicit_chain_gap(self) -> None:
        def mutate(bank):
            offset = self._first_scope_offset()
            offset += self.layout.scope_fields["total_records"]
            bank[offset : offset + 8] = (2).to_bytes(8, "little")
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "retention chain"):
            self.verify()

    def test_unaccounted_chain_gap_is_rejected_after_rehash(self) -> None:
        def mutate(bank):
            offset = self._first_record_offset()
            offset += self.layout.record_fields["prev_hash"]
            bank[offset : offset + 8] = (123).to_bytes(8, "little")
            self._refresh_first_record_hash(bank)
            self._refresh_outer(bank)

        self._mutate_banks(mutate)
        with self.assertRaisesRegex(ObservationEvidenceError, "retention chain"):
            self.verify()

    def test_each_boot_identity_dimension_is_bound_to_disk(self) -> None:
        for field in (
            "scope", "lifecycle_id", "lifecycle_generation", "agent_id",
            "receipt_sequence", "receipt_record_hash", "receipt_id",
        ):
            prefix = f"{field}="
            parts = self.marker.split()
            index = next(index for index, item in enumerate(parts) if item.startswith(prefix))
            value = int(parts[index].split("=", 1)[1])
            parts[index] = f"{field}={value + 1}"
            with self.subTest(field=field):
                with self.assertRaises(ObservationEvidenceError):
                    self.verify(" ".join(parts))

    def test_contract_unknown_duplicate_and_geometry_mutations_fail_closed(self) -> None:
        document = json.loads(DEFAULT_OBSERVE_CONTRACT.read_text(encoding="utf-8"))
        variants = []
        unknown = json.loads(json.dumps(document))
        unknown["observation"]["future"] = 1
        variants.append(json.dumps(unknown))
        geometry = json.loads(json.dumps(document))
        geometry["observation"]["entry"]["bytes"] += 1
        variants.append(json.dumps(geometry))
        variants.append('{"schema":2,"schema":2}')
        for index, payload in enumerate(variants):
            with self.subTest(index=index):
                path = self.root / f"contract-{index}.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ObservationEvidenceError):
                    load_observation_contract(path)


if __name__ == "__main__":
    unittest.main()
