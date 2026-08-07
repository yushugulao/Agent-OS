#!/usr/bin/env python3
"""编译观察磁盘布局探针并检查其版本化契约。"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probes" / "agent-observe-disk-layout.c"
DEFAULT_CONTRACT = ROOT / "ci" / "agent-observe-disk-format.json"
SECTION = ".agent_observe_layout"
DESCRIPTOR_MAGIC = 0x41474F42534C5931
DESCRIPTOR_VERSION = 2
WORD_COUNT = 129
WORDS = struct.Struct(f"<{WORD_COUNT}Q")


class ProbeError(RuntimeError):
    pass


def _run(command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        raise ProbeError(f"cannot execute {command[0]}: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ProbeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}"
        )


def compile_descriptor(cc: str, objcopy: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="agent-observe-layout-") as directory:
        temporary = Path(directory)
        object_path = temporary / "layout.o"
        section_path = temporary / "layout.bin"
        _run(
            [
                cc,
                "-std=gnu11",
                "-march=rv64imac_zicsr_zifencei",
                "-mabi=lp64",
                "-mcmodel=medany",
                "-fno-builtin",
                "-ffreestanding",
                "-fno-pie",
                f"-I{ROOT}",
                f"-I{ROOT / 'os'}",
                "-c",
                str(PROBE),
                "-o",
                str(object_path),
            ]
        )
        _run(
            [
                objcopy,
                "--dump-section",
                f"{SECTION}={section_path}",
                str(object_path),
            ]
        )
        try:
            return section_path.read_bytes()
        except OSError as error:
            raise ProbeError(f"cannot read probe section: {error}") from error


def descriptor_contract(raw: bytes) -> dict[str, object]:
    if len(raw) != WORDS.size:
        raise ProbeError(f"layout descriptor has {len(raw)} bytes, expected {WORDS.size}")
    values = iter(WORDS.unpack(raw))

    def take() -> int:
        return next(values)

    descriptor_magic, descriptor_version, descriptor_bytes = take(), take(), take()
    if descriptor_magic != DESCRIPTOR_MAGIC:
        raise ProbeError(f"bad descriptor magic 0x{descriptor_magic:016x}")
    if descriptor_version != DESCRIPTOR_VERSION:
        raise ProbeError(f"unsupported descriptor version {descriptor_version}")
    if descriptor_bytes != len(raw):
        raise ProbeError("descriptor byte count differs from section size")

    hash_algorithm, hash_initial, hash_prime = take(), take(), take()
    arena_magic, arena_version, arena_bytes = take(), take(), take()
    section_max, payload_bytes = take(), take()
    arena_fields = dict(
        zip(
            (
                "magic", "version", "bytes", "section_count", "used_bytes",
                "generation", "sections", "payload", "image_hash",
            ),
            (take() for _ in range(9)),
        )
    )
    descriptor_size = take()
    descriptor_fields = dict(
        zip(
            ("kind", "version", "offset", "bytes", "generation", "payload_hash"),
            (take() for _ in range(6)),
        )
    )

    observe_kind, observe_magic, observe_version, observe_bytes = (
        take(), take(), take(), take()
    )
    scope_slots, records_per_scope = take(), take()
    latest_tail, diversity_anchors = take(), take()
    retention_policy, reserved_scope_slots, recovery_scope_slot = (
        take(), take(), take()
    )
    identity_telemetry, identity_causal, identity_authority = (
        take(), take(), take()
    )
    link_prev_retained, link_latest_tail, link_flags_all = (
        take(), take(), take()
    )
    scope_flags_all, scope_used, scope_recovery, scope_reap = (
        take(), take(), take(), take()
    )
    allocator_exhausted_all, lifecycle_cap, first_dynamic_scope, owner_scope_flag = (
        take(), take(), take(), take()
    )
    observe_fields = dict(
        zip(
            (
                "magic", "version", "bytes", "generation", "audit_lease_end",
                "span_lease_end", "event_lease_end", "control_lease_end",
                "agent_lease_end", "retention_policy", "scope_count",
                "allocator_exhausted", "reserved_scope_slots",
                "reserved", "lifecycle_lease_ends", "scopes", "image_hash",
            ),
            (take() for _ in range(17)),
        )
    )
    scope_bytes = take()
    scope_fields = dict(
        zip(
            (
                "used", "scope_id", "lifecycle_id", "record_count",
                "lifecycle_generation", "total_records", "admission_drops",
                "ledger_hash", "records",
            ),
            (take() for _ in range(9)),
        )
    )
    entry_bytes = take()
    entry_record, entry_scope_id, entry_identity_class, identity_class_bytes = (
        take(), take(), take(), take()
    )
    entry_link_flags, link_flags_bytes = take(), take()
    entry_reserved, entry_reserved_bytes = take(), take()
    entry_principal, entry_span_owner, entry_receipt_id = take(), take(), take()
    record_bytes = take()
    record_names = (
        "sequence", "tick", "cause_sequence", "span_id",
        "workflow_lifecycle_generation", "branch_generation",
        "cause_branch_generation", "actor_control_id", "cause_control_id",
        "cause_record_hash", "prev_hash", "record_hash", "value0", "value1",
        "value2", "flags", "kind", "workflow_lifecycle_id", "pid", "tid",
        "source_pid", "target_pid", "agent_id", "role", "loop_state",
        "tool_id", "event_type", "status", "text",
    )
    record_fields = dict(zip(record_names, (take() for _ in record_names)))
    text_bytes, int_bytes, uint_bytes, uint64_bytes = take(), take(), take(), take()
    event_enqueue, event_consume, audit_kind_max, agent_id_max = (
        take(), take(), take(), take()
    )
    try:
        next(values)
    except StopIteration:
        pass
    else:
        raise ProbeError("descriptor word mapping is incomplete")

    return {
        "schema": 2,
        "descriptor": {
            "magic": f"0x{descriptor_magic:016x}",
            "version": descriptor_version,
            "bytes": descriptor_bytes,
        },
        "hash": {
            "algorithm": "agent-fnv1a64-v1",
            "algorithm_id": hash_algorithm,
            "initial": f"0x{hash_initial:016x}",
            "prime": f"0x{hash_prime:016x}",
        },
        "durable_arena": {
            "magic": f"0x{arena_magic:016x}",
            "version": arena_version,
            "bytes": arena_bytes,
            "section_max": section_max,
            "payload_bytes": payload_bytes,
            "fields": arena_fields,
            "section_descriptor": {
                "bytes": descriptor_size,
                "fields": descriptor_fields,
            },
        },
        "observation": {
            "section_kind": observe_kind,
            "magic": f"0x{observe_magic:016x}",
            "version": observe_version,
            "bytes": observe_bytes,
            "scope_slots": scope_slots,
            "records_per_scope": records_per_scope,
            "latest_tail": latest_tail,
            "diversity_anchors": diversity_anchors,
            "retention_policy": retention_policy,
            "reserved_scope_slots": reserved_scope_slots,
            "recovery_scope_slot": recovery_scope_slot,
            "identity_classes": {
                "telemetry": identity_telemetry,
                "causal": identity_causal,
                "authority": identity_authority,
            },
            "link_flags": {
                "all": link_flags_all,
                "prev_retained": link_prev_retained,
                "latest_tail": link_latest_tail,
            },
            "scope_flags": {
                "all": scope_flags_all,
                "used": scope_used,
                "recovery_successor": scope_recovery,
                "reap_authorized": scope_reap,
            },
            "allocator_exhausted_all": allocator_exhausted_all,
            "lifecycle_cap": lifecycle_cap,
            "first_dynamic_scope": first_dynamic_scope,
            "owner_scope_flag": owner_scope_flag,
            "fields": observe_fields,
            "scope": {"bytes": scope_bytes, "fields": scope_fields},
            "entry": {
                "bytes": entry_bytes,
                "fields": {
                    "record": entry_record,
                    "scope_id": entry_scope_id,
                    "identity_class": entry_identity_class,
                    "link_flags": entry_link_flags,
                    "reserved": entry_reserved,
                    "principal": entry_principal,
                    "span_owner": entry_span_owner,
                    "receipt_id": entry_receipt_id,
                },
                "identity_class_bytes": identity_class_bytes,
                "link_flags_bytes": link_flags_bytes,
                "reserved_bytes": entry_reserved_bytes,
            },
            "record": {
                "bytes": record_bytes,
                "fields": record_fields,
                "text_bytes": text_bytes,
                "int_bytes": int_bytes,
                "uint_bytes": uint_bytes,
                "uint64_bytes": uint64_bytes,
            },
            "audit_kinds": {
                "event_enqueue": event_enqueue,
                "event_consume": event_consume,
                "max": audit_kind_max,
            },
            "agent_id_max": agent_id_max,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--cc", default="riscv64-linux-gnu-gcc")
    parser.add_argument("--objcopy", default="riscv64-linux-gnu-objcopy")
    parser.add_argument("--print-actual", action="store_true")
    args = parser.parse_args()
    try:
        actual = descriptor_contract(compile_descriptor(args.cc, args.objcopy))
        expected = json.loads(args.contract.read_text(encoding="utf-8"))
    except (OSError, ValueError, ProbeError) as error:
        parser.error(str(error))
    if args.print_actual:
        print(json.dumps(actual, indent=2, sort_keys=True))
    if actual != expected:
        print("agent observation disk-format contract drifted", flush=True)
        print("expected:", json.dumps(expected, sort_keys=True), flush=True)
        print("actual:  ", json.dumps(actual, sort_keys=True), flush=True)
        return 1
    print("agent observation disk-format contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
