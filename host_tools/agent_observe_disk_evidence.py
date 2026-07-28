#!/usr/bin/env python3
"""Independently verify a durable observation checkpoint in a uCore image."""

from __future__ import annotations

from pathlib import Path
from typing import Any

if __package__:
    from . import plain_ucore_fs_extract as ucore_fs
    from . import agent_observe_disk_acceptance as _acceptance
    from .agent_observe_disk_contract import (
        DEFAULT_OBSERVE_CONTRACT,
        ObservationEvidenceError,
        ObservationLayout,
        load_observation_contract,
    )
    from .agent_metadata_disk_format import (
        BankError,
        ContractError as MetadataContractError,
        load_contract as load_metadata_contract,
        parse_bank,
    )
else:
    import plain_ucore_fs_extract as ucore_fs
    import agent_observe_disk_acceptance as _acceptance
    from agent_observe_disk_contract import (
        DEFAULT_OBSERVE_CONTRACT,
        ObservationEvidenceError,
        ObservationLayout,
        load_observation_contract,
    )
    from agent_metadata_disk_format import (
        BankError,
        ContractError as MetadataContractError,
        load_contract as load_metadata_contract,
        parse_bank,
    )

DEFAULT_METADATA_CONTRACT = _acceptance.DEFAULT_METADATA_CONTRACT
IDENTITY_MARKER = _acceptance.IDENTITY_MARKER
parse_boot1_identity = _acceptance.parse_boot1_identity
validate_observation_acceptance = _acceptance.validate_observation_acceptance
verify_observation_acceptance = _acceptance.verify_observation_acceptance
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def _u(raw: bytes, offset: int, size: int) -> int:
    if offset < 0 or size <= 0 or offset + size > len(raw):
        raise ObservationEvidenceError("disk field lies outside its containing image")
    return int.from_bytes(raw[offset : offset + size], "little", signed=False)


def _s(raw: bytes, offset: int, size: int) -> int:
    if offset < 0 or size <= 0 or offset + size > len(raw):
        raise ObservationEvidenceError("disk field lies outside its containing image")
    return int.from_bytes(raw[offset : offset + size], "little", signed=True)


_RECORD_SIGNED_INT_FIELDS = frozenset(
    {
        "kind",
        "pid",
        "tid",
        "source_pid",
        "target_pid",
        "agent_id",
        "role",
        "loop_state",
        "tool_id",
        "event_type",
        "status",
    }
)


def _fnv(layout: ObservationLayout, raw: bytes) -> int:
    value = layout.hash_initial
    for byte in raw:
        value ^= byte
        value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    return value or 1


def _record_value(raw: bytes, layout: ObservationLayout, name: str) -> int:
    offset = layout.record_fields[name]
    if name == "workflow_lifecycle_id":
        return _u(raw, offset, layout.uint_bytes)
    if name in _RECORD_SIGNED_INT_FIELDS:
        return _s(raw, offset, layout.int_bytes)
    return _u(raw, offset, layout.uint64_bytes)


def _record_hash(raw: bytes, layout: ObservationLayout) -> int:
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
        number = _record_value(raw, layout, name)
        if name in _RECORD_SIGNED_INT_FIELDS:
            number &= (1 << (layout.int_bytes * 8)) - 1
        else:
            number &= 0xFFFFFFFFFFFFFFFF
        for byte in number.to_bytes(8, "little"):
            value ^= byte
            value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    start = layout.record_fields["text"]
    for byte in raw[start : start + layout.text_bytes]:
        value ^= byte
        value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    return value or 1


def _parse_observation(raw: bytes, layout: ObservationLayout, identity: dict[str, int]) -> dict[str, Any]:
    if len(raw) != layout.observe_bytes:
        raise ObservationEvidenceError("observation section byte count differs")
    f = layout.observe_fields
    if (
        _u(raw, f["magic"], 8) != layout.observe_magic
        or _u(raw, f["version"], 4) != layout.observe_version
        or _u(raw, f["bytes"], 4) != layout.observe_bytes
    ):
        raise ObservationEvidenceError("observation checkpoint header differs")
    generation = _u(raw, f["generation"], 8)
    scope_count = _u(raw, f["scope_count"], 4)
    exhausted = _u(raw, f["allocator_exhausted"], 4)
    if (
        generation == 0
        or _u(raw, f["retention_policy"], 4) != layout.retention_policy
        or _u(raw, f["reserved_scope_slots"], 4) != layout.reserved_scope_slots
        or _u(raw, f["reserved"], 4) != 0
        or scope_count > layout.scope_slots
        or exhausted & ~layout.allocator_exhausted_all
        or _u(raw, f["image_hash"], 8) != _fnv(layout, raw[: f["image_hash"]])
    ):
        raise ObservationEvidenceError("observation checkpoint header is corrupt")

    lease_names = ("audit_lease_end", "span_lease_end", "event_lease_end", "control_lease_end", "agent_lease_end")
    leases = {name: _u(raw, f[name], 4 if name == "agent_lease_end" else 8) for name in lease_names}
    for bit, name in enumerate(lease_names):
        if (leases[name] == 0) != bool(exhausted & (1 << bit)):
            raise ObservationEvidenceError("observation allocator exhaustion mask differs")
    lifecycle_ends = [
        _u(raw, f["lifecycle_lease_ends"] + index * 8, 8)
        for index in range(layout.lifecycle_cap)
    ]

    used_count = 0
    seen_lifecycles: set[tuple[int, int]] = set()
    seen_receipts: set[int] = set()
    seen_sequences: set[int] = set()
    max_sequence = max_span = max_event = max_control = max_agent = 0
    max_lifecycle = [0] * layout.lifecycle_cap
    identity_matches: list[dict[str, int]] = []
    aggregate_total_records = 0
    aggregate_record_count = 0
    aggregate_admission_drops = 0
    matched_scope: dict[str, Any] | None = None
    flags = layout.scope_flags
    for slot in range(layout.scope_slots):
        scope_start = f["scopes"] + slot * layout.scope_bytes
        scope_raw = raw[scope_start : scope_start + layout.scope_bytes]
        sf = layout.scope_fields
        used = _u(scope_raw, sf["used"], 4)
        if used == 0:
            if any(scope_raw):
                raise ObservationEvidenceError(f"unused observation scope {slot} is non-zero")
            continue
        used_count += 1
        scope_id = _u(scope_raw, sf["scope_id"], 4)
        lifecycle_id = _u(scope_raw, sf["lifecycle_id"], 4)
        record_count = _u(scope_raw, sf["record_count"], 4)
        lifecycle_generation = _u(scope_raw, sf["lifecycle_generation"], 8)
        total_records = _u(scope_raw, sf["total_records"], 8)
        admission_drops = _u(scope_raw, sf["admission_drops"], 8)
        ledger_hash = _u(scope_raw, sf["ledger_hash"], 8)
        recovery_flag = bool(used & flags["recovery_successor"])
        key = (lifecycle_id, lifecycle_generation)
        if (
            not (used & flags["used"])
            or used & ~flags["all"]
            or recovery_flag != (slot == layout.recovery_scope_slot)
            or not (layout.first_dynamic_scope <= scope_id < layout.owner_scope_flag)
            or not (1 <= lifecycle_id <= layout.lifecycle_cap)
            or lifecycle_generation == 0
            or not (0 <= record_count <= layout.records_per_scope)
            or total_records < record_count
            or admission_drops > total_records - record_count
            or (
                record_count == 0
                and (
                    total_records == 0
                    or admission_drops != total_records
                    or ledger_hash != 0
                )
            )
            or (
                record_count != 0
                and (
                    total_records - admission_drops < record_count
                    or ledger_hash == 0
                )
            )
            or key in seen_lifecycles
        ):
            raise ObservationEvidenceError(f"observation scope {slot} is invalid")
        successful_records = total_records - admission_drops
        hashed_omitted = successful_records - record_count
        aggregate_total_records += total_records
        aggregate_record_count += record_count
        aggregate_admission_drops += admission_drops
        seen_lifecycles.add(key)
        max_lifecycle[lifecycle_id - 1] = max(
            max_lifecycle[lifecycle_id - 1], lifecycle_generation
        )
        prior_hash = prior_sequence = 0
        first_prev_hash = 0
        chain_gap = False
        tail_start = max(0, record_count - layout.latest_tail)
        identity_scope = (
            scope_id == identity["scope"]
            and lifecycle_id == identity["lifecycle_id"]
            and lifecycle_generation == identity["lifecycle_generation"]
        )
        tail_count = anchor_count = 0
        anchor_identity_classes: set[int] = set()
        anchor_kinds: set[int] = set()
        for index in range(layout.records_per_scope):
            entry_start = sf["records"] + index * layout.entry_bytes
            entry = scope_raw[entry_start : entry_start + layout.entry_bytes]
            if index >= record_count:
                if any(entry):
                    raise ObservationEvidenceError(
                        f"unused observation entry {slot}/{index} is non-zero"
                    )
                continue
            ef = layout.entry_fields
            record_start = ef["record"]
            record = entry[record_start : record_start + layout.record_bytes]
            entry_scope = _u(entry, ef["scope_id"], 4)
            identity_class = _u(
                entry, ef["identity_class"], layout.identity_class_bytes
            )
            link_flags = _u(entry, ef["link_flags"], layout.link_flags_bytes)
            reserved = entry[ef["reserved"] : ef["reserved"] + layout.reserved_bytes]
            principal = _u(entry, ef["principal"], 8)
            span_owner = _u(entry, ef["span_owner"], 8)
            receipt_id = _u(entry, ef["receipt_id"], 8)
            sequence = _record_value(record, layout, "sequence")
            span_id = _record_value(record, layout, "span_id")
            record_hash = _record_value(record, layout, "record_hash")
            prev_hash = _record_value(record, layout, "prev_hash")
            record_lifecycle_id = _record_value(record, layout, "workflow_lifecycle_id")
            record_lifecycle_generation = _record_value(
                record, layout, "workflow_lifecycle_generation"
            )
            kind = _record_value(record, layout, "kind")
            agent_id = _record_value(record, layout, "agent_id")
            actor_control = _record_value(record, layout, "actor_control_id")
            direct = index != 0 and prev_hash == prior_hash
            latest_tail = index >= tail_start
            if (
                entry_scope != scope_id
                or identity_class not in layout.identity_classes.values()
                or link_flags & ~layout.link_flags["all"]
                or bool(link_flags & layout.link_flags["latest_tail"])
                != latest_tail
                or (
                    index == 0
                    and bool(link_flags & layout.link_flags["prev_retained"])
                )
                or (
                    index != 0
                    and bool(link_flags & layout.link_flags["prev_retained"])
                    != direct
                )
                or (index != 0 and prev_hash == 0)
                or any(reserved)
                or principal == 0
                or ((span_id == 0) != (span_owner == 0))
                or (
                    identity_class == layout.identity_classes["causal"]
                    and (span_id == 0 or span_owner == 0)
                )
                or (
                    identity_class == layout.identity_classes["authority"]
                    and (actor_control == 0 or principal != actor_control)
                )
                or receipt_id == 0
                or receipt_id in seen_receipts
                or sequence == 0
                or sequence in seen_sequences
                or sequence <= prior_sequence
                or record_lifecycle_id != lifecycle_id
                or record_lifecycle_generation != lifecycle_generation
                or record_hash != _record_hash(record, layout)
                or not (0 <= kind <= layout.audit_kind_max)
                or not (0 <= agent_id <= layout.agent_id_max)
            ):
                raise ObservationEvidenceError(
                    f"observation record {slot}/{index} is invalid"
                )
            if identity_scope:
                if latest_tail:
                    tail_count += 1
                else:
                    anchor_count += 1
                    anchor_identity_classes.add(identity_class)
                    anchor_kinds.add(kind)
            if (index == 0 and prev_hash != 0) or (index != 0 and not direct):
                chain_gap = True
            if index == 0:
                first_prev_hash = prev_hash
            seen_receipts.add(receipt_id)
            seen_sequences.add(sequence)
            prior_hash, prior_sequence = record_hash, sequence
            cause_control = _record_value(record, layout, "cause_control_id")
            max_sequence = max(max_sequence, sequence)
            max_span = max(max_span, span_id)
            max_control = max(
                max_control, actor_control, cause_control, principal, span_owner
            )
            max_agent = max(max_agent, agent_id)
            if kind in layout.event_kinds:
                max_event = max(max_event, _record_value(record, layout, "value0"))
            if (
                scope_id == identity["scope"]
                and lifecycle_id == identity["lifecycle_id"]
                and lifecycle_generation == identity["lifecycle_generation"]
                and sequence == identity["receipt_sequence"]
                and record_hash == identity["receipt_record_hash"]
                and receipt_id == identity["receipt_id"]
                and agent_id == identity["agent_id"]
            ):
                identity_matches.append(
                    {
                        "slot": slot,
                        "record_index": index,
                        "scope": scope_id,
                        "lifecycle_id": lifecycle_id,
                        "lifecycle_generation": lifecycle_generation,
                        "agent_id": agent_id,
                        "identity_class": identity_class,
                        "link_flags": link_flags,
                        "principal": principal,
                        "span_id": span_id,
                        "span_owner": span_owner,
                        "receipt_sequence": sequence,
                        "receipt_record_hash": record_hash,
                        "receipt_id": receipt_id,
                    }
                )
        if ledger_hash != prior_hash:
            raise ObservationEvidenceError(f"observation scope {slot} ledger hash differs")
        if record_count != 0 and (
            (hashed_omitted == 0 and (chain_gap or first_prev_hash != 0))
            or (hashed_omitted != 0 and not chain_gap)
        ):
            raise ObservationEvidenceError(
                f"observation scope {slot} retention chain differs"
            )
        if identity_scope:
            if matched_scope is not None:
                raise ObservationEvidenceError(
                    "boot1 durable identity selects multiple checkpoint scopes"
                )
            matched_scope = {
                "slot": slot,
                "scope": scope_id,
                "lifecycle_id": lifecycle_id,
                "lifecycle_generation": lifecycle_generation,
                "record_count": record_count,
                "total_records": total_records,
                "admission_drops": admission_drops,
                "successful_records": successful_records,
                "omitted_successful_records": hashed_omitted,
                "retained_tail_count": tail_count,
                "retained_anchor_count": anchor_count,
                "anchor_identity_classes": sorted(
                    name
                    for name, value in layout.identity_classes.items()
                    if value in anchor_identity_classes
                ),
                "anchor_kinds": sorted(anchor_kinds),
            }
    if used_count != scope_count:
        raise ObservationEvidenceError("observation scope count differs")
    if any(end != 0 and end <= maximum for end, maximum in zip(lifecycle_ends, max_lifecycle)):
        raise ObservationEvidenceError("observation lifecycle lease does not exceed stored identity")
    maxima = (max_sequence, max_span, max_event, max_control, max_agent)
    if any(end != 0 and end <= maximum for end, maximum in zip(leases.values(), maxima)):
        raise ObservationEvidenceError("observation allocator lease does not exceed stored identity")
    if leases["agent_lease_end"] > layout.agent_id_max:
        raise ObservationEvidenceError("observation Agent lease exceeds signed identity range")
    if len(identity_matches) != 1:
        raise ObservationEvidenceError(
            "boot1 durable identity does not select exactly one checkpoint record"
        )
    if matched_scope is None:
        raise ObservationEvidenceError(
            "boot1 durable identity does not select a checkpoint scope"
        )
    return {
        "generation": generation,
        "image_hash": f"{_u(raw, f['image_hash'], 8):016x}",
        "scope_count": scope_count,
        "total_records": aggregate_total_records,
        "record_count": aggregate_record_count,
        "admission_drops": aggregate_admission_drops,
        "dropped_records": aggregate_total_records - aggregate_record_count,
        "matched_scope": matched_scope,
        "identity": identity_matches[0],
    }


def _parse_arena(raw: bytes, layout: ObservationLayout, identity: dict[str, int]) -> dict[str, Any]:
    if len(raw) != layout.arena_bytes:
        raise ObservationEvidenceError("durable arena byte count differs")
    f = layout.arena_fields
    section_count = _u(raw, f["section_count"], 4)
    used_bytes = _u(raw, f["used_bytes"], 4)
    generation = _u(raw, f["generation"], 8)
    if (
        _u(raw, f["magic"], 8) != layout.arena_magic
        or _u(raw, f["version"], 4) != layout.arena_version
        or _u(raw, f["bytes"], 4) != layout.arena_bytes
        or section_count != 1
        or section_count > layout.arena_section_max
        or used_bytes != layout.observe_bytes
        or used_bytes > layout.arena_payload_bytes
        or generation == 0
        or _u(raw, f["image_hash"], 8) != _fnv(layout, raw[: f["image_hash"]])
    ):
        raise ObservationEvidenceError("durable arena header is corrupt")
    desc_start = f["sections"]
    desc = raw[desc_start : desc_start + layout.descriptor_bytes]
    df = layout.descriptor_fields
    kind = _u(desc, df["kind"], 4)
    version = _u(desc, df["version"], 4)
    offset = _u(desc, df["offset"], 4)
    section_bytes = _u(desc, df["bytes"], 4)
    section_generation = _u(desc, df["generation"], 8)
    payload_hash = _u(desc, df["payload_hash"], 8)
    if (
        kind != layout.section_kind
        or version != layout.observe_version
        or offset != 0
        or section_bytes != layout.observe_bytes
        or section_generation == 0
    ):
        raise ObservationEvidenceError("observation durable section descriptor differs")
    payload_start = f["payload"] + offset
    payload = raw[payload_start : payload_start + section_bytes]
    if payload_hash != _fnv(layout, payload):
        raise ObservationEvidenceError("observation durable section payload hash differs")
    observation = _parse_observation(payload, layout, identity)
    if section_generation != observation["generation"]:
        raise ObservationEvidenceError("section and observation generations differ")
    return {
        "generation": generation,
        "image_hash": f"{_u(raw, f['image_hash'], 8):016x}",
        "section_generation": section_generation,
        "section_payload_hash": f"{payload_hash:016x}",
        "observation": observation,
    }


def _inode_storage_blocks(image: bytes, sb: ucore_fs.Superblock, inode: ucore_fs.Dinode) -> frozenset[int]:
    remaining, owned = inode.size, []
    if remaining <= 0 or remaining > (ucore_fs.NDIRECT + ucore_fs.NINDIRECT) * ucore_fs.BSIZE:
        raise ObservationEvidenceError("metadata bank inode size is outside filesystem policy")
    for blockno in inode.addrs[:ucore_fs.NDIRECT]:
        if remaining <= 0:
            break
        if blockno == 0:
            raise ObservationEvidenceError("metadata bank inode is sparse")
        owned.append(blockno)
        remaining -= min(remaining, ucore_fs.BSIZE)
    if remaining > 0:
        table = inode.addrs[ucore_fs.NDIRECT]
        if table == 0:
            raise ObservationEvidenceError("metadata bank indirect table is missing")
        owned.append(table)
        raw = ucore_fs.block(image, table)
        for index in range(ucore_fs.NINDIRECT):
            if remaining <= 0:
                break
            blockno = ucore_fs.u32(raw, index * 4)
            if blockno == 0:
                raise ObservationEvidenceError("metadata bank inode is sparse")
            owned.append(blockno)
            remaining -= min(remaining, ucore_fs.BSIZE)
    if remaining > 0 or len(set(owned)) != len(owned) or any(
        blockno < (sb.datastart or 0) or blockno >= sb.size for blockno in owned
    ):
        raise ObservationEvidenceError("metadata bank block ownership is invalid")
    return frozenset(owned)


def verify_observation_image(
    image_path: Path | str,
    guest_log: str | bytes,
    metadata_contract: Path | str = DEFAULT_METADATA_CONTRACT,
    observation_contract: Path | str = DEFAULT_OBSERVE_CONTRACT,
) -> dict[str, Any]:
    image_path = Path(image_path)
    if image_path.is_symlink() or not image_path.is_file():
        raise ObservationEvidenceError("observation image is missing or unsafe")
    size = image_path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ObservationEvidenceError("observation image size is outside policy")
    try:
        image = image_path.read_bytes()
        metadata_layout = load_metadata_contract(metadata_contract)
        layout = load_observation_contract(observation_contract)
        superblock = ucore_fs.read_superblock(image)
        entries = ucore_fs.root_entries(image, superblock)
    except (OSError, ValueError, BankError, MetadataContractError) as error:
        raise ObservationEvidenceError(f"cannot parse observation image: {error}") from error
    if superblock.magic != ucore_fs.FSMAGIC_AGENT_PRINCIPAL:
        raise ObservationEvidenceError("observation evidence is not a current AgentOS filesystem")
    identity = parse_boot1_identity(guest_log)
    if not (
        layout.first_dynamic_scope <= identity["scope"] < layout.owner_scope_flag
        and 1 <= identity["lifecycle_id"] <= layout.lifecycle_cap
        and identity["agent_id"] <= layout.agent_id_max
    ):
        raise ObservationEvidenceError("boot1 durable identity is outside disk policy")

    banks: list[dict[str, Any]] = []
    bank_inums: list[int] = []
    bank_blocks: list[frozenset[int]] = []
    for name in metadata_layout.bank_names:
        matches = [inum for inum, entry_name in entries if entry_name == name]
        if len(matches) != 1:
            raise ObservationEvidenceError(
                f"metadata bank {name} has {len(matches)} root entries"
            )
        try:
            inum = matches[0]
            inode = ucore_fs.read_inode(image, superblock, inum)
            if (
                inode.type != ucore_fs.T_FILE
                or inode.vfs_policy != ucore_fs.VFS_POLICY_KERNEL_PRIVATE
                or inode.fs_owner_domain != ucore_fs.FS_OWNER_SYSTEM
            ):
                raise ObservationEvidenceError(
                    f"metadata bank {name} is not a SYSTEM kernel-private file"
                )
            bank = parse_bank(ucore_fs.read_file(image, inode), name, metadata_layout)
        except (ValueError, BankError) as error:
            raise ObservationEvidenceError(f"metadata bank {name} is invalid: {error}") from error
        if bank.get("state") != "valid":
            raise ObservationEvidenceError(f"metadata bank {name} is not committed")
        banks.append(bank)
        bank_inums.append(inum)
        bank_blocks.append(_inode_storage_blocks(image, superblock, inode))
    if len(set(bank_inums)) != len(bank_inums) or bank_blocks[0] & bank_blocks[1]:
        raise ObservationEvidenceError("replicated metadata banks share physical storage")
    if (
        len(banks) != 2
        or banks[0]["generation"] != banks[1]["generation"]
        or banks[0]["payload_hash"] != banks[1]["payload_hash"]
        or banks[0]["store_image"] != banks[1]["store_image"]
    ):
        raise ObservationEvidenceError("replicated metadata banks are not identical")

    arena_start = metadata_layout.header_bytes
    arena_end = arena_start + metadata_layout.durable_arena_bytes
    arena = _parse_arena(banks[0]["store_image"][arena_start:arena_end], layout, identity)
    return {
        "schema": 1,
        "filesystem_magic": f"0x{superblock.magic:08x}",
        "bank_names": list(metadata_layout.bank_names),
        "bank_generation": banks[0]["generation"],
        "bank_payload_hash": banks[0]["payload_hash"],
        "arena": arena,
        "status": "verified",
    }


def main() -> int:
    return _acceptance.main(verify_observation_image)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_METADATA_CONTRACT",
    "DEFAULT_OBSERVE_CONTRACT",
    "IDENTITY_MARKER",
    "ObservationEvidenceError",
    "ObservationLayout",
    "load_observation_contract",
    "parse_boot1_identity",
    "validate_observation_acceptance",
    "verify_observation_acceptance",
    "verify_observation_image",
]
