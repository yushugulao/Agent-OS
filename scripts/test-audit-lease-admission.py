#!/usr/bin/env python3
"""Model and mutation guards for audit identity admission under lease renewal."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = (ROOT / "os/agent_observe_ledger.c").read_text(encoding="utf-8")
LEASE = (ROOT / "os/agent_identity_lease.c").read_text(encoding="utf-8")
LEASE_H = (ROOT / "os/agent_identity_lease.h").read_text(encoding="utf-8")
CORE = (ROOT / "os/agent_core.c").read_text(encoding="utf-8")
CONTEXT = (ROOT / "os/agent_context.c").read_text(encoding="utf-8")
STORE = (ROOT / "os/agent_observe_store.c").read_text(encoding="utf-8")
HOST_EVIDENCE = (
    ROOT / "host_tools/agent_observe_disk_evidence.py"
).read_text(encoding="utf-8")


class ContractError(RuntimeError):
    pass


def function_body(source: str, name: str) -> str:
    marker = f"{name}("
    search = 0
    while True:
        start = source.find(marker, search)
        if start < 0:
            raise ContractError(f"missing function {name}")
        opening = source.find("(", start)
        depth = 0
        closing = -1
        for index in range(opening, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        brace = source.find("{", closing)
        semicolon = source.find(";", closing)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            depth = 0
            for index in range(brace, len(source)):
                if source[index] == "{":
                    depth += 1
                elif source[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[brace + 1 : index]
            raise ContractError(f"unterminated function {name}")
        search = closing + 1


def compact(text: str) -> str:
    return " ".join(text.split())


def require(text: str, token: str, label: str) -> None:
    if token not in text:
        raise ContractError(f"{label}: missing {token}")


def validate(ledger: str, lease: str, lease_h: str, core: str) -> None:
    compact_ledger = compact(ledger)
    compact_lease_h = compact(lease_h)
    for token in (
        "#define AGENT_IDENTITY_LEASE_LOW_WATER",
        "(AGENT_IDENTITY_LEASE_CHUNK / 2ULL)",
    ):
        require(compact_lease_h, token, "proactive renewal window")
    for token in (
        "enum agent_audit_identity_class",
        "AGENT_AUDIT_ID_TELEMETRY = AGENT_OBSERVE_IDENTITY_TELEMETRY",
        "AGENT_AUDIT_ID_CAUSAL = AGENT_OBSERVE_IDENTITY_CAUSAL",
        "AGENT_AUDIT_ID_AUTHORITY = AGENT_OBSERVE_IDENTITY_AUTHORITY",
        "AGENT_AUDIT_CAUSAL_ID_RESERVE <= AGENT_IDENTITY_LEASE_LOW_WATER",
    ):
        require(compact_ledger, token, "derived audit admission classes")

    admit = compact(function_body(lease, "agent_identity_lease_allocator_admit"))
    for token in (
        "agent_identity_leases.admission_ready",
        "end != 0",
        "id < end",
        "reserve < end - id",
    ):
        require(admit, token, "published lease reserve")

    emit = compact(function_body(ledger, "agent_audit_emit"))
    for token in (
        "identity_reserve = identity_class == AGENT_AUDIT_ID_TELEMETRY ? AGENT_AUDIT_CAUSAL_ID_RESERVE : 0",
        "low_class = identity_class != AGENT_AUDIT_ID_AUTHORITY",
        "agent_observe_audit_slot_alloc(scope_state, principal, low_class, span_id, span_owner, kind)",
        "agent_observe_alloc_id( &agent_audit_next_sequence, AGENT_IDENTITY_ALLOCATOR_AUDIT, identity_reserve)",
        "if (agent_audit_next_sequence == 0) { agent_audit_note_drop(scope_state); return; }",
    ):
        require(emit, token, "emit admission")
    if emit.index("agent_observe_audit_slot_alloc(") > emit.index(
        "agent_observe_alloc_id("
    ):
        raise ContractError("audit identity is consumed before slot admission")
    if "kind == AGENT_AUDIT_KIND_SCHED ?" in emit:
        raise ContractError("identity priority regressed to a kind-only special case")
    dropped = compact(function_body(ledger, "agent_audit_note_drop"))
    for token in (
        "if (state->total_records == ~0ULL) return",
        "state->total_records++",
        "state->admission_drops++",
        "agent_observe_checkpoint_generation++",
        "agent_obsstore_mark_dirty(state->scope_id)",
    ):
        require(dropped, token, "durable drop accounting")
    capture = compact(
        function_body(ledger, "agent_observe_checkpoint_capture_scope")
    )
    restore = compact(
        function_body(ledger, "agent_observe_checkpoint_restore_scope")
    )
    for token in (
        "if (state->visible_records == 0)",
        "state->admission_drops != state->total_records",
        "saved->admission_drops = state->admission_drops",
        "state->ledger_hash != 0",
    ):
        require(capture, token, "drop-only checkpoint capture")
    for token in (
        "if (saved->record_count == 0)",
        "successful_records = saved->total_records - saved->admission_drops",
        "successful_records != 0 || saved->ledger_hash != 0",
        "state->total_records = saved->total_records",
        "state->admission_drops = saved->admission_drops",
    ):
        require(restore, token, "drop-only checkpoint restore")
    store_validate = compact(function_body(STORE, "agent_observe_store_validate"))
    require(
        store_validate,
        "scope->record_count == 0 && (successful_records != 0 || scope->ledger_hash != 0)",
        "kernel drop-only disk validation",
    )
    require(
        compact(HOST_EVIDENCE),
        "record_count == 0 and ( total_records == 0 or admission_drops != total_records or ledger_hash != 0 )",
        "host drop-only disk validation",
    )

    context = compact(function_body(ledger, "agent_audit_context"))
    for token in (
        "causal_audit && record->span_id != 0 && span_owner != 0",
        "span_owner, principal, identity_class, authority_effect",
    ):
        require(context, token, "trusted wait-context classification")
    context_append = compact(function_body(CONTEXT, "agent_context_append"))
    context_system = compact(function_body(CONTEXT, "agent_context_append_system"))
    context_causal = compact(
        function_body(CONTEXT, "agent_context_append_system_causal")
    )
    context_manual = compact(function_body(CONTEXT, "sys_context_push"))
    require(
        context_append,
        "authority_effect, 0",
        "ordinary tool context remains telemetry",
    )
    require(
        context_system,
        "value0, value1, value2, 0",
        "ordinary system context remains telemetry",
    )
    require(
        context_causal,
        "value0, value1, value2, 1",
        "trusted system context marks causal evidence",
    )
    require(
        context_manual,
        "AGENT_CONTEXT_RECORD_F_MANUAL, 0, 0",
        "manual context remains telemetry",
    )

    event = compact(function_body(ledger, "agent_audit_event"))
    require(
        event,
        "event->span_id != 0 && span_owner != 0 ? AGENT_AUDIT_ID_CAUSAL : AGENT_AUDIT_ID_TELEMETRY",
        "event correlation classification",
    )
    effect = compact(function_body(ledger, "agent_observe_ledger_record_effect"))
    require(
        effect,
        "p->agent_control_id, AGENT_AUDIT_ID_TELEMETRY, authority_effect",
        "ordinary effect classification",
    )

    sched = compact(function_body(ledger, "agent_observe_ledger_record_sched"))
    for token in (
        "agent_identity_lease_maintenance_pending()",
        "record->dispatch_count == 0",
        "record->dispatch_count & (record->dispatch_count - 1)",
    ):
        require(sched, token, "durable scheduler aggregation")

    maintain = compact(function_body(core, "agent_background_maintain"))
    for token in (
        "agent_observe_recording_suppress_begin(p)",
        "agent_obsstore_lease_maintain();",
        "agent_metadata_background_maintain();",
        "agent_observe_recording_suppress_end(p);",
    ):
        require(maintain, token, "maintenance observation suppression")
    order = [
        maintain.index("agent_observe_recording_suppress_begin(p)"),
        maintain.index("agent_obsstore_lease_maintain();"),
        maintain.index("agent_metadata_background_maintain();"),
        maintain.index("agent_observe_recording_suppress_end(p);"),
    ]
    if order != sorted(order):
        raise ContractError("maintenance escaped the observation suppression extent")


def model_tests() -> None:
    end = 257
    next_id = 1
    reserve = 64
    telemetry: list[int] = []

    def allocate(held: int) -> int:
        nonlocal next_id
        if next_id >= end or held >= end - next_id:
            return 0
        value = next_id
        next_id += 1
        return value

    while True:
        value = allocate(reserve)
        if value == 0:
            break
        telemetry.append(value)
    assert telemetry == list(range(1, 193))
    for _ in range(10_000):
        assert allocate(reserve) == 0
    assert next_id == 193
    causal = [allocate(0) for _ in range(64)]
    assert causal == list(range(193, 257))
    assert allocate(0) == 0
    assert next_id == 257

    sampled = [value for value in range(1, 4097) if value & (value - 1) == 0]
    assert sampled == [1 << shift for shift in range(13)]


validate(LEDGER, LEASE, LEASE_H, CORE)
model_tests()

mutations = (
    (
        LEDGER.replace(
            "slot = agent_observe_audit_slot_alloc(scope_state, principal, low_class,\n\t\t\t\t\t      span_id, span_owner, kind);",
            "sequence = agent_observe_alloc_id(\n\t\t&agent_audit_next_sequence, AGENT_IDENTITY_ALLOCATOR_AUDIT,\n\t\tidentity_reserve);\n\tslot = agent_observe_audit_slot_alloc(scope_state, principal, low_class,\n\t\t\t\t\t      span_id, span_owner, kind);",
            1,
        ),
        LEASE,
        LEASE_H,
        CORE,
        "sequence allocated before slot",
    ),
    (
        LEDGER.replace(
            "identity_class == AGENT_AUDIT_ID_TELEMETRY ?",
            "kind == AGENT_AUDIT_KIND_SCHED ?",
            1,
        ),
        LEASE,
        LEASE_H,
        CORE,
        "kind-only identity priority",
    ),
    (
        LEDGER.replace("agent_identity_lease_maintenance_pending() ||", "0 ||", 1),
        LEASE,
        LEASE_H,
        CORE,
        "scheduler records during lease maintenance",
    ),
    (
        LEDGER.replace(
            "(record->dispatch_count & (record->dispatch_count - 1)) != 0",
            "record->dispatch_count > 0",
            1,
        ),
        LEASE,
        LEASE_H,
        CORE,
        "scheduler aggregation removed",
    ),
    (
        LEDGER,
        LEASE.replace("reserve < end - id", "reserve <= end - id", 1),
        LEASE_H,
        CORE,
        "reserve boundary consumed",
    ),
    (
        LEDGER,
        LEASE,
        LEASE_H.replace(
            "(AGENT_IDENTITY_LEASE_CHUNK / 2ULL)",
            "32ULL",
            1,
        ),
        CORE,
        "renewal window no longer derived",
    ),
    (
        LEDGER,
        LEASE,
        LEASE_H,
        CORE.replace("agent_observe_recording_suppress_begin(p)", "0", 1),
        "maintenance suppression removed",
    ),
    (
        LEDGER.replace(
            "\tagent_obsstore_mark_dirty(state->scope_id);\n",
            "",
            1,
        ),
        LEASE,
        LEASE_H,
        CORE,
        "drop accounting no longer durable",
    ),
    (
        LEDGER.replace(
            "\tif (state->visible_records == 0) {\n",
            "\tif (0) {\n",
            1,
        ),
        LEASE,
        LEASE_H,
        CORE,
        "drop-only scope cannot be captured",
    ),
)

for ledger, lease, lease_h, core, label in mutations:
    try:
        validate(ledger, lease, lease_h, core)
    except ContractError:
        continue
    raise SystemExit(f"mutation survived: {label}")

print(f"[audit-lease-admission] model and {len(mutations)} mutations passed")
