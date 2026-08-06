#!/usr/bin/env python3
"""Inspect and exactly diff AgentOS filesystem allocator state.

This is deliberately a host-only tool.  It reads the durable bitmap, owner
map, inode table, and root reachability without mounting or repairing the
image, which makes it suitable for inspecting a power-cut checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path


HOST_TOOLS = Path(__file__).resolve().parents[1] / "host_tools"
if str(HOST_TOOLS) not in sys.path:
    sys.path.append(str(HOST_TOOLS))

from agent_observe_disk_contract import (  # noqa: E402
    ObservationEvidenceError,
    load_observation_contract,
)
from agent_observe_disk_evidence import validate_observation_payload  # noqa: E402


BLOCK_SIZE = 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
GENERATOR = {"name": "fs-allocator-image.py", "version": "2"}
SNAPSHOT_FORMAT = "agentos-fs-allocator-v2"
DIFF_FORMAT = "agentos-fs-allocator-diff-v2"
VERIFIED_FORMAT = "agentos-fs-allocator-case-v2"
CANONICAL_FORMAT = "agentos-fs-allocator-canonical-v2"
STORAGE_POLICY_VERSION = 2
STORAGE_SCOPE_SLOTS = 4
PUBLIC_PRINCIPAL_ID = 2
SUPERBLOCK_FORMAT = "<16I"
SUPERBLOCK_SIZE = struct.calcsize(SUPERBLOCK_FORMAT)
AGENT_FS_MAGIC = 0x10203047
DINODE_SIZE = 128
DINODES_PER_BLOCK = BLOCK_SIZE // DINODE_SIZE
DIRECT_BLOCKS = 12
INDIRECT_ENTRIES = BLOCK_SIZE // 4
ROOT_INODE = 1
OWNER_NONE = 0
OWNER_STATE_SHIFT = 30
OWNER_PAYLOAD_MASK = (1 << OWNER_STATE_SHIFT) - 1
OWNER_STATE_FREE_LIVE_LOW = 0
OWNER_STATE_ALLOCATING = 1
OWNER_STATE_LIVE_WORKFLOW = 2
OWNER_STATE_FREEING = 3
VFS_LABEL_MAGIC = 0x56465331
VFS_LABEL_VERSION = 3
VFS_LABEL_F_PUBLIC = 0x1
VFS_LABEL_F_PROTECTED = 0x2
VFS_LABEL_F_KERNEL_PRIVATE = 0x4
VFS_LABEL_F_ROOT = 0x8
VFS_LABEL_F_FREE = 0x10
VFS_POLICY_PUBLIC = 1
VFS_POLICY_WORKFLOW = 2
VFS_POLICY_KERNEL_PRIVATE = 3
VFS_POLICY_ROOT = 4
VFS_POLICY_FREE = 5
VFS_POLICY_GENERATION = 2
VFS_EXEC_PROFILE_NONE = 0
FS_OWNER_VERSION = 3
FS_OWNER_SYSTEM = 1
AGENT_META_STORE_MAGIC = 0x41474D4554413036
AGENT_META_STORE_VERSION = 8
AGENT_META_STORE_NAMES = (".agentmeta", ".agentmeta1")
AGENT_META_STORE_HEADER_BYTES = 40
AGENT_META_STORE_DURABLE_BYTES = 8192
AGENT_META_STORE_RECORD_BYTES = 416
AGENT_META_STORE_MAX_RECORDS = 512
AGENT_META_JOURNAL_OFFSET = 222208
AGENT_META_JOURNAL_BYTES = 32 * BLOCK_SIZE
AGENT_META_STORE_MAX_BYTES = AGENT_META_JOURNAL_OFFSET + AGENT_META_JOURNAL_BYTES
AGENT_META_STORE_DATA_BLOCKS = (
    AGENT_META_STORE_MAX_BYTES + BLOCK_SIZE - 1
) // BLOCK_SIZE
AGENT_META_HASH_INITIAL = 1469598103934665603
AGENT_META_HASH_PRIME = 1099511628211
AGENT_DURABLE_ARENA_MAGIC = 0x4147445552413031
AGENT_DURABLE_ARENA_VERSION = 1
AGENT_DURABLE_SECTION_OBSERVE = 1
AGENT_DURABLE_SECTION_BYTES = 32
AGENT_DURABLE_SECTION_MAX = 2
AGENT_DURABLE_PAYLOAD_OFFSET = 96
AGENT_DURABLE_PAYLOAD_BYTES = 8088
AGENT_OBSERVE_CHECKPOINT_VERSION = 8
AGENT_OBSERVE_CHECKPOINT_BYTES = 7592
AGENT_FILE_SYSTEM_LIMIT = 64
AGENT_FILE_SCOPE_LIMIT = 112
AGENT_FILE_ORDINARY_LIMIT = 448
AGENT_FILE_META_F_PERSIST = 2
AGENT_FILE_META_F_AUTOSCAN = 4
VFS_SCOPE_NONE = 0
VFS_SCOPE_SYSTEM = 1
VFS_SCOPE_FIRST_DYNAMIC = 3
FS_OWNER_SCOPE_FLAG = 0x80000000
WORKFLOW_LIFECYCLE_CAP = 8
OWNER_STATE_NAMES = {
    OWNER_STATE_FREE_LIVE_LOW: "LIVE_LOW_OR_FREE",
    OWNER_STATE_ALLOCATING: "ALLOCATING",
    OWNER_STATE_LIVE_WORKFLOW: "LIVE_WORKFLOW",
    OWNER_STATE_FREEING: "FREEING",
}

SUPPORTED_PHASES = {
    "alloc": ("intent", "bitmap", "owner"),
    "free": ("intent", "bitmap", "owner", "refund"),
    "ialloc": ("intent", "owner"),
    "ifree": ("intent", "owner", "refund"),
}
ACTIONS = ("busy", "eio", "crash")

QMAP_CHECKPOINTS: dict[tuple[str, str, str], tuple[str, int]] = {
    ("alloc", "bitmap", "eio"): ("ALLOCATING", 0),
    ("alloc", "owner", "eio"): ("ALLOCATING", 1),
    ("alloc", "intent", "crash"): ("ALLOCATING", 0),
    ("alloc", "bitmap", "crash"): ("ALLOCATING", 1),
    ("free", "bitmap", "eio"): ("FREEING", 1),
    ("free", "owner", "eio"): ("FREEING", 0),
    ("free", "intent", "crash"): ("FREEING", 1),
    ("free", "bitmap", "crash"): ("FREEING", 0),
}
INODE_CHECKPOINTS: dict[tuple[str, str, str], tuple[str, int]] = {
    ("ialloc", "owner", "eio"): ("ALLOCATING", 1),
    ("ialloc", "intent", "crash"): ("ALLOCATING", 1),
    ("ifree", "owner", "eio"): ("FREEING", 1),
    ("ifree", "refund", "eio"): ("FREEING", 0),
    ("ifree", "intent", "crash"): ("FREEING", 1),
    ("ifree", "owner", "crash"): ("FREEING", 0),
}


def _build_case_expectations() -> dict[tuple[str, str, str], dict[str, object]]:
    """Build the closed 36-case oracle consumed by verification and tests."""
    table: dict[tuple[str, str, str], dict[str, object]] = {}
    for operation, phases in SUPPORTED_PHASES.items():
        for phase in phases:
            for action in ACTIONS:
                key = (operation, phase, action)
                committed = operation == "alloc" and action == "busy" and phase in {
                    "bitmap",
                    "owner",
                }
                new_block_source = "none"
                if operation == "alloc" and key in QMAP_CHECKPOINTS:
                    new_block_source = "qmap_transition"
                elif committed:
                    new_block_source = "target_inode"
                elif operation == "alloc" and action == "crash" and phase == "owner":
                    new_block_source = "orphan"
                new_inode_source = "none"
                if operation == "alloc":
                    new_inode_source = "target_name"
                elif operation == "ialloc" and (
                    (action == "crash" and phase in {"intent", "owner"})
                    or (action == "eio" and phase == "owner")
                ):
                    new_inode_source = "orphan"
                table[key] = {
                    "qmap_checkpoint": QMAP_CHECKPOINTS.get(key),
                    "inode_checkpoint": INODE_CHECKPOINTS.get(key),
                    "alloc_committed": committed,
                    "fault_new_block_source": new_block_source,
                    "fault_new_inode_source": new_inode_source,
                    "reboot_block_delta": (
                        1 if committed else -1 if operation == "free" else 0
                    ),
                    "reboot_inode_delta": (
                        1
                        if operation == "alloc"
                        else -1
                        if operation in {"free", "ifree"}
                        else 0
                    ),
                }
    return table


CASE_EXPECTATIONS = _build_case_expectations()


class ImageError(ValueError):
    pass


def storage_policy_checksum(values: tuple[int, ...]) -> int:
    value = 2166136261
    for item in values:
        value ^= item
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def vfs_label_checksum(inum: int, values: tuple[int, ...]) -> int:
    value = 2166136261 ^ inum
    for item in values:
        value ^= item
        value = (value * 16777619) & 0xFFFFFFFF
        value ^= item >> 16
    return value or 1


def decode_owner(raw: int) -> tuple[str, int]:
    state = raw >> OWNER_STATE_SHIFT
    payload = raw & OWNER_PAYLOAD_MASK
    if state == OWNER_STATE_FREE_LIVE_LOW:
        return ("FREE" if payload == 0 else "LIVE_LOW", payload)
    return OWNER_STATE_NAMES[state], payload


def canonical_owner_entry(
    block: int, raw: int, allocated: bool, public_principal: int
) -> tuple[dict[str, object], list[str]]:
    top_state = OWNER_STATE_NAMES[raw >> OWNER_STATE_SHIFT]
    state, payload = decode_owner(raw)
    entry = {
        "block": block,
        "raw": raw,
        "top_state": top_state,
        "state": state,
        "payload": payload,
        "bitmap": int(allocated),
    }
    if state == "ALLOCATING":
        entry["transition_phase"] = "BITMAP_COMMITTED" if allocated else "INTENT"
    elif state == "FREEING":
        entry["transition_phase"] = "INTENT" if allocated else "BITMAP_CLEARED"
    else:
        entry["transition_phase"] = "STABLE"
    violations: list[str] = []
    if state == "FREE" and allocated:
        violations.append(f"block {block}: FREE owner has a set bitmap bit")
    elif state == "LIVE_LOW":
        if payload not in (1, public_principal):
            violations.append(
                f"block {block}: invalid low-principal LIVE payload {payload}"
            )
        if not allocated:
            violations.append(f"block {block}: LIVE_LOW owner has a clear bitmap bit")
    elif state == "LIVE_WORKFLOW":
        if payload <= public_principal:
            violations.append(
                f"block {block}: invalid workflow LIVE payload {payload}"
            )
        if not allocated:
            violations.append(
                f"block {block}: LIVE_WORKFLOW owner has a clear bitmap bit"
            )
    elif state in ("ALLOCATING", "FREEING"):
        # ALLOCATING and FREEING intentionally admit either bitmap value.  The
        # bit identifies which half of the transition survived a power cut.
        if payload == 0:
            violations.append(f"block {block}: {state} has a zero payload")
    return entry, violations


def canonical_inode_owner_entry(
    inum: int, raw: int, allocated: bool, public_principal: int
) -> tuple[dict[str, object], list[str]]:
    block_entry, block_violations = canonical_owner_entry(
        inum, raw, allocated, public_principal
    )
    entry = dict(block_entry)
    entry["inum"] = entry.pop("block")
    if entry["state"] == "ALLOCATING":
        entry["transition_phase"] = "DINODE_COMMITTED" if allocated else "INTENT"
    elif entry["state"] == "FREEING":
        entry["transition_phase"] = "INTENT" if allocated else "DINODE_CLEARED"
    violations = [
        violation.replace(f"block {inum}:", f"inode {inum}:", 1)
        .replace("set bitmap bit", "allocated type")
        .replace("clear bitmap bit", "free type")
        for violation in block_violations
    ]
    return entry, violations


def _read_exact(handle, offset: int, size: int) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise ImageError(
            f"short image read at {offset}: wanted {size}, got {len(data)}"
        )
    return data


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_snapshot_envelope(label: str, snapshot: dict[str, object]) -> None:
    if snapshot.get("format") != SNAPSHOT_FORMAT:
        raise ImageError(f"{label} snapshot format mismatch")
    if snapshot.get("generator") != GENERATOR:
        raise ImageError(f"{label} snapshot generator mismatch")
    image = snapshot.get("image")
    if not isinstance(image, dict) or set(image) != {"bytes", "sha256"}:
        raise ImageError(f"{label} snapshot image provenance is malformed")
    geometry = snapshot.get("geometry")
    if not isinstance(geometry, dict):
        raise ImageError(f"{label} snapshot geometry is malformed")
    size = geometry.get("size")
    byte_count = image.get("bytes")
    digest = image.get("sha256")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count <= 0
        or byte_count > MAX_IMAGE_BYTES
        or byte_count % BLOCK_SIZE
        or byte_count != size * BLOCK_SIZE
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise ImageError(f"{label} snapshot image provenance is invalid")
    state_sha256 = snapshot.get("state_sha256")
    semantic = {
        key: value
        for key, value in snapshot.items()
        if key not in {"image", "generator", "state_sha256"}
    }
    calculated = hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if state_sha256 != calculated:
        raise ImageError(f"{label} snapshot semantic hash mismatch")


def _inode_blocks(handle, inode: dict[str, object], size_blocks: int) -> list[int]:
    addrs = list(inode["addrs"])
    blocks = [block for block in addrs[:DIRECT_BLOCKS] if block]
    indirect = addrs[DIRECT_BLOCKS]
    if indirect:
        if indirect >= size_blocks:
            raise ImageError(f"indirect block {indirect} is outside the image")
        blocks.append(indirect)
        raw = _read_exact(handle, indirect * BLOCK_SIZE, BLOCK_SIZE)
        blocks.extend(block for block in struct.unpack("<256I", raw) if block)
    return blocks


def _file_payload(handle, inode: dict[str, object], size_blocks: int) -> bytes:
    remaining = int(inode["size"])
    if remaining > (DIRECT_BLOCKS + INDIRECT_ENTRIES) * BLOCK_SIZE:
        raise ImageError(f"inode {inode['inum']} exceeds the filesystem limit")
    addrs = list(inode["addrs"])
    data_blocks = addrs[:DIRECT_BLOCKS]
    indirect = addrs[DIRECT_BLOCKS]
    if indirect:
        raw = _read_exact(handle, indirect * BLOCK_SIZE, BLOCK_SIZE)
        data_blocks.extend(struct.unpack("<256I", raw))
    output = bytearray()
    for block in data_blocks:
        if remaining == 0:
            break
        if block == 0 or block >= size_blocks:
            raise ImageError(f"inode {inode['inum']} has an invalid data block")
        chunk = _read_exact(handle, block * BLOCK_SIZE, BLOCK_SIZE)
        take = min(remaining, BLOCK_SIZE)
        output.extend(chunk[:take])
        remaining -= take
    if remaining:
        raise ImageError(f"inode {inode['inum']} has a hole before EOF")
    return bytes(output)


def read_snapshot(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        raw_sb = _read_exact(handle, BLOCK_SIZE, SUPERBLOCK_SIZE)
        fields = struct.unpack(SUPERBLOCK_FORMAT, raw_sb)
        (
            magic,
            size,
            nblocks,
            ninodes,
            inodestart,
            bmapstart,
            qmapstart,
            datastart,
            storage_policy_version,
            storage_scope_slots,
            workflow_block_guarantee,
            workflow_inode_guarantee,
            system_block_reserve,
            system_inode_reserve,
            public_principal,
            policy_checksum,
        ) = fields
        if magic != AGENT_FS_MAGIC:
            raise ImageError(f"unsupported filesystem magic {magic:#x}")
        actual_size = path.stat().st_size
        if actual_size <= 0 or actual_size > MAX_IMAGE_BYTES:
            raise ImageError(f"invalid filesystem image byte length {actual_size}")
        if actual_size < size * BLOCK_SIZE:
            raise ImageError(
                f"image is truncated: superblock says {size * BLOCK_SIZE}, "
                f"file has {actual_size}"
            )
        if actual_size != size * BLOCK_SIZE:
            raise ImageError(
                f"image length mismatch: superblock says {size * BLOCK_SIZE}, "
                f"file has {actual_size}"
            )
        inode_blocks = (ninodes + DINODES_PER_BLOCK - 1) // DINODES_PER_BLOCK
        bitmap_blocks = (size + BLOCK_SIZE * 8 - 1) // (BLOCK_SIZE * 8)
        owner_blocks = (size + 255) // 256
        if not (
            inodestart == 2
            and bmapstart == inodestart + inode_blocks
            and qmapstart == bmapstart + bitmap_blocks
            and datastart == qmapstart + owner_blocks
            and datastart < size
            and nblocks == size - datastart
        ):
            raise ImageError("invalid AgentOS filesystem geometry")
        calculated_policy_checksum = storage_policy_checksum(
            (
                storage_policy_version,
                storage_scope_slots,
                public_principal,
                workflow_block_guarantee,
                workflow_inode_guarantee,
                system_block_reserve,
                system_inode_reserve,
            )
        )
        if policy_checksum != calculated_policy_checksum:
            raise ImageError("storage policy checksum mismatch")
        total_inodes = ninodes - 1
        if (
            storage_policy_version != STORAGE_POLICY_VERSION
            or storage_scope_slots != STORAGE_SCOPE_SLOTS
            or public_principal != PUBLIC_PRINCIPAL_ID
            or workflow_block_guarantee == 0
            or workflow_inode_guarantee == 0
            or system_block_reserve == 0
            or system_inode_reserve == 0
            or workflow_block_guarantee > nblocks // storage_scope_slots
            or workflow_inode_guarantee > total_inodes // storage_scope_slots
            or system_block_reserve
            > nblocks - workflow_block_guarantee * storage_scope_slots
            or system_inode_reserve
            > total_inodes - workflow_inode_guarantee * storage_scope_slots
        ):
            raise ImageError("storage policy contract is not mountable")

        bitmap_raw = _read_exact(
            handle, bmapstart * BLOCK_SIZE, bitmap_blocks * BLOCK_SIZE
        )
        qmap_raw = _read_exact(
            handle, qmapstart * BLOCK_SIZE, owner_blocks * BLOCK_SIZE
        )
        inode_table_raw = _read_exact(
            handle, inodestart * BLOCK_SIZE, inode_blocks * BLOCK_SIZE
        )
        allocated_blocks = [
            block
            for block in range(datastart, size)
            if bitmap_raw[block // 8] & (1 << (block % 8))
        ]
        owners = {
            block: struct.unpack_from("<I", qmap_raw, block * 4)[0]
            for block in range(datastart, size)
        }
        owned_blocks = {
            str(block): owner for block, owner in owners.items() if owner != OWNER_NONE
        }
        qmap_entries: dict[str, dict[str, object]] = {}
        qmap_state_counts = {
            "FREE": 0,
            "LIVE_LOW": 0,
            "ALLOCATING": 0,
            "LIVE_WORKFLOW": 0,
            "FREEING": 0,
        }
        qmap_top_state_counts = {
            "LIVE_LOW_OR_FREE": 0,
            "ALLOCATING": 0,
            "LIVE_WORKFLOW": 0,
            "FREEING": 0,
        }
        canonical_violations: list[str] = []
        allocated_set = set(allocated_blocks)
        for block, raw in owners.items():
            entry, violations = canonical_owner_entry(
                block, raw, block in allocated_set, public_principal
            )
            qmap_state_counts[str(entry["state"])] += 1
            qmap_top_state_counts[str(entry["top_state"])] += 1
            canonical_violations.extend(violations)
            if raw:
                qmap_entries[str(block)] = entry

        inodes: dict[int, dict[str, object]] = {}
        inode_fingerprints: dict[str, dict[str, object]] = {}
        inode_raw_sha256: dict[str, str] = {}
        inode_incarnations: dict[str, int] = {}
        inode_raw_sha256["0"] = hashlib.sha256(
            inode_table_raw[:DINODE_SIZE]
        ).hexdigest()
        inode_incarnations["0"] = struct.unpack_from(
            "<I", inode_table_raw, 112
        )[0]
        free_inode_owners: dict[str, int] = {}
        inode_owner_entries: dict[str, dict[str, object]] = {}
        inode_owner_state_counts = {
            "FREE": 0,
            "LIVE_LOW": 0,
            "ALLOCATING": 0,
            "LIVE_WORKFLOW": 0,
            "FREEING": 0,
        }
        for inum in range(1, ninodes):
            raw = _read_exact(
                handle, inodestart * BLOCK_SIZE + inum * DINODE_SIZE, DINODE_SIZE
            )
            raw_except_size = bytearray(raw)
            raw_except_size[8:12] = b"\0" * 4
            inode_raw_sha256[str(inum)] = hashlib.sha256(raw).hexdigest()
            inode_type = struct.unpack_from("<h", raw, 0)[0]
            inode = {
                "inum": inum,
                "type": inode_type,
                "agent_meta_slot": struct.unpack_from("<h", raw, 2)[0],
                "agent_meta_flags": struct.unpack_from("<h", raw, 4)[0],
                "agent_meta_version": struct.unpack_from("<h", raw, 6)[0],
                "size": struct.unpack_from("<I", raw, 8)[0],
                "addrs": list(struct.unpack_from("<13I", raw, 12)),
                "exec_flags": struct.unpack_from("<I", raw, 64)[0],
                "exec_generation": struct.unpack_from("<I", raw, 68)[0],
                "exec_role_mask": struct.unpack_from("<I", raw, 72)[0],
                "exec_layout_version": struct.unpack_from("<I", raw, 76)[0],
                "exec_rw_offset": struct.unpack_from("<I", raw, 80)[0],
                "vfs_magic": struct.unpack_from("<I", raw, 84)[0],
                "vfs_version": struct.unpack_from("<I", raw, 88)[0],
                "vfs_flags": struct.unpack_from("<I", raw, 92)[0],
                "vfs_scope": struct.unpack_from("<I", raw, 96)[0],
                "vfs_policy": struct.unpack_from("<I", raw, 100)[0],
                "vfs_exec_profile": struct.unpack_from("<I", raw, 104)[0],
                "vfs_policy_generation": struct.unpack_from("<I", raw, 108)[0],
                "vfs_incarnation": struct.unpack_from("<I", raw, 112)[0],
                "owner": struct.unpack_from("<I", raw, 116)[0],
                "owner_version": struct.unpack_from("<I", raw, 120)[0],
                "vfs_checksum": struct.unpack_from("<I", raw, 124)[0],
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_except_size_sha256": hashlib.sha256(raw_except_size).hexdigest(),
            }
            inode_incarnations[str(inum)] = int(inode["vfs_incarnation"])
            inode_owner_entry, violations = canonical_inode_owner_entry(
                inum, int(inode["owner"]), inode_type != 0, public_principal
            )
            inode_owner_state_counts[str(inode_owner_entry["state"])] += 1
            canonical_violations.extend(violations)
            if inode_type != 0 or inode["owner"] != OWNER_NONE:
                expected_label_checksum = vfs_label_checksum(
                    inum,
                    (
                        int(inode["vfs_magic"]),
                        int(inode["vfs_version"]),
                        int(inode["vfs_flags"]),
                        int(inode["vfs_scope"]),
                        int(inode["vfs_policy"]),
                        int(inode["vfs_exec_profile"]),
                        int(inode["vfs_policy_generation"]),
                        int(inode["vfs_incarnation"]),
                        int(inode["owner"]),
                        int(inode["owner_version"]),
                    ),
                )
                if (
                    inode["vfs_magic"] != VFS_LABEL_MAGIC
                    or inode["vfs_version"] != VFS_LABEL_VERSION
                    or inode["vfs_incarnation"] == 0
                    or inode["owner_version"] != FS_OWNER_VERSION
                    or inode["vfs_checksum"] != expected_label_checksum
                ):
                    canonical_violations.append(
                        f"inode {inum}: invalid durable VFS label"
                    )
            if inode["owner"]:
                inode_owner_entries[str(inum)] = inode_owner_entry
            if inode_type:
                inodes[inum] = inode
                inode_fingerprints[str(inum)] = {
                    key: inode[key]
                    for key in (
                        "type",
                        "agent_meta_slot",
                        "agent_meta_flags",
                        "agent_meta_version",
                        "size",
                        "addrs",
                        "exec_flags",
                        "exec_generation",
                        "exec_role_mask",
                        "exec_layout_version",
                        "exec_rw_offset",
                        "vfs_magic",
                        "vfs_version",
                        "vfs_flags",
                        "vfs_scope",
                        "vfs_policy",
                        "vfs_exec_profile",
                        "vfs_policy_generation",
                        "vfs_incarnation",
                        "owner",
                        "owner_version",
                        "vfs_checksum",
                        "raw_sha256",
                        "raw_except_size_sha256",
                    )
                }
            elif inode["owner"] != OWNER_NONE:
                free_inode_owners[str(inum)] = int(inode["owner"])

        if ROOT_INODE not in inodes or inodes[ROOT_INODE]["type"] != 1:
            raise ImageError("missing root directory inode")
        root_data = _file_payload(handle, inodes[ROOT_INODE], size)
        if len(root_data) % 16:
            raise ImageError("root directory size is not dirent-aligned")
        reachable_inodes = {ROOT_INODE}
        root_names: dict[str, int] = {}
        root_dirents: list[dict[str, object]] = []
        for offset in range(0, len(root_data), 16):
            raw_dirent = root_data[offset : offset + 16]
            inum = struct.unpack_from("<H", raw_dirent)[0]
            raw_name = raw_dirent[2:16]
            name = raw_name.split(b"\0", 1)[0].decode("ascii", "backslashreplace")
            root_dirents.append(
                {
                    "offset": offset,
                    "inum": inum,
                    "name": name,
                    "raw_hex": raw_dirent.hex(),
                }
            )
            if inum == 0:
                continue
            if inum not in inodes:
                raise ImageError(f"root entry references free inode {inum}")
            if inum in reachable_inodes:
                raise ImageError(f"duplicate root inode reference {inum}")
            if name in root_names:
                raise ImageError(f"duplicate root name {name!r}")
            root_names[name] = inum
            reachable_inodes.add(inum)

        reachable_blocks: set[int] = set()
        for inum in sorted(reachable_inodes):
            for block in _inode_blocks(handle, inodes[inum], size):
                if block < datastart or block >= size:
                    raise ImageError(f"inode {inum} references non-data block {block}")
                if block in reachable_blocks:
                    raise ImageError(f"duplicate reachable block {block}")
                reachable_blocks.add(block)
        inode_blocks = {
            str(inum): _inode_blocks(handle, inodes[inum], size)
            for inum in sorted(inodes)
        }
        for inum in sorted(reachable_inodes):
            inode_owner = int(inodes[inum]["owner"])
            inode_state, _inode_payload = decode_owner(inode_owner)
            if inode_state not in ("LIVE_LOW", "LIVE_WORKFLOW"):
                canonical_violations.append(
                    f"inode {inum}: reachable inode owner is not stable"
                )
            for block in inode_blocks[str(inum)]:
                block_owner = owners.get(block, OWNER_NONE)
                block_state, _block_payload = decode_owner(block_owner)
                if block not in allocated_set:
                    canonical_violations.append(
                        f"inode {inum}: mapped block {block} is not allocated"
                    )
                if block_state not in ("LIVE_LOW", "LIVE_WORKFLOW"):
                    canonical_violations.append(
                        f"inode {inum}: mapped block {block} owner is not stable"
                    )
                if block_owner != inode_owner:
                    canonical_violations.append(
                        f"inode {inum}: mapped block {block} owner does not match inode"
                    )
        payload_sha256 = {
            str(inum): hashlib.sha256(
                _file_payload(handle, inodes[inum], size)
            ).hexdigest()
            for inum in sorted(reachable_inodes)
        }
        tracked_blocks = allocated_set | set(map(int, owned_blocks)) | reachable_blocks
        block_sha256 = {
            str(block): hashlib.sha256(
                _read_exact(handle, block * BLOCK_SIZE, BLOCK_SIZE)
            ).hexdigest()
            for block in sorted(tracked_blocks)
        }
        nonzero_data_block_sha256: dict[str, str] = {}
        for block in range(datastart, size):
            raw_block = _read_exact(handle, block * BLOCK_SIZE, BLOCK_SIZE)
            if any(raw_block):
                nonzero_data_block_sha256[str(block)] = hashlib.sha256(
                    raw_block
                ).hexdigest()

    allocated = set(allocated_blocks)
    owner_set = set(map(int, owned_blocks))
    snapshot: dict[str, object] = {
        "format": SNAPSHOT_FORMAT,
        "geometry": {
            "size": size,
            "nblocks": nblocks,
            "ninodes": ninodes,
            "inodestart": inodestart,
            "bmapstart": bmapstart,
            "qmapstart": qmapstart,
            "datastart": datastart,
            "storage_policy_version": storage_policy_version,
            "storage_scope_slots": storage_scope_slots,
            "workflow_block_guarantee": workflow_block_guarantee,
            "workflow_inode_guarantee": workflow_inode_guarantee,
            "system_block_reserve": system_block_reserve,
            "system_inode_reserve": system_inode_reserve,
            "public_principal_id": public_principal,
            "storage_policy_checksum": policy_checksum,
            "superblock_sha256": hashlib.sha256(raw_sb).hexdigest(),
        },
        "allocated_blocks": allocated_blocks,
        "owned_blocks": owned_blocks,
        "qmap_entries": qmap_entries,
        "qmap_state_counts": qmap_state_counts,
        "qmap_top_state_counts": qmap_top_state_counts,
        "canonical_violations": canonical_violations,
        "allocated_unowned": sorted(allocated - owner_set),
        "owner_without_bitmap": sorted(owner_set - allocated),
        "inodes": inode_fingerprints,
        "inode_raw_sha256": inode_raw_sha256,
        "inode_incarnations": inode_incarnations,
        "free_inode_owners": free_inode_owners,
        "inode_owner_entries": inode_owner_entries,
        "inode_owner_state_counts": inode_owner_state_counts,
        "root_names": root_names,
        "root_dirents": root_dirents,
        "reachable_inodes": sorted(reachable_inodes),
        "reachable_blocks": sorted(reachable_blocks),
        "inode_blocks": inode_blocks,
        "payload_sha256": payload_sha256,
        "block_sha256": block_sha256,
        "nonzero_data_block_sha256": nonzero_data_block_sha256,
        "allocator_metadata": {
            "inode_table_sha256": hashlib.sha256(inode_table_raw).hexdigest(),
            "bitmap_sha256": hashlib.sha256(bitmap_raw).hexdigest(),
            "qmap_sha256": hashlib.sha256(qmap_raw).hexdigest(),
            "bitmap_hex": bitmap_raw.hex(),
            "qmap_hex": qmap_raw.hex(),
        },
        "orphan_inodes": sorted(set(inodes) - reachable_inodes),
        "orphan_blocks": sorted(allocated - reachable_blocks),
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    snapshot["state_sha256"] = hashlib.sha256(canonical).hexdigest()
    snapshot["image"] = {"bytes": actual_size, "sha256": _file_sha256(path)}
    snapshot["generator"] = dict(GENERATOR)
    return snapshot


def diff_snapshots(before: dict[str, object], after: dict[str, object]) -> dict[str, object]:
    _require_snapshot_envelope("before", before)
    _require_snapshot_envelope("after", after)
    if before["geometry"] != after["geometry"]:
        raise ImageError("cannot diff images with different geometry")
    before_alloc = set(before["allocated_blocks"])
    after_alloc = set(after["allocated_blocks"])
    before_owners = {int(k): v for k, v in dict(before["owned_blocks"]).items()}
    after_owners = {int(k): v for k, v in dict(after["owned_blocks"]).items()}
    owner_changes = []
    for block in sorted(set(before_owners) | set(after_owners)):
        old = before_owners.get(block, OWNER_NONE)
        new = after_owners.get(block, OWNER_NONE)
        if old != new:
            old_state, old_payload = decode_owner(old)
            new_state, new_payload = decode_owner(new)
            owner_changes.append(
                {
                    "block": block,
                    "before": old,
                    "before_state": old_state,
                    "before_payload": old_payload,
                    "after": new,
                    "after_state": new_state,
                    "after_payload": new_payload,
                }
            )
    before_inodes = dict(before["inodes"])
    after_inodes = dict(after["inodes"])
    inode_changes = []
    for inum in sorted(set(before_inodes) | set(after_inodes), key=int):
        old = before_inodes.get(inum)
        new = after_inodes.get(inum)
        if old != new:
            inode_changes.append({"inum": int(inum), "before": old, "after": new})
    before_payloads = dict(before["payload_sha256"])
    after_payloads = dict(after["payload_sha256"])
    payload_changes = [
        {
            "inum": int(inum),
            "before_sha256": before_payloads.get(inum),
            "after_sha256": after_payloads.get(inum),
        }
        for inum in sorted(set(before_payloads) | set(after_payloads), key=int)
        if before_payloads.get(inum) != after_payloads.get(inum)
    ]
    before_blocks = dict(before["block_sha256"])
    after_blocks = dict(after["block_sha256"])
    block_content_changes = [
        {
            "block": int(block),
            "before_sha256": before_blocks.get(block),
            "after_sha256": after_blocks.get(block),
        }
        for block in sorted(set(before_blocks) | set(after_blocks), key=int)
        if before_blocks.get(block) != after_blocks.get(block)
    ]
    return {
        "format": DIFF_FORMAT,
        "generator": dict(GENERATOR),
        "images": {"before": before["image"], "after": after["image"]},
        "bitmap_set": sorted(after_alloc - before_alloc),
        "bitmap_cleared": sorted(before_alloc - after_alloc),
        "owner_changes": owner_changes,
        "inode_changes": inode_changes,
        "payload_changes": payload_changes,
        "block_content_changes": block_content_changes,
        "root_names_before": before["root_names"],
        "root_names_after": after["root_names"],
        "before_sha256": before["state_sha256"],
        "after_sha256": after["state_sha256"],
    }


def _transition_records(
    snapshot: dict[str, object], field: str, identity: str
) -> list[dict[str, object]]:
    return sorted(
        (
            {
                "identity": int(entry[identity]),
                "raw": int(entry["raw"]),
                "state": str(entry["state"]),
                "payload": int(entry["payload"]),
                "bitmap": int(entry["bitmap"]),
            }
            for entry in dict(snapshot[field]).values()
            if entry["state"] in ("ALLOCATING", "FREEING")
        ),
        key=lambda entry: int(entry["identity"]),
    )


def _require_canonical(label: str, snapshot: dict[str, object]) -> None:
    if snapshot["canonical_violations"]:
        raise ImageError(f"{label} image is not canonical")
    if _transition_records(snapshot, "qmap_entries", "block") or _transition_records(
        snapshot, "inode_owner_entries", "inum"
    ):
        raise ImageError(f"{label} image retains allocator transitions")
    for field in (
        "allocated_unowned",
        "owner_without_bitmap",
        "free_inode_owners",
        "orphan_inodes",
        "orphan_blocks",
    ):
        if snapshot[field]:
            raise ImageError(f"{label} image retains {field}")


def _expected_namespace(
    before_names: dict[str, int], operation: str, target_name: str
) -> set[str]:
    names = set(before_names)
    if operation == "alloc":
        names.add(target_name)
    else:
        names.discard(target_name)
    return names


def _first_free_block(snapshot: dict[str, object]) -> int:
    geometry = dict(snapshot["geometry"])
    allocated = set(map(int, snapshot["allocated_blocks"]))
    owned = set(map(int, dict(snapshot["owned_blocks"])))
    for block in range(int(geometry["datastart"]), int(geometry["size"])):
        if block not in allocated and block not in owned:
            return block
    raise ImageError("before image has no free allocator block")


def _first_free_inode(snapshot: dict[str, object]) -> int:
    geometry = dict(snapshot["geometry"])
    allocated = set(map(int, dict(snapshot["inodes"])))
    owned_free = set(map(int, dict(snapshot["free_inode_owners"])))
    for inum in range(1, int(geometry["ninodes"])):
        if inum not in allocated and inum not in owned_free:
            return inum
    raise ImageError("before image has no free allocator inode")


def _require_public_inode(
    label: str,
    snapshot: dict[str, object],
    inum: int,
    public_owner: int,
    expected_incarnation: int | None = None,
) -> None:
    key = str(inum)
    inode = dict(snapshot["inodes"]).get(key)
    if inode is None:
        raise ImageError(f"{label} inode {inum} is absent")
    state, payload = decode_owner(int(inode["owner"]))
    if state != "LIVE_LOW" or payload != public_owner:
        raise ImageError(f"{label} inode {inum} has the wrong owner identity")
    expected_fields = {
        "type": 2,
        "agent_meta_slot": 0,
        "agent_meta_flags": 0,
        "agent_meta_version": 0,
        "exec_flags": 0,
        "exec_generation": 0,
        "exec_role_mask": 0,
        "exec_layout_version": 0,
        "exec_rw_offset": 0,
        "vfs_magic": VFS_LABEL_MAGIC,
        "vfs_version": VFS_LABEL_VERSION,
        "vfs_flags": VFS_LABEL_F_PUBLIC,
        "vfs_scope": 0,
        "vfs_policy": VFS_POLICY_PUBLIC,
        "vfs_exec_profile": VFS_EXEC_PROFILE_NONE,
        "vfs_policy_generation": VFS_POLICY_GENERATION,
        "owner_version": FS_OWNER_VERSION,
    }
    if any(int(inode[field]) != value for field, value in expected_fields.items()) or int(
        inode["vfs_incarnation"]
    ) == 0:
        raise ImageError(f"{label} inode {inum} has an invalid public VFS label")
    if expected_incarnation is not None and int(
        inode["vfs_incarnation"]
    ) != expected_incarnation:
        raise ImageError(f"{label} inode {inum} has the wrong incarnation")

    allocated = set(map(int, snapshot["allocated_blocks"]))
    owners = {
        int(block): int(owner)
        for block, owner in dict(snapshot["owned_blocks"]).items()
    }
    for block in map(int, dict(snapshot["inode_blocks"])[key]):
        block_state, block_payload = decode_owner(owners.get(block, OWNER_NONE))
        if (
            block not in allocated
            or block_state != "LIVE_LOW"
            or block_payload != public_owner
        ):
            raise ImageError(
                f"{label} inode {inum} block {block} has the wrong owner identity"
            )


def _require_allocating_inode(
    label: str,
    snapshot: dict[str, object],
    inum: int,
    public_owner: int,
    expected_incarnation: int,
) -> None:
    key = str(inum)
    inode = dict(snapshot["inodes"]).get(key)
    if inode is None:
        raise ImageError(f"{label} inode {inum} is absent")
    state, payload = decode_owner(int(inode["owner"]))
    expected_fields = {
        "type": 2,
        "agent_meta_slot": 0,
        "agent_meta_flags": 0,
        "agent_meta_version": 0,
        "size": 0,
        "exec_flags": 0,
        "exec_generation": 0,
        "exec_role_mask": 0,
        "exec_layout_version": 0,
        "exec_rw_offset": 0,
        "vfs_magic": VFS_LABEL_MAGIC,
        "vfs_version": VFS_LABEL_VERSION,
        "vfs_flags": VFS_LABEL_F_FREE,
        "vfs_scope": 0,
        "vfs_policy": VFS_POLICY_FREE,
        "vfs_exec_profile": VFS_EXEC_PROFILE_NONE,
        "vfs_policy_generation": VFS_POLICY_GENERATION,
        "vfs_incarnation": expected_incarnation,
        "owner_version": FS_OWNER_VERSION,
    }
    if (
        state != "ALLOCATING"
        or payload != public_owner
        or any(int(inode[field]) != value for field, value in expected_fields.items())
        or any(map(int, inode["addrs"]))
    ):
        raise ImageError(f"{label} inode {inum} has an invalid allocation template")


def _require_empty_public_inode(
    label: str,
    snapshot: dict[str, object],
    inum: int,
    public_owner: int,
    expected_incarnation: int,
) -> None:
    _require_public_inode(
        label, snapshot, inum, public_owner, expected_incarnation
    )
    inode = dict(snapshot["inodes"])[str(inum)]
    if int(inode["size"]) != 0 or any(map(int, inode["addrs"])):
        raise ImageError(f"{label} inode {inum} is not an exact empty file")


def _free_inode_raw_sha256(
    inum: int,
    incarnation: int,
    owner: int = OWNER_NONE,
    owner_version: int = 0,
) -> str:
    raw = bytearray(DINODE_SIZE)
    struct.pack_into("<I", raw, 84, VFS_LABEL_MAGIC)
    struct.pack_into("<I", raw, 88, VFS_LABEL_VERSION)
    struct.pack_into("<I", raw, 92, VFS_LABEL_F_FREE)
    struct.pack_into("<I", raw, 100, VFS_POLICY_FREE)
    struct.pack_into("<I", raw, 108, VFS_POLICY_GENERATION)
    struct.pack_into("<I", raw, 112, incarnation)
    struct.pack_into("<I", raw, 116, owner)
    struct.pack_into("<I", raw, 120, owner_version)
    checksum = vfs_label_checksum(
        inum,
        (
            VFS_LABEL_MAGIC,
            VFS_LABEL_VERSION,
            VFS_LABEL_F_FREE,
            0,
            VFS_POLICY_FREE,
            VFS_EXEC_PROFILE_NONE,
            VFS_POLICY_GENERATION,
            incarnation,
            owner,
            owner_version,
        ),
    )
    struct.pack_into("<I", raw, 124, checksum)
    return hashlib.sha256(raw).hexdigest()


def _require_free_inode(
    label: str, snapshot: dict[str, object], inum: int, incarnation: int
) -> None:
    key = str(inum)
    if key in dict(snapshot["inodes"]) or key in dict(snapshot["free_inode_owners"]):
        raise ImageError(f"{label} inode {inum} is not stably free")
    expected_hash = _free_inode_raw_sha256(inum, incarnation)
    if dict(snapshot["inode_raw_sha256"])[key] != expected_hash:
        raise ImageError(f"{label} inode {inum} has an invalid free template")


def _inode_record_raw_sha256(record: dict[str, object]) -> str:
    raw = bytearray(DINODE_SIZE)
    struct.pack_into(
        "<4h",
        raw,
        0,
        int(record["type"]),
        int(record["agent_meta_slot"]),
        int(record["agent_meta_flags"]),
        int(record["agent_meta_version"]),
    )
    struct.pack_into("<I", raw, 8, int(record["size"]))
    struct.pack_into("<13I", raw, 12, *map(int, record["addrs"]))
    fields = (
        "exec_flags",
        "exec_generation",
        "exec_role_mask",
        "exec_layout_version",
        "exec_rw_offset",
        "vfs_magic",
        "vfs_version",
        "vfs_flags",
        "vfs_scope",
        "vfs_policy",
        "vfs_exec_profile",
        "vfs_policy_generation",
        "vfs_incarnation",
        "owner",
        "owner_version",
        "vfs_checksum",
    )
    struct.pack_into("<16I", raw, 64, *(int(record[field]) for field in fields))
    return hashlib.sha256(raw).hexdigest()


def _ifree_allocated_freeing_sha256(
    before_inode: dict[str, object], inum: int, public_owner: int
) -> str:
    record = dict(before_inode)
    record["addrs"] = list(before_inode["addrs"])
    record["owner"] = (OWNER_STATE_FREEING << OWNER_STATE_SHIFT) | public_owner
    record["owner_version"] = FS_OWNER_VERSION
    record["vfs_checksum"] = vfs_label_checksum(
        inum,
        (
            int(record["vfs_magic"]),
            int(record["vfs_version"]),
            int(record["vfs_flags"]),
            int(record["vfs_scope"]),
            int(record["vfs_policy"]),
            int(record["vfs_exec_profile"]),
            int(record["vfs_policy_generation"]),
            int(record["vfs_incarnation"]),
            int(record["owner"]),
            int(record["owner_version"]),
        ),
    )
    return _inode_record_raw_sha256(record)


def _require_ifree_fault_template(
    before: dict[str, object],
    fault: dict[str, object],
    inum: int,
    public_owner: int,
    phase: str,
    action: str,
) -> None:
    key = str(inum)
    before_inode = dict(before["inodes"])[key]
    incarnation = int(before_inode["vfs_incarnation"])
    if action == "busy" or (action == "crash" and phase == "refund"):
        expected = _free_inode_raw_sha256(inum, incarnation)
    elif action == "eio" and phase == "intent":
        expected = dict(before["inode_raw_sha256"])[key]
    elif (action == "crash" and phase == "intent") or (
        action == "eio" and phase == "owner"
    ):
        expected = _ifree_allocated_freeing_sha256(
            before_inode, inum, public_owner
        )
    elif (action == "crash" and phase == "owner") or (
        action == "eio" and phase == "refund"
    ):
        freeing = (OWNER_STATE_FREEING << OWNER_STATE_SHIFT) | public_owner
        expected = _free_inode_raw_sha256(
            inum, incarnation, freeing, FS_OWNER_VERSION
        )
    else:
        raise ImageError(f"missing ifree fault oracle for {phase}:{action}")
    if dict(fault["inode_raw_sha256"])[key] != expected:
        raise ImageError("ifree fault inode does not match the exact phase template")


def _require_stage_receipts(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    before_names: dict[str, int],
    fault_names: dict[str, int],
    reboot_names: dict[str, int],
    public_owner: int,
) -> int:
    inum = before_names.get("fsalloc_state")
    if inum is None:
        raise ImageError("before image lacks exact P stage receipt")
    if fault_names.get("fsalloc_state") != inum or reboot_names.get(
        "fsalloc_state"
    ) != inum:
        raise ImageError("stage receipt changed inode identity")

    key = str(inum)
    hash_p = hashlib.sha256(b"P").hexdigest()
    hash_f = hashlib.sha256(b"F").hexdigest()
    if dict(before["payload_sha256"]).get(key) != hash_p:
        raise ImageError("before image lacks exact P stage receipt")
    if dict(fault["payload_sha256"]).get(key) != hash_f or dict(
        reboot["payload_sha256"]
    ).get(key) != hash_f:
        raise ImageError("fault/reboot image lacks exact F stage receipt")

    before_inode = dict(before["inodes"])[key]
    before_blocks = list(map(int, dict(before["inode_blocks"])[key]))
    if len(before_blocks) != 1:
        raise ImageError("stage receipt does not use one stable data block")
    block_key = str(before_blocks[0])
    block_hash_p = hashlib.sha256(b"P" + b"\0" * (BLOCK_SIZE - 1)).hexdigest()
    block_hash_f = hashlib.sha256(b"F" + b"\0" * (BLOCK_SIZE - 1)).hexdigest()
    if dict(before["block_sha256"]).get(block_key) != block_hash_p:
        raise ImageError("before stage receipt block contains trailing data")
    if dict(fault["block_sha256"]).get(block_key) != block_hash_f or dict(
        reboot["block_sha256"]
    ).get(block_key) != block_hash_f:
        raise ImageError("fault/reboot stage receipt block contains trailing data")
    for label, snapshot in (
        ("before stage", before),
        ("fault stage", fault),
        ("reboot stage", reboot),
    ):
        if dict(snapshot["inodes"]).get(key) != before_inode:
            raise ImageError("stage receipt inode metadata changed")
        if list(map(int, dict(snapshot["inode_blocks"])[key])) != before_blocks:
            raise ImageError("stage receipt block identity changed")
        _require_public_inode(label, snapshot, inum, public_owner)
    return inum


def _require_root_inode_invariants(
    before: dict[str, object], after: dict[str, object]
) -> None:
    key = str(ROOT_INODE)
    before_inode = dict(before["inodes"])[key]
    after_inode = dict(after["inodes"]).get(key)
    if after_inode is None:
        raise ImageError("root inode disappeared")
    if before_inode != after_inode:
        raise ImageError("root inode fingerprint changed")


def _require_root_inode_absolute(snapshot: dict[str, object]) -> None:
    inode = dict(snapshot["inodes"])[str(ROOT_INODE)]
    expected_fields = {
        "type": 1,
        "agent_meta_slot": 0,
        "agent_meta_flags": 0,
        "agent_meta_version": 0,
        "size": int(dict(snapshot["geometry"])["ninodes"]) * 16,
        "exec_flags": 0,
        "exec_generation": 0,
        "exec_role_mask": 0,
        "exec_layout_version": 0,
        "exec_rw_offset": 0,
        "vfs_magic": VFS_LABEL_MAGIC,
        "vfs_version": VFS_LABEL_VERSION,
        "vfs_flags": VFS_LABEL_F_ROOT,
        "vfs_scope": 0,
        "vfs_policy": VFS_POLICY_ROOT,
        "vfs_exec_profile": VFS_EXEC_PROFILE_NONE,
        "vfs_policy_generation": VFS_POLICY_GENERATION,
        "owner": 1,
        "owner_version": FS_OWNER_VERSION,
    }
    if any(int(inode[field]) != value for field, value in expected_fields.items()) or int(
        inode["vfs_incarnation"]
    ) == 0:
        raise ImageError("root inode does not match the mountable root template")
    allocated = set(map(int, snapshot["allocated_blocks"]))
    owners = {
        int(block): int(owner)
        for block, owner in dict(snapshot["owned_blocks"]).items()
    }
    for block in map(int, dict(snapshot["inode_blocks"])[str(ROOT_INODE)]):
        if block not in allocated or owners.get(block) != 1:
            raise ImageError("root inode block is not SYSTEM-owned")


def _require_root_dirent_manifest(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    operation: str,
    target_name: str,
    before_names: dict[str, int],
    fault_names: dict[str, int],
) -> None:
    before_entries = list(before["root_dirents"])
    fault_entries = list(fault["root_dirents"])
    reboot_entries = list(reboot["root_dirents"])
    if fault_entries != reboot_entries:
        raise ImageError("fault/reboot root dirent manifests differ")
    if len(before_entries) != len(fault_entries):
        raise ImageError("root dirent manifest length changed")
    changed = [
        index
        for index, (old, new) in enumerate(zip(before_entries, fault_entries))
        if old != new
    ]
    if operation == "ialloc":
        if changed:
            raise ImageError("ialloc changed the ordered root dirent manifest")
        return
    if len(changed) != 1:
        raise ImageError("root dirent manifest did not change one exact slot")
    index = changed[0]
    old = before_entries[index]
    new = fault_entries[index]
    if old["offset"] != new["offset"]:
        raise ImageError("root dirent offset changed")
    if operation == "alloc":
        target_inum = fault_names[target_name]
        expected_raw = struct.pack(
            "<H14s", target_inum, target_name.encode("ascii")
        ).hex()
        if (
            old["raw_hex"] != (b"\0" * 16).hex()
            or new["inum"] != target_inum
            or new["name"] != target_name
            or new["raw_hex"] != expected_raw
        ):
            raise ImageError("allocation changed the wrong root dirent slot")
    else:
        target_inum = before_names[target_name]
        expected_raw = struct.pack(
            "<H14s", target_inum, target_name.encode("ascii")
        ).hex()
        if (
            old["inum"] != target_inum
            or old["name"] != target_name
            or old["raw_hex"] != expected_raw
            or new["raw_hex"] != (b"\0" * 16).hex()
        ):
            raise ImageError("free changed the wrong root dirent slot")


def _require_inode_table_manifest(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    operation: str,
    phase: str,
    action: str,
    mutable_inums: set[int],
) -> list[int]:
    before_hashes = dict(before["inode_raw_sha256"])
    fault_hashes = dict(fault["inode_raw_sha256"])
    reboot_hashes = dict(reboot["inode_raw_sha256"])
    if set(before_hashes) != set(fault_hashes) or set(before_hashes) != set(
        reboot_hashes
    ):
        raise ImageError("inode table identity range changed")
    changed_fault = {
        int(inum)
        for inum in before_hashes
        if before_hashes[inum] != fault_hashes[inum]
    }
    changed_reboot = {
        int(inum)
        for inum in before_hashes
        if before_hashes[inum] != reboot_hashes[inum]
    }
    unexpected = (changed_fault | changed_reboot) - mutable_inums
    if unexpected:
        raise ImageError(
            f"unaffected raw inode table records changed: {sorted(unexpected)!r}"
        )
    return []


def _require_allocator_map_manifest(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    mutable_blocks: set[int],
) -> None:
    def masked(snapshot: dict[str, object]) -> tuple[bytes, bytes]:
        metadata = dict(snapshot["allocator_metadata"])
        bitmap = bytearray.fromhex(str(metadata["bitmap_hex"]))
        qmap = bytearray.fromhex(str(metadata["qmap_hex"]))
        for block in mutable_blocks:
            bitmap[block // 8] &= ~(1 << (block % 8))
            qmap[block * 4 : block * 4 + 4] = b"\0" * 4
        return bytes(bitmap), bytes(qmap)

    before_maps = masked(before)
    if masked(fault) != before_maps or masked(reboot) != before_maps:
        raise ImageError("allocator bitmap/qmap changed outside the exact target")


def _is_kernel_metadata_cow_inode(
    name: str, inode: dict[str, object]
) -> bool:
    return (
        name in AGENT_META_STORE_NAMES
        and
        int(inode["type"]) == 2
        and int(inode["agent_meta_slot"]) == 0
        and int(inode["agent_meta_flags"]) == 0
        and int(inode["agent_meta_version"]) == 0
        and int(inode["size"]) == AGENT_META_STORE_MAX_BYTES
        and int(inode["exec_flags"]) == 0
        and int(inode["exec_generation"]) == 0
        and int(inode["exec_role_mask"]) == 0
        and int(inode["exec_layout_version"]) == 0
        and int(inode["exec_rw_offset"]) == 0
        and int(inode["vfs_magic"]) == VFS_LABEL_MAGIC
        and int(inode["vfs_version"]) == VFS_LABEL_VERSION
        and int(inode["vfs_flags"]) == VFS_LABEL_F_KERNEL_PRIVATE
        and int(inode["vfs_scope"]) == VFS_SCOPE_NONE
        and int(inode["vfs_policy"]) == VFS_POLICY_KERNEL_PRIVATE
        and int(inode["vfs_exec_profile"]) == VFS_EXEC_PROFILE_NONE
        and int(inode["vfs_policy_generation"]) == VFS_POLICY_GENERATION
        and int(inode["vfs_incarnation"]) != 0
        and int(inode["owner"]) == FS_OWNER_SYSTEM
        and int(inode["owner_version"]) == FS_OWNER_VERSION
    )


def _assert_unaffected_objects(
    before: dict[str, object],
    after: dict[str, object],
    target_name: str,
) -> None:
    before_names = {str(k): int(v) for k, v in dict(before["root_names"]).items()}
    after_names = {str(k): int(v) for k, v in dict(after["root_names"]).items()}
    before_inodes = dict(before["inodes"])
    after_inodes = dict(after["inodes"])
    before_payloads = dict(before["payload_sha256"])
    after_payloads = dict(after["payload_sha256"])
    mutable_names = {target_name, "fsalloc_state"}
    mutable_blocks: set[int] = set()
    identity_mutable_blocks: set[int] = set()
    for name in mutable_names:
        if name in before_names:
            blocks = set(
                map(int, dict(before["inode_blocks"])[str(before_names[name])])
            )
            mutable_blocks.update(blocks)
            if name == target_name:
                identity_mutable_blocks.update(blocks)
        if name in after_names:
            blocks = set(
                map(int, dict(after["inode_blocks"])[str(after_names[name])])
            )
            mutable_blocks.update(blocks)
            if name == target_name:
                identity_mutable_blocks.update(blocks)
    # Root directory contents encode namespace changes and are expected to move.
    mutable_blocks.update(map(int, dict(before["inode_blocks"])[str(ROOT_INODE)]))
    mutable_blocks.update(map(int, dict(after["inode_blocks"])[str(ROOT_INODE)]))

    for name, inum in before_names.items():
        if name in mutable_names or inum == ROOT_INODE or name not in after_names:
            continue
        if after_names[name] != inum:
            raise ImageError(f"unaffected name {name!r} changed inode identity")
        key = str(inum)
        if before_inodes[key] != after_inodes.get(key):
            raise ImageError(f"unaffected inode {inum} changed")
        inode = dict(before_inodes[key])
        if _is_kernel_metadata_cow_inode(name, inode):
            # Kernel-private journals are execution state, not allocator-test
            # payload. Their identity and fixed block map remain protected,
            # while canonical validation checks every resulting image.
            mutable_blocks.update(map(int, dict(before["inode_blocks"])[key]))
            mutable_blocks.update(map(int, dict(after["inode_blocks"])[key]))
            continue
        if before_payloads[key] != after_payloads.get(key):
            raise ImageError(f"unaffected payload {name!r} changed")

    before_blocks = dict(before["block_sha256"])
    after_blocks = dict(after["block_sha256"])
    before_allocated = set(map(int, before["allocated_blocks"]))
    after_allocated = set(map(int, after["allocated_blocks"]))
    before_owners = {int(k): int(v) for k, v in dict(before["owned_blocks"]).items()}
    after_owners = {int(k): int(v) for k, v in dict(after["owned_blocks"]).items()}
    for block in set(map(int, before_blocks)):
        if block in identity_mutable_blocks:
            continue
        if str(block) not in after_blocks:
            raise ImageError(f"unaffected block {block} disappeared")
        if block not in mutable_blocks and (
            before_blocks[str(block)] != after_blocks[str(block)]
        ):
            raise ImageError(f"unaffected block {block} payload changed")
        if (block in before_allocated) != (block in after_allocated):
            raise ImageError(f"unaffected block {block} bitmap changed")
        if before_owners.get(block, OWNER_NONE) != after_owners.get(
            block, OWNER_NONE
        ):
            raise ImageError(f"unaffected block {block} owner changed")


def _verify_case_snapshots(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    operation: str,
    phase: str,
    action: str,
) -> dict[str, object]:
    """Verify semantic snapshots; raw-image callers must use verify_case_raw."""
    for label, snapshot in (
        ("before", before),
        ("fault", fault),
        ("reboot", reboot),
    ):
        _require_snapshot_envelope(label, snapshot)
    case_key = (operation, phase, action)
    expectation = CASE_EXPECTATIONS.get(case_key)
    if expectation is None:
        raise ImageError(f"unsupported allocator case {operation}:{phase}:{action}")
    if not (
        before["geometry"] == fault["geometry"] == reboot["geometry"]
    ):
        raise ImageError("allocator case changed filesystem geometry")

    actual_q = _transition_records(fault, "qmap_entries", "block")
    actual_inode = _transition_records(fault, "inode_owner_entries", "inum")
    public_owner = int(dict(before["geometry"])["public_principal_id"])
    alloc_committed = bool(expectation["alloc_committed"])
    expected_q = expectation["qmap_checkpoint"]
    expected_inode = expectation["inode_checkpoint"]
    if len(actual_q) != (1 if expected_q else 0):
        raise ImageError(f"{case_key} qmap transition count {actual_q!r}")
    if actual_q and (
        (actual_q[0]["state"], actual_q[0]["bitmap"]) != expected_q
        or actual_q[0]["payload"] != public_owner
    ):
        raise ImageError(f"{case_key} qmap checkpoint identity {actual_q!r}")
    if len(actual_inode) != (1 if expected_inode else 0):
        raise ImageError(f"{case_key} inode transition count {actual_inode!r}")
    if actual_inode and (
        (actual_inode[0]["state"], actual_inode[0]["bitmap"])
        != expected_inode
        or actual_inode[0]["payload"] != public_owner
    ):
        raise ImageError(f"{case_key} inode checkpoint identity {actual_inode!r}")
    if fault["canonical_violations"]:
        raise ImageError("fault checkpoint contains an invalid encoded state")

    _require_canonical("before", before)
    _require_canonical("reboot", reboot)
    for snapshot in (before, fault, reboot):
        _require_root_inode_absolute(snapshot)
    expected_alloc_block = _first_free_block(before) if operation == "alloc" else 0
    expected_alloc_inum = (
        _first_free_inode(before) if operation in {"alloc", "ialloc"} else 0
    )
    expected_alloc_incarnation = 0
    if expected_alloc_inum:
        previous_incarnation = int(
            dict(before["inode_incarnations"])[str(expected_alloc_inum)]
        )
        expected_alloc_incarnation = (previous_incarnation + 1) & 0xFFFFFFFF
        if expected_alloc_incarnation == 0:
            expected_alloc_incarnation = 1

    before_names = {str(k): int(v) for k, v in dict(before["root_names"]).items()}
    target_name = {
        "alloc": "fsalloc_block",
        "free": "fsalloc_free",
        "ialloc": "fsalloc_inode",
        "ifree": "fsalloc_ifree",
    }[operation]
    expected_names = _expected_namespace(before_names, operation, target_name)
    fault_names = {str(k): int(v) for k, v in dict(fault["root_names"]).items()}
    reboot_names = {str(k): int(v) for k, v in dict(reboot["root_names"]).items()}
    if set(fault_names) != expected_names or set(reboot_names) != expected_names:
        raise ImageError(
            f"case namespace mismatch: expected {sorted(expected_names)!r}"
        )
    for name, inum in before_names.items():
        if name == target_name:
            continue
        if fault_names.get(name) != inum or reboot_names.get(name) != inum:
            raise ImageError(f"existing name {name!r} changed inode identity")

    _require_stage_receipts(
        before,
        fault,
        reboot,
        before_names,
        fault_names,
        reboot_names,
        public_owner,
    )
    _require_root_inode_invariants(before, fault)
    _require_root_inode_invariants(before, reboot)
    _require_root_dirent_manifest(
        before,
        fault,
        reboot,
        operation,
        target_name,
        before_names,
        fault_names,
    )

    target_before_inum = before_names.get(target_name)
    target_before = (
        dict(before["inodes"]).get(str(target_before_inum))
        if target_before_inum is not None
        else None
    )
    target_incarnation = (
        int(target_before["vfs_incarnation"]) if target_before is not None else 0
    )
    if operation in {"alloc", "ialloc"} and target_before_inum is not None:
        raise ImageError(f"{operation} fixture already contains its target inode")
    if operation in {"free", "ifree"}:
        if target_before_inum is None or target_before is None:
            raise ImageError(f"{operation} fixture lacks its target inode")
        _require_public_inode(
            f"before {operation} target",
            before,
            target_before_inum,
            public_owner,
        )
        expected_size = BLOCK_SIZE if operation == "free" else 0
        if int(target_before["size"]) != expected_size:
            raise ImageError(f"{operation} fixture has the wrong target size")
        expected_payload = hashlib.sha256(
            bytes((index ^ 0x5A) & 0xFF for index in range(BLOCK_SIZE))
            if operation == "free"
            else b""
        ).hexdigest()
        if (
            dict(before["payload_sha256"])[str(target_before_inum)]
            != expected_payload
        ):
            raise ImageError(f"{operation} fixture has the wrong target payload")
        if operation == "free":
            block_key = str(int(target_before["addrs"][0]))
            for label, snapshot in (
                ("before", before),
                ("fault", fault),
                ("reboot", reboot),
            ):
                if dict(snapshot["nonzero_data_block_sha256"]).get(
                    block_key
                ) != expected_payload:
                    raise ImageError(
                        f"{label} free target block payload changed"
                    )
        _require_free_inode(
            f"reboot {operation} target",
            reboot,
            target_before_inum,
            target_incarnation,
        )
        if operation == "free":
            _require_free_inode(
                "fault free target",
                fault,
                target_before_inum,
                target_incarnation,
            )
        else:
            _require_ifree_fault_template(
                before,
                fault,
                target_before_inum,
                public_owner,
                phase,
                action,
            )
    target_block = 0
    if target_before is not None:
        target_block = int(target_before["addrs"][0])
    mutable_allocator_blocks: set[int] = set()
    if operation == "alloc":
        mutable_allocator_blocks.add(expected_alloc_block)
    elif operation == "free" and target_block:
        mutable_allocator_blocks.add(target_block)
    _require_allocator_map_manifest(
        before, fault, reboot, mutable_allocator_blocks
    )
    if actual_q:
        identity = int(actual_q[0]["identity"])
        if operation == "alloc":
            if identity != expected_alloc_block:
                raise ImageError("alloc transition did not use the first free block")
        elif identity != target_block:
            raise ImageError("free transition does not own the target block")
    if actual_inode:
        identity = int(actual_inode[0]["identity"])
        if operation == "ialloc":
            if identity != expected_alloc_inum:
                raise ImageError("ialloc transition did not use the first free inode")
            _require_allocating_inode(
                "fault ialloc transition",
                fault,
                identity,
                public_owner,
                expected_alloc_incarnation,
            )
        elif identity != target_before_inum:
            raise ImageError("ifree transition does not own the target inode")
    if operation == "free" and not actual_q:
        if target_block == 0:
            raise ImageError("free fixture has no target block")
        fault_raw = int(dict(fault["owned_blocks"]).get(str(target_block), 0))
        fault_bit = target_block in set(map(int, fault["allocated_blocks"]))
        if action == "eio" and phase == "intent":
            expected_raw = int(dict(before["owned_blocks"])[str(target_block)])
            expected_bit = True
        else:
            expected_raw = OWNER_NONE
            expected_bit = False
        if fault_raw != expected_raw or fault_bit != expected_bit:
            raise ImageError("free checkpoint changed the wrong owner identity")
    if operation == "ifree" and not actual_inode:
        if target_before_inum is None:
            raise ImageError("ifree fixture has no target inode")
        key = str(target_before_inum)
        fault_type = 1 if key in dict(fault["inodes"]) else 0
        if key in dict(fault["inodes"]):
            fault_raw = int(dict(fault["inodes"])[key]["owner"])
        else:
            fault_raw = int(dict(fault["free_inode_owners"]).get(key, 0))
        if action == "eio" and phase == "intent":
            expected_raw = int(dict(before["inodes"])[key]["owner"])
            expected_type = 1
        else:
            expected_raw = OWNER_NONE
            expected_type = 0
        if fault_raw != expected_raw or fault_type != expected_type:
            raise ImageError("ifree checkpoint changed the wrong inode identity")

    before_tracked = set(map(int, dict(before["block_sha256"])))
    fault_tracked = set(map(int, dict(fault["block_sha256"])))
    new_fault_blocks = fault_tracked - before_tracked
    expected_new_fault_blocks: set[int] = set()
    new_block_source = str(expectation["fault_new_block_source"])
    if new_block_source == "qmap_transition" and actual_q:
        expected_new_fault_blocks.add(expected_alloc_block)
    elif new_block_source == "target_inode":
        expected_new_fault_blocks.add(expected_alloc_block)
    elif new_block_source == "orphan":
        expected_new_fault_blocks.add(expected_alloc_block)
    if new_fault_blocks != expected_new_fault_blocks:
        raise ImageError(
            f"unexpected fault block identities {sorted(new_fault_blocks)!r}"
        )
    fault_owners = {
        int(key): int(value) for key, value in dict(fault["owned_blocks"]).items()
    }
    for block in new_fault_blocks:
        state, payload = decode_owner(fault_owners.get(block, OWNER_NONE))
        if payload != public_owner or state not in {
            "LIVE_LOW",
            "ALLOCATING",
            "FREEING",
        }:
            raise ImageError("new fault block has the wrong owner identity")
    if operation == "alloc":
        expected_block_payload = (
            bytes((index * 17 + 3) & 0xFF for index in range(BLOCK_SIZE))
            if alloc_committed
            else b"\0" * BLOCK_SIZE
        )
        expected_block_hash = hashlib.sha256(expected_block_payload).hexdigest()
        for block in new_fault_blocks:
            if dict(fault["block_sha256"]).get(str(block)) != expected_block_hash:
                raise ImageError("new allocation block has unexpected durable content")

    before_inode_ids = set(map(int, dict(before["inodes"])))
    fault_inode_ids = set(map(int, dict(fault["inodes"])))
    new_fault_inodes = fault_inode_ids - before_inode_ids
    expected_new_fault_inodes: set[int] = set()
    new_inode_source = str(expectation["fault_new_inode_source"])
    if new_inode_source == "target_name":
        expected_new_fault_inodes.add(expected_alloc_inum)
    elif new_inode_source == "orphan":
        expected_new_fault_inodes.add(expected_alloc_inum)
    if new_fault_inodes != expected_new_fault_inodes:
        raise ImageError(
            f"unexpected fault inode identities {sorted(new_fault_inodes)!r}"
        )
    for inum in new_fault_inodes:
        raw_owner = int(dict(fault["inodes"])[str(inum)]["owner"])
        state, payload = decode_owner(raw_owner)
        if payload != public_owner or state not in {
            "LIVE_LOW",
            "ALLOCATING",
        }:
            raise ImageError("new fault inode has the wrong owner identity")
        if operation == "ialloc":
            if state == "ALLOCATING":
                _require_allocating_inode(
                    "fault ialloc inode",
                    fault,
                    inum,
                    public_owner,
                    expected_alloc_incarnation,
                )
            else:
                _require_empty_public_inode(
                    "fault ialloc inode",
                    fault,
                    inum,
                    public_owner,
                    expected_alloc_incarnation,
                )

    ialloc_durable_attempt = operation == "ialloc" and (
        action == "crash" or (phase == "owner" and action in {"busy", "eio"})
    )
    if ialloc_durable_attempt:
        _require_free_inode(
            "reboot ialloc attempt",
            reboot,
            expected_alloc_inum,
            expected_alloc_incarnation,
        )

    if operation == "alloc":
        fault_inum = fault_names[target_name]
        reboot_inum = reboot_names[target_name]
        if fault_inum != reboot_inum:
            raise ImageError("allocated target changed inode identity across reboot")
        if dict(fault["inodes"])[str(fault_inum)] != dict(reboot["inodes"])[
            str(reboot_inum)
        ]:
            raise ImageError("allocated target inode fingerprint changed across reboot")
        expected_size = BLOCK_SIZE if alloc_committed else 0
        expected_payload = hashlib.sha256(
            bytes((index * 17 + 3) & 0xFF for index in range(BLOCK_SIZE))
            if alloc_committed
            else b""
        ).hexdigest()
        for label, snapshot, inum in (
            ("fault allocation", fault, fault_inum),
            ("reboot allocation", reboot, reboot_inum),
        ):
            _require_public_inode(
                label,
                snapshot,
                inum,
                public_owner,
                expected_alloc_incarnation,
            )
            inode = dict(snapshot["inodes"])[str(inum)]
            if int(inode["size"]) != expected_size:
                raise ImageError(
                    f"{label} size {inode['size']}, expected {expected_size}"
                )
            if dict(snapshot["payload_sha256"])[str(inum)] != expected_payload:
                raise ImageError(f"{label} payload mismatch")

    mutable_inums: set[int] = set(new_fault_inodes)
    if target_before_inum is not None:
        mutable_inums.add(target_before_inum)
    if operation == "alloc":
        mutable_inums.add(expected_alloc_inum)
    mutable_inums.update(int(entry["identity"]) for entry in actual_inode)
    retired_attempts: list[int] = []
    if (operation, phase, action) == ("ialloc", "owner", "busy"):
        _require_free_inode(
            "fault ialloc abort",
            fault,
            expected_alloc_inum,
            expected_alloc_incarnation,
        )
        _require_free_inode(
            "reboot ialloc abort",
            reboot,
            expected_alloc_inum,
            expected_alloc_incarnation,
        )
        mutable_inums.add(expected_alloc_inum)
        retired_attempts.append(expected_alloc_inum)
    _require_inode_table_manifest(
        before,
        fault,
        reboot,
        operation,
        phase,
        action,
        mutable_inums,
    )

    block_delta = len(reboot["allocated_blocks"]) - len(before["allocated_blocks"])
    inode_delta = len(dict(reboot["inodes"])) - len(dict(before["inodes"]))
    expected_block_delta = int(expectation["reboot_block_delta"])
    expected_inode_delta = int(expectation["reboot_inode_delta"])
    if block_delta != expected_block_delta:
        raise ImageError(
            f"reboot block settlement {block_delta}, expected {expected_block_delta}"
        )
    if inode_delta != expected_inode_delta:
        raise ImageError(
            f"reboot inode settlement {inode_delta}, expected {expected_inode_delta}"
        )

    expected_allocated = set(map(int, before["allocated_blocks"]))
    expected_owners = {
        int(key): int(value) for key, value in dict(before["owned_blocks"]).items()
    }
    expected_inode_ids = set(map(int, dict(before["inodes"])))
    if operation == "alloc":
        new_inum = reboot_names[target_name]
        expected_inode_ids.add(new_inum)
        if alloc_committed:
            new_blocks = set(
                map(int, dict(reboot["inode_blocks"])[str(new_inum)])
            ) - set(map(int, dict(before["inode_blocks"])[str(ROOT_INODE)]))
            if len(new_blocks) != 1:
                raise ImageError("committed allocation lacks one exact data block")
            new_block = next(iter(new_blocks))
            expected_allocated.add(new_block)
            expected_owners[new_block] = public_owner
    elif operation == "free":
        if target_before_inum is None or target_block == 0:
            raise ImageError("free fixture lacks exact target identity")
        expected_inode_ids.remove(target_before_inum)
        expected_allocated.remove(target_block)
        expected_owners.pop(target_block, None)
    elif operation == "ifree":
        if target_before_inum is None:
            raise ImageError("ifree fixture lacks exact target identity")
        expected_inode_ids.remove(target_before_inum)

    actual_reboot_allocated = set(map(int, reboot["allocated_blocks"]))
    actual_reboot_owners = {
        int(key): int(value) for key, value in dict(reboot["owned_blocks"]).items()
    }
    actual_reboot_inode_ids = set(map(int, dict(reboot["inodes"])))
    if actual_reboot_allocated != expected_allocated:
        raise ImageError("reboot bitmap identity manifest mismatch")
    if actual_reboot_owners != expected_owners:
        raise ImageError("reboot owner identity manifest mismatch")
    if actual_reboot_inode_ids != expected_inode_ids:
        raise ImageError("reboot inode identity manifest mismatch")
    if operation == "alloc":
        inum = str(reboot_names[target_name])
        target = dict(reboot["inodes"])[inum]
        expected_size = BLOCK_SIZE if alloc_committed else 0
        if target["size"] != expected_size:
            raise ImageError(
                f"reboot allocation size {target['size']}, expected {expected_size}"
            )
        expected_payload = hashlib.sha256(
            bytes((index * 17 + 3) & 0xFF for index in range(BLOCK_SIZE))
            if alloc_committed
            else b""
        ).hexdigest()
        if dict(reboot["payload_sha256"])[inum] != expected_payload:
            raise ImageError("reboot allocation payload mismatch")

    _assert_unaffected_objects(before, fault, target_name)
    _assert_unaffected_objects(before, reboot, target_name)

    return {
        "format": VERIFIED_FORMAT,
        "generator": dict(GENERATOR),
        "images": {
            "before": before["image"],
            "fault": fault["image"],
            "reboot": reboot["image"],
        },
        "operation": operation,
        "phase": phase,
        "action": action,
        "expected_manifest": {
            "public_owner": public_owner,
            "target_name": target_name,
            "target_before_inum": target_before_inum,
            "target_before_block": target_block or None,
            "first_free_block": expected_alloc_block or None,
            "first_free_inode": expected_alloc_inum or None,
            "retired_ialloc_attempts": retired_attempts,
            "namespace": sorted(expected_names),
            "qmap_checkpoint": (
                None
                if expected_q is None
                else {"state": expected_q[0], "bitmap": expected_q[1]}
            ),
            "inode_checkpoint": (
                None
                if expected_inode is None
                else {"state": expected_inode[0], "allocated_type": expected_inode[1]}
            ),
            "reboot_allocated_blocks": sorted(expected_allocated),
            "reboot_owned_blocks": {
                str(block): owner for block, owner in sorted(expected_owners.items())
            },
            "reboot_inode_ids": sorted(expected_inode_ids),
            "reboot_block_delta": expected_block_delta,
            "reboot_inode_delta": expected_inode_delta,
        },
        "fault_qmap_transitions": actual_q,
        "fault_inode_transitions": actual_inode,
        "before_sha256": before["state_sha256"],
        "fault_sha256": fault["state_sha256"],
        "fault_exact_diff": diff_snapshots(before, fault),
        "reboot_exact_diff": diff_snapshots(before, reboot),
        "reboot_block_delta": block_delta,
        "reboot_inode_delta": inode_delta,
        "reboot_sha256": reboot["state_sha256"],
        "verified": True,
    }


def _read_bound_raw(path: Path, snapshot: dict[str, object]) -> bytes:
    raw = path.read_bytes()
    image = dict(snapshot["image"])
    if len(raw) != int(image["bytes"]) or hashlib.sha256(raw).hexdigest() != str(
        image["sha256"]
    ):
        raise ImageError("raw image changed during verification")
    return raw


def _inode_logical_data_blocks(
    snapshot: dict[str, object], raw: bytes, inum: int
) -> list[int]:
    inode = dict(snapshot["inodes"])[str(inum)]
    remaining = (int(inode["size"]) + BLOCK_SIZE - 1) // BLOCK_SIZE
    addrs = list(map(int, inode["addrs"]))
    blocks = [block for block in addrs[:DIRECT_BLOCKS] if block]
    if remaining > DIRECT_BLOCKS:
        indirect = addrs[DIRECT_BLOCKS]
        if indirect == 0:
            raise ImageError(f"inode {inum} has no indirect data table")
        entries = struct.unpack_from(f"<{INDIRECT_ENTRIES}I", raw, indirect * BLOCK_SIZE)
        blocks.extend(block for block in entries if block)
    if len(blocks) < remaining:
        raise ImageError(f"inode {inum} has an incomplete raw block map")
    return blocks[:remaining]


def _agent_disk_hash(raw: bytes) -> int:
    value = AGENT_META_HASH_INITIAL
    for byte in raw:
        value ^= byte
        value = (value * AGENT_META_HASH_PRIME) & 0xFFFFFFFFFFFFFFFF
    return value


def _agent_durable_hash(raw: bytes) -> int:
    return _agent_disk_hash(raw) or 1


def _inode_payload_from_raw(
    snapshot: dict[str, object], raw: bytes, inum: int
) -> bytes:
    inode = dict(snapshot["inodes"])[str(inum)]
    size = int(inode["size"])
    payload = b"".join(
        raw[block * BLOCK_SIZE : (block + 1) * BLOCK_SIZE]
        for block in _inode_logical_data_blocks(snapshot, raw, inum)
    )
    if len(payload) < size:
        raise ImageError(f"inode {inum} payload is shorter than its durable size")
    return payload[:size]


def _canonical_metadata_text_fields(
    fields: dict[str, bytes], label: str, index: int
) -> dict[str, bytes]:
    """Decode the fixed-width disk strings into their C-visible identity."""

    canonical: dict[str, bytes] = {}
    for name, field in fields.items():
        end = field.find(b"\0")
        if end < 0 or field[-1] != 0:
            raise ImageError(
                f"{label}: invalid metadata record {index} string terminator"
            )
        canonical[name] = field[:end]
    return canonical


def _validate_agent_metadata_records(
    raw: bytes, records_offset: int, count: int, label: str
) -> None:
    records: list[dict[str, object]] = []
    text_fields = (
        ("physical_name", 8, 32),
        ("logical_path", 40, 80),
        ("project", 120, 16),
        ("workflow", 136, 24),
        ("run_id", 160, 16),
        ("stage", 176, 16),
        ("kind", 192, 16),
        ("status", 208, 16),
        ("summary", 224, 96),
    )
    ordinary = 0
    for index in range(count):
        offset = records_offset + index * AGENT_META_STORE_RECORD_BYTES
        record_raw = raw[offset : offset + AGENT_META_STORE_RECORD_BYTES]
        used, fid = struct.unpack_from("<ii", record_raw)
        fields = {
            name: record_raw[field_offset : field_offset + field_bytes]
            for name, field_offset, field_bytes in text_fields
        }
        canonical_fields = _canonical_metadata_text_fields(
            fields, label, index
        )
        (
            dependency_mask,
            updated_tick,
            flags,
            dev,
            inum,
            incarnation,
            size,
            fs_generation,
            update_mask,
        ) = struct.unpack_from("<9Q", record_raw, 320)
        scope_id, slot, lifecycle_id = struct.unpack_from("<III", record_raw, 392)
        lifecycle_padding = record_raw[404:408]
        lifecycle_generation = struct.unpack_from("<Q", record_raw, 408)[0]
        identity_parts = (dev != 0, inum != 0, incarnation != 0)
        if (
            used != 1
            or fid <= 0
            or slot >= AGENT_META_STORE_MAX_RECORDS
            or not (
                scope_id == VFS_SCOPE_SYSTEM
                or VFS_SCOPE_FIRST_DYNAMIC <= scope_id < FS_OWNER_SCOPE_FLAG
            )
            or not canonical_fields["physical_name"]
            or len(canonical_fields["physical_name"]) > 14
            or canonical_fields["physical_name"]
            in {name.encode("ascii") for name in AGENT_META_STORE_NAMES}
            or any(lifecycle_padding)
            or (any(identity_parts) and not all(identity_parts))
            or update_mask != 0
            or not (flags & AGENT_FILE_META_F_PERSIST)
            or flags & ~(AGENT_FILE_META_F_PERSIST | AGENT_FILE_META_F_AUTOSCAN)
        ):
            raise ImageError(f"{label}: invalid metadata record {index}")
        if scope_id == VFS_SCOPE_SYSTEM:
            if lifecycle_id != 0 or lifecycle_generation != 0:
                raise ImageError(
                    f"{label}: SYSTEM metadata record {index} has a lifecycle"
                )
            limit = AGENT_FILE_SYSTEM_LIMIT
        else:
            ordinary += 1
            if (
                ordinary > AGENT_FILE_ORDINARY_LIMIT
                or not 1 <= lifecycle_id <= WORKFLOW_LIFECYCLE_CAP
                or lifecycle_generation == 0
            ):
                raise ImageError(
                    f"{label}: invalid workflow metadata record {index}"
                )
            limit = AGENT_FILE_SCOPE_LIMIT

        owned = 0
        for prior in records:
            if int(prior["slot"]) == slot:
                raise ImageError(f"{label}: duplicate metadata slot {slot}")
            if int(prior["scope_id"]) != scope_id:
                continue
            owned += 1
            prior_fields = dict(prior["fields"])
            if (
                int(prior["fid"]) == fid
                or prior_fields["physical_name"]
                == canonical_fields["physical_name"]
                or (
                    canonical_fields["logical_path"]
                    and prior_fields["logical_path"]
                    == canonical_fields["logical_path"]
                )
                or (
                    dev != 0
                    and int(prior["dev"]) == dev
                    and int(prior["inum"]) == inum
                    and int(prior["incarnation"]) == incarnation
                )
            ):
                raise ImageError(
                    f"{label}: duplicate metadata identity in scope {scope_id}"
                )
        if owned >= limit:
            raise ImageError(f"{label}: metadata scope {scope_id} exceeds its limit")
        records.append(
            {
                "fid": fid,
                "slot": slot,
                "scope_id": scope_id,
                "fields": canonical_fields,
                "dev": dev,
                "inum": inum,
                "incarnation": incarnation,
                # Bind the unpacked ABI even when it has no independent rule.
                "dependency_mask": dependency_mask,
                "updated_tick": updated_tick,
                "size": size,
                "fs_generation": fs_generation,
            }
        )


def _validate_agent_durable_arena(arena: bytes, label: str) -> dict[str, object]:
    if len(arena) != AGENT_META_STORE_DURABLE_BYTES:
        raise ImageError(f"{label}: durable metadata arena size changed")
    magic, version, arena_bytes, section_count, used_bytes, generation = (
        struct.unpack_from("<QIIIIQ", arena)
    )
    expected_hash = struct.unpack_from("<Q", arena, len(arena) - 8)[0]
    if (
        magic != AGENT_DURABLE_ARENA_MAGIC
        or version != AGENT_DURABLE_ARENA_VERSION
        or arena_bytes != AGENT_META_STORE_DURABLE_BYTES
        or section_count > AGENT_DURABLE_SECTION_MAX
        or used_bytes > AGENT_DURABLE_PAYLOAD_BYTES
        or generation == 0
        or expected_hash != _agent_durable_hash(arena[:-8])
    ):
        raise ImageError(f"{label}: invalid durable metadata arena")

    end = 0
    sections: list[dict[str, object]] = []
    seen_kinds: set[int] = set()
    for index in range(section_count):
        desc_offset = 32 + index * AGENT_DURABLE_SECTION_BYTES
        kind, section_version, offset, section_bytes, section_generation, payload_hash = (
            struct.unpack_from("<IIIIQQ", arena, desc_offset)
        )
        if (
            kind != AGENT_DURABLE_SECTION_OBSERVE
            or kind in seen_kinds
            or section_version != AGENT_OBSERVE_CHECKPOINT_VERSION
            or section_bytes != AGENT_OBSERVE_CHECKPOINT_BYTES
            or offset != end
            or offset > used_bytes
            or section_bytes > used_bytes - offset
        ):
            raise ImageError(f"{label}: invalid durable section descriptor {index}")
        payload_start = AGENT_DURABLE_PAYLOAD_OFFSET + offset
        payload = arena[payload_start : payload_start + section_bytes]
        if payload_hash != _agent_durable_hash(payload):
            raise ImageError(f"{label}: durable section payload hash mismatch")
        try:
            observation = validate_observation_payload(
                payload, load_observation_contract()
            )
        except ObservationEvidenceError as error:
            raise ImageError(
                f"{label}: invalid observation durable section: {error}"
            ) from error
        if section_generation != int(observation["generation"]):
            raise ImageError(
                f"{label}: durable section generation does not bind its payload"
            )
        sections.append(
            {
                "kind": kind,
                "version": section_version,
                "offset": offset,
                "bytes": section_bytes,
                "generation": section_generation,
                "payload_hash": f"{payload_hash:016x}",
            }
        )
        seen_kinds.add(kind)
        end += section_bytes
    if end != used_bytes:
        raise ImageError(f"{label}: durable metadata sections are not contiguous")
    return {
        "generation": generation,
        "image_hash": f"{expected_hash:016x}",
        "sections": sections,
    }


def _parse_agent_metadata_bank(raw: bytes, label: str) -> dict[str, object]:
    if len(raw) != AGENT_META_STORE_MAX_BYTES:
        raise ImageError(f"{label}: metadata bank capacity changed")
    header = struct.unpack_from("<5Q", raw)
    if all(value == 0 for value in header):
        return {"label": label, "state": "uncommitted"}
    magic, version, count, generation, expected_hash = header
    if magic != AGENT_META_STORE_MAGIC or version != AGENT_META_STORE_VERSION:
        raise ImageError(f"{label}: invalid metadata bank magic/version")
    if count > AGENT_META_STORE_MAX_RECORDS or generation == 0:
        raise ImageError(f"{label}: invalid metadata bank count/generation")
    store_bytes = (
        AGENT_META_STORE_HEADER_BYTES
        + AGENT_META_STORE_DURABLE_BYTES
        + count * AGENT_META_STORE_RECORD_BYTES
    )
    payload = raw[AGENT_META_STORE_HEADER_BYTES : store_bytes]
    actual_hash = _agent_disk_hash(raw[:32] + payload)
    if actual_hash != expected_hash:
        raise ImageError(f"{label}: metadata bank payload hash mismatch")

    arena = payload[:AGENT_META_STORE_DURABLE_BYTES]
    arena_summary = _validate_agent_durable_arena(arena, label)
    records_offset = AGENT_META_STORE_HEADER_BYTES + AGENT_META_STORE_DURABLE_BYTES
    _validate_agent_metadata_records(raw, records_offset, count, label)
    store_image = raw[:store_bytes]
    return {
        "label": label,
        "state": "valid",
        "generation": generation,
        "count": count,
        "payload_hash": f"{expected_hash:016x}",
        "store_sha256": hashlib.sha256(store_image).hexdigest(),
        "bank_sha256": hashlib.sha256(raw).hexdigest(),
        "store_bytes": store_bytes,
        "durable_arena": arena_summary,
    }


def _metadata_bank_stage(
    snapshot: dict[str, object], raw: bytes, stage: str, required: bool
) -> dict[str, object]:
    names = {str(k): int(v) for k, v in dict(snapshot["root_names"]).items()}
    present = [name for name in AGENT_META_STORE_NAMES if name in names]
    if not present:
        if required:
            raise ImageError(f"{stage}: canonical metadata COW banks are missing")
        return {"stage": stage, "banks": [], "max_generation": None}
    if tuple(present) != AGENT_META_STORE_NAMES:
        raise ImageError(f"{stage}: canonical metadata COW bank set is incomplete")

    banks: list[dict[str, object]] = []
    bank_inums: set[int] = set()
    bank_blocks: set[int] = set()
    for name in AGENT_META_STORE_NAMES:
        inum = names[name]
        if inum in bank_inums:
            raise ImageError(f"{stage}: metadata COW bank inode is aliased")
        inode = dict(snapshot["inodes"])[str(inum)]
        if not _is_kernel_metadata_cow_inode(name, inode):
            raise ImageError(f"{stage}:{name}: non-canonical metadata bank inode")
        incarnation = int(inode["vfs_incarnation"])
        expected_checksum = vfs_label_checksum(
            inum,
            (
                VFS_LABEL_MAGIC,
                VFS_LABEL_VERSION,
                VFS_LABEL_F_KERNEL_PRIVATE,
                VFS_SCOPE_NONE,
                VFS_POLICY_KERNEL_PRIVATE,
                VFS_EXEC_PROFILE_NONE,
                VFS_POLICY_GENERATION,
                incarnation,
                FS_OWNER_SYSTEM,
                FS_OWNER_VERSION,
            )
        )
        if int(inode["vfs_checksum"]) != expected_checksum:
            raise ImageError(f"{stage}:{name}: metadata bank label checksum differs")
        storage = list(map(int, dict(snapshot["inode_blocks"])[str(inum)]))
        if (
            len(storage) != AGENT_META_STORE_DATA_BLOCKS + 1
            or len(set(storage)) != len(storage)
            or int(list(inode["addrs"])[DIRECT_BLOCKS]) == 0
            or bank_blocks.intersection(storage)
        ):
            raise ImageError(f"{stage}:{name}: metadata bank block map is invalid")
        qmap = dict(snapshot["qmap_entries"])
        for block in storage:
            entry = dict(qmap.get(str(block), {}))
            if entry.get("state") != "LIVE_LOW" or int(entry.get("payload", 0)) != FS_OWNER_SYSTEM:
                raise ImageError(
                    f"{stage}:{name}: metadata bank block is not SYSTEM-owned"
                )
        bank = _parse_agent_metadata_bank(
            _inode_payload_from_raw(snapshot, raw, inum),
            f"{stage}:{name}:{inum}",
        )
        bank.update(
            {
                "name": name,
                "inum": inum,
                "inode_sha256": str(inode["raw_sha256"]),
                "storage_blocks": storage,
            }
        )
        banks.append(bank)
        bank_inums.add(inum)
        bank_blocks.update(storage)

    max_generation = _validate_metadata_bank_set(banks, stage)
    return {"stage": stage, "banks": banks, "max_generation": max_generation}


def _validate_metadata_bank_set(
    banks: list[dict[str, object]], stage: str
) -> int:
    valid = [bank for bank in banks if bank["state"] == "valid"]
    if not valid:
        raise ImageError(f"{stage}: no valid metadata COW bank")
    for index, left in enumerate(valid):
        for right in valid[index + 1 :]:
            if left["generation"] == right["generation"] and (
                left["payload_hash"] != right["payload_hash"]
                or left["store_sha256"] != right["store_sha256"]
            ):
                raise ImageError(
                    f"{stage}: same-generation metadata COW fork"
                )
    generations = sorted({int(bank["generation"]) for bank in valid})
    if generations[-1] - generations[0] > 1:
        raise ImageError(f"{stage}: non-adjacent metadata COW generations")
    return generations[-1]


def _validate_metadata_bank_transitions(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    before_raw: bytes,
    fault_raw: bytes,
    reboot_raw: bytes,
    required: bool = False,
) -> list[dict[str, object]]:
    stages = [
        _metadata_bank_stage(before, before_raw, "before", required),
        _metadata_bank_stage(fault, fault_raw, "fault", required),
        _metadata_bank_stage(reboot, reboot_raw, "reboot", required),
    ]
    _validate_metadata_stage_sequence(stages)
    return stages


def _validate_metadata_stage_sequence(
    stages: list[dict[str, object]],
) -> None:
    if all(stage["max_generation"] is None for stage in stages):
        return
    if any(stage["max_generation"] is None for stage in stages):
        raise ImageError("metadata COW bank set appeared or disappeared")
    generations = [int(stage["max_generation"]) for stage in stages]
    if generations != sorted(generations):
        raise ImageError("metadata COW generation rolled back across allocator case")

    identities: dict[str, tuple[int, str, tuple[int, ...]]] = {}
    generation_images: dict[int, tuple[str, str]] = {}
    identity_generations: dict[tuple[str, int], str] = {}
    last_generation: dict[str, int] = {}
    for stage in stages:
        for bank in list(stage["banks"]):
            name = str(bank["name"])
            identity = (
                int(bank["inum"]),
                str(bank["inode_sha256"]),
                tuple(map(int, bank["storage_blocks"])),
            )
            if name in identities and identities[name] != identity:
                raise ImageError(f"metadata COW identity changed for {name}")
            identities[name] = identity
            if bank["state"] != "valid":
                continue
            generation = int(bank["generation"])
            store_identity = (str(bank["payload_hash"]), str(bank["store_sha256"]))
            prior_image = generation_images.get(generation)
            if prior_image is not None and prior_image != store_identity:
                raise ImageError(
                    f"metadata COW generation {generation} was rewritten"
                )
            generation_images[generation] = store_identity
            identity_key = (name, generation)
            prior_bank = identity_generations.get(identity_key)
            if prior_bank is not None and prior_bank != str(bank["bank_sha256"]):
                raise ImageError(
                    f"metadata COW bank {name} rewrote generation {generation}"
                )
            identity_generations[identity_key] = str(bank["bank_sha256"])
            if name in last_generation and generation < last_generation[name]:
                raise ImageError(f"metadata COW bank {name} rolled back")
            last_generation[name] = generation


def _raw_case_allowlist(
    before: dict[str, object],
    fault: dict[str, object],
    reboot: dict[str, object],
    before_raw: bytes,
    verified: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    manifest = dict(verified["expected_manifest"])
    operation = str(verified["operation"])
    target_name = str(manifest["target_name"])
    geometry = dict(before["geometry"])
    ranges: list[dict[str, object]] = []
    bits: list[dict[str, object]] = []

    stage_inum = int(dict(before["root_names"])["fsalloc_state"])
    stage_blocks = list(map(int, dict(before["inode_blocks"])[str(stage_inum)]))
    if len(stage_blocks) != 1:
        raise ImageError("stage receipt raw block identity is ambiguous")
    ranges.append(
        {
            "label": "stage-receipt-block",
            "offset": stage_blocks[0] * BLOCK_SIZE,
            "bytes": BLOCK_SIZE,
        }
    )

    # Reboots legitimately advance fixed-layout kernel journals. Keep their
    # inode identity and block map immutable, but declare their data arena as
    # execution state rather than pretending it is allocator-test payload.
    before_names = {str(k): int(v) for k, v in dict(before["root_names"]).items()}
    for name in AGENT_META_STORE_NAMES:
        if name not in before_names:
            continue
        inum = before_names[name]
        inode = dict(before["inodes"])[str(inum)]
        if not _is_kernel_metadata_cow_inode(name, inode):
            raise ImageError(f"non-canonical metadata bank inode for {name}")
        for logical, block in enumerate(
            _inode_logical_data_blocks(before, before_raw, inum)
        ):
            ranges.append(
                {
                    "label": f"kernel-private-data:{inum}:{logical}:{name}",
                    "offset": block * BLOCK_SIZE,
                    "bytes": BLOCK_SIZE,
                }
            )

    if operation != "ialloc":
        entries = fault["root_dirents"] if operation == "alloc" else before["root_dirents"]
        matches = [entry for entry in entries if entry["name"] == target_name]
        if len(matches) != 1:
            raise ImageError("target root dirent raw identity is ambiguous")
        logical_offset = int(matches[0]["offset"])
        root_blocks = _inode_logical_data_blocks(before, before_raw, ROOT_INODE)
        logical_block = logical_offset // BLOCK_SIZE
        if logical_block >= len(root_blocks):
            raise ImageError("target root dirent is outside the raw root map")
        ranges.append(
            {
                "label": "target-root-dirent",
                "offset": root_blocks[logical_block] * BLOCK_SIZE
                + logical_offset % BLOCK_SIZE,
                "bytes": 16,
            }
        )

    target_inum_value = (
        manifest["first_free_inode"]
        if operation in {"alloc", "ialloc"}
        else manifest["target_before_inum"]
    )
    if target_inum_value is not None:
        target_inum = int(target_inum_value)
        key = str(target_inum)
        before_hash = dict(before["inode_raw_sha256"])[key]
        if (
            dict(fault["inode_raw_sha256"])[key] != before_hash
            or dict(reboot["inode_raw_sha256"])[key] != before_hash
        ):
            ranges.append(
                {
                    "label": "target-inode-record",
                    "offset": int(geometry["inodestart"]) * BLOCK_SIZE
                    + target_inum * DINODE_SIZE,
                    "bytes": DINODE_SIZE,
                }
            )

    target_block_value = (
        manifest["first_free_block"]
        if operation == "alloc"
        else manifest["target_before_block"]
        if operation == "free"
        else None
    )
    if target_block_value is not None:
        target_block = int(target_block_value)
        bitmap_offset = (
            int(geometry["bmapstart"]) * BLOCK_SIZE + target_block // 8
        )
        bits.append(
            {
                "label": "target-bitmap-bit",
                "offset": bitmap_offset,
                "mask": 1 << (target_block % 8),
            }
        )
        ranges.append(
            {
                "label": "target-qmap-entry",
                "offset": int(geometry["qmapstart"]) * BLOCK_SIZE
                + target_block * 4,
                "bytes": 4,
            }
        )
        if operation == "alloc":
            ranges.append(
                {
                    "label": "zeroed-or-committed-target-data-block",
                    "offset": target_block * BLOCK_SIZE,
                    "bytes": BLOCK_SIZE,
                }
            )

    ranges.sort(key=lambda entry: (int(entry["offset"]), str(entry["label"])))
    bits.sort(key=lambda entry: (int(entry["offset"]), int(entry["mask"])))
    return ranges, bits


def _masked_raw_sha256(
    raw: bytes,
    ranges: list[dict[str, object]],
    bits: list[dict[str, object]],
) -> str:
    masked = bytearray(raw)
    for entry in ranges:
        offset = int(entry["offset"])
        count = int(entry["bytes"])
        if offset < 0 or count <= 0 or offset + count > len(masked):
            raise ImageError("raw allowlist range is outside the image")
        masked[offset : offset + count] = b"\0" * count
    for entry in bits:
        offset = int(entry["offset"])
        mask = int(entry["mask"])
        if offset < 0 or offset >= len(masked) or mask <= 0 or mask > 0xFF:
            raise ImageError("raw allowlist bit is outside the image")
        masked[offset] &= ~mask & 0xFF
    return hashlib.sha256(masked).hexdigest()


def verify_case_raw(
    before_path: Path,
    fault_path: Path,
    reboot_path: Path,
    operation: str,
    phase: str,
    action: str,
    require_metadata_cow: bool = False,
) -> dict[str, object]:
    """Verify a case from raw images and bind every accepted byte transition."""
    before = read_snapshot(Path(before_path))
    fault = read_snapshot(Path(fault_path))
    reboot = read_snapshot(Path(reboot_path))
    verified = _verify_case_snapshots(
        before, fault, reboot, operation, phase, action
    )
    before_raw = _read_bound_raw(Path(before_path), before)
    fault_raw = _read_bound_raw(Path(fault_path), fault)
    reboot_raw = _read_bound_raw(Path(reboot_path), reboot)
    metadata_stages = _validate_metadata_bank_transitions(
        before,
        fault,
        reboot,
        before_raw,
        fault_raw,
        reboot_raw,
        required=require_metadata_cow,
    )
    ranges, bits = _raw_case_allowlist(
        before, fault, reboot, before_raw, verified
    )
    if operation == "alloc":
        target_block = int(
            dict(verified["expected_manifest"])["first_free_block"]
        )
        committed = bool(
            CASE_EXPECTATIONS[(operation, phase, action)]["alloc_committed"]
        )
        expected_target = (
            bytes((index * 17 + 3) & 0xFF for index in range(BLOCK_SIZE))
            if committed
            else b"\0" * BLOCK_SIZE
        )
        start = target_block * BLOCK_SIZE
        end = start + BLOCK_SIZE
        if fault_raw[start:end] != expected_target or reboot_raw[start:end] != expected_target:
            raise ImageError("alloc target block does not match the exact durable content")
    before_masked = _masked_raw_sha256(before_raw, ranges, bits)
    fault_masked = _masked_raw_sha256(fault_raw, ranges, bits)
    reboot_masked = _masked_raw_sha256(reboot_raw, ranges, bits)
    if fault_masked != before_masked or reboot_masked != before_masked:
        raise ImageError("raw image changed outside exact allocator case bytes")
    verified["raw_exact_manifest"] = {
        "format": "agentos-fs-allocator-raw-mask-v2",
        "allowed_ranges": ranges,
        "allowed_bits": bits,
        "masked_sha256": before_masked,
        "metadata_cow_stages": metadata_stages,
    }
    return verified


def _write_json(value: object, output: Path | None) -> None:
    rendered = json.dumps(value, sort_keys=True, indent=2) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        output.write_bytes(rendered.encode("utf-8"))


def _write_cli_error(code: str, message: str) -> None:
    sys.stderr.write(
        json.dumps(
            {"error": {"code": code, "message": message}},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("image", type=Path)
    snapshot_parser.add_argument("--output", type=Path)
    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)
    diff_parser.add_argument("--output", type=Path)
    diff_parser.add_argument(
        "--expect", type=Path, help="fail unless the complete diff equals this JSON"
    )
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("image", type=Path)
    validate_parser.add_argument("--output", type=Path)
    verify_parsers = (
        subparsers.add_parser("verify-case"),
        subparsers.add_parser("verify-case-raw"),
    )
    for verify_parser in verify_parsers:
        verify_parser.add_argument("before", type=Path)
        verify_parser.add_argument("fault", type=Path)
        verify_parser.add_argument("reboot", type=Path)
        verify_parser.add_argument(
            "--operation", required=True, choices=tuple(SUPPORTED_PHASES)
        )
        verify_parser.add_argument(
            "--phase",
            required=True,
            choices=tuple(sorted({phase for phases in SUPPORTED_PHASES.values() for phase in phases})),
        )
        verify_parser.add_argument("--action", required=True, choices=ACTIONS)
        verify_parser.add_argument("--output", type=Path)
        verify_parser.add_argument(
            "--require-metadata-cow",
            action="store_true",
            help="require the two canonical Agent metadata COW banks",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            value = read_snapshot(args.image)
        elif args.command == "diff":
            value = diff_snapshots(read_snapshot(args.before), read_snapshot(args.after))
        elif args.command == "validate":
            snapshot = read_snapshot(args.image)
            value = {
                "format": CANONICAL_FORMAT,
                "generator": dict(GENERATOR),
                "image": snapshot["image"],
                "state_sha256": snapshot["state_sha256"],
                "qmap_state_counts": snapshot["qmap_state_counts"],
                "qmap_top_state_counts": snapshot["qmap_top_state_counts"],
                "inode_owner_state_counts": snapshot["inode_owner_state_counts"],
                "transitions": [
                    entry
                    for entry in dict(snapshot["qmap_entries"]).values()
                    if entry["state"] in ("ALLOCATING", "FREEING")
                ],
                "inode_transitions": [
                    entry
                    for entry in dict(snapshot["inode_owner_entries"]).values()
                    if entry["state"] in ("ALLOCATING", "FREEING")
                ],
                "violations": snapshot["canonical_violations"],
            }
        else:
            value = verify_case_raw(
                args.before,
                args.fault,
                args.reboot,
                args.operation,
                args.phase,
                args.action,
                require_metadata_cow=args.require_metadata_cow,
            )
        _write_json(value, args.output)
        if args.command == "diff" and args.expect is not None:
            expected = json.loads(args.expect.read_text(encoding="utf-8"))
            if value != expected:
                sys.stderr.write("fs allocator exact diff mismatch\n")
                return 1
        return 1 if args.command == "validate" and (
            value["violations"] or value["transitions"] or value["inode_transitions"]
        ) else 0
    except ImageError as error:
        _write_cli_error("FS_ALLOCATOR_IMAGE_INVALID", str(error))
        return 2
    except OSError as error:
        _write_cli_error(
            "FS_ALLOCATOR_IMAGE_IO",
            error.strerror or error.__class__.__name__,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
