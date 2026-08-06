#!/usr/bin/env python3
"""Construct a small, format-valid uCore observation image for Host tests."""

from __future__ import annotations

from pathlib import Path

if __package__:
    from . import plain_ucore_fs_extract as fs
    from .agent_metadata_disk_format import load_contract as load_metadata_contract
    from .agent_metadata_disk_format import payload_hash as metadata_payload_hash
    from .agent_observe_disk_evidence import (
        DEFAULT_METADATA_CONTRACT,
        DEFAULT_OBSERVE_CONTRACT,
        _fnv,
        _record_hash,
        load_observation_contract,
    )
else:
    import plain_ucore_fs_extract as fs
    from agent_metadata_disk_format import load_contract as load_metadata_contract
    from agent_metadata_disk_format import payload_hash as metadata_payload_hash
    from agent_observe_disk_evidence import (
        DEFAULT_METADATA_CONTRACT,
        DEFAULT_OBSERVE_CONTRACT,
        _fnv,
        _record_hash,
        load_observation_contract,
    )


FIXTURE_IDENTITY = {
    "scope": 3,
    "lifecycle_id": 1,
    "lifecycle_generation": 10,
    "agent_id": 7,
    "receipt_sequence": 100,
    "receipt_id": 9001,
}


def _put(raw: bytearray, offset: int, size: int, value: int, *, signed: bool = False) -> None:
    raw[offset : offset + size] = int(value).to_bytes(size, "little", signed=signed)


def _label(
    image: bytearray, inum: int, file_type: int, policy: int, incarnation: int
) -> None:
    offset = (inum // (fs.BSIZE // fs.DINODE_SIZE_EXEC_POLICY) + 2) * fs.BSIZE
    offset += (inum % (fs.BSIZE // fs.DINODE_SIZE_EXEC_POLICY)) * fs.DINODE_SIZE_EXEC_POLICY
    if policy == fs.VFS_POLICY_ROOT:
        flags = fs.VFS_LABEL_F_ROOT
    elif policy == fs.VFS_POLICY_KERNEL_PRIVATE:
        flags = fs.VFS_LABEL_F_KERNEL_PRIVATE
    else:
        raise AssertionError("fixture only creates root and kernel-private files")
    words = [
        fs.VFS_LABEL_MAGIC,
        fs.VFS_LABEL_VERSION,
        flags,
        fs.VFS_SCOPE_NONE,
        policy,
        fs.VFS_EXEC_PROFILE_NONE,
        fs.VFS_POLICY_GENERATION,
        incarnation,
        fs.FS_OWNER_SYSTEM,
        fs.FS_OWNER_VERSION,
    ]
    for index, value in enumerate(words):
        _put(image, offset + 84 + index * 4, 4, value)
    _put(image, offset + 124, 4, fs.vfs_label_checksum(inum, words))


def _inode(
    image: bytearray,
    inum: int,
    file_type: int,
    size: int,
    blocks: list[int],
    policy: int,
) -> None:
    ipb = fs.BSIZE // fs.DINODE_SIZE_EXEC_POLICY
    offset = (inum // ipb + 2) * fs.BSIZE + (inum % ipb) * fs.DINODE_SIZE_EXEC_POLICY
    _put(image, offset, 2, file_type)
    _put(image, offset + 8, 4, size)
    for index, block in enumerate(blocks):
        _put(image, offset + 12 + index * 4, 4, block)
    _label(image, inum, file_type, policy, inum)


def _write_file(image: bytearray, blocks: list[int], payload: bytes) -> None:
    for index, block in enumerate(blocks):
        chunk = payload[index * fs.BSIZE : (index + 1) * fs.BSIZE]
        image[block * fs.BSIZE : block * fs.BSIZE + len(chunk)] = chunk


def _build_observation(layout, full_acceptance: bool) -> tuple[bytes, dict[str, int]]:
    identity = dict(FIXTURE_IDENTITY)
    if full_acceptance and (
        layout.records_per_scope, layout.latest_tail, layout.diversity_anchors
    ) != (6, 4, 2):
        raise AssertionError("fixture requires the Observation v8 6/4/2 geometry")
    raw = bytearray(layout.observe_bytes)
    f = layout.observe_fields
    _put(raw, f["magic"], 8, layout.observe_magic)
    _put(raw, f["version"], 4, layout.observe_version)
    _put(raw, f["bytes"], 4, layout.observe_bytes)
    _put(raw, f["generation"], 8, 5)
    for name, value in (
        ("audit_lease_end", 1000),
        ("span_lease_end", 1000),
        ("event_lease_end", 1000),
        ("control_lease_end", 1000),
    ):
        _put(raw, f[name], 8, value)
    _put(raw, f["agent_lease_end"], 4, 100)
    _put(raw, f["retention_policy"], 4, layout.retention_policy)
    _put(raw, f["scope_count"], 4, 1)
    _put(raw, f["allocator_exhausted"], 4, 0)
    _put(raw, f["reserved_scope_slots"], 4, layout.reserved_scope_slots)
    _put(raw, f["reserved"], 4, 0)
    _put(raw, f["lifecycle_lease_ends"], 8, 100)

    scope_start = f["scopes"]
    scope = memoryview(raw)[scope_start : scope_start + layout.scope_bytes]
    sf = layout.scope_fields
    _put(scope, sf["used"], 4, layout.scope_flags["used"])
    _put(scope, sf["scope_id"], 4, identity["scope"])
    _put(scope, sf["lifecycle_id"], 4, identity["lifecycle_id"])
    record_count = layout.records_per_scope if full_acceptance else 1
    _put(scope, sf["record_count"], 4, record_count)
    _put(scope, sf["lifecycle_generation"], 8, identity["lifecycle_generation"])
    _put(scope, sf["total_records"], 8, 12 if full_acceptance else 1)
    _put(scope, sf["admission_drops"], 8, 0)

    classes = (
        ("causal", "authority", "telemetry", "causal",
         "telemetry", "authority")
        if full_acceptance else ("authority",)
    )
    prior_hash = 0xA11CE if full_acceptance else 0
    ef, rf = layout.entry_fields, layout.record_fields
    for index, class_name in enumerate(classes):
        entry_start = sf["records"] + index * layout.entry_bytes
        entry = scope[entry_start : entry_start + layout.entry_bytes]
        record = entry[ef["record"] : ef["record"] + layout.record_bytes]
        sequence = identity["receipt_sequence"] - len(classes) + index + 1
        actor_control = 300 + index
        span_id = 200 + index if class_name == "causal" or not full_acceptance else 0
        kind = (layout.event_kinds[0] + index) % (layout.audit_kind_max + 1)
        values = {
            "sequence": sequence, "tick": index + 1, "cause_sequence": 0,
            "span_id": span_id,
            "workflow_lifecycle_generation": identity["lifecycle_generation"],
            "branch_generation": index + 1, "cause_branch_generation": 0,
            "actor_control_id": actor_control, "cause_control_id": 0,
            "cause_record_hash": 0, "prev_hash": prior_hash, "record_hash": 0,
            "value0": 400 + index, "value1": 0, "value2": 0, "flags": 0,
        }
        for name, value in values.items():
            _put(record, rf[name], 8, value)
        integers = {
            "kind": kind, "pid": 2, "tid": 2, "source_pid": 0,
            "target_pid": 2, "agent_id": identity["agent_id"], "role": 1,
            "loop_state": 0, "tool_id": 1, "event_type": 1, "status": 0,
        }
        _put(record, rf["workflow_lifecycle_id"], 4, identity["lifecycle_id"])
        for name, value in integers.items():
            _put(record, rf[name], 4, value, signed=True)
        text = f"fixture-observation-{index}".encode("ascii")
        record[rf["text"] : rf["text"] + len(text)] = text
        record_hash = _record_hash(bytes(record), layout)
        _put(record, rf["record_hash"], 8, record_hash)
        link_flags = layout.link_flags["prev_retained"] if index else 0
        if index >= record_count - layout.latest_tail:
            link_flags |= layout.link_flags["latest_tail"]
        _put(entry, ef["scope_id"], 4, identity["scope"])
        _put(entry, ef["identity_class"], layout.identity_class_bytes,
             layout.identity_classes[class_name])
        _put(entry, ef["link_flags"], layout.link_flags_bytes, link_flags)
        _put(entry, ef["principal"], 8, actor_control)
        _put(entry, ef["span_owner"], 8, identity["agent_id"] if span_id else 0)
        _put(entry, ef["receipt_id"], 8, identity["receipt_id"] - len(classes) + index + 1)
        prior_hash = record_hash
        if sequence == identity["receipt_sequence"]:
            identity["receipt_record_hash"] = record_hash
    _put(scope, sf["ledger_hash"], 8, prior_hash)
    _put(raw, f["image_hash"], 8, _fnv(layout, bytes(raw[: f["image_hash"]])))
    return bytes(raw), identity


def _build_arena(layout, observation: bytes) -> bytes:
    raw = bytearray(layout.arena_bytes)
    f = layout.arena_fields
    _put(raw, f["magic"], 8, layout.arena_magic)
    _put(raw, f["version"], 4, layout.arena_version)
    _put(raw, f["bytes"], 4, layout.arena_bytes)
    _put(raw, f["section_count"], 4, 1)
    _put(raw, f["used_bytes"], 4, len(observation))
    _put(raw, f["generation"], 8, 9)
    desc = memoryview(raw)[f["sections"] : f["sections"] + layout.descriptor_bytes]
    df = layout.descriptor_fields
    _put(desc, df["kind"], 4, layout.section_kind)
    _put(desc, df["version"], 4, layout.observe_version)
    _put(desc, df["offset"], 4, 0)
    _put(desc, df["bytes"], 4, len(observation))
    _put(desc, df["generation"], 8, 5)
    _put(desc, df["payload_hash"], 8, _fnv(layout, observation))
    raw[f["payload"] : f["payload"] + len(observation)] = observation
    _put(raw, f["image_hash"], 8, _fnv(layout, bytes(raw[: f["image_hash"]])))
    return bytes(raw)


def build_fixture(
    metadata_contract: Path | str = DEFAULT_METADATA_CONTRACT,
    observation_contract: Path | str = DEFAULT_OBSERVE_CONTRACT,
    *,
    full_acceptance: bool = False,
) -> tuple[bytes, str]:
    metadata = load_metadata_contract(metadata_contract)
    layout = load_observation_contract(observation_contract)
    observation, identity = _build_observation(layout, full_acceptance)
    arena = _build_arena(layout, observation)
    header = {
        "magic": metadata.disk_magic,
        "version": metadata.disk_version,
        "count": 0,
        "generation": 20,
        "payload_hash": 0,
    }
    header["payload_hash"] = metadata_payload_hash(metadata, header, arena)
    bank_header = bytearray(metadata.header_bytes)
    for name, offset in metadata.header_offsets.items():
        _put(bank_header, offset, metadata.header_integer_bytes, header[name])
    bank = bytes(bank_header) + arena

    size, ninodes = 256, 64
    inode_blocks = (ninodes + fs.BSIZE // fs.DINODE_SIZE_EXEC_POLICY - 1) // (
        fs.BSIZE // fs.DINODE_SIZE_EXEC_POLICY
    )
    bmapstart = 2 + inode_blocks
    bitmap_blocks = (size + fs.BSIZE * 8 - 1) // (fs.BSIZE * 8)
    qmapstart = bmapstart + bitmap_blocks
    owner_blocks = (size + fs.QPB - 1) // fs.QPB
    datastart = qmapstart + owner_blocks
    image = bytearray(size * fs.BSIZE)
    sb = fs.BSIZE
    for index, value in enumerate(
        (
            fs.FSMAGIC_AGENT_PRINCIPAL,
            size,
            size - datastart,
            ninodes,
            2,
            bmapstart,
            qmapstart,
            datastart,
            fs.FS_STORAGE_POLICY_VERSION,
            fs.FS_WORKFLOW_SCOPE_SLOTS,
            8,
            8,
            4,
            4,
            fs.FS_PUBLIC_PRINCIPAL_ID,
            fs.storage_policy_checksum(
                fs.FS_STORAGE_POLICY_VERSION,
                fs.FS_WORKFLOW_SCOPE_SLOTS,
                fs.FS_PUBLIC_PRINCIPAL_ID,
                8,
                8,
                4,
                4,
            ),
        )
    ):
        _put(image, sb + index * 4, 4, value)

    root_block = datastart
    bank_block_count = (len(bank) + fs.BSIZE - 1) // fs.BSIZE
    bank0_blocks = list(range(root_block + 1, root_block + 1 + bank_block_count))
    bank1_blocks = list(range(bank0_blocks[-1] + 1, bank0_blocks[-1] + 1 + bank_block_count))
    root = bytearray(fs.BSIZE)
    for slot, (inum, name) in enumerate(
        ((1, "."), (1, ".."), (2, metadata.bank_names[0]), (3, metadata.bank_names[1]))
    ):
        offset = slot * 16
        _put(root, offset, 2, inum)
        encoded = name.encode("ascii")
        root[offset + 2 : offset + 2 + len(encoded)] = encoded
    image[root_block * fs.BSIZE : (root_block + 1) * fs.BSIZE] = root
    _inode(image, 1, 1, 64, [root_block], fs.VFS_POLICY_ROOT)
    _inode(image, 2, fs.T_FILE, len(bank), bank0_blocks, fs.VFS_POLICY_KERNEL_PRIVATE)
    _inode(image, 3, fs.T_FILE, len(bank), bank1_blocks, fs.VFS_POLICY_KERNEL_PRIVATE)
    _write_file(image, bank0_blocks, bank)
    _write_file(image, bank1_blocks, bank)

    for block in range(bank1_blocks[-1] + 1):
        image[bmapstart * fs.BSIZE + block // 8] |= 1 << (block % 8)
        _put(image, qmapstart * fs.BSIZE + block * 4, 4, fs.FS_OWNER_SYSTEM)

    marker = (
        "agentobsreboot_ucore: boot1_durable_identity "
        f"scope={identity['scope']} lifecycle_id={identity['lifecycle_id']} "
        f"lifecycle_generation={identity['lifecycle_generation']} "
        f"agent_id={identity['agent_id']} "
        f"receipt_sequence={identity['receipt_sequence']} "
        f"receipt_record_hash={identity['receipt_record_hash']} "
        f"receipt_id={identity['receipt_id']}"
    )
    return bytes(image), marker


def write_fixture(image_path: Path | str, *, full_acceptance: bool = True) -> str:
    image, marker = build_fixture(full_acceptance=full_acceptance)
    Path(image_path).write_bytes(image)
    return marker


__all__ = ["FIXTURE_IDENTITY", "build_fixture", "write_fixture"]
