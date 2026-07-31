#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_tools.agent_metadata_disk_format import (
    DEFAULT_CONTRACT,
    BankError,
    ContractError,
    RecoveryInvariantError,
    canonical_genesis_store,
    inspect_genesis_image_data,
    load_contract,
    parse_bank,
    payload_hash,
    validate_bank_set,
)
from host_tools import plain_ucore_fs_extract as ucore_fs
from host_probe_toolchain import (
    host_compiler,
    probe_environment,
    probe_mode,
    required_sanitizer_flags,
)


class MetadataDiskFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = load_contract()

    def make_bank(
        self, status, generation=41, *, physical="metafile", version=None
    ):
        layout = self.layout
        count = 0 if status is None else 1
        raw = bytearray(
            layout.header_bytes
            + layout.durable_arena_bytes
            + count * layout.record_bytes
        )
        header = {
            "magic": layout.disk_magic,
            "version": layout.disk_version if version is None else version,
            "count": count,
            "generation": generation,
            "payload_hash": 0,
        }
        if count:
            start = layout.header_bytes + layout.durable_arena_bytes
            raw[
                start + layout.record_offsets["used"] :
                start + layout.record_offsets["used"] + layout.used_bytes
            ] = (1).to_bytes(layout.used_bytes, "little", signed=True)
            raw[
                start + layout.record_offsets["fid"] :
                start + layout.record_offsets["fid"] + layout.fid_bytes
            ] = (1).to_bytes(layout.fid_bytes, "little", signed=True)
            self.put_c_string(
                raw,
                start + layout.record_offsets["physical_name"],
                layout.physical_name_bytes,
                physical,
            )
            self.put_c_string(
                raw,
                start + layout.record_offsets["status"],
                layout.status_bytes,
                status,
            )
        for field, value in header.items():
            offset = layout.header_offsets[field]
            raw[offset : offset + layout.header_integer_bytes] = value.to_bytes(
                layout.header_integer_bytes, "little"
            )
        header["payload_hash"] = payload_hash(
            layout, header, bytes(raw[layout.header_bytes :])
        )
        offset = layout.header_offsets["payload_hash"]
        raw[offset : offset + layout.header_integer_bytes] = header[
            "payload_hash"
        ].to_bytes(layout.header_integer_bytes, "little")
        return bytes(raw)

    @staticmethod
    def put_c_string(raw, offset, width, value):
        encoded = value.encode("ascii")
        if len(encoded) >= width:
            raise ValueError("test string is too long")
        raw[offset : offset + width] = encoded + bytes(width - len(encoded))

    def reports(self, *banks):
        return [
            parse_bank(raw, self.layout.bank_names[index], self.layout)
            for index, raw in enumerate(banks)
        ]

    def test_versioned_contract_loads(self):
        self.assertEqual(self.layout.disk_version, 7)
        self.assertEqual(self.layout.bank_names, (".agentmeta", ".agentmeta1"))

    def test_unpublished_v6_is_rejected(self):
        with self.assertRaisesRegex(BankError, "unsupported version"):
            parse_bank(
                self.make_bank("baseline", version=6),
                self.layout.bank_names[0],
                self.layout,
            )

    def test_kernel_preserves_only_published_v5_migration(self):
        disk = (ROOT / "os/agent_metadata_disk.h").read_text(encoding="utf-8")
        probe = (ROOT / "os/agent_metadata_probe.c").read_text(encoding="utf-8")
        fmt = (ROOT / "os/agent_metadata_store_format.c").read_text(
            encoding="utf-8"
        )
        header = (ROOT / "os/agent_metadata_store_format.h").read_text(
            encoding="utf-8"
        )

        combined = disk + probe + fmt + header
        self.assertNotIn("VERSION_LIFECYCLE", combined)
        self.assertNotIn("store_v6", combined)
        self.assertNotIn("migrate_v6", combined)
        self.assertIn("version != AGENT_META_STORE_VERSION_V5", probe)
        self.assertIn("agent_meta_format_store_v5_bytes", probe)
        self.assertIn("agent_meta_format_migrate_v5(store)", probe)
        self.assertIn("store->header.version = AGENT_META_STORE_VERSION;", fmt)
        self.assertIn(
            "store->records[i].scope_id != VFS_SCOPE_SYSTEM", fmt
        )

    def test_kernel_size_helpers_share_fail_closed_bounds(self):
        fmt = (ROOT / "os/agent_metadata_store_format.c").read_text(
            encoding="utf-8"
        )

        self.assertEqual(fmt.count("return format_bytes("), 2)
        for token in (
            "bytes == 0 || count > AGENT_FILE_META_MAX",
            "prefix + count * stride",
            "total > sizeof(struct agent_meta_store)",
            "total > MAXFILE * BSIZE",
            "sizeof(struct agent_durable_arena)",
            "sizeof(struct agent_meta_record_v5)",
        ):
            self.assertIn(token, fmt)

    def test_unknown_hash_contract_fails_closed(self):
        document = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
        document["disk"]["hash"]["algorithm"] = "unreviewed-hash"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "format.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "unsupported metadata hash"):
                load_contract(path)

    def test_unknown_contract_field_fails_closed(self):
        document = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
        document["disk"]["future_field"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "format.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "expected keys"):
                load_contract(path)

    def test_duplicate_contract_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "format.json"
            path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "duplicate JSON field"):
                load_contract(path)

    def test_parse_valid_bank_uses_nonfixed_generation(self):
        report = parse_bank(
            self.make_bank("baseline", generation=901),
            self.layout.bank_names[0],
            self.layout,
        )
        self.assertEqual(report["state"], "valid")
        self.assertEqual(report["generation"], 901)
        self.assertEqual(report["metafile_status"], "baseline")

    def test_zero_header_is_uncommitted(self):
        raw = bytes(self.layout.header_bytes + self.layout.durable_arena_bytes)
        report = parse_bank(raw, self.layout.bank_names[0], self.layout)
        self.assertEqual(report["state"], "uncommitted")

    def test_corrupt_payload_hash_is_rejected(self):
        raw = bytearray(self.make_bank("baseline"))
        raw[-1] ^= 0x80
        with self.assertRaisesRegex(BankError, "payload hash mismatch"):
            parse_bank(bytes(raw), self.layout.bank_names[0], self.layout)

    def test_unterminated_record_string_is_rejected(self):
        raw = bytearray(self.make_bank("baseline"))
        start = (
            self.layout.header_bytes
            + self.layout.durable_arena_bytes
            + self.layout.record_offsets["physical_name"]
        )
        raw[start : start + self.layout.physical_name_bytes] = b"x" * self.layout.physical_name_bytes
        header = {
            field: int.from_bytes(
                raw[
                    offset : offset + self.layout.header_integer_bytes
                ],
                "little",
            )
            for field, offset in self.layout.header_offsets.items()
        }
        header["payload_hash"] = payload_hash(
            self.layout, header, bytes(raw[self.layout.header_bytes :])
        )
        offset = self.layout.header_offsets["payload_hash"]
        raw[offset : offset + self.layout.header_integer_bytes] = header[
            "payload_hash"
        ].to_bytes(self.layout.header_integer_bytes, "little")
        with self.assertRaisesRegex(BankError, "unterminated C string"):
            parse_bank(bytes(raw), self.layout.bank_names[0], self.layout)

    def test_same_generation_fork_is_rejected(self):
        banks = self.reports(
            self.make_bank("baseline", generation=77),
            self.make_bank("updated", generation=77),
        )
        with self.assertRaisesRegex(RecoveryInvariantError, "same-generation"):
            validate_bank_set(
                banks,
                "interrupted-update",
                interrupted_leg="primary",
                phase=6,
            )

    def matrix_reports(self, leg, phase, *, phase6_published=False):
        baseline = self.make_bank("baseline", generation=80)
        updated = self.make_bank("updated", generation=81)
        uncommitted = bytes(
            self.layout.header_bytes + self.layout.durable_arena_bytes
        )
        if leg == "primary":
            if phase == 1:
                raw = (baseline, baseline)
            elif phase <= 5 or (phase == 6 and not phase6_published):
                raw = (baseline, uncommitted)
            else:
                raw = (baseline, updated)
        elif phase == 1:
            raw = (baseline, updated)
        elif phase <= 5 or (phase == 6 and not phase6_published):
            raw = (updated, uncommitted)
        else:
            raw = (updated, updated)
        return self.reports(*raw)

    def mutated_matrix_reports(self, leg, phase):
        baseline = self.make_bank("baseline", generation=80)
        updated = self.make_bank("updated", generation=81)
        uncommitted = bytes(
            self.layout.header_bytes + self.layout.durable_arena_bytes
        )
        if leg == "primary":
            if phase == 1 or phase <= 5:
                raw = (baseline, updated)
            elif phase == 6:
                raw = (baseline, baseline)
            else:
                raw = (baseline, uncommitted)
        elif phase == 1:
            raw = (updated, updated)
        elif phase <= 5:
            raw = (baseline, updated)
        elif phase == 6:
            raw = (baseline, updated)
        else:
            raw = (updated, uncommitted)
        return self.reports(*raw)

    def test_exact_interrupted_phase_matrix_accepts_every_cell(self):
        for leg in ("primary", "mirror"):
            for phase in range(1, 9):
                with self.subTest(leg=leg, phase=phase):
                    banks = self.matrix_reports(leg, phase)
                    validate_bank_set(
                        banks,
                        "interrupted-update",
                        interrupted_leg=leg,
                        phase=phase,
                    )
                    validate_bank_set(
                        list(reversed(banks)),
                        "interrupted-update",
                        interrupted_leg=leg,
                        phase=phase,
                    )

    def test_phase6_accepts_only_uncommitted_or_published_target(self):
        for leg in ("primary", "mirror"):
            with self.subTest(leg=leg, state="uncommitted"):
                validate_bank_set(
                    self.matrix_reports(leg, 6),
                    "interrupted-update",
                    interrupted_leg=leg,
                    phase=6,
                )
            with self.subTest(leg=leg, state="published"):
                validate_bank_set(
                    self.matrix_reports(leg, 6, phase6_published=True),
                    "interrupted-update",
                    interrupted_leg=leg,
                    phase=6,
                )

    def test_every_interrupted_phase_rejects_role_state_mutation(self):
        for leg in ("primary", "mirror"):
            for phase in range(1, 9):
                with self.subTest(leg=leg, phase=phase):
                    with self.assertRaisesRegex(
                        RecoveryInvariantError, f"{leg} phase {phase}"
                    ):
                        validate_bank_set(
                            self.mutated_matrix_reports(leg, phase),
                            "interrupted-update",
                            interrupted_leg=leg,
                            phase=phase,
                        )

    def test_every_interrupted_phase_rejects_absent_or_corrupt_target(self):
        for leg in ("primary", "mirror"):
            for phase in range(1, 9):
                for state in ("absent", "corrupt"):
                    banks = self.matrix_reports(leg, phase)
                    index = next(
                        (
                            index
                            for index, bank in enumerate(banks)
                            if bank["state"] == "uncommitted"
                        ),
                        0,
                    )
                    banks[index] = {"name": banks[index]["name"], "state": state}
                    with self.subTest(leg=leg, phase=phase, state=state):
                        with self.assertRaises(RecoveryInvariantError):
                            validate_bank_set(
                                banks,
                                "interrupted-update",
                                interrupted_leg=leg,
                                phase=phase,
                            )

    def test_primary_published_generation_must_follow_baseline(self):
        banks = self.reports(
            self.make_bank("baseline", generation=901),
            self.make_bank("updated", generation=903),
        )
        for phase in (6, 7, 8):
            with self.subTest(phase=phase):
                with self.assertRaisesRegex(RecoveryInvariantError, "adjacent"):
                    validate_bank_set(
                        banks,
                        "interrupted-update",
                        interrupted_leg="primary",
                        phase=phase,
                    )

    def test_mirror_published_target_must_equal_verified_primary(self):
        updated_902 = self.make_bank("updated", generation=902)
        updated_903 = self.make_bank("updated", generation=903)
        for phase in (6, 7, 8):
            with self.subTest(phase=phase):
                with self.assertRaisesRegex(RecoveryInvariantError, "identical"):
                    validate_bank_set(
                        self.reports(updated_902, updated_903),
                        "interrupted-update",
                        interrupted_leg="mirror",
                        phase=phase,
                    )

    def test_same_generation_requires_both_hash_and_image_identity(self):
        raw = self.make_bank("updated", generation=902)
        for field, mutation in (
            ("payload_hash", "0000000000000000"),
            ("store_image", raw + b"x"),
        ):
            banks = self.reports(raw, raw)
            banks[1][field] = mutation
            with self.subTest(field=field):
                with self.assertRaisesRegex(RecoveryInvariantError, "same-generation"):
                    validate_bank_set(
                        banks,
                        "interrupted-update",
                        interrupted_leg="mirror",
                        phase=8,
                    )

    def test_recovered_banks_must_be_identical_and_stale_free(self):
        raw = self.make_bank(None, generation=1200)
        banks = self.reports(raw, raw)
        validate_bank_set(banks, "recovered")
        stale = copy.deepcopy(banks)
        stale[1]["metafile_status"] = "baseline"
        with self.assertRaisesRegex(RecoveryInvariantError, "stale workflow"):
            validate_bank_set(stale, "recovered")


class MetadataGenesisImageTests(unittest.TestCase):
    @staticmethod
    def _wsl_path(path: Path) -> str:
        resolved = path.resolve()
        drive = resolved.drive.rstrip(":").lower()
        tail = resolved.as_posix().split(":", 1)[1].lstrip("/")
        return f"/mnt/{drive}/{tail}"

    @classmethod
    def setUpClass(cls):
        cls.layout = load_contract()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls._temporary.name)
        cls.mkfs = cls.directory / "mkfs-agent-metadata-genesis"
        cls.image_path = cls.directory / "fs.img"
        compiler = host_compiler()
        sanitizer_flags = required_sanitizer_flags(compiler, cls.directory)
        environment = probe_environment(sanitizer_flags)
        if os.name == "nt":
            root = cls._wsl_path(ROOT)
            mkfs = cls._wsl_path(cls.mkfs)
            image = cls._wsl_path(cls.image_path)
            command = (
                f"cd {shlex.quote(root)} && "
                f"{shlex.join(compiler + sanitizer_flags)} "
                "nfs/fs.c nfs/host_image_snapshot.c "
                f"-o {shlex.quote(mkfs)} && "
                f"{shlex.join([mkfs, image])}"
            )
            result = subprocess.run(
                ["wsl", "bash", "-lc", command],
                capture_output=True,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        else:
            result = subprocess.run(
                compiler
                + [
                    *sanitizer_flags,
                    "nfs/fs.c",
                    "nfs/host_image_snapshot.c",
                    "-o",
                    str(cls.mkfs),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                result = subprocess.run(
                    [str(cls.mkfs), str(cls.image_path)],
                    cwd=ROOT,
                    capture_output=True,
                    env=environment,
                    text=True,
                )
        if result.returncode != 0:
            cls._temporary.cleanup()
            raise RuntimeError(
                "cannot build canonical metadata genesis fixture:\n"
                + result.stdout
                + result.stderr
            )
        cls.image = cls.image_path.read_bytes()
        cls.superblock = ucore_fs.read_superblock(cls.image)
        cls.sanitizer_mode = probe_mode(sanitizer_flags)
        print(f"[agent-metadata-genesis] fixture passed; mode={cls.sanitizer_mode}")

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    @classmethod
    def _inode_offset(cls, inum: int) -> int:
        sb = cls.superblock
        return (
            (inum // sb.ipb + sb.inodestart) * ucore_fs.BSIZE
            + (inum % sb.ipb) * sb.dinode_size
        )

    @classmethod
    def _bank_inodes(cls, image: bytes) -> dict[str, int]:
        return {
            name: inum
            for inum, name in ucore_fs.root_entries(image, cls.superblock)
            if name in cls.layout.bank_names
        }

    @classmethod
    def _logical_offset(
        cls, image: bytes, inode: ucore_fs.Dinode, logical: int
    ) -> int:
        fbn, within = divmod(logical, ucore_fs.BSIZE)
        if fbn < ucore_fs.NDIRECT:
            blockno = inode.addrs[fbn]
        else:
            indirect = ucore_fs.block(image, inode.addrs[ucore_fs.NDIRECT])
            blockno = ucore_fs.u32(indirect, (fbn - ucore_fs.NDIRECT) * 4)
        if blockno == 0:
            raise AssertionError("test fixture unexpectedly contains a hole")
        return blockno * ucore_fs.BSIZE + within

    @classmethod
    def _root_dirent_offsets(cls, image: bytes) -> dict[str, int]:
        inode = ucore_fs.read_inode(image, cls.superblock, ucore_fs.ROOTINO)
        result: dict[str, int] = {}
        for logical in range(0, inode.size, 16):
            physical = cls._logical_offset(image, inode, logical)
            name = image[physical + 2 : physical + 16].split(b"\0", 1)[0]
            decoded = name.decode("ascii")
            if decoded in cls.layout.bank_names:
                result[decoded] = physical
        return result

    def assert_genesis_rejected(self, image: bytearray, pattern: str):
        with self.assertRaisesRegex(RecoveryInvariantError, pattern):
            inspect_genesis_image_data(bytes(image), self.layout)

    def test_mkfs_installs_independently_reconstructed_genesis(self):
        canonical = canonical_genesis_store(self.layout)
        reports = inspect_genesis_image_data(self.image, self.layout)
        self.assertEqual(len(reports), 2)
        self.assertEqual(len(canonical), 8232)
        self.assertEqual(
            {report["payload_hash"] for report in reports},
            {"4c04cdefefa6f030"},
        )
        self.assertTrue(
            all(report["store_image"] == canonical for report in reports)
        )

    def test_double_absent_is_not_a_genesis_authority(self):
        image = bytearray(self.image)
        offsets = self._root_dirent_offsets(image)
        self.assertEqual(set(offsets), set(self.layout.bank_names))
        for offset in offsets.values():
            image[offset : offset + 2] = b"\0\0"
        self.assert_genesis_rejected(image, "no valid metadata bank")

    def test_double_uncommitted_is_not_a_genesis_authority(self):
        image = bytearray(self.image)
        for inum in self._bank_inodes(image).values():
            inode = ucore_fs.read_inode(image, self.superblock, inum)
            offset = self._logical_offset(image, inode, 0)
            image[offset : offset + self.layout.header_bytes] = bytes(
                self.layout.header_bytes
            )
        self.assert_genesis_rejected(image, "non-canonical genesis bytes")

    def test_double_corrupt_is_not_a_genesis_authority(self):
        image = bytearray(self.image)
        for inum in self._bank_inodes(image).values():
            inode = ucore_fs.read_inode(image, self.superblock, inum)
            image[
                self._logical_offset(image, inode, self.layout.header_bytes)
            ] ^= 1
        self.assert_genesis_rejected(image, "non-canonical genesis bytes")

    def test_absent_and_uncommitted_are_not_a_genesis_authority(self):
        image = bytearray(self.image)
        offsets = self._root_dirent_offsets(image)
        absent_offset = offsets[self.layout.bank_names[0]]
        image[absent_offset : absent_offset + 2] = b"\0\0"
        inum = self._bank_inodes(image)[self.layout.bank_names[1]]
        inode = ucore_fs.read_inode(image, self.superblock, inum)
        offset = self._logical_offset(image, inode, 0)
        image[offset : offset + self.layout.header_bytes] = bytes(
            self.layout.header_bytes
        )
        self.assert_genesis_rejected(image, "non-canonical genesis bytes")

    def test_nonzero_preallocated_tail_is_rejected(self):
        image = bytearray(self.image)
        inum = self._bank_inodes(image)[self.layout.bank_names[0]]
        inode = ucore_fs.read_inode(image, self.superblock, inum)
        image[self._logical_offset(image, inode, inode.size - 1)] ^= 1
        self.assert_genesis_rejected(image, "tail is not zero")

    def test_non_system_qmap_owner_is_rejected(self):
        image = bytearray(self.image)
        inum = self._bank_inodes(image)[self.layout.bank_names[0]]
        inode = ucore_fs.read_inode(image, self.superblock, inum)
        blockno = inode.addrs[0]
        assert self.superblock.qmapstart is not None
        owner_offset = (
            self.superblock.qmapstart + blockno // ucore_fs.QPB
        ) * ucore_fs.BSIZE + (blockno % ucore_fs.QPB) * 4
        image[owner_offset : owner_offset + 4] = (
            ucore_fs.FS_OWNER_PUBLIC.to_bytes(4, "little")
        )
        self.assert_genesis_rejected(image, "not SYSTEM-owned")

    def test_free_bitmap_block_is_rejected(self):
        image = bytearray(self.image)
        inum = self._bank_inodes(image)[self.layout.bank_names[0]]
        inode = ucore_fs.read_inode(image, self.superblock, inum)
        blockno = inode.addrs[0]
        bits_per_block = ucore_fs.BSIZE * 8
        bitmap_offset = (
            self.superblock.bmapstart + blockno // bits_per_block
        ) * ucore_fs.BSIZE + (blockno % bits_per_block) // 8
        image[bitmap_offset] &= ~(1 << (blockno % 8))
        self.assert_genesis_rejected(image, "free in bitmap")

    def test_cross_bank_block_alias_is_rejected(self):
        image = bytearray(self.image)
        inodes = self._bank_inodes(image)
        left = ucore_fs.read_inode(
            image, self.superblock, inodes[self.layout.bank_names[0]]
        )
        right_offset = self._inode_offset(inodes[self.layout.bank_names[1]])
        image[right_offset + 12 : right_offset + 16] = left.addrs[0].to_bytes(
            4, "little"
        )
        self.assert_genesis_rejected(image, "mappings alias")

    def test_invalid_inode_label_checksum_is_rejected(self):
        image = bytearray(self.image)
        inum = self._bank_inodes(image)[self.layout.bank_names[0]]
        checksum_offset = self._inode_offset(inum) + 124
        image[checksum_offset] ^= 1
        self.assert_genesis_rejected(image, "invalid genesis inode label")


if __name__ == "__main__":
    unittest.main()
