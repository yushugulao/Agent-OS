#!/usr/bin/env python3
"""不可变 Agent 元数据 v8 增量日志的恢复模型。

日志位于 metadata bank 的块对齐尾部，只允许追加完整块。恢复仅接纳连续的
已提交事务前缀，并把末尾未提交段视为断电残尾；残尾之后若再有有效事务，
则判为完整性错误。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


FNV1A64_INITIAL = 1469598103934665603
FNV1A64_PRIME = 1099511628211
U64_MASK = (1 << 64) - 1

STORE_VERSION_V7 = 7
STORE_VERSION_V8 = 8
JOURNAL_MAGIC = 0x41474D4A4E4C3038
JOURNAL_VERSION = 1
JOURNAL_OFFSET = 222208
JOURNAL_BLOCK_BYTES = 1024
JOURNAL_BLOCKS = 32
JOURNAL_BYTES = JOURNAL_BLOCK_BYTES * JOURNAL_BLOCKS
SLOT_BYTES = 512
SLOT_HEADER_BYTES = 96
SLOT_PAYLOAD_BYTES = SLOT_BYTES - SLOT_HEADER_BYTES
ARENA_BYTES = 8192
ARENA_PATCH_DATA_BYTES = 400
METADATA_RECORD_SLOT_OFFSET = 396
SLOTS_PER_BLOCK = JOURNAL_BLOCK_BYTES // SLOT_BYTES
SLOT_COUNT = JOURNAL_BYTES // SLOT_BYTES
MAX_DATA_SLOTS = 15
MAX_TRANSACTION_BLOCKS = 8

KIND_DATA = 1
KIND_COMMIT = 2
KIND_PAD = 3
OP_NONE = 0
OP_UPSERT = 1
OP_DELETE = 2
OP_ARENA_PATCH = 3

# 字段顺序和宽度属于 v8 磁盘 ABI。
SLOT_HEADER = struct.Struct("<QIIQQIIQIIIIQQQQ")
assert SLOT_HEADER.size == SLOT_HEADER_BYTES
ARENA_PATCH = struct.Struct(f"<IIQ{ARENA_PATCH_DATA_BYTES}s")
assert ARENA_PATCH.size == SLOT_PAYLOAD_BYTES


class JournalError(ValueError):
    """v8 日志错误基类。"""


class JournalFormatError(JournalError):
    """槽位或事务违反固定磁盘格式。"""


class JournalIntegrityError(JournalError):
    """只追加的代际/哈希链无法安全恢复。"""


class JournalCapacityError(JournalError):
    """固定日志已容不下新事务。"""


class JournalRewriteError(JournalError):
    """模型写入器试图修改物理日志块。"""


class JournalDowngradeError(JournalError):
    """有效 v8 介质出现后又发现 v7 头。"""


@dataclass(frozen=True)
class JournalIdentity:
    scope_id: int
    lifecycle_id: int
    lifecycle_generation: int

    def validate(self) -> None:
        if not 0 < self.scope_id <= 0xFFFFFFFF:
            raise JournalFormatError("scope_id must be a non-zero uint32")
        if not 0 < self.lifecycle_id <= 0xFFFFFFFF:
            raise JournalFormatError("lifecycle_id must be a non-zero uint32")
        if not 0 < self.lifecycle_generation <= U64_MASK:
            raise JournalFormatError(
                "lifecycle_generation must be a non-zero uint64"
            )


@dataclass(frozen=True)
class JournalDelta:
    operation: int
    payload: bytes

    def validate(self) -> None:
        if self.operation not in (OP_UPSERT, OP_DELETE, OP_ARENA_PATCH):
            raise JournalFormatError(f"unsupported DATA operation {self.operation}")
        if len(self.payload) != SLOT_PAYLOAD_BYTES:
            raise JournalFormatError(
                f"DATA payload must be exactly {SLOT_PAYLOAD_BYTES} bytes"
            )
        if self.operation == OP_ARENA_PATCH:
            decode_arena_patch(self.payload)


@dataclass(frozen=True)
class JournalSlot:
    kind: int
    base_generation: int
    generation: int
    identity: JournalIdentity
    record_index: int
    record_count: int
    operation: int
    payload_bytes: int
    previous_commit_hash: int
    payload_hash: int
    group_hash: int
    slot_hash: int
    payload: bytes


@dataclass(frozen=True)
class JournalTransaction:
    base_generation: int
    generation: int
    identity: JournalIdentity
    previous_commit_hash: int
    group_hash: int
    commit_hash: int
    deltas: tuple[JournalDelta, ...]
    start_slot: int
    end_slot: int

    @property
    def blocks(self) -> int:
        return (self.end_slot - self.start_slot) // SLOTS_PER_BLOCK


@dataclass(frozen=True)
class JournalRecovery:
    base_generation: int
    base_payload_hash: int
    base_chain_hash: int
    last_generation: int
    last_commit_hash: int
    transactions: tuple[JournalTransaction, ...]
    next_slot: int
    ignored_tail_slot: int | None


@dataclass(frozen=True)
class VersionedRecovery:
    mode: str
    journal: JournalRecovery | None


def _u64(value: int, where: str, *, nonzero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalFormatError(f"{where} must be an integer")
    if value < 0 or value > U64_MASK or (nonzero and value == 0):
        qualifier = "non-zero " if nonzero else ""
        raise JournalFormatError(f"{where} must be a {qualifier}uint64")
    return value


def _fnv1a64_raw(data: bytes, initial: int = FNV1A64_INITIAL) -> int:
    value = _u64(initial, "hash initial")
    for byte in data:
        value ^= byte
        value = (value * FNV1A64_PRIME) & U64_MASK
    return value


def fnv1a64(data: bytes, initial: int = FNV1A64_INITIAL) -> int:
    return _fnv1a64_raw(data, initial) or 1


def hash_mix(value: int, number: int) -> int:
    return _fnv1a64_raw(
        _u64(number, "hash input").to_bytes(8, "little"), value
    )


def base_chain_hash(base_generation: int, base_payload_hash: int) -> int:
    value = FNV1A64_INITIAL
    for number in (
        JOURNAL_MAGIC,
        JOURNAL_VERSION,
        _u64(base_generation, "base_generation", nonzero=True),
        _u64(base_payload_hash, "base_payload_hash", nonzero=True),
    ):
        value = hash_mix(value, number)
    return value or 1


def transaction_group_hash(
    previous_commit_hash: int,
    base_generation: int,
    generation: int,
    identity: JournalIdentity,
    data_slot_hashes: Iterable[int],
) -> int:
    identity.validate()
    hashes = tuple(data_slot_hashes)
    value = _u64(previous_commit_hash, "previous_commit_hash", nonzero=True)
    for number in (
        base_generation,
        generation,
        identity.scope_id,
        identity.lifecycle_id,
        identity.lifecycle_generation,
        len(hashes),
        *hashes,
    ):
        value = hash_mix(value, number)
    return value or 1


def _pack_slot(
    *,
    kind: int,
    base_generation: int,
    generation: int,
    identity: JournalIdentity,
    record_index: int,
    record_count: int,
    operation: int,
    payload_bytes: int,
    previous_commit_hash: int,
    payload: bytes,
    group_hash: int,
) -> bytes:
    identity.validate()
    if len(payload) != SLOT_PAYLOAD_BYTES:
        raise JournalFormatError("slot payload has the wrong physical size")
    if not 0 <= payload_bytes <= SLOT_PAYLOAD_BYTES:
        raise JournalFormatError("payload_bytes is outside the slot payload")
    payload_digest = fnv1a64(payload)
    values = (
        JOURNAL_MAGIC,
        JOURNAL_VERSION,
        kind,
        _u64(base_generation, "base_generation", nonzero=True),
        _u64(generation, "generation", nonzero=True),
        identity.scope_id,
        identity.lifecycle_id,
        identity.lifecycle_generation,
        record_index,
        record_count,
        operation,
        payload_bytes,
        _u64(previous_commit_hash, "previous_commit_hash", nonzero=True),
        payload_digest,
        _u64(group_hash, "group_hash"),
        0,
    )
    raw = bytearray(SLOT_HEADER.pack(*values) + payload)
    slot_digest = fnv1a64(bytes(raw))
    struct.pack_into("<Q", raw, 88, slot_digest)
    return bytes(raw)


def encode_transaction(
    *,
    base_generation: int,
    generation: int,
    identity: JournalIdentity,
    previous_commit_hash: int,
    deltas: Sequence[JournalDelta],
) -> tuple[bytes, int]:
    """编码块对齐的不可变事务及其提交哈希。"""

    identity.validate()
    if not 1 <= len(deltas) <= MAX_DATA_SLOTS:
        raise JournalFormatError(
            f"a transaction requires 1..{MAX_DATA_SLOTS} DATA slots"
        )
    if generation <= base_generation:
        raise JournalFormatError("generation must follow the base generation")
    for delta in deltas:
        delta.validate()

    data_slots: list[bytes] = []
    for index, delta in enumerate(deltas):
        data_slots.append(
            _pack_slot(
                kind=KIND_DATA,
                base_generation=base_generation,
                generation=generation,
                identity=identity,
                record_index=index,
                record_count=len(deltas),
                operation=delta.operation,
                payload_bytes=SLOT_PAYLOAD_BYTES,
                previous_commit_hash=previous_commit_hash,
                payload=delta.payload,
                group_hash=0,
            )
        )
    data_hashes = tuple(struct.unpack_from("<Q", raw, 88)[0] for raw in data_slots)
    group_digest = transaction_group_hash(
        previous_commit_hash,
        base_generation,
        generation,
        identity,
        data_hashes,
    )
    zero_payload = bytes(SLOT_PAYLOAD_BYTES)
    commit = _pack_slot(
        kind=KIND_COMMIT,
        base_generation=base_generation,
        generation=generation,
        identity=identity,
        record_index=len(deltas),
        record_count=len(deltas),
        operation=OP_NONE,
        payload_bytes=0,
        previous_commit_hash=previous_commit_hash,
        payload=zero_payload,
        group_hash=group_digest,
    )
    commit_hash = struct.unpack_from("<Q", commit, 88)[0]
    slots = data_slots + [commit]
    if len(slots) % SLOTS_PER_BLOCK:
        slots.append(
            _pack_slot(
                kind=KIND_PAD,
                base_generation=base_generation,
                generation=generation,
                identity=identity,
                record_index=len(deltas) + 1,
                record_count=len(deltas),
                operation=OP_NONE,
                payload_bytes=0,
                previous_commit_hash=commit_hash,
                payload=zero_payload,
                group_hash=group_digest,
            )
        )
    raw = b"".join(slots)
    blocks = len(raw) // JOURNAL_BLOCK_BYTES
    if not 1 <= blocks <= MAX_TRANSACTION_BLOCKS:
        raise AssertionError("transaction block geometry escaped its ABI bounds")
    return raw, commit_hash


def _decode_slot(raw: bytes) -> JournalSlot:
    if len(raw) != SLOT_BYTES:
        raise JournalFormatError("short journal slot")
    if not any(raw):
        raise JournalFormatError("empty journal slot")
    values = SLOT_HEADER.unpack(raw[:SLOT_HEADER_BYTES])
    (
        magic,
        version,
        kind,
        base_generation,
        generation,
        scope_id,
        lifecycle_id,
        lifecycle_generation,
        record_index,
        record_count,
        operation,
        payload_bytes,
        previous_commit_hash,
        payload_digest,
        group_digest,
        slot_digest,
    ) = values
    if magic != JOURNAL_MAGIC or version != JOURNAL_VERSION:
        raise JournalFormatError("unsupported journal slot magic/version")
    if kind not in (KIND_DATA, KIND_COMMIT, KIND_PAD):
        raise JournalFormatError(f"unsupported journal slot kind {kind}")
    identity = JournalIdentity(scope_id, lifecycle_id, lifecycle_generation)
    identity.validate()
    if base_generation == 0 or generation == 0 or previous_commit_hash == 0:
        raise JournalFormatError("journal generation/hash identity is zero")
    if payload_bytes > SLOT_PAYLOAD_BYTES:
        raise JournalFormatError("slot payload_bytes exceeds its fixed payload")
    payload = raw[SLOT_HEADER_BYTES:]
    if payload_digest != fnv1a64(payload):
        raise JournalFormatError("journal payload hash mismatch")
    checked = bytearray(raw)
    checked[88:96] = bytes(8)
    if slot_digest == 0 or slot_digest != fnv1a64(bytes(checked)):
        raise JournalFormatError("journal slot hash mismatch")
    return JournalSlot(
        kind=kind,
        base_generation=base_generation,
        generation=generation,
        identity=identity,
        record_index=record_index,
        record_count=record_count,
        operation=operation,
        payload_bytes=payload_bytes,
        previous_commit_hash=previous_commit_hash,
        payload_hash=payload_digest,
        group_hash=group_digest,
        slot_hash=slot_digest,
        payload=payload,
    )


def _same_transaction(left: JournalSlot, right: JournalSlot) -> bool:
    return (
        left.base_generation == right.base_generation
        and left.generation == right.generation
        and left.identity == right.identity
        and left.record_count == right.record_count
    )


def _decode_transaction_at(
    slots: Sequence[bytes], start: int
) -> tuple[JournalTransaction, int]:
    if start % SLOTS_PER_BLOCK:
        raise JournalFormatError("transaction does not start on a block boundary")
    if start >= len(slots):
        raise JournalFormatError("transaction starts beyond the journal")
    first = _decode_slot(slots[start])
    if first.kind != KIND_DATA or first.record_index != 0:
        raise JournalFormatError("transaction must start with DATA index zero")
    count = first.record_count
    if not 1 <= count <= MAX_DATA_SLOTS:
        raise JournalFormatError("transaction DATA count is outside 1..15")
    physical_slots = count + 1 + (1 if count % 2 == 0 else 0)
    if start + physical_slots > len(slots):
        raise JournalFormatError("transaction extends beyond the journal")

    decoded_data: list[JournalSlot] = []
    deltas: list[JournalDelta] = []
    for index in range(count):
        slot = _decode_slot(slots[start + index])
        if (
            slot.kind != KIND_DATA
            or slot.record_index != index
            or not _same_transaction(first, slot)
            or slot.previous_commit_hash != first.previous_commit_hash
            or slot.group_hash != 0
            or slot.payload_bytes != SLOT_PAYLOAD_BYTES
            or slot.operation not in (OP_UPSERT, OP_DELETE, OP_ARENA_PATCH)
        ):
            raise JournalFormatError("inconsistent DATA slot in transaction")
        decoded_data.append(slot)
        deltas.append(JournalDelta(slot.operation, slot.payload))

    commit = _decode_slot(slots[start + count])
    expected_group = transaction_group_hash(
        first.previous_commit_hash,
        first.base_generation,
        first.generation,
        first.identity,
        (slot.slot_hash for slot in decoded_data),
    )
    if (
        commit.kind != KIND_COMMIT
        or commit.record_index != count
        or not _same_transaction(first, commit)
        or commit.previous_commit_hash != first.previous_commit_hash
        or commit.operation != OP_NONE
        or commit.payload_bytes != 0
        or any(commit.payload)
        or commit.group_hash != expected_group
    ):
        raise JournalFormatError("invalid transaction COMMIT")

    if count % 2 == 0:
        pad = _decode_slot(slots[start + count + 1])
        if (
            pad.kind != KIND_PAD
            or pad.record_index != count + 1
            or not _same_transaction(first, pad)
            or pad.previous_commit_hash != commit.slot_hash
            or pad.operation != OP_NONE
            or pad.payload_bytes != 0
            or any(pad.payload)
            or pad.group_hash != expected_group
        ):
            raise JournalFormatError("invalid transaction PAD")

    end = start + physical_slots
    return (
        JournalTransaction(
            base_generation=first.base_generation,
            generation=first.generation,
            identity=first.identity,
            previous_commit_hash=first.previous_commit_hash,
            group_hash=expected_group,
            commit_hash=commit.slot_hash,
            deltas=tuple(deltas),
            start_slot=start,
            end_slot=end,
        ),
        end,
    )


def _later_complete_transaction(
    slots: Sequence[bytes], start: int, last_generation: int
) -> JournalTransaction | None:
    for candidate in range(start + SLOTS_PER_BLOCK, len(slots), SLOTS_PER_BLOCK):
        try:
            transaction, _ = _decode_transaction_at(slots, candidate)
        except JournalFormatError:
            continue
        if transaction.generation > last_generation:
            return transaction
    return None


def recover_journal(
    raw: bytes, *, base_generation: int, base_payload_hash: int
) -> JournalRecovery:
    """恢复最长的可信已提交事务前缀。

    末尾畸形事务仅在其后没有可完整验证的更高代事务时才视为断电残尾。
    完整事务组若破坏基线、代际或前序哈希链，即使位于末尾也闭锁失败。
    """

    if len(raw) != JOURNAL_BYTES:
        raise JournalFormatError(
            f"journal must be exactly {JOURNAL_BYTES} bytes, got {len(raw)}"
        )
    base_generation = _u64(base_generation, "base_generation", nonzero=True)
    base_payload_hash = _u64(
        base_payload_hash, "base_payload_hash", nonzero=True
    )
    slots = tuple(
        raw[offset : offset + SLOT_BYTES]
        for offset in range(0, JOURNAL_BYTES, SLOT_BYTES)
    )
    expected_generation = base_generation + 1
    if expected_generation > U64_MASK:
        raise JournalIntegrityError("base generation cannot be advanced")
    chain = base_chain_hash(base_generation, base_payload_hash)
    transactions: list[JournalTransaction] = []
    cursor = 0
    ignored_tail: int | None = None

    while cursor < SLOT_COUNT:
        if not any(slots[cursor]):
            if any(any(slot) for slot in slots[cursor + 1 :]):
                later = _later_complete_transaction(
                    slots, cursor, expected_generation - 1
                )
                if later is not None:
                    raise JournalIntegrityError(
                        "valid transaction appears after an uncommitted journal gap"
                    )
                ignored_tail = cursor
            break
        try:
            first = _decode_slot(slots[cursor])
        except JournalFormatError:
            later = _later_complete_transaction(
                slots, cursor, expected_generation - 1
            )
            if later is not None:
                raise JournalIntegrityError(
                    "valid transaction appears after a torn journal tail"
                )
            ignored_tail = cursor
            break
        if first.kind != KIND_DATA or first.record_index != 0:
            raise JournalIntegrityError(
                "valid non-DATA slot appears at a transaction boundary"
            )
        if (
            first.base_generation != base_generation
            or first.generation != expected_generation
            or first.previous_commit_hash != chain
        ):
            raise JournalIntegrityError(
                "journal base/generation/previous-commit chain is discontinuous"
            )
        try:
            transaction, next_cursor = _decode_transaction_at(slots, cursor)
        except JournalFormatError:
            later = _later_complete_transaction(
                slots, cursor, expected_generation - 1
            )
            if later is not None:
                raise JournalIntegrityError(
                    "valid transaction follows an incomplete transaction"
                )
            ignored_tail = cursor
            break
        transactions.append(transaction)
        chain = transaction.commit_hash
        expected_generation += 1
        cursor = next_cursor

    return JournalRecovery(
        base_generation=base_generation,
        base_payload_hash=base_payload_hash,
        base_chain_hash=base_chain_hash(base_generation, base_payload_hash),
        last_generation=expected_generation - 1,
        last_commit_hash=chain,
        transactions=tuple(transactions),
        next_slot=cursor,
        ignored_tail_slot=ignored_tail,
    )


def contains_valid_v8_slot(raw: bytes) -> bool:
    """判断介质是否含校验和有效的 v8 槽位。"""

    if len(raw) != JOURNAL_BYTES:
        raise JournalFormatError(
            f"journal must be exactly {JOURNAL_BYTES} bytes, got {len(raw)}"
        )
    for offset in range(0, len(raw), SLOT_BYTES):
        try:
            _decode_slot(raw[offset : offset + SLOT_BYTES])
        except JournalFormatError:
            continue
        return True
    return False


def recover_versioned_store(
    store_version: int,
    journal_raw: bytes,
    *,
    base_generation: int,
    base_payload_hash: int,
) -> VersionedRecovery:
    """应用 v7 到 v8 的单向迁移及防降级规则。"""

    if store_version == STORE_VERSION_V7:
        if contains_valid_v8_slot(journal_raw):
            raise JournalDowngradeError(
                "valid v8 journal media forbids recovery through a v7 header"
            )
        return VersionedRecovery(mode="migrate-v7-to-v8", journal=None)
    if store_version != STORE_VERSION_V8:
        raise JournalFormatError(f"unsupported metadata store version {store_version}")
    return VersionedRecovery(
        mode="recover-v8",
        journal=recover_journal(
            journal_raw,
            base_generation=base_generation,
            base_payload_hash=base_payload_hash,
        ),
    )


def extract_journal(bank_image: bytes) -> bytes:
    end = JOURNAL_OFFSET + JOURNAL_BYTES
    if len(bank_image) < end:
        raise JournalFormatError(f"metadata bank is shorter than v8 tail end {end}")
    return bank_image[JOURNAL_OFFSET:end]


def metadata_record_fid(payload: bytes) -> int:
    """提取 416 字节元数据记录的稳定 fid。"""

    if len(payload) != SLOT_PAYLOAD_BYTES:
        raise JournalFormatError("metadata record has the wrong size")
    used, fid = struct.unpack_from("<ii", payload, 0)
    if used != 1 or fid <= 0:
        raise JournalFormatError(f"invalid metadata record identity {used}/{fid}")
    return fid


def metadata_record_slot(payload: bytes) -> int:
    """提取规范化物化排序所用的目录槽位。"""

    if len(payload) != SLOT_PAYLOAD_BYTES:
        raise JournalFormatError("metadata record has the wrong size")
    slot = int.from_bytes(
        payload[METADATA_RECORD_SLOT_OFFSET : METADATA_RECORD_SLOT_OFFSET + 4],
        "little",
    )
    if slot >= 512:
        raise JournalFormatError(f"metadata record slot {slot} is out of range")
    return slot


def encode_arena_patch(base_arena: bytes, next_arena: bytes, offset: int) -> bytes:
    """编码带基线保护的规范持久区窗口。"""

    if len(base_arena) != ARENA_BYTES or len(next_arena) != ARENA_BYTES:
        raise JournalFormatError(f"durable arena must be exactly {ARENA_BYTES} bytes")
    if not 0 <= offset < ARENA_BYTES or offset % ARENA_PATCH_DATA_BYTES:
        raise JournalFormatError("arena patch offset is not a canonical window")
    count = min(ARENA_PATCH_DATA_BYTES, ARENA_BYTES - offset)
    before = base_arena[offset : offset + count]
    after = next_arena[offset : offset + count]
    padded = after + bytes(ARENA_PATCH_DATA_BYTES - count)
    return ARENA_PATCH.pack(offset, count, fnv1a64(before), padded)


def decode_arena_patch(payload: bytes) -> tuple[int, int, int, bytes]:
    """解码并校验规范持久区窗口。"""

    if len(payload) != SLOT_PAYLOAD_BYTES:
        raise JournalFormatError("arena patch has the wrong physical size")
    offset, count, before_hash, data = ARENA_PATCH.unpack(payload)
    if not 0 <= offset < ARENA_BYTES or offset % ARENA_PATCH_DATA_BYTES:
        raise JournalFormatError("arena patch offset is not a canonical window")
    expected = min(ARENA_PATCH_DATA_BYTES, ARENA_BYTES - offset)
    if count != expected or before_hash == 0 or any(data[count:]):
        raise JournalFormatError("arena patch geometry/hash padding is invalid")
    return offset, count, before_hash, data[:count]


def materialize_records(
    base_records: Iterable[bytes] | Mapping[int, bytes],
    recovery: JournalRecovery,
) -> dict[int, bytes]:
    """把恢复出的 UPSERT/DELETE 记录应用到 fid 索引的基线快照。"""

    source = base_records.values() if isinstance(base_records, Mapping) else base_records
    records: dict[int, bytes] = {}
    for payload in source:
        records[metadata_record_fid(payload)] = bytes(payload)
    for transaction in recovery.transactions:
        for delta in transaction.deltas:
            if delta.operation == OP_ARENA_PATCH:
                continue
            fid = metadata_record_fid(delta.payload)
            if delta.operation == OP_UPSERT:
                records[fid] = delta.payload
            elif delta.operation == OP_DELETE:
                current = records.get(fid)
                if current is None or current != delta.payload:
                    raise JournalIntegrityError(
                        f"DELETE tombstone does not match current fid {fid}"
                    )
                records.pop(fid, None)
            else:  # 解码阶段已拒绝该值，此处保留防御边界。
                raise JournalFormatError(
                    f"unsupported recovered operation {delta.operation}"
                )
    return records


def materialize_arena(base_arena: bytes, recovery: JournalRecovery) -> bytes:
    """依据精确前态重放带校验和的持久区窗口。"""

    if len(base_arena) != ARENA_BYTES:
        raise JournalFormatError(f"durable arena must be exactly {ARENA_BYTES} bytes")
    arena = bytearray(base_arena)
    for transaction in recovery.transactions:
        patched_offsets: set[int] = set()
        for delta in transaction.deltas:
            if delta.operation != OP_ARENA_PATCH:
                continue
            offset, count, before_hash, data = decode_arena_patch(delta.payload)
            if offset in patched_offsets:
                raise JournalFormatError("transaction patches an arena window twice")
            patched_offsets.add(offset)
            if fnv1a64(bytes(arena[offset : offset + count])) != before_hash:
                raise JournalIntegrityError(
                    f"arena patch baseline mismatch at offset {offset}"
                )
            arena[offset : offset + count] = data
    return bytes(arena)


class ImmutableJournalMedia:
    """显式执行物理块不可重写规则的 Host 小模型。"""

    def __init__(self, raw: bytes | None = None):
        initial = bytes(JOURNAL_BYTES) if raw is None else bytes(raw)
        if len(initial) != JOURNAL_BYTES:
            raise JournalFormatError("initial journal media has the wrong size")
        self._raw = bytearray(initial)
        self._written = [
            any(initial[offset : offset + JOURNAL_BLOCK_BYTES])
            for offset in range(0, JOURNAL_BYTES, JOURNAL_BLOCK_BYTES)
        ]

    def write_block_once(self, block_index: int, block: bytes) -> None:
        if not 0 <= block_index < JOURNAL_BLOCKS:
            raise JournalCapacityError("journal block index is out of range")
        if len(block) != JOURNAL_BLOCK_BYTES:
            raise JournalFormatError("journal writes must be one physical block")
        if self._written[block_index]:
            raise JournalRewriteError(
                f"journal physical block {block_index} cannot be rewritten"
            )
        offset = block_index * JOURNAL_BLOCK_BYTES
        self._raw[offset : offset + JOURNAL_BLOCK_BYTES] = block
        self._written[block_index] = True

    def bytes(self) -> bytes:
        return bytes(self._raw)


class JournalAppender:
    """向不可变 Host 模型介质追加完整事务。"""

    def __init__(
        self,
        *,
        base_generation: int,
        base_payload_hash: int,
        raw: bytes | None = None,
    ):
        initial = bytes(JOURNAL_BYTES) if raw is None else bytes(raw)
        recovered = recover_journal(
            initial,
            base_generation=base_generation,
            base_payload_hash=base_payload_hash,
        )
        if recovered.ignored_tail_slot is not None:
            raise JournalRewriteError(
                "an interrupted journal tail must be compacted, not overwritten"
            )
        self.base_generation = base_generation
        self.base_payload_hash = base_payload_hash
        self.media = ImmutableJournalMedia(initial)
        self.next_slot = recovered.next_slot
        self.next_generation = recovered.last_generation + 1
        self.previous_commit_hash = recovered.last_commit_hash

    def encode_next(
        self, identity: JournalIdentity, deltas: Sequence[JournalDelta]
    ) -> tuple[bytes, int]:
        return encode_transaction(
            base_generation=self.base_generation,
            generation=self.next_generation,
            identity=identity,
            previous_commit_hash=self.previous_commit_hash,
            deltas=deltas,
        )

    def append(self, identity: JournalIdentity, deltas: Sequence[JournalDelta]) -> int:
        raw, commit_hash = self.encode_next(identity, deltas)
        start_block = self.next_slot // SLOTS_PER_BLOCK
        blocks = len(raw) // JOURNAL_BLOCK_BYTES
        if start_block + blocks > JOURNAL_BLOCKS:
            raise JournalCapacityError("metadata journal requires compaction")
        for relative in range(blocks):
            offset = relative * JOURNAL_BLOCK_BYTES
            self.media.write_block_once(
                start_block + relative,
                raw[offset : offset + JOURNAL_BLOCK_BYTES],
            )
        self.next_slot += blocks * SLOTS_PER_BLOCK
        self.previous_commit_hash = commit_hash
        self.next_generation += 1
        return commit_hash

    def bytes(self) -> bytes:
        return self.media.bytes()
