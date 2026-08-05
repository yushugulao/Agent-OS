#!/usr/bin/env python3
"""Strict parser and recovery invariants for Agent metadata COW banks."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_tools import plain_ucore_fs_extract as ucore_fs
from host_tools.agent_metadata_journal import (
    JOURNAL_BYTES,
    JOURNAL_OFFSET,
    JournalError,
    extract_journal,
    materialize_arena,
    materialize_records,
    metadata_record_slot,
    recover_journal,
)


DEFAULT_CONTRACT = ROOT / "ci" / "agent-metadata-disk-format.json"
SUPPORTED_SCHEMA = 1
SUPPORTED_DESCRIPTOR_MAGIC = 0x41474D4449534B31
SUPPORTED_DESCRIPTOR_VERSION = 1
SUPPORTED_HASH = "agent-fnv1a64-v1"
SUPPORTED_HASH_ID = 1
DURABLE_ARENA_MAGIC = 0x4147445552413031
DURABLE_ARENA_VERSION = 1
GENESIS_GENERATION = 1


class ContractError(ValueError):
    pass


class BankError(ValueError):
    pass


class RecoveryInvariantError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _exact_keys(value: dict[str, Any], expected: set[str], where: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ContractError(
            f"{where}: expected keys {sorted(expected)}, got {sorted(actual)}"
        )


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected object")
    return value


def _integer(value: Any, where: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where}: expected integer")
    if value < 0 or (positive and value == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ContractError(f"{where}: expected {qualifier} integer")
    return value


def _u64_hex(value: Any, where: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ContractError(f"{where}: expected 0x-prefixed string")
    try:
        number = int(value, 16)
    except ValueError as error:
        raise ContractError(f"{where}: invalid hexadecimal value") from error
    if number < 0 or number > 0xFFFFFFFFFFFFFFFF:
        raise ContractError(f"{where}: value is outside uint64")
    return number


def _ascii_name(value: Any, width: int, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where}: expected non-empty string")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ContractError(f"{where}: bank name must be ASCII") from error
    if len(encoded) >= width:
        raise ContractError(f"{where}: bank name does not fit its C field")
    return value


def _validate_ranges(
    container_bytes: int,
    fields: dict[str, tuple[int, int]],
    where: str,
) -> None:
    occupied: list[tuple[int, int, str]] = []
    for name, (offset, size) in fields.items():
        if offset < 0 or size <= 0 or offset + size > container_bytes:
            raise ContractError(
                f"{where}.{name}: range {offset}+{size} exceeds {container_bytes}"
            )
        occupied.append((offset, offset + size, name))
    occupied.sort()
    for left, right in zip(occupied, occupied[1:]):
        if left[1] > right[0]:
            raise ContractError(
                f"{where}: overlapping fields {left[2]} and {right[2]}"
            )


@dataclass(frozen=True)
class DiskLayout:
    descriptor_bytes: int
    disk_magic: int
    disk_version: int
    hash_initial: int
    hash_prime: int
    header_bytes: int
    header_integer_bytes: int
    header_offsets: dict[str, int]
    durable_arena_bytes: int
    record_bytes: int
    record_offsets: dict[str, int]
    used_bytes: int
    fid_bytes: int
    physical_name_bytes: int
    status_bytes: int
    max_count: int
    bank_name_bytes: int
    bank_names: tuple[str, ...]


def load_contract(path: Path | str = DEFAULT_CONTRACT) -> DiskLayout:
    contract_path = Path(path)
    try:
        document = json.loads(
            contract_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot load format contract {contract_path}: {error}") from error

    root = _mapping(document, "contract")
    _exact_keys(root, {"schema", "descriptor", "disk"}, "contract")
    if _integer(root["schema"], "schema") != SUPPORTED_SCHEMA:
        raise ContractError(f"unsupported contract schema {root['schema']}")

    descriptor = _mapping(root["descriptor"], "descriptor")
    _exact_keys(descriptor, {"magic", "version", "bytes"}, "descriptor")
    descriptor_magic = _u64_hex(descriptor["magic"], "descriptor.magic")
    descriptor_version = _integer(descriptor["version"], "descriptor.version")
    if descriptor_magic != SUPPORTED_DESCRIPTOR_MAGIC:
        raise ContractError("unsupported layout descriptor magic")
    if descriptor_version != SUPPORTED_DESCRIPTOR_VERSION:
        raise ContractError("unsupported layout descriptor version")
    descriptor_bytes = _integer(descriptor["bytes"], "descriptor.bytes", positive=True)

    disk = _mapping(root["disk"], "disk")
    _exact_keys(
        disk,
        {
            "magic",
            "version",
            "byte_order",
            "bank_name_bytes",
            "bank_names",
            "hash",
            "header",
            "durable_arena_bytes",
            "record",
        },
        "disk",
    )
    if disk["byte_order"] != "little":
        raise ContractError("only little-endian metadata banks are supported")

    hash_contract = _mapping(disk["hash"], "disk.hash")
    _exact_keys(
        hash_contract,
        {"algorithm", "algorithm_id", "initial", "prime"},
        "disk.hash",
    )
    if hash_contract["algorithm"] != SUPPORTED_HASH:
        raise ContractError(f"unsupported metadata hash {hash_contract['algorithm']!r}")
    if _integer(hash_contract["algorithm_id"], "disk.hash.algorithm_id") != SUPPORTED_HASH_ID:
        raise ContractError("unsupported metadata hash algorithm id")
    hash_initial = _u64_hex(hash_contract["initial"], "disk.hash.initial")
    hash_prime = _u64_hex(hash_contract["prime"], "disk.hash.prime")
    if hash_prime == 0 or hash_prime % 2 == 0:
        raise ContractError("disk.hash.prime must be a non-zero odd uint64")

    header = _mapping(disk["header"], "disk.header")
    _exact_keys(
        header,
        {"bytes", "integer_bytes", "fields", "payload_hash_bytes"},
        "disk.header",
    )
    header_bytes = _integer(header["bytes"], "disk.header.bytes", positive=True)
    integer_bytes = _integer(
        header["integer_bytes"], "disk.header.integer_bytes", positive=True
    )
    hash_bytes = _integer(
        header["payload_hash_bytes"],
        "disk.header.payload_hash_bytes",
        positive=True,
    )
    if integer_bytes != 8 or hash_bytes != integer_bytes:
        raise ContractError("metadata header integers must be uint64")
    header_fields = _mapping(header["fields"], "disk.header.fields")
    header_names = {"magic", "version", "count", "generation", "payload_hash"}
    _exact_keys(header_fields, header_names, "disk.header.fields")
    header_offsets = {
        name: _integer(value, f"disk.header.fields.{name}")
        for name, value in header_fields.items()
    }
    _validate_ranges(
        header_bytes,
        {name: (offset, integer_bytes) for name, offset in header_offsets.items()},
        "disk.header",
    )

    record = _mapping(disk["record"], "disk.record")
    _exact_keys(
        record,
        {
            "bytes",
            "fields",
            "used_bytes",
            "fid_bytes",
            "physical_name_bytes",
            "status_bytes",
            "max_count",
        },
        "disk.record",
    )
    record_bytes = _integer(record["bytes"], "disk.record.bytes", positive=True)
    used_bytes = _integer(record["used_bytes"], "disk.record.used_bytes", positive=True)
    fid_bytes = _integer(record["fid_bytes"], "disk.record.fid_bytes", positive=True)
    physical_bytes = _integer(
        record["physical_name_bytes"],
        "disk.record.physical_name_bytes",
        positive=True,
    )
    status_bytes = _integer(
        record["status_bytes"], "disk.record.status_bytes", positive=True
    )
    if used_bytes != 4 or fid_bytes != 4:
        raise ContractError("metadata record used/fid fields must be int32")
    record_fields = _mapping(record["fields"], "disk.record.fields")
    record_names = {"used", "fid", "physical_name", "status"}
    _exact_keys(record_fields, record_names, "disk.record.fields")
    record_offsets = {
        name: _integer(value, f"disk.record.fields.{name}")
        for name, value in record_fields.items()
    }
    _validate_ranges(
        record_bytes,
        {
            "used": (record_offsets["used"], used_bytes),
            "fid": (record_offsets["fid"], fid_bytes),
            "physical_name": (record_offsets["physical_name"], physical_bytes),
            "status": (record_offsets["status"], status_bytes),
        },
        "disk.record",
    )
    max_count = _integer(record["max_count"], "disk.record.max_count", positive=True)
    arena_bytes = _integer(
        disk["durable_arena_bytes"], "disk.durable_arena_bytes", positive=True
    )

    raw_names = disk["bank_names"]
    if not isinstance(raw_names, list) or len(raw_names) != 2:
        raise ContractError("disk.bank_names must contain exactly two COW banks")
    bank_name_bytes = _integer(
        disk["bank_name_bytes"], "disk.bank_name_bytes", positive=True
    )
    bank_names = tuple(
        _ascii_name(value, bank_name_bytes, f"disk.bank_names[{index}]")
        for index, value in enumerate(raw_names)
    )
    if len(set(bank_names)) != len(bank_names):
        raise ContractError("disk.bank_names must be unique")

    return DiskLayout(
        descriptor_bytes=descriptor_bytes,
        disk_magic=_u64_hex(disk["magic"], "disk.magic"),
        disk_version=_integer(disk["version"], "disk.version", positive=True),
        hash_initial=hash_initial,
        hash_prime=hash_prime,
        header_bytes=header_bytes,
        header_integer_bytes=integer_bytes,
        header_offsets=header_offsets,
        durable_arena_bytes=arena_bytes,
        record_bytes=record_bytes,
        record_offsets=record_offsets,
        used_bytes=used_bytes,
        fid_bytes=fid_bytes,
        physical_name_bytes=physical_bytes,
        status_bytes=status_bytes,
        max_count=max_count,
        bank_name_bytes=bank_name_bytes,
        bank_names=bank_names,
    )


def _read_integer(raw: bytes, offset: int, size: int, *, signed: bool = False) -> int:
    return int.from_bytes(raw[offset : offset + size], "little", signed=signed)


def _c_string(raw: bytes, where: str) -> str:
    end = raw.find(b"\0")
    if end < 0:
        raise BankError(f"{where}: unterminated C string")
    try:
        return raw[:end].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise BankError(f"{where}: non-ASCII C string") from error


def _hash_mix(layout: DiskLayout, value: int, number: int) -> int:
    for byte in number.to_bytes(8, "little"):
        value ^= byte
        value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    return value


def payload_hash(layout: DiskLayout, header: dict[str, int], payload: bytes) -> int:
    value = layout.hash_initial
    for field in ("magic", "version", "count", "generation"):
        value = _hash_mix(layout, value, header[field])
    for byte in payload:
        value ^= byte
        value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    return value


def _disk_hash(layout: DiskLayout, payload: bytes) -> int:
    value = layout.hash_initial
    for byte in payload:
        value ^= byte
        value = (value * layout.hash_prime) & 0xFFFFFFFFFFFFFFFF
    return value or 1


def canonical_genesis_store(layout: DiskLayout) -> bytes:
    arena = bytearray(layout.durable_arena_bytes)
    arena[0:8] = DURABLE_ARENA_MAGIC.to_bytes(8, "little")
    arena[8:12] = DURABLE_ARENA_VERSION.to_bytes(4, "little")
    arena[12:16] = layout.durable_arena_bytes.to_bytes(4, "little")
    arena[24:32] = GENESIS_GENERATION.to_bytes(8, "little")
    arena[-8:] = _disk_hash(layout, bytes(arena[:-8])).to_bytes(8, "little")
    header = {
        "magic": layout.disk_magic,
        "version": layout.disk_version,
        "count": 0,
        "generation": GENESIS_GENERATION,
        "payload_hash": 0,
    }
    raw = bytearray(layout.header_bytes)
    for field, value in header.items():
        offset = layout.header_offsets[field]
        raw[offset : offset + layout.header_integer_bytes] = value.to_bytes(
            layout.header_integer_bytes, "little"
        )
    header["payload_hash"] = payload_hash(layout, header, bytes(arena))
    offset = layout.header_offsets["payload_hash"]
    raw[offset : offset + layout.header_integer_bytes] = header[
        "payload_hash"
    ].to_bytes(layout.header_integer_bytes, "little")
    raw.extend(arena)
    return bytes(raw)


def parse_bank(raw: bytes, name: str, layout: DiskLayout) -> dict[str, Any]:
    if len(raw) < layout.header_bytes:
        if not raw or not any(raw):
            return {
                "name": name,
                "state": "uncommitted",
                "bytes": len(raw),
                "header_bytes": layout.header_bytes,
                "store_image": raw,
            }
        raise BankError(f"{name}: partial non-zero header ({len(raw)} bytes)")
    header = {
        field: _read_integer(
            raw, offset, layout.header_integer_bytes, signed=False
        )
        for field, offset in layout.header_offsets.items()
    }
    if all(value == 0 for value in header.values()):
        if any(raw[: layout.header_bytes]):
            raise BankError(f"{name}: invalidated header contains non-zero bytes")
        return {
            "name": name,
            "state": "uncommitted",
            "bytes": len(raw),
            "header_bytes": layout.header_bytes,
            "store_image": raw,
        }
    if header["magic"] != layout.disk_magic:
        raise BankError(f"{name}: unsupported magic 0x{header['magic']:016x}")
    if header["version"] != layout.disk_version:
        raise BankError(f"{name}: unsupported version {header['version']}")
    if header["count"] > layout.max_count or header["generation"] == 0:
        raise BankError(
            f"{name}: invalid count/generation "
            f"{header['count']}/{header['generation']}"
        )

    store_bytes = (
        layout.header_bytes
        + layout.durable_arena_bytes
        + header["count"] * layout.record_bytes
    )
    if len(raw) < store_bytes:
        raise BankError(f"{name}: short bank {len(raw)} < {store_bytes}")
    payload = raw[layout.header_bytes : store_bytes]
    actual_hash = payload_hash(layout, header, payload)
    if actual_hash != header["payload_hash"]:
        raise BankError(
            f"{name}: payload hash mismatch "
            f"{header['payload_hash']:016x}!={actual_hash:016x}"
        )

    statuses: list[str] = []
    record_images: list[bytes] = []
    records_offset = layout.header_bytes + layout.durable_arena_bytes
    for index in range(header["count"]):
        start = records_offset + index * layout.record_bytes
        record_images.append(bytes(raw[start : start + layout.record_bytes]))
        used = _read_integer(
            raw,
            start + layout.record_offsets["used"],
            layout.used_bytes,
            signed=True,
        )
        fid = _read_integer(
            raw,
            start + layout.record_offsets["fid"],
            layout.fid_bytes,
            signed=True,
        )
        if used != 1 or fid <= 0:
            raise BankError(f"{name}: invalid record {index} used={used} fid={fid}")
        physical_start = start + layout.record_offsets["physical_name"]
        physical = _c_string(
            raw[physical_start : physical_start + layout.physical_name_bytes],
            f"{name}: record {index} physical_name",
        )
        if physical == "metafile":
            status_start = start + layout.record_offsets["status"]
            statuses.append(
                _c_string(
                    raw[status_start : status_start + layout.status_bytes],
                    f"{name}: record {index} status",
                )
            )
    if len(statuses) > 1:
        raise BankError(f"{name}: duplicate metafile records {statuses}")
    effective_header = dict(header)
    effective_payload = payload
    journal_summary = {
        "base_generation": header["generation"],
        "transactions": 0,
        "blocks": 0,
        "next_slot": 0,
        "ignored_tail_slot": None,
    }
    if len(raw) >= JOURNAL_OFFSET + JOURNAL_BYTES:
        try:
            recovered = recover_journal(
                extract_journal(raw),
                base_generation=header["generation"],
                base_payload_hash=header["payload_hash"],
            )
            materialized = materialize_records(record_images, recovered)
            materialized_arena = materialize_arena(
                bytes(raw[layout.header_bytes : records_offset]), recovered
            )
        except JournalError as error:
            raise BankError(f"{name}: invalid v8 journal: {error}") from error
        records = sorted(
            materialized.values(),
            key=metadata_record_slot,
        )
        effective_header["count"] = len(records)
        effective_header["generation"] = recovered.last_generation
        effective_payload = (
            materialized_arena
            + b"".join(records)
        )
        effective_header["payload_hash"] = payload_hash(
            layout, effective_header, effective_payload
        )
        journal_summary = {
            "base_generation": recovered.base_generation,
            "transactions": len(recovered.transactions),
            "blocks": sum(transaction.blocks for transaction in recovered.transactions),
            "next_slot": recovered.next_slot,
            "ignored_tail_slot": recovered.ignored_tail_slot,
            "last_commit_hash": f"{recovered.last_commit_hash:016x}",
        }
    logical = bytearray(layout.header_bytes)
    for field, value in effective_header.items():
        offset = layout.header_offsets[field]
        logical[offset : offset + layout.header_integer_bytes] = value.to_bytes(
            layout.header_integer_bytes, "little"
        )
    logical.extend(effective_payload)
    return {
        "name": name,
        "state": "valid",
        "generation": effective_header["generation"],
        "payload_hash": f"{effective_header['payload_hash']:016x}",
        "count": effective_header["count"],
        "header_bytes": layout.header_bytes,
        "metafile_status": statuses[0] if statuses else None,
        "store_image": bytes(logical),
        "journal": journal_summary,
    }


def _bank_images_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        left[field] == right[field]
        for field in ("generation", "payload_hash", "store_image")
    )


def _require_identical_banks(valid: list[dict[str, Any]], where: str) -> None:
    if len(valid) != 2 or not _bank_images_equal(valid[0], valid[1]):
        raise RecoveryInvariantError(f"{where}: banks are not identical")


def _validate_interrupted_bank_set(
    banks: list[dict[str, Any]],
    interrupted_leg: str,
    phase: int,
    reference_updated: dict[str, Any] | None,
) -> None:
    """Validate the exact raw-media state at every COW crash checkpoint.

    The primary leg replaces one bank of a replicated generation-B baseline
    with generation B+1.  The mirror leg then replaces the remaining baseline
    bank with that verified image. Phase 1 is before the epoch can write back.
    Capacity or age pressure may commit a partial, still-invalid target during
    phase 2. Phase 3 is the prepared-image fence, phase 5 only stages the valid
    header, and phases 6..8 follow its fence, verification and commit.
    """
    if len(banks) != 2 or len({bank.get("name") for bank in banks}) != 2:
        raise RecoveryInvariantError(
            "interrupted update requires two distinct metadata banks"
        )

    groups: dict[str, list[dict[str, Any]]] = {
        "baseline": [],
        "updated": [],
        "uncommitted": [],
    }
    for bank in banks:
        state = bank.get("state")
        if state == "uncommitted":
            groups["uncommitted"].append(bank)
            continue
        if state != "valid":
            raise RecoveryInvariantError(
                "exact interrupted checkpoint contains "
                f"{bank.get('name', '<unnamed>')} bank in {state!r} state"
            )
        status = bank.get("metafile_status")
        if status not in {"baseline", "updated"}:
            raise RecoveryInvariantError(
                f"invalid complete metadata value {status!r} in {bank['name']}"
            )
        groups[status].append(bank)

    def counts() -> tuple[int, int, int]:
        return tuple(
            len(groups[name]) for name in ("baseline", "updated", "uncommitted")
        )

    def require_counts(
        expected: tuple[int, int, int], description: str
    ) -> None:
        actual = counts()
        if actual != expected:
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} requires {description}; "
                "got baseline/updated/uncommitted "
                f"{actual[0]}/{actual[1]}/{actual[2]}"
            )

    def require_adjacent() -> None:
        baseline = groups["baseline"][0]
        updated = groups["updated"][0]
        if updated["generation"] != baseline["generation"] + 1:
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} requires adjacent "
                "baseline/updated generations; got "
                f"{baseline['generation']}/{updated['generation']}"
            )

    def require_replicated(status: str) -> None:
        left, right = groups[status]
        if not _bank_images_equal(left, right):
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} requires identical "
                f"{status} target/peer images"
            )

    def expected_updated() -> dict[str, Any] | None:
        if reference_updated is not None:
            if (
                reference_updated.get("state") != "valid"
                or reference_updated.get("metafile_status") != "updated"
            ):
                raise RecoveryInvariantError(
                    "reference metadata bank is not a verified update"
                )
            return reference_updated
        if interrupted_leg == "mirror" and len(groups["updated"]) == 1:
            return groups["updated"][0]
        return None

    def require_prepared_payload() -> None:
        if len(groups["uncommitted"]) != 1:
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} lacks one prepared target"
            )
        expected = expected_updated()
        if expected is None:
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} requires a verified "
                "updated reference"
            )
        if groups["baseline"] and (
            expected.get("generation")
            != groups["baseline"][0].get("generation", -1) + 1
        ):
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} reference generation does "
                "not follow the baseline"
            )
        raw = groups["uncommitted"][0].get("store_image")
        published = expected.get("store_image")
        if not isinstance(raw, bytes) or not isinstance(published, bytes):
            raise RecoveryInvariantError("metadata bank payload bytes are absent")
        header_bytes = expected.get("header_bytes")
        if not isinstance(header_bytes, int) or header_bytes <= 0:
            raise RecoveryInvariantError("metadata header size is absent")
        if len(raw) < len(published) or (
            raw[header_bytes : len(published)]
            != published[header_bytes:]
        ):
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} contains a partial or "
                "foreign prepared payload"
            )

    def require_partial_target() -> None:
        target = groups["uncommitted"][0]
        raw = target.get("store_image")
        header_bytes = target.get("header_bytes")
        if (
            not isinstance(raw, bytes)
            or not isinstance(header_bytes, int)
            or len(raw) < header_bytes
        ):
            raise RecoveryInvariantError(
                f"{interrupted_leg} phase {phase} has a truncated invalid target"
            )

    def require_updated_reference() -> None:
        expected = expected_updated()
        if expected is None:
            return
        for updated in groups["updated"]:
            if not _bank_images_equal(updated, expected):
                raise RecoveryInvariantError(
                    f"{interrupted_leg} phase {phase} published an unexpected "
                    "updated payload"
                )

    if interrupted_leg == "primary":
        if phase == 1:
            require_counts(
                (2, 0, 0), "valid replicated baseline target/peer banks"
            )
            require_replicated("baseline")
        elif phase == 2:
            if counts() == (2, 0, 0):
                require_replicated("baseline")
            elif counts() != (1, 0, 1):
                require_counts(
                    (1, 0, 1),
                    "a baseline peer and a baseline or partial invalid target",
                )
            else:
                require_partial_target()
        elif phase <= 4:
            require_counts(
                (1, 0, 1),
                "a fully prepared uncommitted target and a valid baseline peer",
            )
            require_prepared_payload()
        elif phase == 5:
            require_counts(
                (1, 0, 1),
                "a baseline peer and a fully prepared uncommitted target",
            )
            require_prepared_payload()
        else:
            require_counts(
                (1, 1, 0), "a valid updated target and a valid baseline peer"
            )
            require_adjacent()
            require_updated_reference()
        return

    if phase == 1:
        require_counts(
            (1, 1, 0), "a valid baseline target and verified updated peer"
        )
        require_adjacent()
        require_updated_reference()
    elif phase == 2:
        if counts() == (1, 1, 0):
            require_adjacent()
        elif counts() != (0, 1, 1):
            require_counts(
                (0, 1, 1),
                "an updated peer and a baseline or partial invalid target",
            )
        else:
            require_partial_target()
        require_updated_reference()
    elif phase <= 4:
        require_counts(
            (0, 1, 1),
            "a fully prepared uncommitted target and verified updated peer",
        )
        require_prepared_payload()
        require_updated_reference()
    elif phase == 5:
        require_counts(
            (0, 1, 1),
            "a verified updated peer and fully prepared uncommitted target",
        )
        require_prepared_payload()
        require_updated_reference()
    else:
        require_counts(
            (0, 2, 0), "valid identical updated target/peer banks"
        )
        require_replicated("updated")
        require_updated_reference()


def validate_bank_set(
    banks: list[dict[str, Any]],
    stage: str,
    *,
    interrupted_leg: str = "none",
    phase: int | None = None,
    reference_updated: dict[str, Any] | None = None,
) -> None:
    valid = [bank for bank in banks if bank["state"] == "valid"]
    if not valid:
        raise RecoveryInvariantError(f"no valid metadata bank after {stage}: {banks}")
    for index, left in enumerate(valid):
        for right in valid[index + 1 :]:
            if left["generation"] != right["generation"]:
                continue
            if (
                left["payload_hash"] != right["payload_hash"]
                or left["store_image"] != right["store_image"]
            ):
                raise RecoveryInvariantError(
                    "same-generation metadata fork at generation "
                    f"{left['generation']}"
                )

    if stage == "interrupted-update":
        if interrupted_leg not in {"primary", "mirror"}:
            raise RecoveryInvariantError(
                "interrupted update requires primary/mirror leg"
            )
        if phase is None or phase < 1 or phase > 8:
            raise RecoveryInvariantError("interrupted update requires phase in [1, 8]")
        _validate_interrupted_bank_set(
            banks, interrupted_leg, phase, reference_updated
        )
        return

    if stage == "genesis":
        _require_identical_banks(valid, stage)
        if len(valid) != len(banks) or any(
            bank["count"] != 0
            or bank["generation"] != GENESIS_GENERATION
            or bank["metafile_status"] is not None
            for bank in valid
        ):
            raise RecoveryInvariantError("metadata genesis is not canonical")
        return

    if stage in {"baseline", "recovered"}:
        _require_identical_banks(valid, stage)
        if any(bank["metafile_status"] is not None for bank in valid):
            raise RecoveryInvariantError(f"stale workflow metadata remains in {stage}")
        return

    raise RecoveryInvariantError(f"unknown validation stage {stage!r}")


def _inode_blocks(
    image: bytes, inode: ucore_fs.Dinode, expected_bytes: int
) -> tuple[list[int], int | None]:
    expected_data = (expected_bytes + ucore_fs.BSIZE - 1) // ucore_fs.BSIZE
    direct_count = min(expected_data, ucore_fs.NDIRECT)
    direct = inode.addrs[:direct_count]
    if len(direct) != direct_count or any(block == 0 for block in direct):
        raise RecoveryInvariantError("metadata genesis has a sparse direct mapping")
    if any(inode.addrs[index] for index in range(direct_count, ucore_fs.NDIRECT)):
        raise RecoveryInvariantError("metadata genesis has an extra direct mapping")
    blocks = list(direct)
    indirect_block = inode.addrs[ucore_fs.NDIRECT]
    indirect_count = expected_data - direct_count
    if indirect_count:
        if indirect_block == 0:
            raise RecoveryInvariantError("metadata genesis has no indirect table")
        indirect = ucore_fs.block(image, indirect_block)
        entries = [
            ucore_fs.u32(indirect, index * 4)
            for index in range(ucore_fs.NINDIRECT)
        ]
        if any(block == 0 for block in entries[:indirect_count]) or any(
            entries[indirect_count:]
        ):
            raise RecoveryInvariantError(
                "metadata genesis indirect mapping is sparse or oversized"
            )
        blocks.extend(entries[:indirect_count])
    elif indirect_block:
        raise RecoveryInvariantError("metadata genesis has an extra indirect table")
    return blocks, indirect_block or None


def inspect_genesis_image_data(
    image: bytes, layout: DiskLayout
) -> list[dict[str, Any]]:
    superblock = ucore_fs.read_superblock(image)
    entries_list = ucore_fs.root_entries(image, superblock)
    canonical = canonical_genesis_store(layout)
    snapshot_capacity = (
        layout.header_bytes
        + layout.durable_arena_bytes
        + layout.max_count * layout.record_bytes
    )
    if snapshot_capacity > JOURNAL_OFFSET:
        raise RecoveryInvariantError("metadata snapshot overlaps the v8 journal")
    capacity = JOURNAL_OFFSET + JOURNAL_BYTES
    reports: list[dict[str, Any]] = []
    all_blocks: set[int] = set()
    bank_blocks: list[int] = []

    root_inode = ucore_fs.read_inode(image, superblock, ucore_fs.ROOTINO)
    root_blocks, root_indirect = _inode_blocks(image, root_inode, root_inode.size)
    all_blocks.update(root_blocks)
    if root_indirect is not None:
        all_blocks.add(root_indirect)

    for name in layout.bank_names:
        matches = [inum for inum, entry_name in entries_list if entry_name == name]
        if len(matches) != 1:
            reports.append({"name": name, "state": "absent"})
            continue
        inum = matches[0]
        inode_offset = (
            (inum // superblock.ipb + superblock.inodestart) * ucore_fs.BSIZE
            + (inum % superblock.ipb) * superblock.dinode_size
        )
        inode_raw = image[inode_offset : inode_offset + superblock.dinode_size]
        try:
            label_policy = ucore_fs.validate_vfs_label(
                inode_raw, inum, ucore_fs.u16(inode_raw, 0), superblock.magic
            )
            inode = ucore_fs.read_inode(image, superblock, inum)
        except ValueError as error:
            raise RecoveryInvariantError(
                f"{name}: invalid genesis inode label: {error}"
            ) from error
        if (
            inode.type != ucore_fs.T_FILE
            or inode.size != capacity
            or label_policy != ucore_fs.VFS_POLICY_KERNEL_PRIVATE
            or inode.vfs_policy != label_policy
            or inode.vfs_scope_id != ucore_fs.VFS_SCOPE_NONE
            or inode.fs_owner_domain != ucore_fs.FS_OWNER_SYSTEM
            or inode.fs_owner_version != ucore_fs.FS_OWNER_VERSION
        ):
            raise RecoveryInvariantError(f"{name}: invalid genesis inode policy")
        raw = ucore_fs.read_file(image, inode)
        if len(raw) != capacity or raw[: len(canonical)] != canonical:
            raise RecoveryInvariantError(f"{name}: non-canonical genesis bytes")
        if any(raw[len(canonical) :]):
            raise RecoveryInvariantError(f"{name}: preallocated tail is not zero")
        blocks, indirect = _inode_blocks(image, inode, capacity)
        physical = blocks + ([indirect] if indirect is not None else [])
        if len(set(physical)) != len(physical) or all_blocks.intersection(physical):
            raise RecoveryInvariantError("metadata genesis block mappings alias")
        all_blocks.update(physical)
        bank_blocks.extend(physical)
        reports.append(parse_bank(raw, name, layout))

    validate_bank_set(reports, "genesis")
    if len(bank_blocks) != len(layout.bank_names) * (
        (capacity + ucore_fs.BSIZE - 1) // ucore_fs.BSIZE + 1
    ):
        raise RecoveryInvariantError("metadata genesis allocation count mismatch")
    if superblock.qmapstart is None or superblock.datastart is None:
        raise RecoveryInvariantError("metadata genesis requires quota filesystem")
    for blockno in bank_blocks:
        if not superblock.datastart <= blockno < superblock.size:
            raise RecoveryInvariantError("metadata genesis block is outside data arena")
        bitmap = ucore_fs.block(
            image, superblock.bmapstart + blockno // (ucore_fs.BSIZE * 8)
        )
        if not bitmap[(blockno % (ucore_fs.BSIZE * 8)) // 8] & (
            1 << (blockno % 8)
        ):
            raise RecoveryInvariantError("metadata genesis block is free in bitmap")
        owner_offset = (
            superblock.qmapstart + blockno // ucore_fs.QPB
        ) * ucore_fs.BSIZE + (blockno % ucore_fs.QPB) * 4
        if ucore_fs.u32(image, owner_offset) != ucore_fs.FS_OWNER_SYSTEM:
            raise RecoveryInvariantError("metadata genesis block is not SYSTEM-owned")

    allocated_data = 0
    for blockno in range(superblock.datastart, superblock.size):
        bitmap = ucore_fs.block(
            image, superblock.bmapstart + blockno // (ucore_fs.BSIZE * 8)
        )
        allocated_data += bool(
            bitmap[(blockno % (ucore_fs.BSIZE * 8)) // 8]
            & (1 << (blockno % 8))
        )
    used_inodes = sum(
        ucore_fs.u16(
            image,
            (inum // superblock.ipb + superblock.inodestart) * ucore_fs.BSIZE
            + (inum % superblock.ipb) * superblock.dinode_size,
        )
        != 0
        for inum in range(1, superblock.ninodes)
    )
    free_blocks = superblock.nblocks - allocated_data
    free_inodes = superblock.ninodes - 1 - used_inodes
    assert superblock.system_block_reserve is not None
    assert superblock.system_inode_reserve is not None
    assert superblock.workflow_block_guarantee is not None
    assert superblock.workflow_inode_guarantee is not None
    if (
        free_blocks < superblock.system_block_reserve
        or free_inodes < superblock.system_inode_reserve
        or (free_blocks - superblock.system_block_reserve)
        // ucore_fs.FS_WORKFLOW_SCOPE_SLOTS
        < superblock.workflow_block_guarantee
        or (free_inodes - superblock.system_inode_reserve)
        // ucore_fs.FS_WORKFLOW_SCOPE_SLOTS
        < superblock.workflow_inode_guarantee
    ):
        raise RecoveryInvariantError("metadata genesis consumed promised reserves")
    return reports


def _read_image_banks(
    image_path: Path, layout: DiskLayout
) -> list[dict[str, Any]]:
    image = image_path.read_bytes()
    superblock = ucore_fs.read_superblock(image)
    entries = {name: inum for inum, name in ucore_fs.root_entries(image, superblock)}
    banks: list[dict[str, Any]] = []
    for name in layout.bank_names:
        inum = entries.get(name)
        if inum is None:
            banks.append({"name": name, "state": "absent"})
            continue
        inode = ucore_fs.read_inode(image, superblock, inum)
        raw = ucore_fs.read_file(image, inode)
        try:
            banks.append(parse_bank(raw, name, layout))
        except BankError as error:
            banks.append({"name": name, "state": "corrupt", "error": str(error)})
    return banks


def inspect_image(
    image_path: Path,
    layout: DiskLayout,
    stage: str,
    *,
    interrupted_leg: str = "none",
    phase: int | None = None,
    reference_image_path: Path | None = None,
) -> list[dict[str, Any]]:
    if stage == "genesis":
        if reference_image_path is not None:
            raise RecoveryInvariantError(
                "reference image is only valid for an interrupted update"
            )
        return inspect_genesis_image_data(image_path.read_bytes(), layout)
    if reference_image_path is not None and stage != "interrupted-update":
        raise RecoveryInvariantError(
            "reference image is only valid for an interrupted update"
        )
    banks = _read_image_banks(image_path, layout)
    reference_updated = None
    if reference_image_path is not None:
        reference = _read_image_banks(reference_image_path, layout)
        _validate_interrupted_bank_set(reference, "primary", 6, None)
        updated = [
            bank
            for bank in reference
            if bank.get("state") == "valid"
            and bank.get("metafile_status") == "updated"
        ]
        if len(updated) != 1:
            raise RecoveryInvariantError(
                "reference image must contain exactly one verified update"
            )
        reference_updated = updated[0]
    validate_bank_set(
        banks,
        stage,
        interrupted_leg=interrupted_leg,
        phase=phase,
        reference_updated=reference_updated,
    )
    return banks


def _serializable_bank(bank: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bank.items() if key != "store_image"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--stage",
        choices=("genesis", "interrupted-update", "baseline", "recovered"),
        required=True,
    )
    parser.add_argument("--interrupted-leg", choices=("none", "primary", "mirror"), default="none")
    parser.add_argument("--phase", type=int)
    parser.add_argument("--reference-image", type=Path)
    args = parser.parse_args()

    try:
        layout = load_contract(args.contract)
        banks = inspect_image(
            args.image,
            layout,
            args.stage,
            interrupted_leg=args.interrupted_leg,
            phase=args.phase,
            reference_image_path=args.reference_image,
        )
    except (ContractError, BankError, RecoveryInvariantError, OSError, ValueError) as error:
        parser.error(str(error))
    print(
        "metadata_bank_check: "
        + json.dumps(
            {
                "stage": args.stage,
                "interrupted_leg": args.interrupted_leg,
                "phase": args.phase,
                "banks": [_serializable_bank(bank) for bank in banks],
                "at_least_one_valid": True,
                "same_generation_fork": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
