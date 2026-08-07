#!/usr/bin/env python3
"""Static guards for durable-observation exhaustion and test-hook wiring."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for offset in range(brace, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise AssertionError(f"unterminated function: {signature}")


def function_body_named(source: str, name: str) -> str:
    """Find a C function definition without depending on its split signature."""
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", source):
        depth = 1
        cursor = match.end()
        while cursor < len(source) and depth:
            if source[cursor] == "(":
                depth += 1
            elif source[cursor] == ")":
                depth -= 1
            cursor += 1
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if depth == 0 and cursor < len(source) and source[cursor] == "{":
            brace = cursor
            depth = 0
            for cursor in range(brace, len(source)):
                if source[cursor] == "{":
                    depth += 1
                elif source[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        return source[match.start() : cursor + 1]
    raise AssertionError(f"function definition not found: {name}")


def python_function_body(source: str, name: str) -> str:
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    node = matches[0]
    assert node.end_lineno is not None
    return "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])


def compact(source: str) -> str:
    return " ".join(source.split())


def c_tokens(source: str) -> list[str]:
    clean = re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.findall(
        r"[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+|\d+|->|==|!=|<=|>=|&&|\|\||"
        r"\+\+|--|[{}()\[\];,.=*+!<>?:&|~-]",
        clean,
    )


def token_index(tokens: list[str], pattern: tuple[str, ...], start: int = 0) -> int:
    for index in range(start, len(tokens) - len(pattern) + 1):
        if tuple(tokens[index : index + len(pattern)]) == pattern:
            return index
    raise AssertionError(f"missing token sequence: {' '.join(pattern)}")


def token_count(tokens: list[str], pattern: tuple[str, ...]) -> int:
    count = 0
    start = 0
    while start <= len(tokens) - len(pattern):
        try:
            found = token_index(tokens, pattern, start)
        except AssertionError:
            break
        count += 1
        start = found + len(pattern)
    return count


def matching_brace(tokens: list[str], opening: int) -> int:
    assert tokens[opening] == "{"
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index] == "{":
            depth += 1
        elif tokens[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError("unterminated C block")


def expect_rejected(checker, source: str, label: str) -> None:
    try:
        checker(source)
    except AssertionError:
        return
    raise AssertionError(f"observation contract mutation survived: {label}")


ipc = (ROOT / "os/agent_ipc.c").read_text(encoding="utf-8")
allocator = function_body(ipc, "agent_ipc_event_id_alloc(uint64 *event_id)")
queue = function_body(ipc, "agent_ipc_queue_event_locked(")
cancel = function_body(ipc, "sys_agent_wait_cancel(int pid, uint64 reasonaddr)")
assert allocator.count("agent_observe_alloc_event_id()") == 1
assert "*event_id == 0 ? -1 : 0" in allocator
assert queue.count("agent_ipc_event_id_alloc(&event_id)") == 1
assert cancel.count("agent_ipc_event_id_alloc(&event_id)") == 1
assert ipc.count("agent_observe_alloc_event_id()") == 1

store = (ROOT / "os/agent_observe_store.c").read_text(encoding="utf-8")
ledger = (ROOT / "os/agent_observe_ledger.c").read_text(encoding="utf-8")
capacity = (ROOT / "os/agent_observe_capacity.c").read_text(encoding="utf-8")
capacity_header = (ROOT / "os/agent_observe_capacity.h").read_text(
    encoding="utf-8"
)
recovery = (ROOT / "os/agent_observe_recovery.c").read_text(encoding="utf-8")
recovery_store_contract = (
    ROOT / "os/agent_observe_recovery_store.h"
).read_text(encoding="utf-8")
reap = function_body(store, "agent_obsstore_mark_reap(")
reap_replicated = function_body(
    store, "agent_observe_store_replicated_scope(uint scope_id)"
)
assert "agent_observe_capacity_reap_begin(" in reap
assert "agent_observe_capacity_replicated(scope_id)" in reap_replicated
assert "agent_observe_reaps" not in store
assert "agent_observe_test_operation(request.operation)" in recovery
assert (
    "agent_observe_test_execute(&request, recordsaddr,\n"
    "\t\t\t\t       &bank_generation, &returned, &status)"
    in recovery
)
assert recovery.count("#ifdef AGENT_OBSERVE_TEST_PROFILE") == 3
assert "sys_agent_observe_recovery" not in store
assert "agent_durable_section_active_read" not in recovery
assert '#include "agent_observe_store.h"' not in recovery
assert "struct agent_observe_checkpoint" not in recovery_store_contract
for operation in (
    "agent_obsstore_snapshot_begin",
    "agent_obsstore_snapshot_scope_capacity",
    "agent_obsstore_snapshot_record_capacity",
    "agent_obsstore_snapshot_scope",
    "agent_obsstore_snapshot_record",
    "agent_obsstore_snapshot_confirm",
    "agent_obsstore_recovery_reap",
    "agent_obsstore_recovery_reap_resume",
    "agent_obsstore_reap_query",
    "agent_obsstore_reap_consume",
):
    assert len(
        re.findall(rf"\b{re.escape(operation)}\s*\(", recovery_store_contract)
    ) == 1
assert "AGENT_OBSERVE_CHECKPOINT_SCOPES >=\n" in store
assert "AGENT_OBSERVE_CHECKPOINT_SCOPES ==\n" not in store
assert "scope_capacity = agent_obsstore_snapshot_scope_capacity()" in recovery
assert "agent_obsstore_snapshot_record_capacity()" in recovery


def validate_recovery_reap_wrapper(source: str) -> None:
    body = function_body_named(source, "agent_obsstore_recovery_reap")
    assert "agent_observe_capacity_reap_begin(" in body
    assert "agent_observe_capacity_reap_resume(" in body
    begin = body.index("agent_observe_capacity_reap_begin(")
    resume = body.index("agent_observe_capacity_reap_resume(", begin)
    assert begin < resume
    assert "lifecycle, token, bank_generation" in body[resume:]
    assert ") == 1 ? 0 : -1;" in body[resume:]


validate_recovery_reap_wrapper(store)
expect_rejected(
    validate_recovery_reap_wrapper,
    store.replace(
        "return agent_observe_capacity_reap_resume(",
        "return agent_observe_capacity_reap_begin(",
        1,
    ),
    "ordinary REAP stopped returning token-bound source generation",
)


def validate_checkpoint_sparse_chain_contract(
    store_source: str, ledger_source: str
) -> None:
    validate = function_body_named(store_source, "agent_observe_store_validate")
    entry = function_body_named(
        ledger_source, "agent_observe_checkpoint_entry_validate"
    )

    admission_bound = (
        "scope->admission_drops >\n"
        "\t\t\t    scope->total_records - scope->record_count"
    )
    successful = (
        "successful_records =\n"
        "\t\t\tscope->total_records - scope->admission_drops;"
    )
    omitted = "hashed_omitted = successful_records - scope->record_count;"
    drop_only = (
        "scope->record_count == 0 &&\n"
        "\t\t     (successful_records != 0 || scope->ledger_hash != 0)"
    )
    shared_validate = "agent_observe_checkpoint_entry_validate("
    ledger_tail = (
        "scope->ledger_hash !=\n"
        "\t\t\t     scope->records[scope->record_count - 1]\n"
        "\t\t\t\t     .record.record_hash"
    )
    omission_gap = (
        "hashed_omitted == 0 ?\n"
        "\t\t\t      (gap || scope->records[0].record.prev_hash != 0) :\n"
        "\t\t\t      !gap"
    )
    for required in (
        admission_bound,
        successful,
        omitted,
        drop_only,
        shared_validate,
        ledger_tail,
        omission_gap,
        "prior->records[pj].record.sequence ==",
        "record->sequence",
        "if (entry->principal > max_control)",
        "if (entry->span_owner > max_control)",
    ):
        assert required in validate
    assert validate.index(admission_bound) < validate.index(successful)
    assert validate.index(successful) < validate.index(omitted)
    assert validate.index(omitted) < validate.index(drop_only)
    assert validate.index(drop_only) < validate.index(shared_validate)
    assert validate.index(shared_validate) < validate.index(ledger_tail)
    assert validate.index(ledger_tail) < validate.index(omission_gap)

    for required in (
        "AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL",
        "entry->identity_class > AGENT_OBSERVE_IDENTITY_MAX",
        "entry->reserved[0] != 0 || entry->reserved[1] != 0",
        "entry->link_flags & ~AGENT_OBSERVE_LINK_FLAGS_ALL",
        "entry->link_flags & AGENT_OBSERVE_LINK_LATEST_TAIL",
        "entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED",
        "entry->identity_class == AGENT_OBSERVE_IDENTITY_CAUSAL",
        "entry->identity_class == AGENT_OBSERVE_IDENTITY_AUTHORITY",
        "record->kind < 0 ||\n"
        "\t    (uint)record->kind >= AGENT_AUDIT_KIND_SLOT_COUNT",
        "record->agent_id < 0",
        "record->record_hash != agent_observe_checkpoint_record_hash(record)",
    ):
        assert required in entry
    direct = "record->prev_hash == prior->record.record_hash"
    link_matches = (
        "!!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) !=\n"
        "\t\t     direct"
    )
    gap = (
        "if ((index == 0 && record->prev_hash != 0) ||\n"
        "\t    (index != 0 && !direct))\n"
        "\t\t*gap = 1;"
    )
    assert direct in entry
    assert link_matches in entry
    assert gap in entry
    assert entry.index(direct) < entry.index(link_matches) < entry.index(gap)


validate_checkpoint_sparse_chain_contract(store, ledger)
expect_rejected(
    lambda mutated: validate_checkpoint_sparse_chain_contract(mutated, ledger),
    store.replace(
        "successful_records =\n"
        "\t\t\tscope->total_records - scope->admission_drops;",
        "successful_records = scope->total_records;",
        1,
    ),
    "checkpoint validation conflated admission drops with retained omission",
)
expect_rejected(
    lambda mutated: validate_checkpoint_sparse_chain_contract(mutated, ledger),
    store.replace(
        "hashed_omitted == 0 ?\n"
        "\t\t\t      (gap || scope->records[0].record.prev_hash != 0) :\n"
        "\t\t\t      !gap",
        "hashed_omitted == 0 ? 0 : !gap",
        1,
    ),
    "complete checkpoint accepted a sparse predecessor chain",
)
expect_rejected(
    lambda mutated: validate_checkpoint_sparse_chain_contract(store, mutated),
    ledger.replace(
        "!!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) !=\n"
        "\t\t     direct",
        "!!(entry->link_flags & AGENT_OBSERVE_LINK_PREV_RETAINED) == direct",
        1,
    ),
    "checkpoint link sidecar stopped authenticating retained predecessors",
)
expect_rejected(
    lambda mutated: validate_checkpoint_sparse_chain_contract(store, mutated),
    ledger.replace(
        "if ((index == 0 && record->prev_hash != 0) ||\n"
        "\t    (index != 0 && !direct))\n"
        "\t\t*gap = 1;",
        "if (0)\n\t\t*gap = 1;",
        1,
    ),
    "sparse checkpoint gaps stopped contributing to omission validation",
)
expect_rejected(
    lambda mutated: validate_checkpoint_sparse_chain_contract(mutated, ledger),
    store.replace(
        " ||\n\t\t\t\t\t    prior->records[pj].record.sequence ==\n"
        "\t\t\t\t\t\t    record->sequence",
        "",
        1,
    ),
    "checkpoint accepted a duplicate global audit sequence",
)
expect_rejected(
    lambda mutated: validate_checkpoint_sparse_chain_contract(mutated, ledger),
    store.replace(
        "\t\t\tif (entry->span_owner > max_control)\n"
        "\t\t\t\tmax_control = entry->span_owner;\n",
        "",
        1,
    ),
    "checkpoint omitted span owner from the control lease high-water",
)


def validate_capacity_contract(source: str) -> None:
    snapshot = function_body_named(source, "agent_observe_capacity_snapshot")
    admit = function_body_named(source, "agent_observe_capacity_admit")
    token = function_body_named(source, "agent_observe_reap_token")
    start = function_body_named(source, "agent_observe_reap_start")
    for state_guard in (
        "!workflow_lifecycle_active(slots[i].lifecycle)",
        "!workflow_lifecycle_closing(slots[i].lifecycle)",
        "!workflow_lifecycle_retiring(slots[i].lifecycle)",
    ):
        assert state_guard in snapshot
    assert "(i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT) !=" in snapshot
    assert "AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR" in snapshot
    assert snapshot.count("agent_durable_section_active_read(") == 3
    assert "confirmed_generation != generation" in snapshot
    assert (
        "scope.admission_drops >\n"
        "\t\t\t    scope.total_records - scope.record_count"
    ) in snapshot
    assert (
        "scope.record_count == 0 &&\n"
        "\t\t     (scope.admission_drops != scope.total_records ||\n"
        "\t\t      scope.ledger_hash != 0)"
    ) in snapshot
    assert (
        "scope.record_count != 0 &&\n"
        "\t\t     (scope.total_records - scope.admission_drops <\n"
        "\t\t\t      scope.record_count ||\n"
        "\t\t      scope.ledger_hash == 0)"
    ) in snapshot
    assert "scope.record_count == 0 ||" not in snapshot
    ordinary_end = (
        "end = class == AGENT_OBSERVE_CAPACITY_RECOVERY ?\n"
        "\t\tAGENT_OBSERVE_CHECKPOINT_SCOPES :\n"
        "\t\tAGENT_OBSERVE_ORDINARY_SCOPE_SLOTS;"
    )
    assert ordinary_end in admit
    assert "AGENT_OBSERVE_RECOVERY_SCOPE_SLOT : 0" in admit
    assert "state->phase != AGENT_OBSERVE_SLOT_FREE" in admit
    assert "AGENT_OBSERVE_SLOT_DONE && slots[i].flags == 0" not in admit
    assert "slot_recovery !=\n\t\t\t\t    (i == AGENT_OBSERVE_RECOVERY_SCOPE_SLOT)" in admit
    assert "recovery && !slot_recovery" in admit
    assert "slots[i].sealed &&" in admit
    successor_only = (
        "(AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR |\n"
        "\t\t\tAGENT_OBSERVE_SCOPE_REAP_AUTHORIZED)) ==\n"
        "\t\t      AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR"
    )
    assert successor_only in admit
    guard = admit.index("if (replace &&")
    publish = admit.index("state->phase = AGENT_OBSERVE_SLOT_ADMITTED")
    assert guard < publish
    assert "tail_sequence" not in admit
    assert "RETENTION_SEALED_FLOOR" not in admit

    assert "state->detail.reap.serial, state->detail.reap.target" in token
    target = start.index("state->detail.reap.target = target")
    serial = start.index("state->detail.reap.serial = serial", target)
    mint = start.index(
        "state->detail.reap.token = agent_observe_reap_token(state)", serial
    )
    assert target < serial < mint
    assert "state->flags & AGENT_OBSERVE_SLOT_TOKEN_ISSUED" in start

    resume = function_body_named(source, "agent_observe_capacity_reap_resume")
    assert "state->phase < AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING" in resume
    assert "state->phase > AGENT_OBSERVE_SLOT_DONE" in resume
    assert "if (matched)\n\t\t\tgoto fail" in resume
    assert (
        "if (!(state->flags & AGENT_OBSERVE_SLOT_TOKEN_ISSUED) ||\n"
        "\t\t    state->detail.reap.token == 0)\n\t\t\tcontinue"
    ) in resume
    assert "*bank_generation = state->detail.reap.source_generation" in resume

    reap_begin = function_body_named(source, "agent_observe_capacity_reap_begin")
    assert "!slots[exact_slot].sealed" in reap_begin
    assert "AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED" in reap_begin
    assert "if (state->phase != AGENT_OBSERVE_SLOT_FREE)" in reap_begin
    assert "AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING" in reap_begin
    assert "current_admission ?\n\t\tscope_id : VFS_SCOPE_SYSTEM" in reap_begin
    assert "if (exact_slot < 0)" in reap_begin
    assert "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING" in reap_begin
    assert "state->slot_or_persist_scope = scope_id" in reap_begin
    assert reap_begin.count("state->flags = token != 0 ?") == 2
    assert (
        "if (token != 0 && state->detail.reap.source_generation == 0)\n"
        "\t\t\tstate->detail.reap.source_generation = generation"
    ) in reap_begin
    replicated = function_body_named(source, "agent_observe_reap_replicated")
    assert replicated.index("AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING") < replicated.index(
        "AGENT_OBSERVE_SLOT_ERASE_PENDING"
    )
    assert "state->slot_or_persist_scope = VFS_SCOPE_SYSTEM" in replicated
    assert (
        "if (state->detail.reap.token == 0)\n"
        "\t\t\tmemset(state, 0, sizeof(*state))"
    ) in replicated
    recover = function_body_named(source, "agent_observe_capacity_recover_reap")
    assert "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING" in recover
    assert "state->slot_or_persist_scope = VFS_SCOPE_SYSTEM" in recover
    assert "agent_observe_slot_matches(\n\t\t\tstate, scope_id, lifecycle)" in recover
    assert "state->phase >= AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING" in recover
    assert "state->phase <= AGENT_OBSERVE_SLOT_DONE" in recover
    assert (
        "if (same && state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING)"
    ) in recover
    assert "state->detail.reap.target = state->detail.reap.serial = 0" in recover
    assert "return same ? 0 : -1" in recover

    query = function_body_named(source, "agent_observe_capacity_reap_query")
    match = query.index("state->detail.reap.token != token")
    snapshot_done = query.index(
        "agent_observe_capacity_snapshot(slots, bank_generation)", match
    )
    cookie = query.index("cookie->bank_generation = *bank_generation", snapshot_done)
    assert "memset(state, 0, sizeof(*state))" not in query
    assert match < snapshot_done < cookie

    consume = function_body_named(source, "agent_observe_capacity_reap_consume")
    assert "agent_durable_section_active_read" not in consume
    assert "agent_durable_section_mark_dirty" not in consume
    assert "agent_observe_capacity_snapshot" not in consume
    for guard in (
        "state->phase == AGENT_OBSERVE_SLOT_DONE",
        "state, cookie->scope_id, cookie->lifecycle",
        "state->detail.reap.token == cookie->token",
        "state->detail.reap.source_generation == cookie->source_generation",
        "agent_durable_section_active_generation() ==",
    ):
        assert guard in consume
    assert consume.index("state->detail.reap.token == cookie->token") < consume.index(
        "memset(state, 0, sizeof(*state))"
    )


validate_capacity_contract(capacity)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "scope.admission_drops != scope.total_records",
        "scope.admission_drops > scope.total_records",
        1,
    ),
    "capacity scan rejected or weakened a valid drop-only checkpoint",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "!workflow_lifecycle_active(slots[i].lifecycle)",
        "workflow_lifecycle_active(slots[i].lifecycle)",
        1,
    ),
    "active evidence became a retention candidate",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS;",
        "AGENT_OBSERVE_CHECKPOINT_SCOPES;",
        1,
    ),
    "ordinary admission consumed the trusted Recovery slot",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace("slots[i].sealed &&", "1 &&", 1),
    "trusted Recovery replacement accepted active evidence",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "AGENT_OBSERVE_SCOPE_RECOVERY_SUCCESSOR |\n\t\t\tAGENT_OBSERVE_SCOPE_REAP_AUTHORIZED",
        "AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED |\n\t\t\tAGENT_OBSERVE_SCOPE_REAP_AUTHORIZED",
        1,
    ),
    "trusted Recovery replacement lost successor authorization",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "if (state->phase != AGENT_OBSERVE_SLOT_FREE)\n\t\t\tgoto fail;",
        "if (0 && state->phase != AGENT_OBSERVE_SLOT_FREE)\n\t\t\tgoto fail;",
        1,
    ),
    "sealed-evidence REAP stole a slot from an admitted successor",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "confirmed_generation != generation",
        "confirmed_generation == generation",
        1,
    ),
    "capacity admission ignored active-bank rollover after its slot scan",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "state->detail.reap.serial, state->detail.reap.target,",
        "state->detail.reap.source_generation,",
        1,
    ),
    "REAP completion token stopped binding acquired serial and target",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace("(recovery && !slot_recovery)", "(0 && !slot_recovery)", 1),
    "ordinary observation admission became upgradable to Recovery",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "if (state->phase == AGENT_OBSERVE_SLOT_ADMITTED &&",
        "if (state->phase == AGENT_OBSERVE_SLOT_DONE && slots[i].flags == 0)\n"
        "\t\t\tmemset(state, 0, sizeof(*state));\n"
        "\t\tif (state->phase == AGENT_OBSERVE_SLOT_ADMITTED &&",
        1,
    ),
    "new admission discarded an unconsumed DONE token",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace("return same ? 0 : -1;", "return 0;", 1),
    "runtime REAP recovery accepted a conflicting slot identity",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "if (same && state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING)",
        "if (0 && same && state->phase == AGENT_OBSERVE_SLOT_AUTHORIZE_PENDING)",
        1,
    ),
    "runtime reload failed to promote a now-durable REAP authorization",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "if (token != 0 && state->detail.reap.source_generation == 0)",
        "if (0 && token != 0 && state->detail.reap.source_generation == 0)",
        1,
    ),
    "recovered REAP token omitted its first active bank generation",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "if (state->detail.reap.token == 0)\n"
        "\t\t\tmemset(state, 0, sizeof(*state));",
        "if (0 && state->detail.reap.token == 0)\n"
        "\t\t\tmemset(state, 0, sizeof(*state));",
        1,
    ),
    "tokenless teardown retained an unconsumable DONE slot",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "if (agent_observe_capacity_snapshot(slots, bank_generation) < 0)",
        "memset(state, 0, sizeof(*state));\n"
        "\t\tif (agent_observe_capacity_snapshot(slots, bank_generation) < 0)",
        1,
    ),
    "STATUS consumed DONE before confirming the durable generation",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "state->detail.reap.source_generation == cookie->source_generation",
        "state->detail.reap.source_generation == state->detail.reap.source_generation",
        1,
    ),
    "completion consume stopped binding the peek generation cookie",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "agent_durable_section_active_generation() ==",
        "cookie->bank_generation ==",
        1,
    ),
    "completion consume stopped confirming the active bank generation",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "state, cookie->scope_id, cookie->lifecycle",
        "state, state->scope_id, cookie->lifecycle",
        1,
    ),
    "completion consume stopped binding the peek scope cookie",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "state->detail.reap.token == 0)\n\t\t\tcontinue;",
        "state->detail.reap.token == 0)\n\t\t\tgoto fail;",
        1,
    ),
    "zero-target REAP retry became permanently non-resumable",
)
expect_rejected(
    validate_capacity_contract,
    capacity.replace(
        "*bank_generation = state->detail.reap.source_generation;",
        "*bank_generation = 0;",
        1,
    ),
    "REAP retry stopped reproducing its original bank generation",
)


def validate_reap_delivery_contract(source: str) -> None:
    body = function_body_named(source, "sys_agent_observe_recovery")
    resume = body.index("agent_obsstore_recovery_reap_resume(")
    find_scope = body.index("agent_observe_recovery_find_scope(", resume)
    assert "(reap_resume = agent_obsstore_recovery_reap_resume(" in body
    assert "evidence, &resumed_token, &bank_generation" in body[resume:find_scope]
    assert resume < find_scope
    query = body.index("agent_obsstore_reap_query(")
    assert "&bank_generation, &reap_cookie" in body[query:]
    assert "reap_delivery = status == AGENT_STATUS_OK" in body[query:]
    final = body.index("if (copyout(p->pagetable, requestaddr", query)
    consume = body.index("agent_obsstore_reap_consume(&reap_cookie)", final)
    assert body.count("agent_obsstore_reap_consume(&reap_cookie)") == 1
    assert final < consume < body.rindex("agent_metadata_txn_unlock()")
    failed_delivery = body[final:consume]
    assert "agent_metadata_txn_unlock();\n\t\treturn -1;" in failed_delivery
    assert "if (reap_delivery &&" in body[final:consume + 64]
    ordinary = body.index("agent_obsstore_recovery_reap(", find_scope)
    ordinary_tokens = c_tokens(body[ordinary:])
    token_index(
        ordinary_tokens,
        (
            "agent_obsstore_recovery_reap", "(", "scope", ".", "scope_id",
            ",", "evidence", ",", "&", "completion_token", ",", "&",
            "bank_generation", ")",
        ),
    )


validate_reap_delivery_contract(recovery)
expect_rejected(
    validate_reap_delivery_contract,
    recovery.replace(
        "if (copyout(p->pagetable, requestaddr, (char *)&request,",
        "agent_obsstore_reap_consume(&reap_cookie);\n"
        "\tif (copyout(p->pagetable, requestaddr, (char *)&request,",
        1,
    ),
    "STATUS consumed its completion before response delivery",
)
expect_rejected(
    validate_reap_delivery_contract,
    recovery.replace(
        "if (reap_delivery && agent_obsstore_reap_consume(&reap_cookie) < 0)",
        "if (0 && reap_delivery && agent_obsstore_reap_consume(&reap_cookie) < 0)",
        1,
    ),
    "successful STATUS delivery stopped consuming its exact completion",
)
expect_rejected(
    validate_reap_delivery_contract,
    recovery.replace(
        "(reap_resume = agent_obsstore_recovery_reap_resume(",
        "(reap_resume = 0 && agent_obsstore_recovery_reap_resume(",
        1,
    ),
    "REAP retry stopped reissuing its token before durable scope lookup",
)
expect_rejected(
    validate_reap_delivery_contract,
    recovery.replace("&bank_generation) < 0)", "0) < 0)", 1),
    "ordinary REAP stopped exporting its token-bound source generation",
)


def validate_empty_teardown_fence(capacity_source: str, store_source: str) -> None:
    reap_begin = function_body_named(
        capacity_source, "agent_observe_capacity_reap_begin"
    )
    update = function_body_named(store_source, "agent_observe_store_update_scope")
    for required in (
        "if (exact_slot < 0)",
        "!workflow_lifecycle_retiring(lifecycle)",
        "state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING",
        "state->slot_or_persist_scope = scope_id",
        "agent_observe_reap_start(state)",
    ):
        assert required in reap_begin
    for required in (
        "agent_observe_capacity_suppresses_capture(",
        "agent_observe_capacity_claim(",
    ):
        assert required in update
    empty = reap_begin.index("if (exact_slot < 0)")
    phase = reap_begin.index("state->phase = AGENT_OBSERVE_SLOT_ERASE_PENDING", empty)
    persist_scope = reap_begin.index("state->slot_or_persist_scope = scope_id", phase)
    fence = reap_begin.index("agent_observe_reap_start(state)", persist_scope)
    assert empty < phase < persist_scope < fence
    assert update.index("agent_observe_capacity_suppresses_capture(") < update.index(
        "agent_observe_capacity_claim("
    )


validate_empty_teardown_fence(capacity, store)
expect_rejected(
    lambda mutated: validate_empty_teardown_fence(mutated, store),
    capacity.replace(
        "state->slot_or_persist_scope = scope_id;",
        "state->slot_or_persist_scope = VFS_SCOPE_SYSTEM;",
        1,
    ),
    "empty-slot teardown lost its retiring-scope persistence fence",
)
expect_rejected(
    lambda mutated: validate_empty_teardown_fence(capacity, mutated),
    store.replace(
        "agent_observe_capacity_suppresses_capture(",
        "agent_observe_capacity_claim(",
        1,
    ),
    "empty-slot teardown retried capture after releasing its admission claim",
)

assert "AGENT_OBSERVE_RESERVED_SCOPE_SLOTS 1U" in (
    ROOT / "os/agent_observe_store.h"
).read_text(encoding="utf-8")
assert "AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS" in capacity_header or (
    "AGENT_OBSERVE_ORDINARY_SCOPE_SLOTS" in
    (ROOT / "os/agent_observe_store.h").read_text(encoding="utf-8")
)
assert "AGENT_OBSERVE_CHECKPOINT_VERSION 8U" in (
    ROOT / "os/agent_observe_store.h"
).read_text(encoding="utf-8")
core_source = (ROOT / "os/agent_core.c").read_text(encoding="utf-8")
make_role = function_body_named(core_source, "agent_make_role")
assert make_role.index("agent_context_init(p)") < make_role.index(
    "vfs_scope_bind_controller("
)
assert make_role.index("vfs_scope_bind_controller(") < make_role.index(
    "agent_observe_recovery_bind("
)
assert make_role.index("agent_observe_recovery_bind(") < make_role.index(
    "agent_observe_capacity_admit("
)
assert "recovery_bound > 0 ?" in make_role
assert "role == AGENT_ROLE_RECOVERY ?" not in make_role
assert "agent_observe_recovery_unbind_proc(p)" in make_role
assert "agent_observe_capacity_abort(" in make_role
update_scope = function_body_named(store, "agent_observe_store_update_scope")
assert update_scope.index(
    "agent_observe_capacity_suppresses_capture("
) < update_scope.index("agent_observe_capacity_reap_action(")
assert update_scope.index("agent_observe_capacity_reap_action(") < update_scope.index(
    "agent_observe_capacity_claim("
)
assert update_scope.index("agent_observe_capacity_claim(") < update_scope.index(
    "agent_observe_checkpoint_capture_scope("
)
assert "scope->used |= AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED" in update_scope
assert "action == AGENT_OBSERVE_REAP_ERASE" in update_scope
assert "target->used = expected_flags" in update_scope
assert "free_scope" not in update_scope

recover_store = function_body_named(store, "agent_observe_store_recover")
assert "scope->used & AGENT_OBSERVE_SCOPE_REAP_AUTHORIZED" in recover_store
assert "agent_observe_capacity_recover_reap(" in recover_store


USED = 1
RECOVERY_SUCCESSOR = 2
REAP_AUTHORIZED = 4


def capacity_model(slots, trusted_recovery):
    candidates = [4] if trusted_recovery else [0, 1, 2, 3]
    for index in candidates:
        state, flags = slots[index]
        if state == "empty":
            return index
        if (
            trusted_recovery
            and state == "sealed"
            and flags == USED | RECOVERY_SUCCESSOR
        ):
            return index
    return None


# Cold boot leaves five sealed evidence owners. Ordinary evidence is never
# overwritten, while the trusted Recovery successor has one pre-authorized slot.
five_sealed = [
    ("sealed", USED),
    ("sealed", USED),
    ("sealed", USED),
    ("sealed", USED),
    ("sealed", USED | RECOVERY_SUCCESSOR),
]
assert capacity_model(five_sealed, False) is None
assert capacity_model(five_sealed, True) == 4
assert capacity_model(five_sealed[:4] + [("active", USED | RECOVERY_SUCCESSOR)], True) is None
assert capacity_model(
    five_sealed[:4] + [("sealed", USED | RECOVERY_SUCCESSOR | REAP_AUTHORIZED)],
    True,
) is None

# REAP is a durable authorization followed by a SYSTEM-owned erase. Rebooting
# between the two phases resumes only already-authorized work.
authorized = list(five_sealed)
authorized[0] = ("sealed", USED | REAP_AUTHORIZED)
assert capacity_model(authorized, False) is None
after_system_erase = list(authorized)
after_system_erase[0] = ("empty", 0)
assert capacity_model(after_system_erase, False) == 0
assert capacity_model([("active", USED)] + five_sealed[1:], False) is None

empty_slots = [("empty", 0)] * 5
for expected in range(4):
    assert capacity_model(empty_slots, False) == expected
    empty_slots[expected] = ("active", USED)
assert capacity_model(empty_slots, False) is None
assert capacity_model(empty_slots, True) == 4


LINK_PREV_RETAINED = 1
LINK_LATEST_TAIL = 2
LINK_FLAGS_ALL = LINK_PREV_RETAINED | LINK_LATEST_TAIL


def sparse_checkpoint_model(total_records, admission_drops, records, ledger_hash):
    """Model v8 admission loss separately from hashed-record retention loss."""
    record_count = len(records)
    if (
        total_records == 0
        or record_count > 6
        or total_records < record_count
        or admission_drops > total_records - record_count
    ):
        return False
    successful_records = total_records - admission_drops
    if record_count == 0:
        return successful_records == 0 and ledger_hash == 0
    if successful_records < record_count or ledger_hash == 0:
        return False

    gap = False
    tail_start = max(0, record_count - 4)
    for index, (prev_hash, record_hash, link_flags) in enumerate(records):
        if link_flags & ~LINK_FLAGS_ALL:
            return False
        if bool(link_flags & LINK_LATEST_TAIL) != (index >= tail_start):
            return False
        direct = index != 0 and prev_hash == records[index - 1][1]
        if index == 0 and link_flags & LINK_PREV_RETAINED:
            return False
        if index != 0 and bool(link_flags & LINK_PREV_RETAINED) != direct:
            return False
        if index != 0 and prev_hash == 0:
            return False
        if (index == 0 and prev_hash != 0) or (index != 0 and not direct):
            gap = True
        if record_hash == 0:
            return False

    hashed_omitted = successful_records - record_count
    if records[-1][1] != ledger_hash:
        return False
    if hashed_omitted == 0:
        return not gap and records[0][0] == 0
    return gap


full_chain = [
    (0, 11, LINK_LATEST_TAIL),
    (11, 22, LINK_PREV_RETAINED | LINK_LATEST_TAIL),
    (22, 33, LINK_PREV_RETAINED | LINK_LATEST_TAIL),
]
assert sparse_checkpoint_model(3, 0, full_chain, 33)
# Admission failures were never hashed and therefore do not imply a chain gap.
assert sparse_checkpoint_model(5, 2, full_chain, 33)
assert sparse_checkpoint_model(4, 4, [], 0)
assert not sparse_checkpoint_model(4, 3, [], 0)
assert not sparse_checkpoint_model(4, 4, [], 7)

sparse_chain = [
    (0, 11, 0),
    (11, 22, LINK_PREV_RETAINED),
    (99, 33, LINK_LATEST_TAIL),
    (33, 44, LINK_PREV_RETAINED | LINK_LATEST_TAIL),
    (44, 55, LINK_PREV_RETAINED | LINK_LATEST_TAIL),
    (55, 66, LINK_PREV_RETAINED | LINK_LATEST_TAIL),
]
assert sparse_checkpoint_model(8, 0, sparse_chain, 66)
assert not sparse_checkpoint_model(6, 0, sparse_chain, 66)
assert not sparse_checkpoint_model(8, 0, full_chain, 33)
assert not sparse_checkpoint_model(3, 0, sparse_chain[:3], 33)
assert not sparse_checkpoint_model(
    8,
    0,
    sparse_chain[:2]
    + [(99, 33, LINK_PREV_RETAINED | LINK_LATEST_TAIL)]
    + sparse_chain[3:],
    66,
)
assert not sparse_checkpoint_model(
    8,
    0,
    [(0, 11, LINK_LATEST_TAIL)] + sparse_chain[1:],
    66,
)
assert not sparse_checkpoint_model(8, 0, sparse_chain, 55)


def select_checkpoint_records(records):
    """Mirror the bounded v8 tail-plus-diversity retention policy."""
    tail_count = min(len(records), 4)
    tail_start = len(records) - tail_count
    selected = list(records[tail_start:])
    anchors = 0
    while anchors < 2 and len(selected) < len(records):
        best = None
        for candidate in records[:tail_start]:
            if candidate in selected:
                continue
            identity_class, kind, principal, span_id, span_owner, sequence = candidate
            class_seen = any(item[0] == identity_class for item in selected)
            kind_seen = any(item[1] == kind for item in selected)
            principal_seen = any(item[2] == principal for item in selected)
            span_seen = any(
                item[3] == span_id and item[4] == span_owner for item in selected
            )
            score = (
                (not class_seen) << 7
                | (not kind_seen) << 6
                | (not principal_seen) << 5
                | (not span_seen) << 4
                | identity_class
            )
            rank = (score, sequence)
            if best is None or rank > best[0]:
                best = (rank, candidate)
        if best is None:
            break
        selected.append(best[1])
        anchors += 1
    return sorted(selected, key=lambda record: record[5])


selection_input = [
    (0, 0, 10, 0, 0, 1),
    (0, 1, 11, 101, 201, 2),
    (1, 0, 10, 102, 202, 3),
    (2, 0, 12, 0, 0, 4),
    (0, 2, 13, 103, 203, 5),
    (0, 0, 10, 0, 0, 6),
    (0, 0, 10, 0, 0, 7),
    (0, 0, 10, 0, 0, 8),
    (0, 0, 10, 0, 0, 9),
    (0, 0, 10, 0, 0, 10),
    (0, 0, 10, 0, 0, 11),
    (0, 0, 10, 0, 0, 12),
]
selection = select_checkpoint_records(selection_input)
assert len(selection) == 6
assert [record[5] for record in selection] == [3, 4, 9, 10, 11, 12]
assert [record[5] for record in selection[-4:]] == [9, 10, 11, 12]
assert {record[0] for record in selection[:-4]} == {1, 2}
assert select_checkpoint_records(selection_input[:3]) == selection_input[:3]


def validate_capture_diversity_contract(source: str) -> None:
    select = function_body_named(source, "agent_observe_checkpoint_select")
    score = function_body_named(
        source, "agent_observe_checkpoint_selection_score"
    )
    capture = function_body_named(source, "agent_observe_checkpoint_capture_scope")
    restore = function_body_named(source, "agent_observe_checkpoint_restore_scope")

    for required in (
        "AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL",
        "AGENT_OBSERVE_CHECKPOINT_DIVERSITY_ANCHORS",
        "agent_observe_checkpoint_selection_score(",
        "score > best_score",
        "score == best_score && sequence > best_sequence",
        "agent_audit_records[selected[j - 1]].sequence >",
    ):
        assert required in select
    for required in (
        "!class_seen << 7",
        "!kind_seen << 6",
        "!principal_seen << 5",
        "!span_seen << 4",
        "identity_class",
    ):
        assert required in score

    drop_only = (
        "state->visible_records == 0"
    )
    drop_only_exact = "state->admission_drops != state->total_records"
    select_call = "agent_observe_checkpoint_select(state, selected,"
    persist_admission = "saved->admission_drops = state->admission_drops;"
    tail_flag = "entry->link_flags |= AGENT_OBSERVE_LINK_LATEST_TAIL;"
    direct_link = (
        "record->prev_hash ==\n"
        "\t\t\t      saved->records[j - 1].record.record_hash"
    )
    prev_flag = "entry->link_flags |= AGENT_OBSERVE_LINK_PREV_RETAINED;"
    for required in (
        drop_only,
        drop_only_exact,
        select_call,
        "saved->ledger_hash = state->ledger_hash;",
        "AGENT_OBSERVE_CHECKPOINT_LATEST_TAIL",
        "entry->identity_class = identity_class;",
        tail_flag,
        direct_link,
        prev_flag,
        "entry->receipt_id == 0",
    ):
        assert required in capture
    assert capture.count(persist_admission) == 2
    assert capture.index(drop_only) < capture.index(drop_only_exact)
    assert capture.index(drop_only_exact) < capture.index(select_call)
    assert capture.index(select_call) < capture.index(tail_flag)
    assert capture.index(tail_flag) < capture.index(direct_link)
    assert capture.index(direct_link) < capture.index(prev_flag)

    live_guard = (
        "state->visible_records != 0 || state->total_records != 0 ||\n"
        "\t    state->admission_drops != 0 || state->ledger_hash != 0"
    )
    successful = "successful_records = saved->total_records - saved->admission_drops;"
    drop_restore = (
        "state->total_records = saved->total_records;\n"
        "\t\tstate->admission_drops = saved->admission_drops;\n"
        "\t\tstate->ledger_hash = 0;"
    )
    shared_validate = "agent_observe_checkpoint_entry_validate("
    gap_rule = (
        "successful_records - saved->record_count == 0) ?\n"
        "\t\t\t     (gap || saved->records[0].record.prev_hash != 0) :\n"
        "\t\t\t     !gap"
    )
    atomic_begin = "enabled = intr_save();"
    free_preflight = "agent_audit_scopes[slot] == VFS_SCOPE_NONE"
    rollback = "rollback:"
    atomic_end = "intr_restore(enabled);"
    for required in (
        live_guard,
        successful,
        drop_restore,
        shared_validate,
        gap_rule,
        "agent_audit_identity_classes[slot] = entry->identity_class;",
        "entry->identity_class != AGENT_OBSERVE_IDENTITY_AUTHORITY;",
        atomic_begin,
        free_preflight,
        rollback,
        "agent_audit_slot_clear(slot);",
        "memset(state->sequence_slots, 0, sizeof(state->sequence_slots));",
        atomic_end,
    ):
        assert required in restore
    assert restore.index(successful) < restore.index(shared_validate)
    assert restore.index(shared_validate) < restore.index(gap_rule)
    assert restore.index(gap_rule) < restore.index(atomic_begin)
    assert restore.index(atomic_begin) < restore.index(live_guard)
    assert restore.index(live_guard) < restore.index(drop_restore)
    assert restore.index(drop_restore) < restore.index(free_preflight)
    assert restore.index(free_preflight) < restore.index(rollback)
    assert restore.index(rollback) < restore.index(atomic_end)


validate_capture_diversity_contract(ledger)
expect_rejected(
    validate_capture_diversity_contract,
    ledger.replace(
        "anchors < AGENT_OBSERVE_CHECKPOINT_DIVERSITY_ANCHORS",
        "anchors < 0",
        1,
    ),
    "checkpoint selection stopped retaining bounded diversity anchors",
)
expect_rejected(
    validate_capture_diversity_contract,
    ledger.replace(
        "state->admission_drops != state->total_records",
        "state->admission_drops > state->total_records",
        1,
    ),
    "drop-only capture stopped requiring every attempt to be an admission drop",
)
expect_rejected(
    validate_capture_diversity_contract,
    ledger.replace(
        "record->prev_hash ==\n"
        "\t\t\t      saved->records[j - 1].record.record_hash",
        "record->prev_hash != saved->records[j - 1].record.record_hash",
        1,
    ),
    "checkpoint capture forged sparse-chain predecessor sidecars",
)
expect_rejected(
    validate_capture_diversity_contract,
    ledger.replace(
        "state->visible_records != 0 || state->total_records != 0 ||\n"
        "\t    state->admission_drops != 0 || state->ledger_hash != 0",
        "state->visible_records != 0",
        1,
    ),
    "restore overwrote a non-empty live drop-only scope",
)
expect_rejected(
    validate_capture_diversity_contract,
    ledger.replace(
        "successful_records = saved->total_records - saved->admission_drops;",
        "successful_records = saved->total_records;",
        1,
    ),
    "restore conflated admission drops with omitted hashed records",
)

scope_reclaim = function_body(ledger, "agent_observe_scope_reclaim(uint scope_id)")
assert "return agent_obsstore_mark_reap(scope_id, lifecycle);" in scope_reclaim


def validate_timeline_publish_contract(source: str) -> None:
    tokens = c_tokens(
        function_body_named(source, "agent_observe_timeline_publish_locked")
    )
    assignment = (
        "observe_epoch",
        "=",
        "agent_observe_scope_epoch_advance_locked",
        "(",
        "scope_id",
        ")",
        ";",
    )
    assert token_count(tokens, assignment) == 1
    assert token_count(
        tokens, ("agent_observe_scope_epoch_advance_locked", "(")
    ) == 1
    assert token_count(tokens, ("agent_observe_scope_epoch", "(")) == 0
    advance = token_index(tokens, assignment)
    filter_match = token_index(
        tokens,
        ("agent_observe_timeline_match", "(", "&", "state", "->", "filter"),
        advance,
    )
    wake = token_index(
        tokens, ("agent_observe_timeline_waiter_wake", "(", "t", ")"), filter_match
    )
    assert token_count(tokens, ("for", "(", "int", "tid", "=", "0", ";")) >= 1
    assert token_count(tokens, ("t", "->", "agent_timeline_wait_state")) >= 1
    assert advance < filter_match < wake


validate_timeline_publish_contract(ledger)
expect_rejected(
    validate_timeline_publish_contract,
    ledger.replace(
        "observe_epoch = agent_observe_scope_epoch_advance_locked(scope_id);",
        "observe_epoch = agent_observe_scope_epoch(scope_id);",
        1,
    ),
    "timeline publish stopped advancing epoch",
)
expect_rejected(
    validate_timeline_publish_contract,
    ledger.replace(
        "observe_epoch = agent_observe_scope_epoch_advance_locked(scope_id);",
        "observe_epoch = agent_observe_scope_epoch(scope_id);\n"
        "\tif (0)\n"
        "\t\tobserve_epoch = "
        "agent_observe_scope_epoch_advance_locked(scope_id);",
        1,
    ),
    "timeline publish retained a dead advance token",
)
expect_rejected(
    validate_timeline_publish_contract,
    ledger.replace(
        "agent_observe_timeline_waiter_wake(t)",
        "wait_queue_wake_all(&p->agent_timeline_waiters)",
        1,
    ),
    "timeline publish lost targeted per-thread wake",
)
objects = (ROOT / "os/agent_metadata_objects.c").read_text(encoding="utf-8")
reclaim_begin = function_body(objects, "agent_scope_reclaim_begin(")
assert "if (agent_observe_scope_reclaim(scope_id) < 0)" in reclaim_begin

test_owner = (ROOT / "os/agent_observe_test.c").read_text(encoding="utf-8")
test_header = (ROOT / "os/agent_observe_test.h").read_text(encoding="utf-8")
assert "#ifdef AGENT_OBSERVE_TEST_PROFILE" in test_owner
assert "agent_observe_checkpoint_exhaust_highwater(" in test_owner
assert "agent_observe_alloc_event_id() == 0" in test_owner
assert "AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_WAIT" in test_owner
assert "AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_WAIT_STATUS" in test_owner
assert "AGENT_OBSERVE_RECOVERY_TEST_ARM_TIMELINE_THREADS" in test_owner
assert "AGENT_OBSERVE_RECOVERY_TEST_TIMELINE_THREADS_STATUS" in test_owner
assert "t->agent_timeline_wait_state" in test_owner
for waiter_token in (
    "t->state != SLEEPING",
    "t->wait_channel != &p->agent_timeline_waiters",
    "t->wait_reason != WAIT_REASON_TIMELINE",
    "t->wait_key != state->thread_generation",
):
    assert waiter_token in test_owner
assert "agent_observe_timeline_publish_locked(scope_id, &record, 0)" in test_owner
assert "agent_observe_scope_epoch_advance_locked" not in test_owner
assert "agent_observe_timeline_wait_test.rechecks++" in test_owner
assert "agent_observe_timeline_wait_test.remaining_injections = 2" in test_owner
assert "#ifdef AGENT_OBSERVE_TEST_PROFILE" in test_header
assert "#else" not in test_header
assert "static inline" not in test_header


def validate_test_reply_ownership(source: str) -> None:
    execute = function_body(source, "agent_observe_test_execute(")
    assert "uint64 *bank_generation, uint *returned, int *status" in execute
    assert "*bank_generation =" in execute
    assert "*returned =" in execute
    assert "request->bank_generation =" not in execute
    assert "request->returned =" not in execute


validate_test_reply_ownership(test_owner)
expect_rejected(
    validate_test_reply_ownership,
    test_owner.replace(
        "*bank_generation =", "request->bank_generation =", 1
    ),
    "observe test bypassed canonical bank-generation output",
)
expect_rejected(
    validate_test_reply_ownership,
    test_owner.replace("*returned =", "request->returned =", 1),
    "observe test bypassed canonical returned-count output",
)


def validate_waiter_snapshot(source: str) -> None:
    snapshot = function_body(source, "agent_observe_timeline_threads_snapshot(")
    for token in (
        "t->state != SLEEPING",
        "t->wait_channel != &p->agent_timeline_waiters",
        "t->wait_reason != WAIT_REASON_TIMELINE",
        "t->wait_key != state->thread_generation",
    ):
        assert token in snapshot


validate_waiter_snapshot(test_owner)
expect_rejected(
    validate_waiter_snapshot,
    test_owner.replace("t->state != SLEEPING", "0", 1),
    "observe test counted a published but non-sleeping sidecar",
)


def validate_recovery_test_reply(source: str) -> None:
    execute = function_body(source, "sys_agent_observe_recovery(")
    hook_call = (
        "agent_observe_test_execute(&request, recordsaddr,\n"
        "\t\t\t\t       &bank_generation, &returned, &status)"
    )
    assert hook_call in execute
    hook = execute.index(hook_call)
    complete = execute.index("complete:", hook)
    returned_copy = execute.index("request.returned = returned", complete)
    generation_copy = execute.index(
        "request.bank_generation = bank_generation", returned_copy
    )
    assert hook < complete < returned_copy < generation_copy


validate_recovery_test_reply(recovery)
expect_rejected(
    validate_recovery_test_reply,
    recovery.replace(
        "agent_observe_test_execute(&request, recordsaddr,\n"
        "\t\t\t\t       &bank_generation, &returned, &status)",
        "agent_observe_test_execute(&request, recordsaddr,\n"
        "\t\t\t\t       0, 0, &status)",
        1,
    ),
    "observe test outputs disconnected from the canonical reply",
)

durable = (ROOT / "os/agent_durable_section.h").read_text(encoding="utf-8")
assert "AGENT_DURABLE_DIRTY_MAX (WORKFLOW_LIFECYCLE_CAP + 1U)" in durable
durable_source = (ROOT / "os/agent_durable_section.c").read_text(encoding="utf-8")


def validate_serial_fail_closed(source: str) -> None:
    body = function_body_named(source, "agent_durable_section_mark_dirty_evidence")
    tokens = c_tokens(body)
    allocate = token_index(
        tokens,
        (
            "uint64",
            "next_serial",
            "=",
            "agent_durable_serial_alloc",
            "(",
            ")",
            ";",
        ),
    )
    reject = token_index(
        tokens, ("if", "(", "next_serial", "==", "0", ")", "{"), allocate
    )
    restore = token_index(
        tokens, ("intr_restore", "(", "enabled", ")", ";", "return", "0", ";"), reject
    )
    assign = token_index(
        tokens, ("state", "->", "serial", "=", "next_serial", ";"), restore
    )
    assert allocate < reject < restore < assign
    direct = (
        "state",
        "->",
        "serial",
        "=",
        "agent_durable_serial_alloc",
        "(",
        ")",
        ";",
    )
    try:
        token_index(tokens, direct)
    except AssertionError:
        pass
    else:
        raise AssertionError("serial allocation overwrites pending state before success")


validate_serial_fail_closed(durable_source)
expect_rejected(
    validate_serial_fail_closed,
    durable_source.replace(
        "uint64 next_serial = agent_durable_serial_alloc();",
        "state->serial = agent_durable_serial_alloc();\n"
        "\t\t\tuint64 next_serial = state->serial;",
        1,
    ),
    "serial overwrite before exhaustion check",
)
expect_rejected(
    validate_serial_fail_closed,
    durable_source.replace(
        "if (next_serial == 0)", "if (0 && next_serial == 0)", 1
    ),
    "serial exhaustion accepted",
)

assert "#define AGENT_DURABLE_DIRTY_URGENT (1U << 0)" in durable
assert "void (*expedite)(uint);" in durable


def validate_durable_expedite(source: str) -> None:
    notify = function_body_named(source, "agent_durable_notify_locked")
    retry = function_body_named(source, "agent_durable_retry_locked")
    retry_pending = function_body_named(
        source, "agent_durable_section_retry_pending"
    )
    ordinary = function_body_named(source, "agent_durable_section_mark_dirty")
    mark_dirty = function_body_named(
        source, "agent_durable_section_mark_dirty_evidence"
    )
    commit_scope = function_body_named(
        source, "agent_durable_section_commit_scope"
    )

    notify_tokens = c_tokens(notify)
    mark_tokens = c_tokens(mark_dirty)
    retry_tokens = c_tokens(retry)
    commit_tokens = c_tokens(commit_scope)

    target = token_index(
        notify_tokens,
        (
            "agent_durable_store",
            "->",
            "mark_dirty",
            "(",
            "state",
            "->",
            "scope_id",
            ")",
        ),
    )
    notified = token_index(
        notify_tokens,
        ("state", "->", "notified", "=", "target", "!=", "0", ";"),
        target,
    )
    expedite_gate = token_index(
        notify_tokens,
        (
            "if",
            "(",
            "target",
            "!=",
            "0",
            "&&",
            "state",
            "->",
            "urgent_serial",
            "!=",
            "0",
            "&&",
            "agent_durable_store",
            "->",
            "expedite",
            "!=",
            "0",
            ")",
        ),
        notified,
    )
    expedite_call = token_index(
        notify_tokens,
        (
            "agent_durable_store",
            "->",
            "expedite",
            "(",
            "state",
            "->",
            "scope_id",
            ")",
            ";",
        ),
        expedite_gate,
    )
    assert target < notified < expedite_gate < expedite_call

    # Serial fences are ordinary by default; urgency is an explicit policy bit.
    ordinary_tokens = c_tokens(ordinary)
    token_index(
        ordinary_tokens,
        (
            "return",
            "agent_durable_section_mark_dirty_evidence",
            "(",
            "kind",
            ",",
            "scope_id",
            ",",
            "0",
            ",",
            "0",
            ")",
            ";",
        ),
    )
    reject_flags = token_index(
        mark_tokens,
        (
            "flags",
            "&",
            "~",
            "AGENT_DURABLE_DIRTY_URGENT",
            ")",
            "!=",
            "0",
        ),
    )
    state_urgent = token_index(
        mark_tokens,
        (
            "if",
            "(",
            "flags",
            "&",
            "AGENT_DURABLE_DIRTY_URGENT",
            ")",
            "state",
            "->",
            "urgent_serial",
            "=",
            "next_serial",
            ";",
        ),
        reject_flags,
    )
    state_notify = token_index(
        mark_tokens,
        (
            "agent_durable_notify_locked",
            "(",
            "state",
            ")",
            ";",
        ),
        state_urgent,
    )
    free_urgent = token_index(
        mark_tokens,
        (
            "free_state",
            "->",
            "urgent_serial",
            "=",
            "(",
            "flags",
            "&",
            "AGENT_DURABLE_DIRTY_URGENT",
            ")",
            "?",
            "next_serial",
            ":",
            "0",
            ";",
        ),
        state_notify,
    )
    free_notify = token_index(
        mark_tokens,
        (
            "agent_durable_notify_locked",
            "(",
            "free_state",
            ")",
            ";",
        ),
        free_urgent,
    )
    assert reject_flags < state_urgent < state_notify < free_urgent < free_notify
    assert token_count(
        mark_tokens, ("state", "->", "urgent_serial", "=", "0", ";")
    ) == 0

    # Both provider installation and background retry reuse the common notify
    # path, so a failed urgent mark cannot silently lose its expedite action.
    token_index(
        retry_tokens,
        (
            "agent_durable_notify_locked",
            "(",
            "state",
            ")",
            "==",
            "0",
        ),
    )
    assert token_count(retry_tokens, ("state", "->", "urgent_serial", "=")) == 0
    assert "agent_durable_retry_locked()" in retry_pending

    token_index(
        commit_tokens,
        (
            "memset",
            "(",
            "state",
            ",",
            "0",
            ",",
            "sizeof",
            "(",
            "*",
            "state",
            ")",
            ")",
            ";",
        ),
    )
    clear_stale_urgent = token_index(
        commit_tokens,
        (
            "if", "(", "state", "->", "urgent_serial", "!=", "0", "&&",
            "state", "->", "urgent_serial", "<=", "captured_serial", ")",
            "state", "->", "urgent_serial", "=", "0", ";",
        ),
    )
    notify_late = token_index(
        commit_tokens,
        ("agent_durable_notify_locked", "(", "state", ")", ";"),
        clear_stale_urgent,
    )
    assert clear_stale_urgent < notify_late
    assert "memset(free_state, 0" not in mark_dirty


validate_durable_expedite(durable_source)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "agent_durable_store->expedite(state->scope_id);", "(void)state;", 1
    ),
    "urgent durable notify no longer expedites the provider",
)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "agent_durable_notify_locked(state) == 0", "0 == 0", 1
    ),
    "urgent retry bypassed the common notify path",
)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "if (flags & AGENT_DURABLE_DIRTY_URGENT)\n"
        "\t\t\t\tstate->urgent_serial = next_serial;",
        "if (0)\n\t\t\t\tstate->urgent_serial = next_serial;",
        1,
    ),
    "coalesced urgent policy bit ignored",
)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "free_state->urgent_serial =\n"
        "\t\t\t(flags & AGENT_DURABLE_DIRTY_URGENT) ? next_serial : 0;",
        "free_state->urgent_serial = 0;",
        1,
    ),
    "new urgent dirty entry downgraded",
)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "if ((flags & ~AGENT_DURABLE_DIRTY_URGENT) != 0 ||",
        "if (0 ||",
        1,
    ),
    "unknown durable dirty flags accepted",
)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "if (state->urgent_serial != 0 &&\n"
        "\t\t    state->urgent_serial <= captured_serial)\n"
        "\t\t\tstate->urgent_serial = 0;",
        "if (0)\n\t\t\tstate->urgent_serial = 0;",
        1,
    ),
    "committed urgent fence leaks into a later ordinary generation",
)
expect_rejected(
    validate_durable_expedite,
    durable_source.replace(
        "memset(state, 0, sizeof(*state));", "state->used = 0;", 1
    ),
    "committed urgency state not cleared",
)

metadata_store = (ROOT / "os/agent_metadata_store.c").read_text(encoding="utf-8")


def validate_metadata_expedite_provider(source: str) -> None:
    start = source.index(
        "static const struct agent_durable_store_ops agent_meta_durable_store"
    )
    end = source.index("};", start)
    provider = source[start : end + 2]
    assert ".mark_dirty = agent_meta_durable_dirty," in provider
    assert ".expedite = agent_metadata_store_expedite," in provider
    assert ".active_replicated = agent_meta_durable_active_replicated," in provider
    init = function_body_named(source, "agent_metadata_store_init")
    assert "agent_durable_section_set_store_provider(&agent_meta_durable_store)" in init


validate_metadata_expedite_provider(metadata_store)
expect_rejected(
    validate_metadata_expedite_provider,
    metadata_store.replace(
        ".expedite = agent_metadata_store_expedite,", ".expedite = 0,", 1
    ),
    "metadata store dropped durable expedite binding",
)
expect_rejected(
    validate_metadata_expedite_provider,
    metadata_store.replace(
        ".active_replicated = agent_meta_durable_active_replicated,",
        ".active_replicated = 0,",
        1,
    ),
    "metadata store dropped active-generation replication binding",
)


def validate_active_replication_fence(source: str) -> None:
    target = compact(function_body_named(source, "agent_meta_persist_target_locked"))
    clear_token = "agent_meta_store_set_replicated_generation(0);"
    invalidate_token = "agent_meta_persist.phase = AGENT_META_PERSIST_INVALIDATE;"
    assert clear_token in target and invalidate_token in target
    clear = target.index(clear_token)
    invalidate = target.index(invalidate_token, clear)
    assert clear < invalidate

    load = compact(function_body_named(source, "agent_file_load_snapshot"))
    assert (
        "agent_meta_store_set_replicated_generation( repair_mode == "
        "AGENT_META_REPAIR_NONE ? selected_generation : 0);" in load
    )

    shadow_invalidate = compact(
        function_body_named(source, "agent_meta_bank_shadow_invalidate")
    )
    if "agent_meta_store_set_replicated_generation" in shadow_invalidate:
        raise ContractError("scratch shadow invalidation changed physical replication")
    assert "agent_meta_store_set_replicated_generation(0);" in compact(
        function_body_named(source, "agent_meta_store_require_mirror")
    )

    active = compact(
        function_body_named(source, "agent_meta_durable_active_replicated")
    )
    for token in (
        "generation != 0",
        "generation == agent_meta_store_generation",
        "generation == agent_meta_store_replicated_generation",
    ):
        assert token in active

    step = compact(function_body_named(source, "agent_meta_persist_step_locked"))
    commit_token = "if (state->phase == AGENT_META_PERSIST_COMMIT)"
    mirror_token = "if (!state->mirroring)"
    value_token = "uint64 replicated_generation = state->expected_generation;"
    publish_token = (
        "agent_meta_store_set_replicated_generation(replicated_generation);"
    )
    for token in (commit_token, mirror_token, value_token, publish_token):
        assert token in step
    commit = step.index(commit_token)
    mirror_guard = step.index(mirror_token, commit)
    replicated_value = step.index(value_token, mirror_guard)
    publish = step.index(publish_token, replicated_value)
    release = step.index("agent_meta_persist_release_locked(0);", publish)
    assert commit < mirror_guard < replicated_value < publish < release


def validate_active_replication_model() -> None:
    active_generation = 7
    replicated_generation = 7
    assert active_generation == replicated_generation
    replicated_generation = 0  # bind overwrite target before INVALIDATE
    assert active_generation != replicated_generation
    active_generation = 8  # primary publish is not a replication proof
    assert active_generation != replicated_generation
    replicated_generation = 8  # verified mirror commit
    assert active_generation == replicated_generation
    assert 7 != replicated_generation
    replicated_generation = 0  # the next overwrite revokes the proof again
    assert active_generation != replicated_generation


validate_active_replication_fence(metadata_store)
validate_active_replication_model()
expect_rejected(
    validate_active_replication_fence,
    metadata_store.replace(
        "agent_meta_store_set_replicated_generation(0);\n"
        "\tagent_meta_persist.phase = AGENT_META_PERSIST_INVALIDATE;",
        "agent_meta_persist.phase = AGENT_META_PERSIST_INVALIDATE;",
        1,
    ),
    "bank overwrite retained a stale replication proof",
)
expect_rejected(
    validate_active_replication_fence,
    metadata_store.replace(
        "repair_mode == AGENT_META_REPAIR_NONE ? selected_generation : 0",
        "selected_generation",
        1,
    ),
    "boot repair state was mislabeled as replicated",
)
expect_rejected(
    validate_active_replication_fence,
    metadata_store.replace(
        "agent_meta_store_set_replicated_generation(replicated_generation);",
        "agent_meta_store_set_replicated_generation(0);",
        1,
    ),
    "verified mirror commit did not publish its generation fence",
)

retire = function_body(metadata_store, "agent_file_scope_state_retire(")
target_done = function_body(
    metadata_store, "agent_metadata_store_scope_target_done("
)
background = function_body(
    metadata_store, "agent_metadata_store_background_maintain(void)"
)
store_tick = function_body(metadata_store, "agent_metadata_store_tick(uint64 now)")
assert "agent_durable_section_scope_pending(scope_id)" in retire
assert "state->dirty_generation != state->durable_generation" in retire
assert "state->dirty_generation != state->replicated_generation" in retire
assert "agent_file_writeback_scope_busy(scope_id)" in retire
assert "agent_metadata_txn_lock(0)" in target_done
assert "agent_file_scope_state_retire(scope_id, target)" in target_done
assert "agent_durable_section_retry_pending()" not in background
assert "agent_background_request()" not in background
assert "agent_durable_section_retry_pending()" in store_tick
assert "agent_file_writeback_ready(now)" in store_tick
assert "agent_background_request()" in store_tick

workload = (ROOT / "user/src/agentobsreboot_ucore.c").read_text(
    encoding="utf-8"
)
phase_abi = (ROOT / "agent_observe_test_phase_abi.h").read_text(
    encoding="utf-8"
)
phase_control = (
    ROOT / "host_tools/agent_observe_phase_control.py"
).read_text(encoding="utf-8")
mkfs_source = (ROOT / "nfs/fs.c").read_text(encoding="utf-8")
user_makefile = (ROOT / "user/Makefile").read_text(encoding="utf-8")
vfs_security_source = (ROOT / "os/vfs_security.c").read_text(encoding="utf-8")
exec_image_policy_source = (ROOT / "exec_image_policy.h").read_text(
    encoding="utf-8"
)
fs_source = (ROOT / "os/fs.c").read_text(encoding="utf-8")
file_source = (ROOT / "os/file.c").read_text(encoding="utf-8")
exec_manifest = (ROOT / "user/include/exec_policy_manifest.h").read_text(
    encoding="utf-8"
)

assert "#define AGENT_OBSERVE_TEST_PHASE_MAGIC 0x4f425350U" in phase_abi
assert "#define AGENT_OBSERVE_TEST_PHASE_STATE_BYTES 168U" in phase_abi
for field in (
    "struct agent_workflow_lifecycle_key lifecycle;",
    "unsigned long long max_sequence;",
    "unsigned long long max_span_id;",
    "unsigned long long max_event_id;",
    "unsigned long long actor_control_id;",
    "unsigned long long receipt_sequence;",
    "unsigned long long receipt_record_hash;",
    "unsigned long long receipt_id;",
):
    assert phase_abi.count(field) == 1
assert "AGENT_OBSERVE_TEST_PHASE_STATE_BYTES" in phase_abi
assert "#include <agent_observe_test_phase_abi.h>" in workload
assert "struct evidence_identity {" not in workload
assert "struct phase_state {" not in workload
assert "OBSERVE_RECOVERY_TESTS := agentobsreboot_ucore\n" in user_makefile
assert "agentobsphaseinit_ucore" not in user_makefile


def validate_powercut_phase_workload(source: str) -> None:
    phase_reader_source = function_body_named(source, "load_phase")
    phase_reader = c_tokens(phase_reader_source)
    assert "#define PHASE_OPEN_ATTEMPTS 256" in source
    assert "#define WORKFLOW_CREATE_ATTEMPTS 256" in source
    opened = token_index(
        phase_reader,
        ("fd", "=", "open", "(", "PHASE_FILE", ",", "O_RDONLY", ")", ";"),
    )
    read = token_index(
        phase_reader,
        ("read_exact", "(", "fd", ",", "state", ",", "sizeof", "(", "*", "state", ")"),
        opened,
    )
    eof = token_index(
        phase_reader,
        ("check", "(", "read", "(", "fd", ",", "&", "trailing", ",", "1", ")", "==", "0"),
        read,
    )
    closed = token_index(
        phase_reader,
        ("check", "(", "close", "(", "fd", ")", "==", "0"),
        eof,
    )
    assert opened < read < eof < closed
    for recovery_guard in (
        "attempts < PHASE_OPEN_ATTEMPTS",
        "if (fd >= 0)",
        "check(sleep(1) == 0, \"wait for bounded metadata boot recovery\")",
        "check(fd >= 0, \"open protected phase state\")",
    ):
        assert recovery_guard in phase_reader_source
    assert phase_reader_source.index("fd = open(PHASE_FILE, O_RDONLY);") < phase_reader_source.index(
        "check(sleep(1) == 0, \"wait for bounded metadata boot recovery\")"
    ) < phase_reader_source.index("check(fd >= 0, \"open protected phase state\")")
    assert "O_WRONLY" not in phase_reader and "O_RDWR" not in phase_reader
    assert "O_CREATE" not in phase_reader and "O_TRUNC" not in phase_reader
    allowed_calls = {
        "load_phase",
        "memset",
        "sizeof",
        "open",
        "check",
        "read_exact",
        "read",
        "close",
        "printf",
        "if",
        "for",
        "sleep",
        "phase_state_empty",
    }
    calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", phase_reader_source))
    assert calls <= allowed_calls

    create_source = function_body_named(source, "create_workflow_ready")
    create = c_tokens(create_source)
    loop_pattern = (
        "for", "(", "int", "attempts", "=", "0", ";", "attempts", "<",
        "WORKFLOW_CREATE_ATTEMPTS", ";", "attempts", "++", ")", "{",
    )
    loop = token_index(create, loop_pattern)
    loop_open = loop + len(loop_pattern) - 1
    loop_close = matching_brace(create, loop_open)
    loop_body = create[loop_open + 1 : loop_close]
    tail = create[loop_close + 1 :]
    delegated_report = token_index(
        loop_body,
        ("agent_scope_delegate_fd", "(", "report_fd", ")", "==", "AGENT_STATUS_OK"),
    )
    delegated_input = token_index(
        loop_body,
        ("agent_scope_delegate_fd", "(", "input_fd", ")", "==", "AGENT_STATUS_OK"),
        delegated_report,
    )
    created = token_index(
        loop_body,
        ("pid", "=", "agent_workflow_create", "(", "role", ")", ";"),
        delegated_input,
    )
    retry = token_index(
        loop_body,
        ("if", "(", "pid", "!=", "AGENT_STATUS_RETRY", ")"),
        created,
    )
    terminal_print = token_index(loop_body, ("printf", "("), retry)
    terminal_status = token_index(
        loop_body, ("pid", ",", "attempts", "+", "1"), terminal_print
    )
    terminal_return = token_index(
        loop_body, ("return", "pid", ";"), terminal_status
    )
    slept = token_index(
        loop_body, ("sleep", "(", "1", ")", "==", "0"), terminal_return
    )
    assert delegated_report < delegated_input < created < retry
    assert retry < terminal_print < terminal_status < terminal_return < slept
    assert token_count(loop_body, ("agent_scope_delegate_fd", "(", "report_fd")) == 1
    assert token_count(loop_body, ("agent_scope_delegate_fd", "(", "input_fd")) == 1
    assert token_count(loop_body, ("agent_workflow_create", "(", "role", ")")) == 1
    assert token_count(create, ("agent_workflow_create", "(", "role", ")")) == 1
    diagnostics = token_index(tail, ("agent_info", "(", "&", "info", ")"))
    exhausted_print = token_index(tail, ("printf", "("), diagnostics)
    exhausted_status = token_index(
        tail,
        ("pid", ",", "WORKFLOW_CREATE_ATTEMPTS"),
        exhausted_print,
    )
    exhausted_return = token_index(tail, ("return", "pid", ";"), exhausted_status)
    assert diagnostics < exhausted_print < exhausted_status < exhausted_return

    main = c_tokens(function_body_named(source, "main"))
    permission = token_index(main, ("verify_receipt_not_agent", "(", ")", ";"))
    load = token_index(main, ("load_phase", "(", "&", "state", ")", ";"))
    empty = token_index(
        main,
        ("if", "(", "state", ".", "magic", "==", "0", ")", "{"),
        load,
    )
    cut = token_index(
        main,
        (
            "run_child_phase", "(", "AGENT_ROLE_RECOVERY", ",", "4", ",",
            "0", ",", "&", "result", ")", ";",
        ),
        empty,
    )
    assert permission < load < empty < cut
    assert "save_phase" not in source
    assert "unlink(PHASE_FILE" not in source
    assert "agent_observe_test_phase_read" not in source
    phase_parent = function_body_named(source, "run_child_phase")
    assert "pid = create_workflow_ready(role, reports[1], inputs[0]);" in phase_parent
    assert "agent_workflow_create(role)" not in phase_parent
    identity = phase_parent.index('print_phase_identity("phase1_identity"')
    release = phase_parent.index("write_exact(inputs[1], &release", identity)
    assert identity < release
    main_body = function_body_named(source, "main")
    successor = main_body.index('print_phase_identity("phase2_successor"')
    completed = main_body.index(
        'printf("agentobsreboot_ucore: boot2_reap_replicated=1\\n")',
        successor,
    )
    assert successor < completed


validate_powercut_phase_workload(workload)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
		"\tfd = open(PHASE_FILE, O_RDONLY);",
		"\tfd = open(PHASE_FILE, O_RDWR);",
        1,
    ),
    "Guest phase reader regained write authority",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "\tread_exact(fd, state, sizeof(*state),",
        "\twrite(fd, state, 1);\n\tread_exact(fd, state, sizeof(*state),",
        1,
    ),
    "Guest phase reader writes the Host-owned slot",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "\tread_exact(fd, state, sizeof(*state),",
        "\tagent_phase_slot_read(state);\n"
        "\tread_exact(fd, state, sizeof(*state),",
        1,
    ),
    "Guest phase reader regained a typed kernel bypass",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "if (pid != AGENT_STATUS_RETRY)",
        "if (pid >= 0)",
        1,
    ),
    "workflow create retry accepted an incomplete terminal predicate",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "attempts < WORKFLOW_CREATE_ATTEMPTS",
        "attempts <= WORKFLOW_CREATE_ATTEMPTS",
        1,
    ),
    "workflow create retry exceeded its bounded attempt budget",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "for (int attempts = 0; attempts < WORKFLOW_CREATE_ATTEMPTS; attempts++) {",
        "for (int attempts = 0; attempts < WORKFLOW_CREATE_ATTEMPTS; attempts++) {}\n\t{",
        1,
    ),
    "workflow create operations escaped the bounded retry loop",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "pid, attempts + 1);",
        "AGENT_STATUS_RETRY, attempts + 1);",
        1,
    ),
    "workflow create terminal diagnostic stopped reporting the real status",
)
expect_rejected(
    validate_powercut_phase_workload,
    workload.replace(
        "pid, WORKFLOW_CREATE_ATTEMPTS);",
        "pid, 0);",
        1,
    ),
    "workflow create exhaustion diagnostic stopped reporting its bound",
)


def validate_live_reload_workload(source: str) -> None:
    body = function_body_named(source, "boot2_live_reload")
    assert body.count("event_id = emit_identity_activity();") == 2
    first_activity = body.find("event_id = emit_identity_activity();")
    before = body.find("agent_ledger_snapshot(&before)", first_activity)
    reload = body.find("agent_file_meta_init()", before)
    after = body.find("agent_ledger_snapshot(&after)", reload)
    marker = body.find("live_reload_ledger_monotonic=1", after)
    second_activity = body.find("event_id = emit_identity_activity();", marker)
    identity = body.find(
        "snapshot_identity_since(&result.identity, event_id,", second_activity
    )
    assert min(first_activity, before, reload, after, marker, second_activity, identity) >= 0
    assert first_activity < before < reload < after < marker < second_activity < identity
    for guard in (
        "before.latest_sequence != 0 && before.ledger_hash != 0",
        "after.latest_sequence >= before.latest_sequence",
        "after.latest_sequence != ~0ULL && after.ledger_hash != 0",
        "after.total_records >= before.total_records",
        "after.latest_sequence != before.latest_sequence ||",
        "after.ledger_hash == before.ledger_hash",
        "after.latest_sequence + 1",
        "result.identity.lifecycle.id != expected.lifecycle.id ||",
        "result.identity.lifecycle.generation >",
    ):
        assert guard in body

    snapshot = function_body_named(source, "snapshot_audit_identity")
    assert "unsigned long long start_sequence" in snapshot
    assert "unsigned long long start_sequence = 0" not in snapshot
    start_filter = snapshot.find("if (start_sequence != 0)")
    flags = snapshot.find("filter->flags = AGENT_AUDIT_FILTER_START_SEQUENCE", start_filter)
    value = snapshot.find("filter->start_sequence = start_sequence", flags)
    query = snapshot.find("count = agent_audit_query(filter, records, limit)", value)
    assert start_filter >= 0 and start_filter < flags < value < query

    identity_since = function_body_named(source, "snapshot_identity_since")
    assert (
        "snapshot_audit_identity(identity, info.agent_id, lifecycle.key,\n"
        "\t\t\t\t\tstart_sequence)" in identity_since
    )


validate_live_reload_workload(workload)
expect_rejected(
    validate_live_reload_workload,
    workload.replace(
        "after.latest_sequence >= before.latest_sequence",
        "after.latest_sequence < before.latest_sequence",
        1,
    ),
    "live reload accepts an older audit sequence",
)
expect_rejected(
    validate_live_reload_workload,
    workload.replace(
        "after.ledger_hash == before.ledger_hash",
        "after.ledger_hash != before.ledger_hash",
        1,
    ),
    "equal live sequence no longer binds the ledger hash",
)
expect_rejected(
    validate_live_reload_workload,
    workload.replace(
        "/* Exercise identity fields with fresh records after the retention-heavy reload. */\n"
        "\tevent_id = emit_identity_activity();",
        "/* Identity evidence was not refreshed after reload. */",
        1,
    ),
    "identity assertion depends on records displaced during live reload",
)
expect_rejected(
    validate_live_reload_workload,
    workload.replace("after.latest_sequence + 1", "after.latest_sequence", 1),
    "fresh identity query includes the pre-reload fence",
)
expect_rejected(
    validate_live_reload_workload,
    workload.replace(
        "filter->start_sequence = start_sequence",
        "filter->start_sequence = 0",
        1,
    ),
    "audit snapshot ignores its caller-provided fence",
)
expect_rejected(
    validate_live_reload_workload,
    workload.replace(
        "after.total_records >= before.total_records",
        "after.total_records < before.total_records",
        1,
    ),
    "live reload accepts a lower total-record high-water mark",
)


def validate_identity_probe_role_separation(source: str) -> None:
    boot2_recovery = function_body_named(source, "boot2_recovery")
    boot3_recovery = function_body_named(source, "boot3_recovery")
    probe = function_body_named(source, "boot3_identity_successor")
    dispatcher = function_body_named(source, "run_child_phase")
    main = function_body_named(source, "main")
    for recovery in (boot2_recovery, boot3_recovery):
        assert "verify_stable_successor(" not in recovery
        assert "snapshot_identity(" not in recovery
        assert "agent_ledger_snapshot(" not in recovery
        assert "agent_audit_query(" not in recovery
    assert "verify_stable_successor(&expected, &result.identity);" in probe
    assert "boot3_identity_successor(inputs[0], reports[1]);" in dispatcher
    orchestrator = (
        "run_child_phase(AGENT_ROLE_ORCHESTRATOR, 6, "
        "&state.successor, &result);"
    )
    recovery = "run_child_phase(AGENT_ROLE_RECOVERY, 2, &state, &result);"
    assert orchestrator in main and recovery in main
    assert main.index(orchestrator) < main.index(recovery)


validate_identity_probe_role_separation(workload)
expect_rejected(
    validate_identity_probe_role_separation,
    workload.replace(
        "run_child_phase(AGENT_ROLE_ORCHESTRATOR, 6, &state.successor, &result);",
        "run_child_phase(AGENT_ROLE_RECOVERY, 6, &state.successor, &result);",
        1,
    ),
    "Recovery role regained audit-orchestration authority for identity proof",
)


def validate_phase_control_helper(source: str) -> None:
    assert "PHASE_STATE_BYTES = 168" in source
    extent = python_function_body(source, "_phase_extent")
    for guard in (
        "superblock.magic != ucore_fs.FSMAGIC_AGENT_PRINCIPAL",
        "len(matches) != 1",
        "inode.type != ucore_fs.T_FILE or inode.size != PHASE_STATE_BYTES",
        "inode.vfs_flags == ucore_fs.VFS_LABEL_F_PROTECTED",
        "inode.vfs_policy == ucore_fs.VFS_POLICY_WORKFLOW",
        "inode.vfs_scope_id == ucore_fs.VFS_SCOPE_SYSTEM",
        "inode.vfs_exec_profile == ucore_fs.VFS_EXEC_PROFILE_NONE",
        "inode.fs_owner_domain == ucore_fs.FS_OWNER_SYSTEM",
        "inode.fs_owner_version == ucore_fs.FS_OWNER_VERSION",
        "inode.exec_flags == 0",
        "inode.exec_generation == 0",
        "inode.exec_role_mask == 0",
        "inode.exec_layout_version == 0",
        "inode.exec_rw_offset == 0",
        "any(inode.addrs[1:])",
        "inode.addrs[0] < superblock.datastart",
        "image[bitmap_offset] & (1 << (phase_block % 8)) == 0",
        "ucore_fs.u32(image, owner_offset) != ucore_fs.FS_OWNER_SYSTEM",
        "for inum in range(1, superblock.ninodes)",
        "if ucore_fs.u16(image, inode_offset) == 0",
        "phase_block in candidate.addrs",
        "phase slot block aliases indirect file data",
        "payload = ucore_fs.read_file(image, inode)",
    ):
        assert guard in extent
    decode = python_function_body(source, "_decode_state")
    assert "if payload == EMPTY_SLOT" in decode
    assert "if len(payload) != PHASE_STATE_BYTES" in decode
    assert "if state.pack() != payload" in decode
    assert "_successor_monotonic(state.evidence, state.successor)" in decode
    monotonic = python_function_body(source, "_successor_monotonic")
    for comparison in (
        "new.agent_id > old.agent_id",
        "new.max_sequence > old.max_sequence",
        "new.max_span_id > old.max_span_id",
        "new.max_event_id > old.max_event_id",
        "new.actor_control_id > old.actor_control_id",
        "new.lifecycle_id != old.lifecycle_id",
        "or new.lifecycle_generation > old.lifecycle_generation",
    ):
        assert comparison in monotonic
    transition = python_function_body(source, "advance_state")
    for step in (
        '("empty", "phase0")',
        '("phase0", "phase1")',
        '("phase1", "phase2")',
        "if actual != source",
        '"phase1_identity"',
        '"lease_cut_alloc"',
        '"lease_cut_successor"',
        "_validate_cut_successor(old_cut, new_cut)",
        '"phase2_successor"',
        "_successor_monotonic(",
        "verified_payload != successor_payload",
    ):
        assert step in transition
    parser = python_function_body(source, "_parse_identity_log")
    assert "IDENTITY_LINE.fullmatch(line)" in parser
    assert "if index >= completions[0]" in parser
    atomic = python_function_body(source, "_atomic_replace_payload")
    for step in (
        "os.O_EXCL",
        "os.fsync(fd)",
        "current.st_dev != status.st_dev",
        "current.st_ino != status.st_ino",
        "current_image != image",
        "os.replace(temporary, image_path)",
        "os.fsync(directory_fd)",
    ):
        assert step in atomic
    identity = python_function_body(source, "_identity_valid")
    assert "ucore_fs.VFS_SCOPE_FIRST_DYNAMIC" in identity
    assert "< ucore_fs.FS_OWNER_SCOPE_FLAG" in identity
    assert "verified_image, _ = _read_image(image_path)" in transition


validate_phase_control_helper(phase_control)
expect_rejected(
    validate_phase_control_helper,
    phase_control.replace(
        "if actual != source:",
        "if False and actual != source:",
        1,
    ),
    "Host phase transition accepts a replayed or skipped predecessor",
)
expect_rejected(
    validate_phase_control_helper,
    phase_control.replace(
        "new.max_sequence > old.max_sequence",
        "new.max_sequence >= old.max_sequence",
        1,
    ),
    "Host phase transition accepts a reused audit identity",
)
expect_rejected(
    validate_phase_control_helper,
    phase_control.replace(
        "inode.addrs[0] == 0 or any(inode.addrs[1:])",
        "inode.addrs[0] == 0",
        1,
    ),
    "phase control accepts aliased or indirect extents",
)
expect_rejected(
    validate_phase_control_helper,
    phase_control.replace(
        'if payload == EMPTY_SLOT:\n        return "empty", None\n'
        "    if len(payload) != PHASE_STATE_BYTES:",
        'if payload == EMPTY_SLOT:\n        return "empty", None\n'
        "    if False and len(payload) != PHASE_STATE_BYTES:",
        1,
    ),
    "phase control accepts a partial state payload",
)
expect_rejected(
    validate_phase_control_helper,
    phase_control.replace(
        "verified_image, _ = _read_image(image_path)",
        "verified_image = image",
        1,
    ),
    "phase control no longer rereads the replaced image",
)


def validate_phase_slot_mkfs(source: str) -> None:
    assert '#include "../agent_observe_test_phase_abi.h"' in source
    install = function_body_named(source, "install_observe_phase_slot")
    for operation in (
        'static const char phase_name[] = "obsphase";',
        "static const struct agent_observe_test_phase_state empty_phase;",
        "reserve_image_name(phase_name);",
        "inum = ialloc(T_FILE);",
        "iappend(rootino, &de, sizeof(de));",
        "iappend(inum, (void *)&empty_phase, sizeof(empty_phase));",
        "label_inode(inum, VFS_LABEL_F_PROTECTED, VFS_SCOPE_SYSTEM,",
        "VFS_POLICY_WORKFLOW, VFS_EXEC_PROFILE_NONE);",
    ):
        assert operation in install
    profile_call = (
        "#ifdef AGENT_OBSERVE_PHASE_CONTROL_PROFILE\n"
        "\tinstall_observe_phase_slot(rootino);\n"
        "#endif\n"
        "\trequire_metadata_genesis_capacity(rootino);"
    )
    assert profile_call in source


validate_phase_slot_mkfs(mkfs_source)
expect_rejected(
    validate_phase_slot_mkfs,
    mkfs_source.replace(
        "iappend(inum, (void *)&empty_phase, sizeof(empty_phase));",
        "iappend(inum, (void *)&empty_phase, sizeof(empty_phase) - 1);",
        1,
    ),
    "mkfs observation phase slot has a partial ABI payload",
)
expect_rejected(
    validate_phase_slot_mkfs,
    mkfs_source.replace(
        "label_inode(inum, VFS_LABEL_F_PROTECTED, VFS_SCOPE_SYSTEM,",
        "label_inode(inum, VFS_LABEL_F_PUBLIC, VFS_SCOPE_NONE,",
        1,
    ),
    "mkfs phase slot became PUBLIC",
)


def validate_system_workflow_data_policy(
    vfs_source: str, directory_source: str, exec_policy_source: str
) -> None:
    data = function_body_named(vfs_source, "vfs_system_workflow_data_valid")
    for zero_field in (
        "ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE",
        "ip->exec_flags == 0",
        "ip->exec_generation == 0",
        "ip->exec_role_mask == 0",
        "ip->exec_layout_version == 0",
        "ip->exec_rw_offset == 0",
    ):
        assert zero_field in data
    executable = function_body_named(vfs_source, "vfs_system_workflow_exec_valid")
    assert "exec_image_protected_shape_valid(" in executable
    assert "ip->vfs_exec_profile, PAGE_SIZE" in executable
    contract = function_body_named(
        exec_policy_source, "exec_image_protected_classify"
    )
    for shape_guard in (
        "EXEC_FLAG_IMMUTABLE | EXEC_FLAG_DOMAIN_SAFE",
        "(flags & ~EXEC_FLAG_KNOWN) != 0",
        "(flags & required) != required",
        "generation != EXEC_MANIFEST_VERSION",
        "(role_mask & ~EXEC_MANIFEST_ROLE_ALL) != 0",
        "layout_version != EXEC_LAYOUT_VERSION",
        "rw_offset < page_size",
        "size <= rw_offset",
        "EXEC_IMAGE_COMPAT",
        "EXEC_IMAGE_WORKER",
        "EXEC_IMAGE_TRUSTED_ENDPOINT",
        "EXEC_IMAGE_TRUSTED_AGENT",
    ):
        assert shape_guard in contract
    assert "required = EXEC_FLAG_TRUSTED" not in contract
    assert "exec_image_profile_valid(profile)" in contract
    install = function_body_named(mkfs_source, "install_file")
    assert install.count("exec_image_protected_shape_valid(") == 2
    assert install.index("exec_image_protected_shape_valid(") < install.index(
        "reserve_image_name(image)"
    )
    assert install.rindex("exec_image_protected_shape_valid(") > install.index(
        "rinode(inum, &din)"
    )
    assert '#include "../exec_image_policy.h"' in mkfs_source
    shape = function_body_named(vfs_source, "vfs_label_shape_valid")
    assert "vfs_system_workflow_data_valid(ip) ||" in shape
    assert "vfs_system_workflow_exec_valid(ip)" in shape
    authorize = function_body_named(vfs_source, "vfs_inode_authorize")
    assert "return (op == VFS_OP_LOOKUP || op == VFS_OP_READ) &&" in authorize
    assert "cred->scope_id >= VFS_SCOPE_FIRST_DYNAMIC" in authorize
    assert "(cred->capabilities & VFS_CAP_CONTENT_READ) != 0" in authorize
    assert "vfs_system_workflow_exec_valid(ip)" in authorize
    link = function_body_named(directory_source, "dirlink")
    compact_link = " ".join(link.split())
    assert "ip->vfs_scope_id == VFS_SCOPE_SYSTEM" in link
    assert (
        "(target_policy == VFS_POLICY_PUBLIC && "
        "ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE)"
    ) in compact_link


validate_system_workflow_data_policy(
    vfs_security_source, fs_source, exec_image_policy_source
)
expect_rejected(
    lambda source: validate_system_workflow_data_policy(
        source, fs_source, exec_image_policy_source
    ),
    vfs_security_source.replace("ip->exec_flags == 0", "ip->exec_flags != 0", 1),
    "SYSTEM/WORKFLOW data can be disguised as executable metadata",
)
expect_rejected(
    lambda source: validate_system_workflow_data_policy(
        source, fs_source, exec_image_policy_source
    ),
    vfs_security_source.replace(
        "return (op == VFS_OP_LOOKUP || op == VFS_OP_READ) &&",
        "return (op == VFS_OP_LOOKUP || op == VFS_OP_READ || "
        "op == VFS_OP_WRITE) &&",
        1,
    ),
    "SYSTEM/WORKFLOW data became writable",
)
expect_rejected(
    lambda source: validate_system_workflow_data_policy(
        vfs_security_source, source, exec_image_policy_source
    ),
    re.sub(
        r"\(target_policy == VFS_POLICY_PUBLIC &&\s*"
        r"ip->vfs_exec_profile == VFS_EXEC_PROFILE_NONE\)",
        "(target_policy == VFS_POLICY_PUBLIC && 0)",
        fs_source,
        count=1,
    ),
    "PUBLIC creation can shadow a protected SYSTEM data object",
)


def validate_system_data_fallback(source: str) -> None:
    opened = function_body_named(source, "fileopen")
    write_guard = (
        "if ((omode & (O_WRONLY | O_RDWR | O_TRUNC)) != 0 ||\n"
        "\t\t\t    cred.scope_id < VFS_SCOPE_FIRST_DYNAMIC)\n"
        "\t\t\t\tgoto fail;"
    )
    fallback = (
        "ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,\n"
        "\t\t\t\t\t       VFS_SCOPE_SYSTEM, &lookup_status);"
    )
    assert write_guard in opened
    assert fallback in opened
    assert opened.index(write_guard) < opened.index(fallback)
    assert "!vfs_inode_authorize(ip, &cred, VFS_OP_READ)" in opened
    assert "!vfs_inode_authorize(ip, &cred, VFS_OP_WRITE)" in opened
    assert "!vfs_inode_authorize(ip, &cred, VFS_OP_TRUNCATE)" in opened


validate_system_data_fallback(file_source)
expect_rejected(
    validate_system_data_fallback,
    file_source.replace(
        "ip = namei_scope_status(path, VFS_POLICY_WORKFLOW,\n"
        "\t\t\t\t\t       VFS_SCOPE_SYSTEM, &lookup_status);",
        "ip = namei_scope_status(path, VFS_POLICY_PUBLIC,\n"
        "\t\t\t\t\t       VFS_SCOPE_NONE, &lookup_status);",
        1,
    ),
    "dynamic fallback crosses into the PUBLIC namespace",
)
for removed_interface in (
    "agent_observe_test_phase_read",
    "agent_observe_test_bind_boot_init",
    "SYS_agent_observe_test_phase_read",
):
    assert removed_interface not in "\n".join(
        (
            (ROOT / "os/loader.c").read_text(encoding="utf-8"),
            (ROOT / "os/syscall.c").read_text(encoding="utf-8"),
            (ROOT / "os/syscall_ids.h").read_text(encoding="utf-8"),
            (ROOT / "os/agent_observe_test.h").read_text(encoding="utf-8"),
            (ROOT / "user/include/agent.h").read_text(encoding="utf-8"),
            (ROOT / "user/lib/syscall.c").read_text(encoding="utf-8"),
            (ROOT / "user/lib/syscall_ids.h").read_text(encoding="utf-8"),
        )
    )
assert (
    'X("agentobsreboot_ucore", "agentobsreboot_ucore", \\\n'
    "\t  EXEC_MANIFEST_F_BOOT_SEALED, EXEC_MANIFEST_ROLE_ALL, 0, \\\n"
    "\t  EXEC_MANIFEST_VFS_PROFILE_WORKFLOW)"
) in exec_manifest


def validate_event_identity_exhaustion_workload(source: str) -> None:
    snapshot = c_tokens(function_body_named(source, "read_event_queue_state"))
    info = token_index(
        snapshot,
        (
            "check",
            "(",
            "agent_info",
            "(",
            "&",
            "info",
            ")",
            "==",
            "AGENT_STATUS_OK",
            ",",
            "message",
            ")",
            ";",
        ),
    )
    queued = token_index(
        snapshot,
        ("*", "queued", "=", "info", ".", "event_queue_count", ";"),
        info,
    )
    dropped = token_index(
        snapshot,
        ("*", "dropped", "=", "info", ".", "event_dropped", ";"),
        queued,
    )
    assert info < queued < dropped

    probe_source = function_body_named(source, "verify_event_identity_exhaustion")
    probe = c_tokens(probe_source)
    before_pattern = (
        "read_event_queue_state",
        "(",
        "&",
        "before_queued",
        ",",
        "&",
        "before_dropped",
        ",",
    )
    wake_pattern = (
        "check",
        "(",
        "agent_wake",
        "(",
        "getpid",
        "(",
        ")",
        ",",
        "&",
        "event",
        ")",
        "==",
        "AGENT_STATUS_NO_SPACE",
        ",",
    )
    after_pattern = (
        "read_event_queue_state",
        "(",
        "&",
        "after_queued",
        ",",
        "&",
        "after_dropped",
        ",",
    )
    invariant_pattern = (
        "check",
        "(",
        "after_queued",
        "==",
        "before_queued",
        "&&",
        "after_dropped",
        "==",
        "before_dropped",
        "+",
        "1",
        ",",
    )
    before = token_index(probe, before_pattern)
    wake = token_index(probe, wake_pattern, before)
    after = token_index(probe, after_pattern, wake)
    invariant = token_index(probe, invariant_pattern, after)
    unwatch_text = (
        'agent_unwatch(AGENT_EVENT_MESSAGE, "event-id-exhausted") ==\n'
        '\t      1,'
    )
    assert unwatch_text in probe_source
    assert before < wake < after < invariant
    assert token_count(probe, before_pattern) == 1
    assert token_count(probe, wake_pattern) == 1
    assert token_count(probe, after_pattern) == 1
    assert token_count(probe, invariant_pattern) == 1
    assert probe_source.count(unwatch_text) == 1


validate_event_identity_exhaustion_workload(workload)
expect_rejected(
    validate_event_identity_exhaustion_workload,
    workload.replace(
        "*queued = info.event_queue_count;",
        "*queued = info.event_dropped;",
        1,
    ),
    "event queue snapshot aliases the dropped counter",
)
expect_rejected(
    validate_event_identity_exhaustion_workload,
    workload.replace(
        "agent_wake(getpid(), &event) == AGENT_STATUS_NO_SPACE",
        "agent_wake(getpid(), &event) == AGENT_STATUS_OK",
        1,
    ),
    "event identity exhaustion accepts publication",
)
expect_rejected(
    validate_event_identity_exhaustion_workload,
    workload.replace(
        "read_event_queue_state(&after_queued, &after_dropped,",
        "read_event_queue_state(&before_queued, &before_dropped,",
        1,
    ),
    "post-failure event queue snapshot is missing",
)
expect_rejected(
    validate_event_identity_exhaustion_workload,
    workload.replace(
        "after_dropped == before_dropped + 1",
        "after_dropped == before_dropped",
        1,
    ),
    "event identity exhaustion does not account one drop",
)
expect_rejected(
    validate_event_identity_exhaustion_workload,
    workload.replace(
        'agent_unwatch(AGENT_EVENT_MESSAGE, "event-id-exhausted") ==\n'
        '\t      1,',
        'agent_unwatch(AGENT_EVENT_MESSAGE, "event-id-exhausted") ==\n'
        '\t      AGENT_STATUS_OK,',
        1,
    ),
    "event identity unwatch ignores the removed-count API",
)


def validate_identity_activity_unwatch(source: str) -> None:
    activity = function_body_named(source, "emit_identity_activity")
    wait = activity.index("agent_wait(&event, 50)")
    unwatch_text = (
        'agent_unwatch(AGENT_EVENT_MESSAGE, "observe-id") ==\n'
        '\t      1,'
    )
    assert activity.count(unwatch_text) == 1
    unwatch = activity.index(unwatch_text)
    assert wait < unwatch


validate_identity_activity_unwatch(workload)
expect_rejected(
    validate_identity_activity_unwatch,
    workload.replace(
        'agent_unwatch(AGENT_EVENT_MESSAGE, "observe-id") ==\n'
        '\t      1,',
        'agent_unwatch(AGENT_EVENT_MESSAGE, "observe-id") ==\n'
        '\t      AGENT_STATUS_OK,',
        1,
    ),
    "identity activity unwatch ignores the removed-count API",
)
wait_marker = (
    "agentobsreboot_ucore: timeline_wait_epoch_recheck=1 "
    "injection=2 retries=1 bounded_timeout=1"
)
assert "request.after_sequence == 2 && request.completion_token == 1" in workload
assert wait_marker in workload
thread_marker = (
    "agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 "
    "targeted=1 timeout=1 cleanup=1"
)
assert "thread_create(timeline_wait_thread" in workload
assert "request.bank_generation == 7" in workload
assert "request.returned == 1" in workload
assert "context.tool_id = TIMELINE_THREADS_TOOL_A" in workload
assert thread_marker in workload

observe_runner = (ROOT / "scripts/run-observe-recovery-tests.sh").read_text(
    encoding="utf-8"
)
reap_probe = (ROOT / "scripts/probes/observe-reap-state.c").read_text(
    encoding="utf-8"
)



def validate_host_owned_phase_runner(source: str) -> None:
    ordered = (
        'host_probe_compile "${TMPDIR_OBSERVE}/mkfs"',
        'make -B "${TMPDIR_OBSERVE}/kernel-build/kernel"',
        'image="${TMPDIR_OBSERVE}/observe-reboot.img"',
        'host_probe_run "${TMPDIR_OBSERVE}/mkfs" "${image}"',
        '--image "${image}" --expect empty',
        "run_boot boot0-cut",
        "--from empty --to phase0",
        "run_boot boot1",
        "--from phase0 --to phase1",
        "host_tools/agent_observe_disk_evidence.py",
        "run_boot boot2",
        "--from phase1 --to phase2",
        "run_boot boot3",
    )
    positions = []
    for fragment in ordered:
        assert source.count(fragment) == 1
        positions.append(source.index(fragment))
    assert positions == sorted(positions)
    mkfs_compile = source[positions[0] : positions[1]]
    assert mkfs_compile.count("-DAGENT_OBSERVE_PHASE_CONTROL_PROFILE") == 1
    crash = source[positions[1] : positions[2]]
    assert crash.count("DURABILITY_POWERCUT_TEST_PROFILE=1") == 1
    mkfs = source[positions[3] : positions[4]]
    assert mkfs.count(
        '"${TMPDIR_OBSERVE}/user-target/bin/agentobsreboot_ucore"'
    ) == 1
    phase1 = source[positions[8] : positions[9]]
    assert '--guest-log "${TMPDIR_OBSERVE}/boot1.log"' in phase1
    assert '--cut-log "${TMPDIR_OBSERVE}/boot0-cut.log"' in phase1
    phase2 = source[positions[11] : positions[12]]
    assert '--guest-log "${TMPDIR_OBSERVE}/boot2.log"' in phase2
    assert "--cut-log" not in phase2
    boot3 = source[positions[12] : source.index("grep -Fxq", positions[12])]
    assert "\tnatural" in boot3
    assert "agentobsphaseinit_ucore" not in source
    assert "phase-kernel-build" not in source
    assert "agent_observe_phase_control.py seal" not in source
    assert "local boot_kernel=" not in source
    assert '--kernel "${kernel}" --image "${image}"' in source
    assert "<<'PY'" not in source
    snapshot = source[source.index("snapshot_image_exclusive()") : positions[5]]
    for guard in (
        '[[ -d "${parent}" && ! -L "${parent}" &&',
        '! -e "${target}" && ! -e "${partial}"',
        'cp "${image}" "${partial}"',
        'mv "${partial}" "${target}"',
    ):
        assert guard in snapshot
    erased = source.index('OBSERVE_RECOVERY_ERASED_SNAPSHOT_FILE')
    assert positions[11] < erased < positions[12]


validate_host_owned_phase_runner(observe_runner)
expect_rejected(
    validate_host_owned_phase_runner,
    observe_runner.replace(
        "\t-DAGENT_OBSERVE_PHASE_CONTROL_PROFILE \\\n",
        "",
        1,
    ),
    "Runner omits the fixed-slot mkfs profile",
)
expect_rejected(
    validate_host_owned_phase_runner,
    observe_runner.replace(
        "--from empty --to phase0", "--from phase0 --to phase0", 1
    ),
    "Runner accepts a replayed phase0 predecessor",
)
expect_rejected(
    validate_host_owned_phase_runner,
    observe_runner.replace(
        '--cut-log "${TMPDIR_OBSERVE}/boot0-cut.log"',
        '--cut-log "${TMPDIR_OBSERVE}/boot1.log"',
        1,
    ),
    "phase1 no longer consumes the power-cut log",
)
expect_rejected(
    validate_host_owned_phase_runner,
    observe_runner.replace(
        "--from phase1 --to phase2", "--from phase2 --to phase2", 1
    ),
    "Runner accepts a replayed phase2 predecessor",
)
expect_rejected(
    validate_host_owned_phase_runner,
    observe_runner.replace(
        '--guest-log "${TMPDIR_OBSERVE}/boot2.log"',
        '--guest-log "${TMPDIR_OBSERVE}/boot1.log"',
        1,
    ),
    "phase2 consumes evidence from the wrong boot",
)
expect_rejected(
    validate_host_owned_phase_runner,
    observe_runner.replace("\tnatural\n\n", "\tcheckpoint\n\n", 1),
    "final boot no longer waits for a clean Guest exit",
)

assert '--marker "${marker}" --marker-mode "${marker_mode}"' in observe_runner
assert observe_runner.count(wait_marker) == 1
assert observe_runner.count(thread_marker) == 2
assert "prod-build/os/agent_observe_recovery.o" in observe_runner
assert "prod-build/os/agent_observe_timeline.o" in observe_runner
assert "bash scripts/test-identity-lease-deferred.sh" in observe_runner
assert "bash scripts/test-observe-reap-state.sh" in observe_runner
assert '#include "../../os/agent_observe_capacity.c"' in reap_probe
for probe_marker in (
    "five_slots=1 sticky_class=1",
    "same_workflow_abort=1 cross_scope=1",
    "zero_target_retry=1 same_token=1",
    "attach_generation_stable=1",
    "serial_target_token=1 reap_retry_same_token=1 reap_delivery_reissue=1 done_race=1 cookie_fields=6 delivery_retry=1 consume_once=1",
    "recover_pending_idempotent=1 recover_done_idempotent=1 conflict_closed=1 recover_authorized_promote=1 recovered_generation_token=1",
):
    assert probe_marker in reap_probe
assert "DURABILITY_POWERCUT_TEST_PROFILE=1" in observe_runner
assert '"agentobsreboot_ucore: lease_cut_alloc " powercut line-prefix' in observe_runner
agent_runner_source = (ROOT / "scripts/agent_test_runner.py").read_text(
    encoding="utf-8"
)
assert 'MARKER_LINE_PREFIX = "line-prefix"' in agent_runner_source
assert "line.startswith(self.marker)" in agent_runner_source
assert "print(notice, file=log" not in agent_runner_source
assert '_parse_cut_log(cut_log, "lease_cut_alloc")' in phase_control
assert '_parse_cut_log(guest_log, "lease_cut_successor")' in phase_control
assert "_validate_cut_successor(old_cut, new_cut)" in phase_control

observe_abi = (ROOT / "agent_observe_abi.h").read_text(encoding="utf-8")
assert "#define AGENT_OBSERVE_RECOVERY_VERSION_V1 1U" in observe_abi
assert "#define AGENT_OBSERVE_RECOVERY_VERSION    2U" in observe_abi
for field in (
    "struct agent_audit_record record;",
    "unsigned long long receipt_id;",
    "unsigned long long bank_generation;",
    "unsigned int durability;",
):
    assert field in observe_abi


def validate_recovery_receipt_export(source: str) -> None:
    body = function_body_named(source, "sys_agent_observe_recovery")
    tokens = c_tokens(body)
    assert (
        "request.version != AGENT_OBSERVE_RECOVERY_VERSION_V1 &&\n"
        "\t    request.version != AGENT_OBSERVE_RECOVERY_VERSION"
    ) in body
    required = (
        "sizeof(struct agent_observe_recovery_record)",
        "tail.receipt_id =\n\t\t\t\t\t\t\tagent_observe_recovery_entry.receipt_id;",
        "tail.bank_generation = bank_generation;",
        "tail.durability =\n\t\t\t\t\t\t\tAGENT_AUDIT_DURABILITY_DURABLE;",
        "agent_obsstore_snapshot_confirm(\n\t\t\t\t\tbank_generation)",
    )
    for fragment in required:
        assert body.count(fragment) == 1, fragment
    record_read = token_index(
        tokens,
        ("result", "=", "agent_obsstore_snapshot_record", "("),
    )
    generation_guard = token_index(
        tokens,
        ("if", "(", "result", "==", "AGENT_OBSSTORE_SNAPSHOT_RETRY", ")", "{"),
        record_read,
    )
    ready_guard = token_index(
        tokens,
        ("if", "(", "result", "!=", "AGENT_OBSSTORE_SNAPSHOT_READY", ")", "{"),
        generation_guard,
    )
    receipt_copy = token_index(
        tokens, ("tail", ".", "receipt_id", "=", "agent_observe_recovery_entry", ".", "receipt_id", ";")
    )
    assert token_count(
        tokens,
        ("agent_obsstore_snapshot_record", "("),
    ) == 1
    assert record_read < generation_guard < ready_guard < receipt_copy
    assert body.index("tail.bank_generation = bank_generation;") < body.rindex(
        "agent_obsstore_snapshot_confirm("
    )
    assert "agent_durable_section_" not in body


def validate_recovery_record_view(source: str) -> None:
    body = function_body_named(source, "agent_obsstore_snapshot_record")
    tokens = c_tokens(body)
    read = token_index(tokens, ("agent_durable_section_active_read", "("))
    generation = token_index(
        tokens,
        ("if", "(", "observed_generation", "!=", "bank_generation", ")"),
        read,
    )
    receipt = token_index(
        tokens, ("entry", ".", "receipt_id", "==", "0"), generation
    )
    record_hash = token_index(
        tokens, ("agent_observe_checkpoint_record_hash", "("), receipt
    )
    publish = token_index(
        tokens, ("view", "->", "record", "=", "entry", ".", "record", ";"), record_hash
    )
    assert read < generation < receipt < record_hash < publish


validate_recovery_receipt_export(recovery)
validate_recovery_record_view(store)
expect_rejected(
    validate_recovery_record_view,
    store.replace(
        "if (observed_generation != bank_generation)\n"
        "\t\treturn AGENT_OBSSTORE_SNAPSHOT_RETRY;\n"
        "\tif (entry.scope_id != scope_id",
        "if (0 && observed_generation != bank_generation)\n"
        "\t\treturn AGENT_OBSSTORE_SNAPSHOT_RETRY;\n"
        "\tif (entry.scope_id != scope_id",
    ),
    "Recovery READ ignored an active-bank rollover",
)
expect_rejected(
    validate_recovery_receipt_export,
    recovery.replace(
        "tail.receipt_id =\n\t\t\t\t\t\t\tagent_observe_recovery_entry.receipt_id;",
        "tail.receipt_id = 0;",
        1,
    ),
    "Recovery READ discarded the durable receipt identity",
)
expect_rejected(
    validate_recovery_receipt_export,
    recovery.replace(
        "tail.durability =\n\t\t\t\t\t\t\tAGENT_AUDIT_DURABILITY_DURABLE;",
        "tail.durability = AGENT_AUDIT_DURABILITY_PENDING;",
        1,
    ),
    "Recovery READ mislabeled durable evidence",
)

receipt_recovery_marker = (
    "agentobsreboot_ucore: receipt_recovery_exact=1 "
    "receipt_v1_compatible=1 bank_generation_bound=1"
)
assert receipt_recovery_marker in workload
assert receipt_recovery_marker in observe_runner
assert "request.version = AGENT_OBSERVE_RECOVERY_VERSION_V1;" in workload


def validate_recovery_workload_receipt_binding(source: str) -> None:
    tokens = c_tokens(function_body_named(source, "verify_recovery_records"))
    read = token_index(
        tokens,
        (
            "agent_observe_recovery",
            "(",
            "&",
            "request",
            ",",
            "records",
            ")",
            "==",
            "AGENT_STATUS_OK",
        ),
    )
    bank = token_index(
        tokens,
        (
            "records",
            "[",
            "i",
            "]",
            ".",
            "bank_generation",
            "==",
            "request",
            ".",
            "bank_generation",
        ),
        read,
    )
    sequence = token_index(
        tokens,
        (
            "records",
            "[",
            "i",
            "]",
            ".",
            "record",
            ".",
            "sequence",
            "==",
            "expected",
            "->",
            "receipt_sequence",
        ),
        bank,
    )
    record_hash = token_index(
        tokens,
        (
            "records",
            "[",
            "i",
            "]",
            ".",
            "record",
            ".",
            "record_hash",
            "==",
            "expected",
            "->",
            "receipt_record_hash",
        ),
        sequence,
    )
    receipt = token_index(
        tokens,
        (
            "records",
            "[",
            "i",
            "]",
            ".",
            "receipt_id",
            "==",
            "expected",
            "->",
            "receipt_id",
        ),
        record_hash,
    )
    accept = token_index(
        tokens, ("receipt_matched", "=", "1", ";"), receipt
    )
    enforce = token_index(
        tokens, ("check", "(", "receipt_matched", ","), accept
    )
    assert read < bank < sequence < record_hash < receipt < accept < enforce


validate_recovery_workload_receipt_binding(workload)
expect_rejected(
    validate_recovery_workload_receipt_binding,
    workload.replace(
        "records[i].bank_generation == request.bank_generation",
        "records[i].bank_generation != request.bank_generation",
        1,
    ),
    "Recovery workload accepts a record from another durable bank",
)
expect_rejected(
    validate_recovery_workload_receipt_binding,
    workload.replace(
        "records[i].receipt_id == expected->receipt_id",
        "records[i].receipt_id != expected->receipt_id",
        1,
    ),
    "Recovery workload accepts a mismatched receipt identity",
)
durable_identity_marker = (
    "agentobsreboot_ucore: boot1_durable_identity scope=%u "
    "lifecycle_id=%u lifecycle_generation=%llu agent_id=%u "
    "receipt_sequence=%llu receipt_record_hash=%llu receipt_id=%llu"
)
assert durable_identity_marker in workload
publish = 'evidence_publish_file "${image}" "observe-recovery-before-reap.img"'
assert publish in observe_runner
disk_verify = "host_tools/agent_observe_disk_evidence.py"
assert disk_verify in observe_runner
assert '--image "${image}" --guest-log "${TMPDIR_OBSERVE}/boot1.log"' in observe_runner
assert observe_runner.index("run_boot boot1") < observe_runner.index(publish)
assert observe_runner.index(disk_verify) < observe_runner.index(publish)
assert observe_runner.index(publish) < observe_runner.index("run_boot boot2")


def validate_identity_lease_contract(source: str) -> None:
    progress = c_tokens(function_body_named(source, "agent_identity_lease_progress"))
    prepare = token_index(
        progress, ("agent_identity_lease_prepare_locked", "(", ")")
    )
    publish = token_index(
        progress, ("agent_identity_lease_publish_locked", "(", ")")
    )
    persist = token_index(progress, ("result", "=", "persist", "("))
    unlock_before_persist = token_index(
        progress, ("intr_restore", "(", "enabled", ")", ";"), prepare
    )
    replicated_only = token_index(
        progress, ("if", "(", "result", ">", "0", ")", "{"), persist
    )
    assert prepare < unlock_before_persist < persist < replicated_only < publish
    assert token_count(progress, ("persist", "(", "&", "serial")) == 1
    assert token_count(progress, ("agent_identity_lease_publish_locked", "(")) == 1
    assert token_count(progress, ("intr_get", "(")) == 0
    assert token_count(progress, ("curr_thread", "(")) == 0
    assert token_count(progress, ("proc_thread_exit_requested", "(")) == 0

    publish_tokens = c_tokens(
        function_body_named(source, "agent_identity_lease_publish_locked")
    )
    assert token_count(
        publish_tokens, ("renew_requested", "=", "0", ";")
    ) == 1

    contains = c_tokens(
        function_body_named(source, "agent_identity_lease_allocator_contains")
    )
    assert token_count(contains, ("admission_ready", "&&", "end", "!=", "0")) == 1
    assert token_count(contains, ("id", "<", "end")) == 1

    advance = c_tokens(function_body_named(source, "agent_identity_lease_advance"))
    assert token_count(advance, ("end", "==", "0")) == 1
    assert token_count(advance, ("return", "0", ";")) >= 1

    maintain = c_tokens(function_body_named(source, "agent_identity_lease_maintain"))
    assert token_count(
        maintain, ("agent_identity_lease_progress", "(", ")")
    ) == 1
    assert token_count(
        maintain, ("agent_identity_lease_pending_locked", "(", ")")
    ) == 1
    pending = c_tokens(
        function_body_named(
            source, "agent_identity_lease_maintenance_pending"
        )
    )
    assert token_count(
        pending, ("agent_identity_lease_pending_locked", "(", ")")
    ) == 1

    for renew_name in (
        "agent_identity_lease_allocator_renew",
        "agent_identity_lease_lifecycle_renew",
    ):
        renew = c_tokens(function_body_named(source, renew_name))
        assert token_count(renew, ("agent_identity_lease_progress", "(")) == 0
        assert token_count(renew, ("renew_requested", "=", "1", ";")) == 1
        assert token_count(renew, ("agent_background_request", "(")) == 0
        assert token_count(renew, ("return", "-", "1", ";")) >= 1

    assert '#include "proc.h"' not in source
    assert "agent_background_request" not in source
    assert token_count(
        c_tokens(source), ("agent_identity_lease_progress", "(")
    ) == 3


lease_source = (ROOT / "os/agent_identity_lease.c").read_text(encoding="utf-8")
validate_identity_lease_contract(lease_source)
expect_rejected(
    validate_identity_lease_contract,
    lease_source.replace("if (result > 0)", "if (result >= 0)", 1),
    "pending lease persistence published allocator identities",
)
expect_rejected(
    validate_identity_lease_contract,
    lease_source.replace(
        "contained = agent_identity_leases.admission_ready &&\n\t\t    end != 0 && id < end;",
        "contained = agent_identity_leases.admission_ready && id < end;",
        1,
    ),
    "zero lease end stopped failing closed",
)
expect_rejected(
    validate_identity_lease_contract,
    lease_source.replace(
        "result = persist(&serial, &target);",
        "agent_identity_lease_publish_locked();\n\tresult = persist(&serial, &target);",
        1,
    ),
    "lease published before durable persistence",
)
expect_rejected(
    validate_identity_lease_contract,
    lease_source.replace(
        "/* Allocation paths never enter the sleeping persistence owner. */\n"
        "\treturn -1;",
        "return agent_identity_lease_progress();",
        1,
    ),
    "allocator boundary synchronously entered durable persistence",
)

core = (ROOT / "os/agent_core.c").read_text(encoding="utf-8")
core_init = function_body_named(core, "agent_core_init")
storage_init = function_body_named(core, "agent_core_storage_init")
background_maintain = c_tokens(
    function_body_named(core, "agent_background_maintain")
)
background_checkpoint = c_tokens(
    function_body_named(core, "agent_background_checkpoint")
)
store_lease_maintain = c_tokens(
    function_body_named(store, "agent_obsstore_lease_maintain")
)
assert core_init.index("agent_durable_section_init();") < core_init.index(
    "agent_identity_lease_init();"
) < core_init.index("agent_observe_init();")
assert "status = agent_metadata_durable_status();" in storage_init
assert "agent_metadata_admission_status()" not in storage_init
assert "if (agent_obsstore_storage_ready() < 0)" in storage_init
assert "Agent admission closed" in storage_init
thread_guard = token_index(background_maintain, ("if", "(", "t", "==", "0"))
lease_exit_gate = token_index(
    background_maintain,
    ("if", "(", "!", "proc_thread_exit_requested", "(", ")", "&&"),
)
lease_maintain = token_index(
    background_maintain, ("agent_obsstore_lease_maintain", "(", ")")
)
metadata_maintain = token_index(
    background_maintain, ("agent_metadata_background_maintain", "(", ")")
)
assert thread_guard < lease_exit_gate < lease_maintain < metadata_maintain
durable_ready = token_index(
    background_maintain,
    ("agent_metadata_durable_status", "(", ")", "==", "AGENT_STATUS_OK"),
    metadata_maintain,
)
storage_ready = token_index(
    background_maintain,
    ("agent_obsstore_storage_ready", "(", ")"),
    durable_ready,
)
assert metadata_maintain < durable_ready < storage_ready
assert token_count(
    background_maintain,
    ("agent_metadata_admission_status", "(", ")", "==", "AGENT_STATUS_OK"),
) == 0
assert token_count(
    store_lease_maintain, ("agent_identity_lease_maintain", "(", ")")
) == 1


def validate_background_checkpoint_contract(source: str) -> None:
    tokens = c_tokens(function_body_named(source, "agent_background_checkpoint"))
    thread_guard = token_index(tokens, ("if", "(", "t", "==", "0"))
    lease = token_index(
        tokens,
        ("lease_pending", "=", "agent_identity_lease_maintenance_pending", "(", ")", ";"),
    )
    pending = token_index(
        tokens, ("pending", "=", "agent_background_take", "(", ")", ";")
    )
    epoch = token_index(
        tokens, ("epoch_due", "=", "fs_epoch_should_commit", "(", ")", ";")
    )
    quiet = token_index(
        tokens,
        (
            "if", "(", "!", "pending", "&&", "!", "lease_pending", "&&",
            "!", "epoch_due", ")", "return", ";",
        ),
    )
    gate = token_index(
        tokens,
        ("if", "(", "fs_epoch_request_begin", "(", ")", "<", "0", ")"),
    )
    gate_open = token_index(tokens, ("{",), gate)
    gate_end = matching_brace(tokens, gate_open)
    gate_failure = tokens[gate_open : gate_end + 1]
    assert token_count(
        gate_failure, ("if", "(", "pending", "||", "lease_pending", ")")
    ) == 1
    assert token_count(
        gate_failure, ("agent_background_request", "(", ")", ";")
    ) == 1
    assert token_count(gate_failure, ("return", ";")) == 1
    maintain = token_index(
        tokens,
        (
            "if", "(", "pending", "||", "lease_pending", ")",
            "agent_background_maintain", "(", ")", ";",
        ),
        gate_end,
    )
    commit_check = token_index(
        tokens,
        ("if", "(", "fs_epoch_should_commit", "(", ")", ")"),
        maintain,
    )
    commit = token_index(
        tokens, ("fs_epoch_commit", "(", ")", ";"), commit_check
    )
    gate_end_call = token_index(
        tokens, ("fs_epoch_request_end", "(", ")", ";"), commit
    )
    assert thread_guard < lease < pending < epoch < quiet < gate
    assert gate_end < maintain < commit_check < commit < gate_end_call
    assert token_count(tokens, ("agent_background_take", "(", ")")) == 1
    assert token_count(tokens, ("fs_epoch_should_commit", "(", ")")) == 2
    assert token_count(tokens, ("agent_background_maintain", "(", ")")) == 1
    assert token_count(tokens, ("fs_epoch_request_end", "(", ")")) == 1
    assert token_count(tokens, ("for", "(")) == 0
    assert token_count(tokens, ("while", "(")) == 0


validate_background_checkpoint_contract(core)
expect_rejected(
    validate_background_checkpoint_contract,
    core.replace(
        "if (!pending && !lease_pending && !epoch_due)",
        "if (!pending && !lease_pending)",
        1,
    ),
    "filesystem epoch work was dropped from checkpoint admission",
)
expect_rejected(
    validate_background_checkpoint_contract,
    core.replace(
        "\tif (pending || lease_pending)\n\t\tagent_background_maintain();",
        "\tif (pending)\n\t\tagent_background_maintain();",
        1,
    ),
    "lease-only maintenance no longer enters the bounded pass",
)
expect_rejected(
    validate_background_checkpoint_contract,
    core.replace(
        "\tif (fs_epoch_should_commit())\n\t\t(void)fs_epoch_commit();",
        "\tif (epoch_due)\n\t\t(void)fs_epoch_commit();",
        1,
    ),
    "epoch commit used a stale pre-admission snapshot",
)
expect_rejected(
    validate_background_checkpoint_contract,
    core.replace(
        "\t\tif (pending || lease_pending)\n"
        "\t\t\tagent_background_request();",
        "\t\tif (pending)\n\t\t\tagent_background_request();",
        1,
    ),
    "failed gate admission stranded lease maintenance",
)

identity = (ROOT / "os/agent_identity.c").read_text(encoding="utf-8")
lifecycle_owner = (ROOT / "os/agent_lifecycle.c").read_text(encoding="utf-8")
workflow = (ROOT / "os/workflow_lifecycle.c").read_text(encoding="utf-8")
for owner in (identity, lifecycle_owner, workflow):
    assert '#include "agent_observe_store.h"' not in owner
    assert '#include "agent_identity_lease.h"' in owner

for source, allocator_name in (
    (ledger, "agent_observe_alloc_id"),
    (identity, "agent_identity_alloc_id"),
    (lifecycle_owner, "agent_lifecycle_alloc_control_id"),
):
    allocator_tokens = c_tokens(function_body_named(source, allocator_name))
    assert token_count(
        allocator_tokens, ("agent_identity_lease_allocator_renew", "(")
    ) == 1
    assert token_count(
        allocator_tokens, ("agent_identity_lease_progress", "(")
    ) == 0


def validate_receipt_persist_context_contract(
    store_source: str, context_source: str
) -> None:
    safe = c_tokens(
        function_body_named(
            context_source, "agent_observe_receipt_persist_context_safe"
        )
    )
    for pattern in (
        ("context", "->", "running"),
        ("context", "->", "kernel_work_depth", "!=", "0"),
        ("context", "->", "io_request_depth", "!=", "0"),
        ("context", "->", "buffer_holds", "==", "0"),
        ("context", "->", "fs_atomic_depth", "==", "0"),
        ("context", "->", "supervisor_previous_mask", "!=", "0"),
        (
            "context",
            "->",
            "sstatus",
            "&",
            "context",
            "->",
            "supervisor_previous_mask",
        ),
        ("!", "context", "->", "metadata_txn_owned"),
        ("!", "context", "->", "exit_requested"),
    ):
        assert token_count(safe, pattern) == 1

    persist = c_tokens(
        function_body_named(store_source, "agent_obsstore_receipt_persist")
    )
    assert token_count(persist, ("intr_get", "(")) == 0
    for pattern in (
        ("t", "=", "curr_thread", "(", ")"),
        ("context", ".", "kernel_work_depth", "=", "t", "->", "kernel_work_depth"),
        ("context", ".", "io_request_depth", "=", "t", "->", "io_request_depth"),
        ("context", ".", "buffer_holds", "=", "t", "->", "bio_buffer_holds"),
        ("context", ".", "fs_atomic_depth", "=", "t", "->", "bio_fs_atomic_depth"),
        ("context", ".", "sstatus", "=", "r_sstatus", "(", ")"),
        ("context", ".", "supervisor_previous_mask", "=", "SSTATUS_SPP"),
        ("context", ".", "metadata_txn_owned", "=", "agent_metadata_txn_owned", "("),
        ("context", ".", "exit_requested", "=", "proc_thread_exit_requested", "("),
    ):
        assert token_count(persist, pattern) == 1
    safe_call = token_index(
        persist, ("agent_observe_receipt_persist_context_safe", "(", "&", "context")
    )
    durable_call = token_index(
        persist, ("agent_durable_section_persist_scope", "("), safe_call
    )
    assert safe_call < durable_call


receipt_context_source = (
    ROOT / "os/agent_observe_persist_context.h"
).read_text(encoding="utf-8")
validate_receipt_persist_context_contract(store, receipt_context_source)
expect_rejected(
    lambda mutated: validate_receipt_persist_context_contract(
        mutated, receipt_context_source
    ),
    store.replace("context.sstatus = r_sstatus();", "context.sstatus = 0;", 1),
    "receipt persistence stopped rejecting supervisor interrupt context",
)
expect_rejected(
    lambda mutated: validate_receipt_persist_context_contract(
        mutated, receipt_context_source
    ),
    store.replace(
        "context.io_request_depth = t->io_request_depth;",
        "context.io_request_depth = 1;",
        1,
    ),
    "receipt persistence bypassed syscall I/O admission",
)
expect_rejected(
    lambda mutated: validate_receipt_persist_context_contract(store, mutated),
    receipt_context_source.replace(
        "context->io_request_depth != 0", "context->io_request_depth >= 0", 1
    ),
    "receipt context accepted an unadmitted I/O caller",
)


def validate_receipt_exact_contract(store_source: str, ledger_source: str) -> None:
    durable_status = c_tokens(
        function_body_named(store_source, "agent_obsstore_receipt_record_status")
    )
    replicated = token_index(
        durable_status, ("replicated", "=", "agent_obsstore_receipt_replicated", "(")
    )
    active_header = token_index(
        durable_status, ("agent_observe_active_header", "("), replicated
    )
    initial_active_fence = token_index(
        durable_status,
        (
            "replicated", "=", "agent_durable_section_active_replicated",
            "(", "generation", ")", ";",
        ),
        active_header,
    )
    active_record = token_index(
        durable_status,
        ("agent_durable_section_active_read", "("),
        initial_active_fence,
    )
    exact_lifecycle = token_index(
        durable_status,
        (
            "entry",
            ".",
            "record",
            ".",
            "workflow_lifecycle_id",
            "==",
            "lifecycle",
            ".",
            "id",
        ),
        active_record,
    )
    exact_sequence = token_index(
        durable_status,
        ("entry", ".", "record", ".", "sequence", "==", "sequence"),
        exact_lifecycle,
    )
    exact_hash = token_index(
        durable_status,
        ("entry", ".", "record", ".", "record_hash", "==", "record_hash"),
        exact_sequence,
    )
    exact_receipt = token_index(
        durable_status,
        ("entry", ".", "receipt_id", "==", "receipt_id"),
        exact_hash,
    )
    exact_found = token_index(
        durable_status, ("found_record", "=", "1", ";"), exact_receipt
    )
    active_confirm = token_index(
        durable_status,
        ("agent_observe_active_header", "("),
        exact_found,
    )
    generation_confirm = token_index(
        durable_status,
        ("if", "(", "confirmed_generation", "!=", "generation", ")"),
        active_confirm,
    )
    final_active_fence = token_index(
        durable_status,
        (
            "replicated", "=", "agent_durable_section_active_replicated", "(",
            "generation", ")", ";",
        ),
        generation_confirm,
    )
    durable_return = token_index(
        durable_status, ("return", "1", ";"), final_active_fence
    )
    assert token_count(durable_status, ("return", "1", ";")) == 1
    assert token_count(
        durable_status,
        ("agent_durable_section_active_replicated", "(", "generation", ")"),
    ) == 2
    assert replicated < active_header < initial_active_fence < active_record
    assert active_record < exact_lifecycle
    assert exact_lifecycle < exact_sequence < exact_hash < exact_receipt < exact_found
    assert exact_found < active_confirm < generation_confirm
    assert generation_confirm < final_active_fence < durable_return

    receipt_status = c_tokens(
        function_body_named(ledger_source, "agent_observe_receipt_status")
    )
    clear_receipt = token_index(
        receipt_status, ("*", "receipt_id", "=", "0", ";")
    )
    clear_durability = token_index(
        receipt_status,
        (
            "*",
            "durability",
            "=",
            "AGENT_AUDIT_DURABILITY_NOT_FOUND",
            ";",
        ),
        clear_receipt,
    )
    durable_fallback = token_index(
        receipt_status,
        (
            "supplied_receipt",
            "!=",
            "0",
            "&&",
            "agent_obsstore_receipt_record_status",
            "(",
        ),
        clear_durability,
    )
    fallback_target = token_index(
        receipt_status,
        ("supplied_receipt", ",", "0", ")", ">", "0"),
        durable_fallback,
    )
    fallback_id = token_index(
        receipt_status,
        ("*", "receipt_id", "=", "supplied_receipt", ";"),
        fallback_target,
    )
    fallback_durable = token_index(
        receipt_status,
        (
            "*",
            "durability",
            "=",
            "AGENT_AUDIT_DURABILITY_DURABLE",
            ";",
        ),
        fallback_id,
    )
    fallback_ok = token_index(
        receipt_status,
        ("return", "AGENT_STATUS_OK", ";"),
        fallback_durable,
    )
    exact_store = token_index(
        receipt_status, ("persisted", "=", "agent_obsstore_receipt_record_status", "(")
    )
    durable_map = token_index(
        receipt_status,
        ("persisted", ">", "0", "?", "AGENT_AUDIT_DURABILITY_DURABLE"),
        exact_store,
    )
    assert clear_receipt < clear_durability < durable_fallback < fallback_target
    assert fallback_target < fallback_id < fallback_durable < fallback_ok
    assert fallback_ok < exact_store < durable_map

    receipt_snapshot = c_tokens(
        function_body_named(ledger_source, "agent_observe_receipt_snapshot")
    )
    missing_status = token_index(
        receipt_snapshot,
        (
            "status",
            "=",
            "supplied_receipt",
            "==",
            "0",
            "?",
            "AGENT_STATUS_NOT_FOUND",
            ":",
            "AGENT_STATUS_STALE",
            ";",
        ),
    )
    lookup = token_index(
        receipt_snapshot,
        ("for", "(", "uint", "i", "=", "0", ";"),
        missing_status,
    )
    assert missing_status < lookup


validate_receipt_exact_contract(store, ledger)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(mutated, ledger),
    store.replace(
        "entry.record.record_hash == record_hash",
        "entry.record.record_hash != record_hash",
        1,
    ),
    "receipt accepted a different durable record hash",
)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(store, mutated),
    ledger.replace(
        "persisted > 0 ? AGENT_AUDIT_DURABILITY_DURABLE",
        "persisted >= 0 ? AGENT_AUDIT_DURABILITY_DURABLE",
        1,
    ),
    "pending receipt was promoted to durable",
)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(store, mutated),
    ledger.replace(
        "int status = supplied_receipt == 0 ? AGENT_STATUS_NOT_FOUND :\n"
        "\t\t\t\t\t      AGENT_STATUS_STALE;",
        "int status = AGENT_STATUS_NOT_FOUND;",
        1,
    ),
    "evicted opaque receipt lost its explicit stale terminal state",
)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(store, mutated),
    ledger.replace(
        "supplied_receipt, 0) > 0",
        "supplied_receipt, 0) < 0",
        1,
    ),
    "receipt durable fallback accepted a missing or invalid active record",
)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(mutated, ledger),
    store.replace(
        "if (confirmed_generation != generation)",
        "if (0 && confirmed_generation != generation)",
        1,
    ),
    "receipt positive result ignored active-bank rollover",
)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(mutated, ledger),
    store.replace(
        "replicated = agent_durable_section_active_replicated(generation);",
        "replicated = 1;",
        1,
    ),
    "targetless receipt scanned an unreplicated primary generation",
)
expect_rejected(
    lambda mutated: validate_receipt_exact_contract(mutated, ledger),
    store.replace(
        "replicated = agent_durable_section_active_replicated(\n"
        "\t\t\t\t\tgeneration);",
        "replicated = 1;",
        1,
    ),
    "targetless receipt returned after its mirror fence was invalidated",
)


def validate_observation_recursion_suppression(
    ledger_source: str,
    facade_source: str,
    scheduler_source: str,
    proc_source: str,
    proc_header_source: str,
    query_source: str,
) -> None:
    assert (
        "uchar agent_observe_suppress_depth;" in proc_header_source
    )
    assert proc_header_source.index("uchar agent_loop_state;") < proc_header_source.index(
        "uchar agent_observe_suppress_depth;"
    ) < proc_header_source.index(
        "struct agent_timeline_wait_state *agent_timeline_wait_state;"
    )

    enter_guard = c_tokens(
        function_body_named(
            ledger_source, "agent_observe_recording_suppress_begin"
        )
    )
    current = token_index(
        enter_guard, ("t", "=", "curr_thread", "(", ")", ";")
    )
    running = token_index(
        enter_guard, ("t", "->", "process", "!=", "p"), current
    )
    overflow = token_index(
        enter_guard,
        ("t", "->", "agent_observe_suppress_depth", "=="),
        running,
    )
    enter = token_index(
        enter_guard,
        ("t", "->", "agent_observe_suppress_depth", "++", ";"),
        overflow,
    )
    restore = token_index(enter_guard, ("intr_restore", "(", "enabled", ")"), enter)
    accepted = token_index(enter_guard, ("return", "0", ";"), restore)
    assert current < running < overflow < enter < restore < accepted
    assert token_count(
        enter_guard,
        ("t", "->", "agent_observe_suppress_depth", "++", ";"),
    ) == 1

    leave_guard = c_tokens(
        function_body_named(ledger_source, "agent_observe_recording_suppress_end")
    )
    leave_same_process = token_index(
        leave_guard, ("t", "->", "process", "!=", "p")
    )
    underflow = token_index(
        leave_guard,
        ("t", "->", "agent_observe_suppress_depth", "==", "0"),
        leave_same_process,
    )
    underflow_panic = token_index(leave_guard, ("panic", "("), underflow)
    leave = token_index(
        leave_guard,
        ("t", "->", "agent_observe_suppress_depth", "--", ";"),
        underflow_panic,
    )
    leave_restore = token_index(
        leave_guard, ("intr_restore", "(", "enabled", ")"), leave
    )
    assert leave_same_process < underflow < underflow_panic < leave < leave_restore
    assert token_count(
        leave_guard,
        ("t", "->", "agent_observe_suppress_depth", "--", ";"),
    ) == 1

    query_reserve = c_tokens(
        function_body_named(ledger_source, "agent_observe_query_reserve")
    )
    query_proc = token_index(
        query_reserve, ("p", "=", "curr_proc", "(", ")", ";")
    )
    query_enter = token_index(
        query_reserve,
        ("agent_observe_recording_suppress_begin", "(", "p", ")"),
        query_proc,
    )
    query_enter_failure = token_index(
        query_reserve, ("return", "-", "1", ";"), query_enter
    )
    checkpoint = token_index(
        query_reserve, ("kernel_work_checkpoint", "("), query_enter_failure
    )
    failure = token_index(
        query_reserve, ("result", "=", "-", "1", ";"), checkpoint
    )
    leave_query = token_index(
        query_reserve,
        ("agent_observe_recording_suppress_end", "(", "p", ")"),
        failure,
    )
    return_result = token_index(
        query_reserve, ("return", "result", ";"), leave_query
    )
    assert query_proc < query_enter < query_enter_failure < checkpoint
    assert checkpoint < failure < leave_query < return_result
    assert token_count(query_reserve[checkpoint:leave_query], ("return",)) == 0

    persist = c_tokens(
        function_body_named(ledger_source, "agent_observe_receipt_persist")
    )
    enter_call = token_index(
        persist,
        ("agent_observe_recording_suppress_begin", "(", "p", ")"),
    )
    durable_call = token_index(
        persist,
        ("agent_obsstore_receipt_persist", "(", "scope_id", ")"),
        enter_call,
    )
    leave_call = token_index(
        persist,
        ("agent_observe_recording_suppress_end", "(", "p", ")"),
        durable_call,
    )
    returned = token_index(persist, ("return", "status", ";"), leave_call)
    assert enter_call < durable_call < leave_call < returned

    predicate = c_tokens(
        function_body_named(ledger_source, "agent_observe_recording_suppressed")
    )
    same_process = token_index(
        predicate, ("t", "->", "process", "==", "p")
    )
    active = token_index(
        predicate,
        ("t", "->", "agent_observe_suppress_depth", "!=", "0"),
        same_process,
    )
    assert same_process < active

    receipt_query = c_tokens(
        function_body_named(query_source, "sys_agent_audit_receipt")
    )
    authorized = token_index(
        receipt_query, ("agent_identity_has_cap", "(", "p", ",", "AGENT_CAP_ORCHESTRATE", ")")
    )
    lifecycle = token_index(
        receipt_query,
        ("workflow_lifecycle_key_equal", "(", "requested", ",", "current", ")"),
        authorized,
    )
    query_enter = token_index(
        receipt_query,
        ("agent_observe_recording_suppress_begin", "(", "p", ")"),
        lifecycle,
    )
    enter_failure = token_index(
        receipt_query,
        ("return", "AGENT_STATUS_INDETERMINATE", ";"),
        query_enter,
    )
    reserve = token_index(
        receipt_query, ("agent_observe_query_reserve", "("), enter_failure
    )
    copyout_call = token_index(
        receipt_query, ("copy_status", "=", "copyout", "("), reserve
    )
    query_leave = token_index(
        receipt_query,
        ("agent_observe_recording_suppress_end", "(", "p", ")"),
        copyout_call,
    )
    final_return = token_index(
        receipt_query, ("return", "status", ";"), query_leave
    )
    assert authorized < lifecycle < query_enter < enter_failure < reserve
    assert reserve < copyout_call < query_leave < final_return
    assert token_count(
        receipt_query,
        ("agent_observe_recording_suppress_begin", "(", "p", ")"),
    ) == 1
    assert token_count(
        receipt_query,
        ("agent_observe_recording_suppress_end", "(", "p", ")"),
    ) == 1
    assert token_count(receipt_query[reserve:], ("return",)) == 2

    facade_contracts = (
        (
            "agent_observe_record_context",
            ("agent_observe_recording_suppressed", "(", "p", ")"),
            ("agent_observe_timeline_record_context", "("),
        ),
        (
            "agent_observe_record_event",
            ("agent_observe_recording_suppressed", "(", "actor", ")"),
            ("agent_observe_ledger_record_event", "("),
        ),
        (
            "agent_observe_record_effect",
            ("agent_observe_recording_suppressed", "(", "p", ")"),
            ("agent_observe_ledger_record_effect", "("),
        ),
    )
    for function_name, suppress_pattern, publish_pattern in facade_contracts:
        body = c_tokens(function_body_named(facade_source, function_name))
        suppress = token_index(body, suppress_pattern)
        publish = token_index(body, publish_pattern, suppress)
        assert suppress < publish

    for retired_producer in (
        "agent_observe_record_prefetch",
        "agent_observe_record_prefetch_handoff_locked",
        "agent_observe_ledger_record_prefetch",
        "agent_observe_ledger_record_prefetch_handoff_locked",
    ):
        assert retired_producer not in facade_source
        assert retired_producer not in ledger_source

    sched_facade = c_tokens(
        function_body_named(facade_source, "agent_observe_record_sched")
    )
    explicit_thread = token_index(
        sched_facade, ("p", "=", "t", "->", "process")
    )
    sched_suppress = token_index(
        sched_facade,
        ("t", "->", "agent_observe_suppress_depth", "!=", "0"),
        explicit_thread,
    )
    sched_timeline = token_index(
        sched_facade,
        ("agent_observe_timeline_record_sched", "("),
        sched_suppress,
    )
    sched_ledger = token_index(
        sched_facade,
        ("agent_observe_ledger_record_sched", "("),
        sched_timeline,
    )
    assert explicit_thread < sched_suppress < sched_timeline < sched_ledger
    dispatch = c_tokens(function_body_named(scheduler_source, "agent_sched_on_dispatch"))
    token_index(
        dispatch,
        ("agent_observe_record_sched", "(", "t", ",", "&", "record", ")"),
    )
    for ring_field in (
        "sched_records",
        "agent_sched_trace_head",
        "agent_sched_trace_count",
    ):
        assert ring_field not in dispatch

    reset = c_tokens(function_body_named(ledger_source, "agent_observe_thread_reset"))
    reset_guard = token_index(
        reset,
        ("t", "->", "agent_observe_suppress_depth", "!=", "0"),
    )
    reset_clear = token_index(
        reset,
        ("t", "->", "agent_observe_suppress_depth", "=", "0", ";"),
        reset_guard,
    )
    timeline_load = token_index(
        reset, ("state", "=", "t", "->", "agent_timeline_wait_state", ";")
    )
    early_return = token_index(
        reset, ("if", "(", "state", "==", "0", ")"), timeline_load
    )
    assert reset_guard < reset_clear < timeline_load < early_return
    assert proc_source.count("agent_observe_suppress_depth = 0;") >= 3
    dying = c_tokens(function_body_named(proc_source, "scheduler_finish_dying_thread"))
    token_index(
        dying, ("t", "->", "agent_observe_suppress_depth", "!=", "0")
    )


observe_facade = (ROOT / "os/agent_observe.c").read_text(encoding="utf-8")
suppression_proc = (ROOT / "os/proc.c").read_text(encoding="utf-8")
suppression_proc_header = (ROOT / "os/proc.h").read_text(encoding="utf-8")
suppression_query = (ROOT / "os/agent_observe_audit_query.c").read_text(
    encoding="utf-8"
)
validate_observation_recursion_suppression(
    ledger,
    observe_facade,
    core_source,
    suppression_proc,
    suppression_proc_header,
    suppression_query,
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace("\tt->agent_observe_suppress_depth++;", "\t(void)t;", 1),
    "observation suppression stopped entering its guarded region",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace("\tt->agent_observe_suppress_depth--;", "\t(void)t;", 1),
    "observation suppression leaked its guarded region",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace(
        "\tif (agent_observe_recording_suppress_begin(p) < 0)",
        "\tif (0)",
        1,
    ),
    "observation query work escaped self-observation suppression",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace(
        "\tagent_observe_recording_suppress_end(p);",
        "\t(void)p;",
        1,
    ),
    "observation query leaked self-observation suppression",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace(
        "\t\t\tresult = -1;\n\t\t\tbreak;",
        "\t\t\treturn -1;",
        1,
    ),
    "observation query failure bypassed suppression cleanup",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace("t->process == p &&", "t->process != p &&", 1),
    "observation suppression lost its same-process binding",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        ledger,
        mutated,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    observe_facade.replace(
        "record == 0 || agent_observe_recording_suppressed(p)",
        "record == 0",
        1,
    ),
    "context observation bypassed persistence suppression",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        ledger,
        mutated,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    observe_facade.replace(
        "t->agent_observe_suppress_depth != 0",
        "t->agent_observe_suppress_depth == 0",
        1,
    ),
    "dispatch observation inverted its explicit-thread suppression",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        ledger,
        mutated,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    observe_facade.replace(
		"(p = t->process) == 0 ||\n"
		"\t    t->agent_observe_suppress_depth != 0)\n"
		"\t\treturn;\n"
		"\tagent_observe_timeline_record_sched(p, record);",
		"(p = t->process) == 0)\n"
		"\t\treturn;\n"
		"\tagent_observe_timeline_record_sched(p, record);\n"
		"\tif (t->agent_observe_suppress_depth != 0)\n"
		"\t\treturn;",
        1,
    ),
    "suppressed dispatch published a query-visible SCHED record",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        mutated,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        suppression_query,
    ),
    ledger.replace("\tt->agent_observe_suppress_depth = 0;", "\t(void)t;", 1),
    "thread reset retained observation suppression",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        ledger,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        mutated,
    ),
    suppression_query.replace(
        "\tif (agent_observe_recording_suppress_begin(p) < 0)",
        "\tif (0)",
        1,
    ),
    "receipt query work escaped observation suppression",
)
expect_rejected(
    lambda mutated: validate_observation_recursion_suppression(
        ledger,
        observe_facade,
        core_source,
        suppression_proc,
        suppression_proc_header,
        mutated,
    ),
    suppression_query.replace(
        "\tagent_observe_recording_suppress_end(p);",
        "\t(void)p;",
        1,
    ),
    "receipt query leaked observation suppression",
)

emit = function_body_named(ledger, "agent_audit_emit")
assert emit.index("agent_obsstore_mark_dirty_receipt(") < emit.index(
    "agent_observe_audit_index_insert(scope_state, slot)"
)
dirty_receipt = function_body_named(store, "agent_obsstore_mark_dirty_receipt")
assert "workflow_lifecycle_key_valid(lifecycle)" in dirty_receipt
assert "agent_observe_capacity_claim(" in dirty_receipt
assert "scope_id, receipt_lifecycle, &receipt_serial" in emit
assert "agent_obsstore_mark_dirty(scope_id)" not in emit
slot_clear = function_body_named(ledger, "agent_audit_slot_clear")
assert "agent_audit_receipts[slot]" in slot_clear
capture_scope = function_body_named(ledger, "agent_observe_checkpoint_capture_scope")
restore_scope = function_body_named(ledger, "agent_observe_checkpoint_restore_scope")
assert "entry->receipt_id = agent_audit_receipts[slot].receipt_id" in capture_scope
assert "agent_audit_receipts[slot].receipt_id = entry->receipt_id" in restore_scope
store_validate = function_body_named(store, "agent_observe_store_validate")
checkpoint_entry_validate = function_body_named(
    ledger, "agent_observe_checkpoint_entry_validate"
)
assert "entry->receipt_id == 0" in checkpoint_entry_validate
assert "agent_observe_checkpoint_entry_validate(" in store_validate

receipt_query = (ROOT / "os/agent_observe_audit_query.c").read_text(
    encoding="utf-8"
)
receipt_syscall = function_body_named(receipt_query, "sys_agent_audit_receipt")
assert "agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)" in receipt_syscall
assert "workflow_lifecycle_key_equal(requested, current)" in receipt_syscall
assert "proc_thread_exit_requested()" in receipt_syscall
assert "AGENT_AUDIT_RECEIPT_WAIT_ATTEMPTS" in receipt_syscall
assert "#define AGENT_AUDIT_RECEIPT_WAIT_ATTEMPTS 128U" in receipt_query
assert "agent_observe_receipt_persist(scope_id)" in receipt_syscall
assert "agent_observe_test_evict_checkpoint_window(p)" in receipt_syscall

receipt_wait = function_body_named(workload, "receipt_wait_for_state")
assert (
    "status == AGENT_STATUS_OK && request.durability == expected &&\n"
    "\t\t    request.receipt_id == receipt_id"
) in receipt_wait
for terminal_guard in (
    "expected == AGENT_AUDIT_DURABILITY_FAILED",
    "status == AGENT_STATUS_STALE",
    "request.durability == AGENT_AUDIT_DURABILITY_NOT_FOUND",
    "request.receipt_id == 0",
):
    assert terminal_guard in receipt_wait
receipt_workload = function_body_named(workload, "verify_audit_receipts")
pending_injection = (
    "request.durability == AGENT_AUDIT_DURABILITY_PENDING,\n"
    '\t      "eviction injection preserves the exact pending receipt");'
)
failed_wait = (
    "receipt_wait_for_state(\n"
    "\t\t&record, receipt_id, AGENT_AUDIT_DURABILITY_FAILED);"
)
assert pending_injection in receipt_workload
assert failed_wait in receipt_workload
assert receipt_workload.index(pending_injection) < receipt_workload.index(failed_wait)
positive_target = '"append durable receipt target"'
positive_identity = "identity->receipt_sequence = record.sequence;"
assert receipt_workload.index(failed_wait) < receipt_workload.index(positive_target)
assert receipt_workload.index(positive_target) < receipt_workload.index(positive_identity)

syscall_source = (ROOT / "os/syscall.c").read_text(encoding="utf-8")
syscall_registry = (ROOT / "os/syscall_counter.h").read_text(encoding="utf-8")
syscall_dispatch = c_tokens(function_body_named(syscall_source, "syscall_dispatch"))
syscall_slow = c_tokens(function_body_named(syscall_source, "syscall_slow_path"))
syscall_begin = c_tokens(
    function_body_named(syscall_source, "syscall_transaction_begin")
)
kernel_ids = (ROOT / "os/syscall_ids.h").read_text(encoding="utf-8")
user_ids = (ROOT / "user/lib/syscall_ids.h").read_text(encoding="utf-8")
user_syscalls = (ROOT / "user/lib/syscall.c").read_text(encoding="utf-8")
user_header = (ROOT / "user/include/agent.h").read_text(encoding="utf-8")
for ids in (kernel_ids, user_ids):
    assert "SYS_agent_audit_receipt 557" in ids
assert "case SYS_agent_audit_receipt:" in syscall_source
assert "sys_agent_audit_receipt(trapframe->a0)" in syscall_source
assert "X(agent_audit_receipt, BLOCK_IO, ALWAYS)" in syscall_registry
transaction_begin = token_index(
    syscall_slow,
    ("syscall_transaction_begin", "(", "&", "transaction", ",", "trapframe", ")"),
)
dispatch_call = token_index(
    syscall_slow,
    ("syscall_dispatch", "(", "id", ",", "trapframe", ",", "&", "transaction", ")"),
)
assert transaction_begin < dispatch_call
io_begin = token_index(syscall_begin, ("bio_request_begin_current", "(", ")"))
io_marked = token_index(
    syscall_begin, ("io_admitted", "=", "1", ";"), io_begin
)
assert io_begin < io_marked
assert "int agent_audit_receipt(struct agent_audit_receipt_request *request)" in user_syscalls
assert "return syscall(SYS_agent_audit_receipt, request);" in user_syscalls
assert "int agent_audit_receipt(struct agent_audit_receipt_request *request);" in user_header

assert "AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_CUT" in test_owner
assert "AGENT_OBSERVE_RECOVERY_TEST_ALLOCATE_IDENTITY_SUCCESSOR" in test_owner
cut_allocate = function_body_named(ledger, "agent_observe_test_allocate_identity_ids")
for allocator_call in (
    "AGENT_IDENTITY_ALLOCATOR_AUDIT",
    "agent_observe_alloc_span_id()",
    "agent_observe_alloc_event_id()",
    "agent_lifecycle_alloc_control_id()",
    "agent_identity_alloc_id()",
    "workflow_lifecycle_test_consume_generation(",
):
    assert allocator_call in cut_allocate
test_execute = function_body_named(test_owner, "agent_observe_test_execute")
assert test_execute.index("agent_observe_test_allocate_identity_ids(&ids)") < test_execute.index(
    'printf("agentobsreboot_ucore: %s audit=%llu'
)
assert test_execute.index('printf("agentobsreboot_ucore: %s audit=%llu') < test_execute.index(
    "copyout(p->pagetable, recordsaddr"
)

receipt_marker = (
    "agentobsreboot_ucore: receipt_pending_not_evidence=1 "
    "receipt_durable_exact=1 receipt_fake_stale=1 "
    "receipt_window_not_evidence=1"
)
for marker in (
    receipt_marker,
    "agentobsreboot_ucore: receipt_teardown_stale=1",
    "agentobsreboot_ucore: live_reload_ledger_monotonic=1",
    "agentobsreboot_ucore: boot3_identity_successor=1",
    "agentobsreboot_ucore: receipt_permission_recovery_denied=1",
    "agentobsreboot_ucore: receipt_permission_not_agent=1",
):
    assert marker in workload
    assert marker in observe_runner

timeline = (ROOT / "os/agent_observe_timeline.c").read_text(encoding="utf-8")


def validate_timeline_wait_contract(source: str) -> None:
    wait_loop = c_tokens(function_body_named(source, "agent_timeline_wait_for_match"))
    enqueue = c_tokens(function_body_named(source, "agent_timeline_wait_enqueue_atomic"))
    sched_record = c_tokens(
        function_body_named(source, "agent_observe_timeline_record_sched")
    )
    ring_slot = token_index(
        sched_record,
        ("slot", "=", "p", "->", "agent_sched_trace_head", "AGENT_SCHED_TRACE_CAP"),
    )
    ring_copy = token_index(
        sched_record,
        (
            "memmove", "(", "&", "p", "->", "agent_ipc_observe_cold",
            "->", "sched_records",
        ),
        ring_slot,
    )
    ring_head = token_index(
        sched_record,
        ("p", "->", "agent_sched_trace_head", "="),
        ring_copy,
    )
    ring_count = token_index(
        sched_record,
        ("p", "->", "agent_sched_trace_count", "++", ";"),
        ring_head,
    )
    ring_convert = token_index(
        sched_record, ("agent_timeline_from_sched", "("), ring_count
    )
    ring_publish = token_index(
        sched_record,
        ("agent_observe_timeline_publish_locked", "("),
        ring_convert,
    )
    assert ring_slot < ring_copy < ring_head < ring_count < ring_convert < ring_publish
    export = token_index(
        wait_loop,
        (
            "agent_timeline_export", "(", "p", ",", "filter", ",",
            "0", ",", "0", ",", "&", "scan_epoch", ")",
        ),
    )
    test_window = token_index(wait_loop, ("AGENT_OBSERVE_TEST_TIMELINE_WINDOW",), export)
    enqueue_call = token_index(
        wait_loop, ("agent_timeline_wait_enqueue_atomic", "("), test_window
    )
    assert export < test_window < enqueue_call
    assert "agent_observe_scope_epoch" not in wait_loop
    assert "intr_save" not in wait_loop
    assert "wait_queue_sleep" not in wait_loop
    assert "agent_timeline_export" not in enqueue
    assert enqueue.count("intr_save") == 1

    lock = token_index(enqueue, ("intr_save", "(", ")"))
    expired = token_index(
        enqueue,
        (
            "expired",
            "=",
            "timeout_ticks",
            ">=",
            "0",
            "&&",
            "now",
            "-",
            "start",
            ">=",
            "(",
            "uint64",
            ")",
            "timeout_ticks",
            ";",
        ),
        lock,
    )
    epoch_load = token_index(
        enqueue,
        (
            "current_epoch",
            "=",
            "agent_observe_scope_epoch",
            "(",
            "scope_id",
            ")",
            ";",
        ),
        expired,
    )
    recheck = token_index(
        enqueue,
        ("if", "(", "current_epoch", "!=", "scan_epoch", ")", "{"),
        epoch_load,
    )
    bounded = token_index(
        enqueue,
        (
            "if",
            "(",
            "expired",
            "&&",
            "*",
            "deadline_rescan_used",
            ")",
        ),
        recheck,
    )
    mark_final = token_index(
        enqueue,
        ("*", "deadline_rescan_used", "=", "1", ";"),
        bounded,
    )
    test_recheck = token_index(
        enqueue, ("AGENT_OBSERVE_TEST_TIMELINE_RECHECK",), mark_final
    )
    retry = token_index(
        enqueue,
        (
            "intr_restore",
            "(",
            "enabled",
            ")",
            ";",
            "return",
            "AGENT_TIMELINE_WAIT_RETRY",
            ";",
        ),
        test_recheck,
    )
    timeout = token_index(
        enqueue,
        ("if", "(", "expired", ")", "goto", "timeline_timeout", ";"),
        retry,
    )
    filter_publish = token_index(
        enqueue,
        ("memmove", "(", "&", "state", "->", "filter"),
        timeout,
    )
    waiter_publish = token_index(
        enqueue,
        ("agent_observe_timeline_waiter_publish", "(", "t", ",", "state", ")"),
        filter_publish,
    )
    sleep = token_index(
        enqueue,
        ("wait_queue_sleep_key_irq", "(", "&", "p", "->", "agent_timeline_waiters", ",", "state", "->", "thread_generation", ")"),
        waiter_publish,
    )
    unpublish = token_index(
        enqueue,
        ("agent_observe_timeline_waiter_unpublish", "(", "t", ",", "state", ")"),
        sleep,
    )
    unlock = token_index(
        enqueue, ("intr_restore", "(", "enabled", ")"), unpublish
    )
    timeout_label = token_index(enqueue, ("timeline_timeout", ":"), unlock)
    timeout_return = token_index(
        enqueue,
        (
            "intr_restore",
            "(",
            "enabled",
            ")",
            ";",
            "return",
            "AGENT_STATUS_TIMEOUT",
            ";",
        ),
        timeout_label,
    )
    assert lock < expired < epoch_load < recheck < bounded < mark_final
    assert mark_final < test_recheck < retry < timeout
    assert timeout < filter_publish < waiter_publish < sleep < unpublish < unlock
    assert unlock < timeout_label < timeout_return

    timeline_export = function_body_named(source, "agent_timeline_export")
    assert (
        "scan_epoch_out == 0 && max == 0" in timeline_export
    )
    scan_window = (
        "for (;;) {\n"
        "\t\tcandidate_epoch = scan_epoch_out != 0 ?\n"
        "\t\t\tagent_observe_scope_epoch(agent_identity_proc_scope(p)) : 0;"
    )
    assert scan_window in timeline_export
    sample = timeline_export.index("candidate_epoch =", timeline_export.index("for (;;)") )
    recount = timeline_export.index("context_visible =", sample)
    capacity = timeline_export.index("scan_visible <= reserved", recount)
    publish = timeline_export.index("*scan_epoch_out = candidate_epoch", capacity)
    reserve = timeline_export.index("agent_observe_query_reserve_to", publish)
    snapshot_start = timeline_export.index("span_id = p->agent_current_span_id", reserve)
    assert sample < recount < capacity < publish < reserve < snapshot_start


validate_timeline_wait_contract(timeline)
proc_header = (ROOT / "os/proc.h").read_text(encoding="utf-8")
for removed_singleton in (
    "agent_timeline_waiting",
    "agent_timeline_wait_deadline_valid",
    "agent_timeline_wait_deadline",
    "agent_timeline_wait_filter",
):
    assert removed_singleton not in proc_header
assert "struct agent_timeline_wait_state *agent_timeline_wait_state" in proc_header
tick = c_tokens(function_body_named(timeline, "agent_observe_tick_proc"))
assert token_count(tick, ("for", "(", "int", "tid", "=", "0", ";")) == 1
assert token_count(tick, ("t", "->", "agent_timeline_wait_state")) == 1
assert token_count(tick, ("agent_observe_timeline_waiter_wake", "(", "t", ")")) == 1
proc_source = (ROOT / "os/proc.c").read_text(encoding="utf-8")
assert proc_source.count("agent_observe_thread_reset(") >= 4
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "\tp->agent_sched_trace_count++;",
        "\t(void)p;",
        1,
    ),
    "SCHED timeline publication stopped committing its owned ring record",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "agent_timeline_export(p, filter, 0, 0,\n\t\t\t\t\t       &scan_epoch)",
        "agent_timeline_export(p, filter, 0, 0, 0)",
        1,
    ),
    "timeline scan epoch disconnected from export",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "for (;;) {\n"
        "\t\tcandidate_epoch = scan_epoch_out != 0 ?\n"
        "\t\t\tagent_observe_scope_epoch(agent_identity_proc_scope(p)) : 0;",
        "candidate_epoch = scan_epoch_out != 0 ?\n"
        "\t\tagent_observe_scope_epoch(agent_identity_proc_scope(p)) : 0;\n"
        "\tfor (;;) {",
        1,
    ),
    "timeline scan epoch sampled before reservation retry",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "if (current_epoch != scan_epoch)",
        "if (0 && current_epoch != scan_epoch)",
        1,
    ),
    "timeline final epoch recheck disabled",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "if (expired && *deadline_rescan_used)",
        "if (0 && expired && *deadline_rescan_used)",
        1,
    ),
    "timeline deadline retry remained unbounded",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "\t\tintr_restore(enabled);\n"
        "\t\treturn AGENT_TIMELINE_WAIT_RETRY;",
        "\t\treturn AGENT_TIMELINE_WAIT_RETRY;",
        1,
    ),
    "timeline retry returned with interrupts disabled",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "\tintr_restore(enabled);\n\treturn AGENT_STATUS_TIMEOUT;",
        "\treturn AGENT_STATUS_TIMEOUT;",
        1,
    ),
    "timeline timeout returned with interrupts disabled",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "wait_queue_sleep_key_irq(\n\t\t&p->agent_timeline_waiters, state->thread_generation)",
        "wait_queue_sleep_irq(&p->agent_timeline_waiters)",
        1,
    ),
    "timeline waiter lost its per-thread key",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "memmove(&state->filter, filter, sizeof(state->filter))",
        "memmove(filter, filter, sizeof(*filter))",
        1,
    ),
    "timeline waiter filter stopped being thread-local",
)
expect_rejected(
    validate_timeline_wait_contract,
    timeline.replace(
        "agent_observe_timeline_waiter_unpublish(t, state);\n\tif (wait_status",
        "(void)t;\n\tif (wait_status",
        1,
    ),
    "timeline finish stopped clearing only its caller",
)

wait_source = (ROOT / "os/wait.c").read_text(encoding="utf-8")
sleep_irq = function_body(wait_source, "int wait_queue_sleep_irq(")
sleep_mode = function_body(wait_source, "wait_queue_sleep_mode(")
assert "return wait_queue_sleep_mode(q, 1, 0)" in sleep_irq
assert sleep_mode.index("intr_save()") < sleep_mode.index("t->wait_channel = q")
assert sleep_mode.index("t->wait_channel = q") < sleep_mode.index("t->state = SLEEPING")
assert sleep_mode.index("t->state = SLEEPING") < sleep_mode.index("sched()")


def wait_attempt(publish_phase: str) -> str:
    epoch = 1
    record = False
    queued = False
    woken = False

    def publish() -> None:
        nonlocal epoch, record, woken
        epoch += 1
        record = True
        if queued:
            woken = True

    if publish_phase == "before_snapshot":
        publish()
    scan_epoch = epoch
    if publish_phase == "after_snapshot":
        publish()
    matched = record
    if publish_phase == "export_miss":
        matched = False
        publish()
    if matched:
        return "matched"
    if publish_phase == "after_export":
        publish()
    if epoch != scan_epoch:
        return "retry"
    queued = True
    if publish_phase in ("atomic_window", "after_enqueue"):
        # An interrupt in the atomic window is deferred until publication.
        publish()
    return "woken" if woken else "sleeping"


assert wait_attempt("before_snapshot") == "matched"
assert wait_attempt("after_snapshot") == "matched"
assert wait_attempt("export_miss") == "retry"
assert wait_attempt("after_export") == "retry"
assert wait_attempt("atomic_window") == "woken"
assert wait_attempt("after_enqueue") == "woken"
assert wait_attempt("none") == "sleeping"


def deadline_churn_attempts() -> tuple[str, int]:
    final_rescan_used = False
    retries = 0
    for _ in range(2):
        expired = True
        epoch_changed = True
        if epoch_changed:
            if expired and final_rescan_used:
                return "timeout", retries
            if expired:
                final_rescan_used = True
            retries += 1
    return "unbounded", retries


assert deadline_churn_attempts() == ("timeout", 1)

print("[observe-recovery-contract] exhaustion, hook and wait window: valid")
