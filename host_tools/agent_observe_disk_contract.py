#!/usr/bin/env python3
"""Strict loader for the durable observation disk-layout contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OBSERVE_CONTRACT = ROOT / "ci" / "agent-observe-disk-format.json"


class ObservationEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObservationLayout:
    hash_initial: int
    hash_prime: int
    arena_magic: int
    arena_version: int
    arena_bytes: int
    arena_section_max: int
    arena_payload_bytes: int
    arena_fields: dict[str, int]
    descriptor_bytes: int
    descriptor_fields: dict[str, int]
    section_kind: int
    observe_magic: int
    observe_version: int
    observe_bytes: int
    scope_slots: int
    records_per_scope: int
    latest_tail: int
    diversity_anchors: int
    retention_policy: int
    reserved_scope_slots: int
    recovery_scope_slot: int
    identity_classes: dict[str, int]
    link_flags: dict[str, int]
    scope_flags: dict[str, int]
    allocator_exhausted_all: int
    lifecycle_cap: int
    first_dynamic_scope: int
    owner_scope_flag: int
    observe_fields: dict[str, int]
    scope_bytes: int
    scope_fields: dict[str, int]
    entry_bytes: int
    entry_fields: dict[str, int]
    identity_class_bytes: int
    link_flags_bytes: int
    reserved_bytes: int
    record_bytes: int
    record_fields: dict[str, int]
    text_bytes: int
    int_bytes: int
    uint_bytes: int
    uint64_bytes: int
    event_kinds: tuple[int, int]
    audit_kind_max: int
    agent_id_max: int


def _keys(value: Any, expected: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ObservationEvidenceError(
            f"{where} schema differs: expected={sorted(expected)} actual={actual}"
        )
    return value


def _integer(value: Any, where: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < (1 if positive else 0):
        raise ObservationEvidenceError(f"{where} is not a valid integer")
    return value


def _hex_u64(value: Any, where: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"0x[0-9a-f]{16}", value) is None:
        raise ObservationEvidenceError(f"{where} is not canonical uint64 hex")
    return int(value, 16)


def _offsets(value: Any, names: set[str], where: str) -> dict[str, int]:
    mapping = _keys(value, names, where)
    return {name: _integer(mapping[name], f"{where}.{name}") for name in names}


def load_observation_contract(
    path: Path | str = DEFAULT_OBSERVE_CONTRACT,
) -> ObservationLayout:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ObservationEvidenceError(
                    f"duplicate JSON field in observation contract: {key}"
                )
            result[key] = value
        return result

    try:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ObservationEvidenceError(
                    f"non-finite JSON value in observation contract: {value}"
                )
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ObservationEvidenceError(
            f"cannot load observation contract: {error}"
        ) from error
    root = _keys(
        raw,
        {"schema", "descriptor", "hash", "durable_arena", "observation"},
        "contract",
    )
    if root["schema"] != 2:
        raise ObservationEvidenceError("unsupported observation contract schema")
    descriptor = _keys(
        root["descriptor"], {"magic", "version", "bytes"}, "descriptor"
    )
    if (
        _hex_u64(descriptor["magic"], "descriptor.magic") != 0x41474F42534C5931
        or descriptor["version"] != 2
        or descriptor["bytes"] != 1032
    ):
        raise ObservationEvidenceError("unsupported observation layout descriptor")
    hash_contract = _keys(
        root["hash"], {"algorithm", "algorithm_id", "initial", "prime"}, "hash"
    )
    if (
        hash_contract["algorithm"] != "agent-fnv1a64-v1"
        or hash_contract["algorithm_id"] != 1
    ):
        raise ObservationEvidenceError("unsupported observation hash algorithm")
    hash_initial = _hex_u64(hash_contract["initial"], "hash.initial")
    hash_prime = _hex_u64(hash_contract["prime"], "hash.prime")
    if hash_initial == 0 or hash_prime == 0 or hash_prime % 2 == 0:
        raise ObservationEvidenceError("invalid observation hash constants")

    arena = _keys(
        root["durable_arena"],
        {
            "magic", "version", "bytes", "section_max", "payload_bytes",
            "fields", "section_descriptor",
        },
        "durable_arena",
    )
    arena_fields = _offsets(
        arena["fields"],
        {
            "magic", "version", "bytes", "section_count", "used_bytes",
            "generation", "sections", "payload", "image_hash",
        },
        "durable_arena.fields",
    )
    section = _keys(
        arena["section_descriptor"], {"bytes", "fields"}, "section_descriptor"
    )
    descriptor_fields = _offsets(
        section["fields"],
        {"kind", "version", "offset", "bytes", "generation", "payload_hash"},
        "section_descriptor.fields",
    )

    observe = _keys(
        root["observation"],
        {
            "section_kind", "magic", "version", "bytes", "scope_slots",
            "records_per_scope", "latest_tail", "diversity_anchors",
            "retention_policy", "reserved_scope_slots", "recovery_scope_slot",
            "identity_classes", "link_flags", "scope_flags",
            "allocator_exhausted_all", "lifecycle_cap", "first_dynamic_scope",
            "owner_scope_flag", "fields", "scope", "entry", "record",
            "audit_kinds", "agent_id_max",
        },
        "observation",
    )
    observe_fields = _offsets(
        observe["fields"],
        {
            "magic", "version", "bytes", "generation", "audit_lease_end",
            "span_lease_end", "event_lease_end", "control_lease_end",
            "agent_lease_end", "retention_policy", "scope_count",
            "allocator_exhausted", "reserved_scope_slots", "reserved",
            "lifecycle_lease_ends", "scopes", "image_hash",
        },
        "observation.fields",
    )
    scope = _keys(observe["scope"], {"bytes", "fields"}, "observation.scope")
    scope_fields = _offsets(
        scope["fields"],
        {
            "used", "scope_id", "lifecycle_id", "record_count",
            "lifecycle_generation", "total_records", "admission_drops",
            "ledger_hash", "records",
        },
        "observation.scope.fields",
    )
    entry = _keys(
        observe["entry"],
        {"bytes", "fields", "identity_class_bytes", "link_flags_bytes", "reserved_bytes"},
        "observation.entry",
    )
    entry_fields = _offsets(
        entry["fields"],
        {
            "record", "scope_id", "identity_class", "link_flags", "reserved",
            "principal", "span_owner", "receipt_id",
        },
        "observation.entry.fields",
    )
    record = _keys(
        observe["record"],
        {"bytes", "fields", "text_bytes", "int_bytes", "uint_bytes", "uint64_bytes"},
        "observation.record",
    )
    record_names = {
        "sequence", "tick", "cause_sequence", "span_id",
        "workflow_lifecycle_generation", "branch_generation",
        "cause_branch_generation", "actor_control_id", "cause_control_id",
        "cause_record_hash", "prev_hash", "record_hash", "value0", "value1",
        "value2", "flags", "kind", "workflow_lifecycle_id", "pid", "tid",
        "source_pid", "target_pid", "agent_id", "role", "loop_state",
        "tool_id", "event_type", "status", "text",
    }
    record_fields = _offsets(
        record["fields"], record_names, "observation.record.fields"
    )
    scope_flags = _keys(
        observe["scope_flags"],
        {"all", "used", "recovery_successor", "reap_authorized"},
        "scope_flags",
    )
    identity_classes = _keys(
        observe["identity_classes"],
        {"telemetry", "causal", "authority"},
        "identity_classes",
    )
    link_flags = _keys(
        observe["link_flags"],
        {"all", "prev_retained", "latest_tail"},
        "link_flags",
    )
    audit_kinds = _keys(
        observe["audit_kinds"],
        {"event_enqueue", "event_consume", "max"},
        "audit_kinds",
    )

    layout = ObservationLayout(
        hash_initial=hash_initial,
        hash_prime=hash_prime,
        arena_magic=_hex_u64(arena["magic"], "durable_arena.magic"),
        arena_version=_integer(arena["version"], "durable_arena.version", positive=True),
        arena_bytes=_integer(arena["bytes"], "durable_arena.bytes", positive=True),
        arena_section_max=_integer(arena["section_max"], "durable_arena.section_max", positive=True),
        arena_payload_bytes=_integer(arena["payload_bytes"], "durable_arena.payload_bytes", positive=True),
        arena_fields=arena_fields,
        descriptor_bytes=_integer(section["bytes"], "section_descriptor.bytes", positive=True),
        descriptor_fields=descriptor_fields,
        section_kind=_integer(observe["section_kind"], "observation.section_kind", positive=True),
        observe_magic=_hex_u64(observe["magic"], "observation.magic"),
        observe_version=_integer(observe["version"], "observation.version", positive=True),
        observe_bytes=_integer(observe["bytes"], "observation.bytes", positive=True),
        scope_slots=_integer(observe["scope_slots"], "observation.scope_slots", positive=True),
        records_per_scope=_integer(observe["records_per_scope"], "observation.records_per_scope", positive=True),
        latest_tail=_integer(observe["latest_tail"], "observation.latest_tail", positive=True),
        diversity_anchors=_integer(observe["diversity_anchors"], "observation.diversity_anchors", positive=True),
        retention_policy=_integer(observe["retention_policy"], "observation.retention_policy", positive=True),
        reserved_scope_slots=_integer(observe["reserved_scope_slots"], "observation.reserved_scope_slots", positive=True),
        recovery_scope_slot=_integer(observe["recovery_scope_slot"], "observation.recovery_scope_slot"),
        identity_classes={
            name: _integer(identity_classes[name], f"identity_classes.{name}")
            for name in identity_classes
        },
        link_flags={
            name: _integer(link_flags[name], f"link_flags.{name}", positive=True)
            for name in link_flags
        },
        scope_flags={
            name: _integer(scope_flags[name], f"scope_flags.{name}", positive=True)
            for name in scope_flags
        },
        allocator_exhausted_all=_integer(observe["allocator_exhausted_all"], "allocator_exhausted_all", positive=True),
        lifecycle_cap=_integer(observe["lifecycle_cap"], "lifecycle_cap", positive=True),
        first_dynamic_scope=_integer(observe["first_dynamic_scope"], "first_dynamic_scope", positive=True),
        owner_scope_flag=_integer(observe["owner_scope_flag"], "owner_scope_flag", positive=True),
        observe_fields=observe_fields,
        scope_bytes=_integer(scope["bytes"], "scope.bytes", positive=True),
        scope_fields=scope_fields,
        entry_bytes=_integer(entry["bytes"], "entry.bytes", positive=True),
        entry_fields=entry_fields,
        identity_class_bytes=_integer(entry["identity_class_bytes"], "entry.identity_class_bytes", positive=True),
        link_flags_bytes=_integer(entry["link_flags_bytes"], "entry.link_flags_bytes", positive=True),
        reserved_bytes=_integer(entry["reserved_bytes"], "entry.reserved_bytes", positive=True),
        record_bytes=_integer(record["bytes"], "record.bytes", positive=True),
        record_fields=record_fields,
        text_bytes=_integer(record["text_bytes"], "record.text_bytes", positive=True),
        int_bytes=_integer(record["int_bytes"], "record.int_bytes", positive=True),
        uint_bytes=_integer(record["uint_bytes"], "record.uint_bytes", positive=True),
        uint64_bytes=_integer(record["uint64_bytes"], "record.uint64_bytes", positive=True),
        event_kinds=(
            _integer(audit_kinds["event_enqueue"], "audit_kinds.event_enqueue"),
            _integer(audit_kinds["event_consume"], "audit_kinds.event_consume"),
        ),
        audit_kind_max=_integer(audit_kinds["max"], "audit_kinds.max", positive=True),
        agent_id_max=_integer(observe["agent_id_max"], "agent_id_max", positive=True),
    )
    if (
        layout.arena_bytes != 8192
        or layout.observe_version != 7
        or layout.observe_bytes != 8024
        or layout.scope_slots != 4
        or layout.scope_bytes != 1968
        or layout.records_per_scope != 8
        or layout.entry_bytes != 240
        or layout.latest_tail != 4
        or layout.diversity_anchors != 4
        or layout.latest_tail + layout.diversity_anchors != layout.records_per_scope
        or layout.retention_policy != 3
        or layout.arena_fields["image_hash"] + 8 != layout.arena_bytes
        or layout.arena_fields["payload"] + layout.arena_payload_bytes != layout.arena_fields["image_hash"]
        or layout.observe_fields["image_hash"] + 8 != layout.observe_bytes
        or layout.scope_fields["records"] + layout.records_per_scope * layout.entry_bytes != layout.scope_bytes
        or layout.observe_fields["scopes"] + layout.scope_slots * layout.scope_bytes != layout.observe_fields["image_hash"]
        or layout.entry_fields["record"] != 0
        or layout.observe_fields["reserved"] != 76
        or layout.observe_fields["reserved"] + 4 != layout.observe_fields["lifecycle_lease_ends"]
        or layout.scope_fields["admission_drops"] != 32
        or layout.entry_fields["identity_class"] != 212
        or layout.entry_fields["identity_class"] + layout.identity_class_bytes != layout.entry_fields["link_flags"]
        or layout.entry_fields["link_flags"] + layout.link_flags_bytes != layout.entry_fields["reserved"]
        or layout.entry_fields["reserved"] + layout.reserved_bytes != layout.entry_fields["principal"]
        or layout.identity_class_bytes != 1
        or layout.link_flags_bytes != 1
        or layout.reserved_bytes != 2
        or layout.identity_classes != {"telemetry": 0, "causal": 1, "authority": 2}
        or layout.link_flags != {"all": 3, "prev_retained": 1, "latest_tail": 2}
        or layout.record_fields["text"] + layout.text_bytes != layout.record_bytes
        or layout.uint64_bytes != 8
        or layout.int_bytes != 4
        or layout.uint_bytes != 4
        or layout.recovery_scope_slot >= layout.scope_slots
        or layout.reserved_scope_slots >= layout.scope_slots
    ):
        raise ObservationEvidenceError("observation contract geometry is inconsistent")
    return layout


__all__ = [
    "DEFAULT_OBSERVE_CONTRACT",
    "ObservationEvidenceError",
    "ObservationLayout",
    "load_observation_contract",
]
