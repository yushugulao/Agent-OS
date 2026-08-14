#!/usr/bin/env python3
"""Static, mutation, and model contracts for Agent execution contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "include/agent_execution_contract_abi.h",
    "os/agent_execution_contract.h",
    "os/agent_execution_contract.c",
    "os/agent_core.c",
    "os/agent_task_bridge.c",
    "os/agent_context.c",
    "os/agent_evidence_ring.c",
    "os/agent_ipc.c",
    "os/agent_observe.c",
    "os/agent_provenance.c",
    "os/agent_tool_protocol.c",
    "os/resource_controller.h",
    "os/resource_controller.c",
    "os/proc.c",
    "os/syscall.c",
)
SOURCES = {
    name: (ROOT / name).read_text(encoding="utf-8")
    for name in FILES
}


class ContractError(AssertionError):
    pass


def require(source: str, needle: str, message: str) -> None:
    if needle not in source:
        raise ContractError(message)


def forbid(source: str, needle: str, message: str) -> None:
    if needle in source:
        raise ContractError(message)


def require_regex(source: str, pattern: str, message: str) -> None:
    if re.search(pattern, source, re.S) is None:
        raise ContractError(message)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        raise ContractError(f"missing function {name}")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise ContractError(f"unterminated function {name}")


def require_order(source: str, needles: tuple[str, ...], message: str) -> None:
    cursor = -1
    for needle in needles:
        found = source.find(needle, cursor + 1)
        if found < 0:
            raise ContractError(f"{message}: missing {needle!r}")
        if found <= cursor:
            raise ContractError(message)
        cursor = found


def validate_llm_correlation(core: str) -> None:
    request = function_body(core, "agent_llm_pending_request")
    response = function_body(core, "agent_llm_pending_response")
    clear = function_body(core, "agent_llm_pending_proc_clear")
    reap = function_body(core, "agent_llm_pending_reap_locked")
    deadline = function_body(core, "agent_llm_pending_deadline")
    terminal = function_body(core, "agent_llm_terminal_remember_locked")
    terminal_status = function_body(core, "agent_llm_terminal_status_locked")
    execute = function_body(core, "agent_execute_op")
    init = function_body(core, "agent_core_init")
    tick = function_body(core, "agent_core_tick")
    teardown = function_body(core, "agent_core_proc_teardown")
    exec_public = function_body(core, "agent_core_exec_public_commit")
    execute_one = function_body(core, "agent_execute_one")

    require(
        core,
        "static struct agent_llm_pending agent_llm_pending[AGENT_LLM_PENDING_MAX];",
        "LLM correlations need a bounded kernel-owned pending table",
    )
    require(
        core,
        "#define AGENT_LLM_PENDING_TTL_TICKS (660ULL * TICKS_PER_SEC)",
        "LLM response authority must cover the 600-second Host deadline plus slack",
    )
    require(
        core,
        "agent_llm_requesters[AGENT_LLM_PENDING_MAX];",
        "LLM correlations need bounded per-requester monotonic state",
    )
    require(
        core,
        "agent_llm_terminal_history[AGENT_LLM_TERMINAL_HISTORY_MAX];",
        "expired and consumed responses need bounded replay history",
    )
    for field in (
        "struct workflow_lifecycle_key lifecycle;",
        "uint64 requester_control_id;",
        "uint64 relay_control_id;",
        "uint64 corr_id;",
        "uint64 deadline_tick;",
        "int requester_pid;",
        "int relay_pid;",
        "int active;",
    ):
        require(core, field, f"LLM pending records lost {field}")
    require(
        init,
        "memset(agent_llm_pending, 0, sizeof(agent_llm_pending));",
        "LLM pending state must start empty",
    )
    require(
        init,
        "memset(agent_llm_requesters, 0, sizeof(agent_llm_requesters));",
        "LLM monotonic state must start empty",
    )
    require(
        init,
        "memset(agent_llm_terminal_history, 0,",
        "LLM replay history must start empty",
    )

    require(
        request,
        "relay_pid <= 0 || corr_id == 0",
        "LLM requests must reject null relay or correlation identities",
    )
    require(
        request,
        "pending->corr_id == corr_id",
        "a requester lifecycle must not publish duplicate correlations",
    )
    require(
        request,
        "status = AGENT_STATUS_DUPLICATE;",
        "duplicate LLM requests need a stable terminal status",
    )
    require(
        request,
        "corr_id <= requester_state->last_corr_id",
        "published correlation IDs must remain strictly monotonic",
    )
    require(
        request,
        "status = AGENT_STATUS_CONFLICT;",
        "non-monotonic correlation IDs need a distinct status",
    )
    require_order(
        request,
        (
            "agent_llm_pending_reap_locked(now);",
            "free_slot < 0 ||",
            "status = agent_ipc_deliver_pid(",
            "if (ipc_delivered != 1)",
            "pending->lifecycle = lifecycle;",
            "pending->requester_control_id = requester->agent_control_id;",
            "pending->relay_control_id = relay->agent_control_id;",
            "pending->corr_id = corr_id;",
            "pending->deadline_tick = agent_llm_pending_deadline(now);",
            "pending->requester_pid = requester->pid;",
            "pending->relay_pid = relay->pid;",
            "pending->active = 1;",
            "requester_state->last_corr_id = corr_id;",
        ),
        "pending publication and monotonic advance must follow successful delivery",
    )
    require_order(
        request,
        (
            "*pending_limit = 1;",
            "status = AGENT_STATUS_NO_SPACE;",
        ),
        "pending-table exhaustion must be distinguishable from IPC queue exhaustion",
    )

    for check in (
        "candidate->requester_pid != requester_pid",
        "candidate->corr_id != corr_id",
        "pending->relay_pid != relay->pid",
        "pending->relay_control_id != relay->agent_control_id",
        "workflow_lifecycle_key_equal(pending->lifecycle, lifecycle)",
        "requester->agent_control_id != pending->requester_control_id",
    ):
        require(response, check, f"LLM response matching lost {check}")
    require(
        response,
        "status = AGENT_STATUS_DENIED;",
        "unsolicited, wrong-relay, wrong-correlation, and replay responses must deny",
    )
    require_order(
        response,
        (
            "agent_llm_pending_reap_locked(now);",
            "status = agent_ipc_deliver_pid(",
            "if (ipc_delivered != 1)",
            "agent_llm_terminal_remember_locked(pending, AGENT_STATUS_OK);",
            "memset(pending, 0, sizeof(*pending));",
            "*delivered = 1;",
        ),
        "LLM response authority must be consumed only after successful delivery",
    )

    require_order(
        reap,
        (
            "now < pending->deadline_tick",
            "agent_llm_terminal_remember_locked(pending, AGENT_STATUS_TIMEOUT);",
            "memset(pending, 0, sizeof(*pending));",
        ),
        "expired LLM requests must become timeout tombstones before freeing capacity",
    )
    require(
        deadline,
        "~0ULL - AGENT_LLM_PENDING_TTL_TICKS",
        "LLM deadline arithmetic must saturate instead of wrapping",
    )
    require(
        terminal,
        "status != AGENT_STATUS_OK && status != AGENT_STATUS_TIMEOUT",
        "terminal history must distinguish consumed responses from expiry",
    )
    require(
        terminal_status,
        "terminal->status == AGENT_STATUS_TIMEOUT",
        "expired LLM responses must remain distinguishable from consumed replays",
    )
    require(
        tick,
        "agent_llm_pending_reap_locked(now);",
        "the timer maintenance path must reclaim silent-relay requests",
    )

    require(
        clear,
        "pending->requester_pid == p->pid",
        "requester teardown must clear its LLM correlations",
    )
    require(
        clear,
        "pending->relay_pid == p->pid",
        "relay teardown must clear its LLM correlations",
    )
    require(
        clear,
        "state->requester_pid == p->pid",
        "requester teardown must clear monotonic correlation state",
    )
    require(
        clear,
        "terminal->relay_pid == p->pid",
        "endpoint teardown must clear replay history",
    )
    require(
        teardown,
        "agent_llm_pending_proc_clear(p);",
        "process teardown must revoke outstanding LLM response authority",
    )
    require(
        exec_public,
        "agent_llm_pending_proc_clear(p);",
        "Agent-to-public exec must revoke outstanding LLM response authority",
    )
    require(
        execute,
        "delivery_status = agent_llm_pending_request(",
        "LLM_REQUEST must use correlated delivery",
    )
    require(
        execute,
        "delivery_status = agent_llm_pending_response(",
        "LLM_RESPONSE must use correlated delivery",
    )
    require_order(
        execute_one,
        (
            "p->agent_call_count++;",
            "res->sequence = p->agent_call_count;",
            "agent_execute_op(p, op, res);",
        ),
        "tool execution must assign the current Context sequence before dispatch",
    )
    if execute.count("agent_ticks(), p->agent_call_count, op->payload,") != 2:
        raise ContractError(
            "LLM request/response events must cite the executing tool sequence"
        )
    require(
        execute,
        "p->agent_call_count, op->payload, 1, &delivered);",
        "SEND_MESSAGE events must cite the executing tool sequence",
    )
    for text in (
        '"corr_not_monotonic"',
        '"llm_pending_limit"',
        '"event_queue_full"',
        '"response_expired"',
        '"response_consumed"',
    ):
        require(execute, text, f"LLM result diagnostics lost {text}")


def validate_agent_loop_provenance(protocol: str, provenance: str) -> None:
    loop_mask_start = protocol.find("#define PROV_ACCEPT_AGENT_LOOP")
    loop_mask_end = protocol.find("#define AGENT_TOOL_REGISTRY", loop_mask_start)
    if loop_mask_start < 0 or loop_mask_end < 0:
        raise ContractError("missing the explicit Agent Loop provenance mask")
    loop_mask = protocol[loop_mask_start:loop_mask_end]
    for label in (
        "PROV_ACCEPT_CONTROL",
        "AGENT_PROVENANCE_UNTRUSTED_FILE_DATA",
        "AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT",
        "AGENT_PROVENANCE_CROSS_AGENT_DATA",
    ):
        require(loop_mask, label, f"Agent Loop mask lost {label}")
    forbid(
        loop_mask,
        "AGENT_PROVENANCE_ALL",
        "Agent Loop tools must enumerate accepted taints instead of using a blanket mask",
    )

    tool_start = protocol.find("#define AGENT_TOOL_REGISTRY(X)")
    tool_end = protocol.find("#define ASSERT_TOOL_STRINGS", tool_start)
    security_start = protocol.find("#define AGENT_TOOL_SECURITY_REGISTRY(X)")
    security_end = protocol.find("#define SECURITY_ENTRY", security_start)
    if min(tool_start, tool_end, security_start, security_end) < 0:
        raise ContractError("tool/security registry boundaries are missing")
    tool_registry = protocol[tool_start:tool_end]
    security_registry = protocol[security_start:security_end]
    tool_ids = re.findall(
        r"^\s*X\((AGENT_TOOL_[A-Z0-9_]+),", tool_registry, re.M
    )
    security_rows = re.findall(
        r"^\s*X\(([^,\n]+),\s*([^,\n]+),\s*([^,\n]+),\s*([^\)\n]+)\)",
        security_registry,
        re.M,
    )
    if len(tool_ids) != len(security_rows) or not tool_ids:
        raise ContractError("tool and security registries must remain index-aligned")
    security = dict(zip(tool_ids, security_rows, strict=True))
    expected_loop_tools = {
        "AGENT_TOOL_SEND_MESSAGE": (
            "AGENT_CAP_MESSAGE_SEND",
            "PROV_ACCEPT_AGENT_LOOP",
            "PROV_DERIVED",
            "AGENT_SIDE_EFFECT_IPC",
        ),
        "AGENT_TOOL_LLM_REQUEST": (
            "AGENT_CAP_MESSAGE_SEND",
            "PROV_ACCEPT_AGENT_LOOP",
            "PROV_DERIVED",
            "AGENT_SIDE_EFFECT_IPC",
        ),
        "AGENT_TOOL_LLM_RESPONSE": (
            "AGENT_CAP_LLM_RELAY",
            "PROV_ACCEPT_AGENT_LOOP",
            "PROV_TOOL",
            "AGENT_SIDE_EFFECT_IPC",
        ),
        "AGENT_TOOL_DELEGATE_TASK": (
            "AGENT_CAP_ORCHESTRATE",
            "PROV_ACCEPT_AGENT_LOOP",
            "PROV_DERIVED",
            "AGENT_SIDE_EFFECT_FILE | AGENT_SIDE_EFFECT_METADATA | AGENT_SIDE_EFFECT_IPC | AGENT_SIDE_EFFECT_PROCESS | AGENT_SIDE_EFFECT_PERMISSION | AGENT_SIDE_EFFECT_ARTIFACT",
        ),
    }
    for tool_id, expected in expected_loop_tools.items():
        if security.get(tool_id) != expected:
            raise ContractError(
                f"{tool_id} must accept loop taint without weakening its capability, output, or side-effect policy"
            )
    for tool_id, (_, accepted, _, effect) in security.items():
        if tool_id in expected_loop_tools or effect == "0":
            continue
        if accepted not in {"PROV_ACCEPT_CONTROL", "PROV_ACCEPT_ARTIFACT"}:
            raise ContractError(
                f"unrelated side-effect tool {tool_id} received a broader provenance policy"
            )

    ipc_labels = function_body(provenance, "agent_provenance_ipc_output_labels")
    require(
        ipc_labels,
        "agent_provenance_current_labels(source)",
        "Agent IPC must propagate the sender's accumulated taint",
    )
    require(
        ipc_labels,
        "AGENT_PROVENANCE_CROSS_AGENT_DATA",
        "Agent IPC must label data crossing an Agent boundary",
    )


def validate_execution_contract(sources: dict[str, str]) -> None:
    abi = sources["include/agent_execution_contract_abi.h"]
    header = sources["os/agent_execution_contract.h"]
    execution = sources["os/agent_execution_contract.c"]
    core = sources["os/agent_core.c"]
    task_bridge = sources["os/agent_task_bridge.c"]
    context = sources["os/agent_context.c"]
    evidence_ring = sources["os/agent_evidence_ring.c"]
    ipc = sources["os/agent_ipc.c"]
    observe = sources["os/agent_observe.c"]
    provenance = sources["os/agent_provenance.c"]
    protocol = sources["os/agent_tool_protocol.c"]
    phase_header = sources["os/resource_controller.h"]
    phase = sources["os/resource_controller.c"]
    proc = sources["os/proc.c"]
    syscall = sources["os/syscall.c"]

    validate_llm_correlation(core)
    validate_agent_loop_provenance(protocol, provenance)

    direct_controller = function_body(
        evidence_ring, "agent_evidence_direct_controller_valid"
    )
    forbid(
        direct_controller,
        "controller->vfs_scope_controller",
        "direct-denial preparation must trust the lifecycle controller binding, not a VFS role flag",
    )
    require(
        direct_controller,
        "workflow_lifecycle_controller_matches(\n"
        "\t\t       key, scope_id, controller->agent_control_id)",
        "direct-denial preparation lost its authoritative lifecycle controller match",
    )

    require_regex(
        abi,
        r"#define\s+AGENT_EXECUTION_DIGEST_SIZE\s+32U\b",
        "execution fingerprints must remain full SHA-256 digests",
    )
    require_regex(
        abi,
        r"#define\s+AGENT_EXECUTION_CONTRACT_MAX_NODES\s+24U\b",
        "the fixed 24-node contract bound changed",
    )
    require_regex(
        abi,
        r"#define\s+AGENT_EXECUTION_NODE_MAX_ATTEMPTS\s+4U\b",
        "the per-node attempt bound changed",
    )
    require_regex(
        abi,
        r"#define\s+AGENT_EXECUTION_CONTRACT_MAX_ATTEMPTS\s+48U\b",
        "the fixed contract attempt bound changed",
    )
    require(
        abi,
        "#define AGENT_EXECUTION_REASON_CANCEL_REQUESTED    21U",
        "cancel-requested reason is not frozen",
    )
    require(
        abi,
        "#define AGENT_EXECUTION_REASON_CANCEL_TOO_LATE     22U",
        "cancel-too-late reason is not frozen",
    )
    require_regex(
        header,
        r"struct\s+agent_execution_outcome\s*\{[^}]*"
        r"\buint64\s+terminal_tick\s*;\s*\};",
        "the kernel execution outcome lost its authoritative terminal tick",
    )
    forbid(
        abi,
        "terminal_tick",
        "the execution terminal tick is kernel-only and must not enter the public contract ABI",
    )
    forbid(
        abi,
        "internal_flags",
        "the Task caller discriminator is kernel-only and must not enter the public contract ABI",
    )
    require(
        execution,
        "#define AGENT_EXECUTION_COMPLETION_CACHE AGENT_EXECUTION_CONTRACT_MAX_ATTEMPTS",
        "completion outcomes must have one stable slot per accepted attempt",
    )
    require_regex(
        execution,
        r"agent_execution_completion_caches\s*\n?\s*"
        r"\[WORKFLOW_LIFECYCLE_CAP\]\[AGENT_EXECUTION_COMPLETION_CACHE\]",
        "completion cache must be lifecycle and node indexed",
    )
    require_regex(
        execution,
        r"struct\s+agent_execution_completion\s*\{[^}]*"
        r"struct\s+agent_execution_outcome\s+outcome\s*;\s*\};",
        "completion-cache entries must retain the complete kernel outcome",
    )

    digest_equal = function_body(execution, "agent_execution_digest_equal")
    require(
        digest_equal,
        "memcmp(a, b, AGENT_EXECUTION_DIGEST_SIZE) == 0",
        "digest equality must compare all 32 bytes",
    )
    inline_digest = function_body(
        execution, "agent_execution_inline_input_digest"
    )
    require(
        inline_digest,
        '"agentos.execution.inline-input.v1"',
        "inline inputs need a domain-separated canonical digest",
    )
    require_order(
        inline_digest,
        (
            "agent_execution_hash_u64(&ctx, (uint)op->tool_id)",
            "agent_execution_hash_u64(&ctx, op->arg0)",
            "agent_execution_hash_u64(&ctx, op->arg1)",
            "agent_execution_hash_u64(&ctx, op->flags)",
            "agent_execution_hash_u64(&ctx, payload_length)",
            "agent_sha256_update(&ctx, op->payload, payload_length)",
            "agent_sha256_final(&ctx, digest)",
        ),
        "inline digest field order changed",
    )
    require(header, "#define AGENT_EXECUTION_INPUT_INLINE   1U", "missing inline mode")
    require(header, "#define AGENT_EXECUTION_INPUT_RESOURCE 2U", "missing resource mode")

    build = function_body(execution, "agent_execution_contract_build_node")
    require(
        build,
        "uint64 lower_mask = index == 0 ? 0 : (1ULL << index) - 1",
        "DAG admission must derive the exact lower-node mask",
    )
    require(
        build,
        "node->node_id != index",
        "node IDs must be the exact declaration sequence",
    )
    require_order(
        build,
        (
            "node->max_attempts > AGENT_EXECUTION_NODE_MAX_ATTEMPTS",
            "record->total_attempts + node->max_attempts >",
            "AGENT_EXECUTION_CONTRACT_MAX_ATTEMPTS",
            "record->total_attempts += node->max_attempts",
        ),
        "contract admission no longer bounds its stable attempt slots",
    )
    require(
        build,
        "(node->predecessor_mask & ~lower_mask) != 0",
        "self and forward predecessor edges must be rejected",
    )
    require(
        build,
        "uint expected_class = p->resource_slot_reserved ?",
        "phase charge class must be derived from the workflow owner",
    )
    require_order(
        build,
        (
            "p->resource_slot_reserved ?",
            "RESOURCE_CHARGE_RESERVED : RESOURCE_CHARGE_ORDINARY",
            "!envelope_nonzero",
            "node->charge_class != expected_class",
        ),
        "class/envelope checks are no longer part of node validation",
    )

    predecessors = function_body(
        execution, "agent_execution_predecessors_complete"
    )
    require(
        predecessors,
        "(node->predecessor_mask & ~record->completed_mask) == 0",
        "all declared predecessors must complete before admission",
    )
    admit = function_body(execution, "agent_execution_contract_admit")
    predecessor_snapshot = function_body(
        execution, "agent_execution_predecessor_snapshot"
    )
    require_regex(
        predecessor_snapshot,
        r"node->predecessor_mask\s*&\s*"
        r"\(1ULL\s*<<\s*binding->source_node_id\)\)\s*==\s*0",
        "the selected predecessor must be an exact declared edge",
    )
    require_regex(
        predecessor_snapshot,
        r"binding->source_context_sequence\s*!=\s*0\s*\|\|\s*"
        r"source_control_id\s*!=\s*0\s*\|\|\s*source_pid\s*!=\s*0",
        "root inline calls must carry a zero producer identity",
    )
    require_regex(
        predecessor_snapshot,
        r"binding->source_context_sequence\s*!=\s*"
        r"source->context_sequence",
        "non-root calls must cite the retained predecessor sequence exactly",
    )
    require_order(
        predecessor_snapshot,
        (
            "node->predecessor_mask & ~record->completed_mask",
            "source = &agent_execution_producers[slot][binding->source_node_id]",
            "source_control_id != source->control_id",
            "source_pid != source->pid",
            "for (uint i = 0; i < record->node_count; i++)",
            "labels |= producer->provenance_labels",
        ),
        "predecessor identity and multi-edge labels must come from retained outputs",
    )
    require(
        admit,
        "agent_execution_predecessor_snapshot(",
        "admission no longer validates the complete predecessor snapshot",
    )
    require(
        admit,
        "agent_execution_binding_input_valid(",
        "admission must validate the complete copied input binding",
    )
    require(
        header,
        "#define AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL (1U << 0)",
        "the kernel-only Task binding discriminator changed",
    )
    require(
        header,
        "uint internal_flags;",
        "execution bindings lost their kernel-only caller discriminator",
    )
    input_valid = function_body(execution, "agent_execution_binding_input_valid")
    require(
        input_valid,
        "(binding->internal_flags &\n"
        "\t     ~AGENT_EXECUTION_BINDING_INTERNAL_F_ALL) != 0",
        "binding validation must reject unknown kernel-only caller flags",
    )
    require_order(
        input_valid,
        (
            "binding->input_mode == AGENT_EXECUTION_INPUT_INLINE",
            "binding->input_flags == 0",
            "binding->resource_slot == 0",
            "binding->resource_generation == 0",
            "agent_execution_digest_equal(binding->input_fingerprint",
            "binding->input_mode != AGENT_EXECUTION_INPUT_RESOURCE",
            "binding->resource_slot != 0",
            "binding->resource_generation != 0",
            "binding->input_flags == AGENT_EXECUTION_INPUT_F_OWNED",
            "binding->input_flags == AGENT_EXECUTION_INPUT_F_BORROWED",
            "!agent_execution_digest_zero(binding->input_fingerprint)",
        ),
        "inline and kernel-owned resource bindings are not disjoint and complete",
    )

    cancel = function_body(execution, "agent_execution_contract_cancel_common")
    require_regex(
        cancel,
        r"runtime->flags\s*&\s*"
        r"AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED\)\s*!=\s*0",
        "cancel/force must lose after timeout has claimed the attempt",
    )
    require_order(
        cancel,
        (
            "runtime->state == AGENT_EXECUTION_NODE_RUNNING",
            "AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED",
            '"cancel_timeout_claimed"',
            "AGENT_EXECUTION_ADMISSION_CANCEL_PENDING",
            "AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED",
            "AGENT_EXECUTION_REASON_CANCEL_TOO_LATE",
            "AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED",
            "AGENT_EXECUTION_RUNTIME_F_FORCE_CANCEL",
            "AGENT_EXECUTION_REASON_CANCEL_REQUESTED",
            "AGENT_EXECUTION_ADMISSION_CANCEL_PENDING",
        ),
        "cancel, timeout, and effect claims must have one IRQ-locked winner",
    )
    effect = function_body(execution, "agent_execution_contract_effect_begin")
    require_regex(
        effect,
        r"runtime->flags\s*&\s*"
        r"AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED\)\s*!=\s*0",
        "provider effects must lose after timeout publication",
    )
    require_order(
        effect,
        (
            "runtime->state != AGENT_EXECUTION_NODE_RUNNING",
            "AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED",
            "AGENT_EXECUTION_REASON_DEADLINE_EXPIRED",
            "AGENT_EXECUTION_EFFECT_STALE",
            "AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED",
            "AGENT_EXECUTION_EFFECT_CANCELLED",
            "AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED",
            "AGENT_EXECUTION_EFFECT_ALLOWED",
        ),
        "effect publication must lose to an already requested cancel",
    )
    complete = function_body(execution, "agent_execution_contract_complete")
    require(
        complete,
        'panic("execution contract cancel winner")',
        "a cancel winner may only publish CANCELLED",
    )
    require_order(
        complete,
        (
            'panic("execution contract cancel winner")',
            "AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED",
            'panic("execution contract timeout winner")',
            "outcome->evidence_ticket == 0",
            "outcome->output_provenance_labels == 0",
            'panic("execution contract terminal without evidence")',
        ),
        "cancel/timeout terminals must match their winner and carry Evidence",
    )
    require_order(
        complete,
        (
            "cache_index = agent_execution_cache_index(",
            "record, claim->node_id, claim->attempt_id)",
            "cached = &agent_execution_completion_caches[claim->slot][cache_index]",
            'panic("execution contract cache overwrite")',
            "cached->valid = 1",
            "runtime->cache_index = cache_index",
        ),
        "terminal attempt outcomes must be stable and non-evicting",
    )
    require_order(
        complete,
        (
            "memmove(&cached->result, result, sizeof(cached->result))",
            "memmove(&cached->outcome, outcome, sizeof(cached->outcome))",
            "runtime->cache_index = cache_index",
        ),
        "the stable completion cache must retain the authoritative terminal outcome",
    )
    cache_copy = function_body(execution, "agent_execution_cache_copy")
    require_order(
        cache_copy,
        (
            "memmove(result, &cached->result, sizeof(*result))",
            "memmove(outcome, &cached->outcome",
            "sizeof(*outcome))",
            "return 1",
        ),
        "cached replay must restore the complete outcome including terminal_tick",
    )
    require_order(
        complete,
        (
            "terminal_failure = claim->retry_forbidden ||",
            "claim->attempt_id >= node->max_attempts",
            "!agent_execution_retry_allowed(node, result->status)",
            "if (terminal_failure)",
            "record->failed_mask |= 1ULL << claim->node_id",
            "agent_execution_propagate_dependency_failure(record)",
            "record->failed_mask &= ~(1ULL << claim->node_id)",
            "~AGENT_EXECUTION_RUNTIME_F_RETRY_FORBIDDEN",
        ),
        "retryable failures must remain private and leave successors runnable",
    )
    propagate_failure = function_body(
        execution, "agent_execution_propagate_dependency_failure"
    )
    forbid(
        propagate_failure,
        "record->failed_mask |=",
        "private dependency poison must not publish another node's failed bit",
    )
    timeout = function_body(execution, "agent_execution_contract_timeout")
    require_regex(
        timeout,
        r"runtime->flags\s*&\s*\("
        r"AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED\s*\|\s*"
        r"AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED\)\)\s*!=\s*0",
        "timeout must lose after effect or cancel publication",
    )
    require_order(
        timeout,
        (
            "runtime->state != AGENT_EXECUTION_NODE_RUNNING",
            "AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED |",
            "AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED",
            "AGENT_EXECUTION_ADMISSION_CANCEL_PENDING",
            "AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED",
            "claim->decision_reason =",
            "AGENT_EXECUTION_REASON_DEADLINE_EXPIRED",
            "claim->retry_forbidden = 1",
            "claim->active = 1",
            "AGENT_EXECUTION_ADMISSION_EXECUTE",
        ),
        "timeout must lose to an existing cancel/effect or publish the sole claim",
    )
    preflight = function_body(execution, "agent_execution_contract_preflight")
    require_regex(
        preflight,
        r"record->state\s*==\s*AGENT_EXECUTION_CONTRACT_RETIRING\)\s*\{"
        r"\s*if\s*\(\(binding->internal_flags\s*&\s*"
        r"AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL\)\s*!=\s*0\)\s*\{"
        r"\s*agent_execution_preflight_decide\(\s*result,\s*"
        r"AGENT_STATUS_OK,\s*AGENT_EXECUTION_REASON_NONE\s*\);"
        r"\s*goto\s+out_locked\s*;\s*\}"
        r"\s*agent_execution_preflight_decide\(\s*result,\s*"
        r"AGENT_STATUS_DENIED,\s*"
        r"AGENT_EXECUTION_REASON_CONTRACT_RETIRING\s*\);"
        r"\s*goto\s+out_locked\s*;\s*\}",
        "RETIRING validation must purely allow Task bindings and deny scalar callers",
    )
    require_order(
        preflight,
        (
            "record->state == AGENT_EXECUTION_CONTRACT_RETIRING",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            "result, AGENT_STATUS_OK",
            "goto out_locked",
            "binding->contract_generation == 0",
        ),
        "Task RETIRING validation must return ALLOW before detailed admission checks",
    )
    require_regex(
        admit,
        r"record->state\s*==\s*AGENT_EXECUTION_CONTRACT_RETIRING\)\s*\{"
        r"\s*agent_execution_result_error\(\s*result,\s*"
        r"\(binding->internal_flags\s*&\s*"
        r"AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL\)\s*!=\s*0\s*\?\s*"
        r"AGENT_STATUS_DENIED\s*:\s*AGENT_STATUS_CANCELLED,\s*"
        r'"contract_retiring",\s*'
        r"AGENT_EXECUTION_REASON_CONTRACT_RETIRING,\s*claim\s*\);",
        "RETIRING admission must deny an accepted Task but cancel a scalar caller",
    )
    require_order(
        preflight,
        (
            "AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY",
            "node.output_artifact_type != AGENT_ARTIFACT_NONE",
            "agent_execution_preflight_decide(",
            "AGENT_STATUS_DENIED",
            "AGENT_EXECUTION_REASON_CONTRACT_INVALID",
        ),
        "OUTPUT_NONE_ONLY must reject a contract that can publish an artifact",
    )
    cache_index = function_body(execution, "agent_execution_cache_index")
    require_order(
        cache_index,
        (
            "attempt_id > record->nodes[node_id].max_attempts",
            "for (uint i = 0; i < node_id; i++)",
            "index += record->nodes[i].max_attempts",
            "index += attempt_id - 1",
            "index < AGENT_EXECUTION_COMPLETION_CACHE",
        ),
        "attempt cache slots must be a deterministic cumulative node prefix",
    )

    direct_enter = function_body(
        execution, "agent_execution_contract_direct_enter"
    )
    require_order(
        direct_enter,
        (
            "record->state == AGENT_EXECUTION_STATE_BUILDING",
            "agent_execution_record_enforced(record, lifecycle)",
            "record->bare_inflight++",
            "guard->active = 1",
        ),
        "direct-effect entry is not linearized against CREATE/ENFORCE",
    )
    for declaration in (
        "int agent_execution_contract_file_pin_enter(",
        "void agent_execution_contract_file_pin_leave(",
    ):
        require(header, declaration, "file-pin contract cut API is not public")
    file_pin_enter = function_body(
        execution, "agent_execution_contract_file_pin_enter"
    )
    require_order(
        file_pin_enter,
        (
            "record->state == AGENT_EXECUTION_STATE_BUILDING",
            "agent_execution_record_enforced(record, lifecycle)",
            "return AGENT_STATUS_OK",
            "record->bare_inflight++",
            "guard->active = 1",
        ),
        "descriptor pin entry is not linearized against contract publication",
    )
    for forbidden in (
        "p->is_agent",
        "resource_domain_admin",
        "exec_policy_process_bootstrap",
        "record->denial_count++",
    ):
        forbid(
            file_pin_enter,
            forbidden,
            "file-pin cut changed workflow-wide authority semantics",
        )
    file_pin_leave = function_body(
        execution, "agent_execution_contract_file_pin_leave"
    )
    require(
        file_pin_leave,
        "agent_execution_contract_direct_leave(guard)",
        "file-pin cut does not settle the exact bare-inflight token",
    )
    create = function_body(execution, "sys_agent_execution_contract")
    require(
        create,
        "control.flags != AGENT_EXECUTION_CONTRACT_F_ENFORCE",
        "contract CREATE must be fail-closed ENFORCE only",
    )
    require_regex(
        create,
        r"record->bare_inflight\s*!=\s*0\s*\|\|\s*"
        r"record->running_count\s*!=\s*0",
        "CREATE must not freeze across a legacy/direct side effect",
    )
    require_order(
        create,
        (
            "record->state = AGENT_EXECUTION_STATE_BUILDING",
            "agent_execution_contract_build_node(",
            "agent_sha256_final(&fingerprint, computed)",
            "record->bare_inflight != 0 || record->running_count != 0",
            "record->state = AGENT_EXECUTION_CONTRACT_FROZEN",
        ),
        "contract publication must happen after deterministic build and recheck",
    )
    create_replay = function_body(
        execution, "agent_execution_contract_create_replay"
    )
    require_order(
        create_replay,
        (
            "record->state != AGENT_EXECUTION_CONTRACT_FROZEN",
            "record->create_request_id != control->request_id",
            "record->flags != control->flags",
            "record->node_count != control->node_count",
            "record->deadline_tick != control->deadline_tick",
            "control->node_size != sizeof(struct agent_execution_contract_node)",
            "for (uint i = 0; i < control->node_count; i++)",
            "copyin(p->pagetable, (char *)&node",
            "if (agent_execution_digest_zero(node.schema_digest))",
            "agent_execution_schema_digests",
            "else if (!agent_execution_digest_equal(",
            "return AGENT_STATUS_CONFLICT",
            "agent_execution_contract_hash_node(&fingerprint, &node)",
            "agent_sha256_final(&fingerprint, computed)",
            "!agent_execution_digest_equal(computed, record->fingerprint)",
            "enabled = intr_save()",
            "record->create_request_id != control->request_id",
            "return AGENT_STATUS_OK",
        ),
        "CREATE replay must revalidate the same request and canonical node digest",
    )
    require_order(
        create,
        (
            "record->state != AGENT_EXECUTION_CONTRACT_EMPTY",
            "record->create_request_id == control.request_id",
            "agent_execution_contract_create_replay(",
            "AGENT_STATUS_CONFLICT",
        ),
        "only an identical frozen CREATE request may recover its result",
    )
    require_regex(
        create,
        r"if\s*\(record->state\s*==\s*"
        r"AGENT_EXECUTION_CONTRACT_RECLAIMED\)\s*\{\s*"
        r"memset\(record,\s*0,\s*sizeof\(\*record\)\);\s*"
        r"record->lifecycle\s*=\s*lifecycle;\s*\}",
        "CREATE must recycle only a quiescent RECLAIMED record",
    )
    require_regex(
        create,
        r"else\s+if\s*\(control\.operation\s*==\s*"
        r"AGENT_EXECUTION_CONTRACT_RETIRE\)\s*\{.*?"
        r"record->state\s*==\s*AGENT_EXECUTION_CONTRACT_RECLAIMED\)\s*"
        r"status\s*=\s*AGENT_STATUS_OK;.*?"
        r"record->state\s*=\s*AGENT_EXECUTION_CONTRACT_RETIRING;.*?"
        r"record->bare_inflight\s*!=\s*0\s*\|\|\s*"
        r"record->running_count\s*!=\s*0\)\s*"
        r"status\s*=\s*AGENT_STATUS_RETRY;.*?"
        r"record->state\s*=\s*"
        r"AGENT_EXECUTION_CONTRACT_RECLAIMED;\s*\}",
        "RETIRE must close admission, wait for exact refs, and publish RECLAIMED",
    )
    require_regex(
        create,
        r"else\s+if\s*\(record->state\s*==\s*"
        r"AGENT_EXECUTION_CONTRACT_RECLAIMED\)\s*\{\s*"
        r"(?:/\*.*?\*/\s*)?status\s*=\s*AGENT_STATUS_OK;\s*\}",
        "QUERY must retain the exact RECLAIMED snapshot until the next CREATE",
    )
    require_regex(
        create,
        r"control\.operation\s*==\s*AGENT_EXECUTION_CONTRACT_QUERY\s*&&\s*"
        r"control\.nodes\s*!=\s*0\).*?"
        r"record->bare_inflight\+\+;\s*query_pinned\s*=\s*1;.*?"
        r"agent_execution_contract_node_export\(record,\s*i,\s*&node\);.*?"
        r"if\s*\(query_pinned\).*?record->bare_inflight--;",
        "QUERY node export must pin the record against RECLAIMED generation reuse",
    )

    effects = function_body(syscall, "syscall_direct_agent_side_effects")
    for syscall_id in (
        "SYS_write",
        "SYS_openat",
        "SYS_mailwrite",
        "SYS_pipe2",
        "SYS_clone",
        "SYS_execve",
        "SYS_agent_scope_delegate_fd",
        "SYS_context_push",
        "SYS_agent_watch",
        "SYS_agent_file_edit_commit",
    ):
        require(effects, f"case {syscall_id}:", f"{syscall_id} escaped direct-effect classification")
    slow_path = function_body(syscall, "syscall_slow_path")
    require_order(
        slow_path,
        (
            "syscall_transaction_prepare(",
            "transaction->file->type == FD_INODE",
            "agent_execution_contract_file_pin_enter(",
            "curr_proc(), file_pin_guard",
            "if (ret != AGENT_STATUS_OK)",
            "goto finish",
            "direct_side_effects = syscall_direct_agent_side_effects(",
            "agent_execution_contract_gate_direct_syscall(",
            "if (ret != AGENT_STATUS_OK)",
            "syscall_merge_ingress_provenance(",
            "syscall_transaction_begin(",
            "syscall_dispatch(id, trapframe, transaction)",
            "syscall_transaction_finish(",
        ),
        "pinned descriptor can bypass the slow-path settlement funnel",
    )
    require(
        slow_path,
        "agent_execution_contract_gate_direct_syscall(\n"
        "\t\t\tcurr_proc(), id, direct_side_effects, direct_guard)",
        "write effect authority no longer uses its independent direct guard",
    )
    require(
        slow_path,
        "id == SYS_read || id == SYS_write || id == SYS_fstat ||\n"
        "\t     id == SYS_agent_task_channel_resource",
        "inode publication cut no longer covers the exact descriptor calls",
    )
    if slow_path.count("agent_execution_contract_file_pin_enter(") != 1:
        raise ContractError("inode publication cut is not uniquely acquired")
    fstat = function_body(syscall, "sys_fstat")
    require_order(
        fstat,
        (
            "if (f == 0)",
            "agent_provenance_merge_current(",
            "copyout(p->pagetable, stataddr",
        ),
        "fstat no longer consumes the transaction-pinned identity",
    )
    forbid(fstat, "fdget(fd)", "fstat repins a racy descriptor identity")
    forbid(fstat, "fileclose(f)", "fstat releases the transaction-owned pin")
    forbid(fstat, "file_pin_guard", "fstat owns a nested file-pin cut")
    dispatch = function_body(syscall, "syscall")
    require_order(
        dispatch,
        (
            "if (syscall_needs_transaction(class))",
            "syscall_slow_path(",
            "&direct_guard",
            "&file_pin_guard",
            "&operation_denied",
            "direct_side_effects = syscall_direct_agent_side_effects(",
            "agent_execution_contract_gate_direct_syscall(",
            "if (ret != AGENT_STATUS_OK)",
            "goto operation_done",
            "syscall_dispatch(id, trapframe, 0)",
            "!operation_denied || direct_guard.active ||",
            "file_pin_guard.active",
            "agent_background_checkpoint()",
            "!background_done && !operation_denied",
            "AGENT_RUN_F_FENCE",
            "agent_background_checkpoint()",
            "agent_execution_contract_file_pin_leave(&file_pin_guard)",
            "agent_execution_contract_direct_leave(&direct_guard)",
            "workflow_lifecycle_operation_leave(lifecycle)",
        ),
        "file/direct guards do not cover dispatch, settlement, and background work",
    )
    if dispatch.count("agent_execution_contract_file_pin_enter(") != 0 or (
        dispatch.count("agent_execution_contract_file_pin_leave(") != 1
    ):
        raise ContractError("outer file-pin cut is not exactly balanced")
    if dispatch.count("agent_execution_contract_direct_leave(") != 1:
        raise ContractError("outer direct-effect cut is not exactly balanced")
    if dispatch.count("agent_background_checkpoint();") != 3:
        raise ContractError("syscall background paths escaped the common guard release")
    direct_gate = function_body(
        core, "agent_execution_contract_gate_direct_syscall"
    )
    require_order(
        direct_gate,
        (
            "agent_execution_contract_direct_enter(",
            "side_effect_mask, guard)",
            "agent_provenance_prepare_denial(",
            "agent_provenance_append_security_denial(",
            "status != AGENT_STATUS_DENIED || ticket == 0",
            "AGENT_STATUS_NO_SPACE : AGENT_STATUS_RETRY",
            "return AGENT_STATUS_DENIED",
        ),
        "ENFORCE denial must fail closed without fabricating an evidence ticket",
    )

    v3_call = function_body(core, "sys_tool_call_v3")
    require_order(
        v3_call,
        (
            "lifecycle = vfs_proc_lifecycle(p)",
            "resp.contract.lifecycle.id = lifecycle.id",
            "resp.contract.lifecycle.reserved = 0",
            "resp.contract.lifecycle.generation = lifecycle.generation",
            "agent_execution_contract_generation(lifecycle)",
            "binding.lifecycle.id = req.contract.lifecycle.id",
            "binding.lifecycle.generation = req.contract.lifecycle.generation",
            "binding.contract_generation = req.contract.generation",
            "req.contract.lifecycle.id != lifecycle.id",
            "req.contract.lifecycle.generation != lifecycle.generation",
            "agent_tool_protocol_resolve(",
        ),
        "V3 must reject the requested key while reporting the authoritative key",
    )
    forbid(
        v3_call,
        "binding.lifecycle.id = lifecycle.id",
        "a mismatched V3 request must not be rebound to the current lifecycle",
    )
    forbid(
        v3_call,
        "resp.contract.lifecycle.id = req.contract.lifecycle.id",
        "the V3 response must not echo an untrusted lifecycle",
    )
    require_order(
        v3_call,
        (
            "op.tool_id = req.tool_id > 0 ? req.tool_id :",
            "enforced = p->is_agent &&",
            "agent_tool_protocol_resolve(",
            "if (status != AGENT_STATUS_OK)",
            "denial_reason = AGENT_EXECUTION_REASON_TOOL_MISMATCH",
            "goto rejected",
            "if (tool.flags & AGENT_TOOL_F_SYSCALL_ONLY)",
            "status = AGENT_STATUS_DENIED",
            "denial_reason = AGENT_EXECUTION_REASON_TOOL_MISMATCH",
            "goto rejected",
            "rejected:",
            "if (enforced && op.tool_id > 0)",
            "if (op.request_id == 0)",
            "op.request_id = (1ULL << 63)",
            "(uint64)(uint)p->pid << 32",
            "p->agent_call_count + 1",
            "agent_tool_call_v3_security_denial(",
            "resp.evidence_ticket = outcome.evidence_ticket",
            "status == AGENT_STATUS_DENIED || status == AGENT_STATUS_STALE",
            "resp.evidence_ticket == 0",
            "status = AGENT_STATUS_RETRY",
            '"security_evidence_unavailable"',
            "resp.status = status",
            "copyout(p->pagetable, respaddr, (char *)&resp, sizeof(resp))",
        ),
        "unknown and syscall-only V3 calls must share the strict denial path",
    )
    forbid(
        v3_call,
        "enforced && req.request_id != 0",
        "zero-correlation V3 denials must still receive an internal audit id",
    )
    v3_denial = function_body(core, "agent_tool_call_v3_security_denial")
    require_order(
        v3_denial,
        (
            "agent_execution_preflight_denial(",
            "result->status = denial.status",
            "outcome->output_provenance_labels =",
            "denial.output_provenance_labels",
            "outcome->evidence_ticket = denial.evidence_ticket",
            "return result->status",
        ),
        "V3 rejection must return the evidence-bound preflight result",
    )
    preflight_denial = function_body(
        core, "agent_execution_preflight_denial"
    )
    require_order(
        preflight_denial,
        (
            "agent_provenance_append_security_denial(",
            "status != AGENT_STATUS_STALE) || ticket == 0",
            "AGENT_STATUS_NO_SPACE : AGENT_STATUS_RETRY",
            "result->evidence_ticket = 0",
            "return AGENT_EXECUTION_PREFLIGHT_ERROR",
            "result->output_provenance_labels = decision.output_labels",
            "result->evidence_ticket = ticket",
            "return AGENT_EXECUTION_PREFLIGHT_TERMINAL",
        ),
        "V3 DENIED/STALE must never escape without a critical ticket",
    )
    task_binding = function_body(task_bridge, "agent_task_bridge_binding")
    require_order(
        task_binding,
        (
            "memset(binding, 0, sizeof(*binding))",
            "binding->internal_flags =",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            "binding->lifecycle.id = sqe->contract.lifecycle.id",
        ),
        "only the internal Task bridge may stamp the Task binding discriminator",
    )
    require_order(
        v3_call,
        (
            "memset(&binding, 0, sizeof(binding))",
            "binding.lifecycle.id = req.contract.lifecycle.id",
        ),
        "scalar V3 bindings must begin with all kernel-only flags cleared",
    )
    forbid(
        v3_call,
        "binding.internal_flags",
        "a scalar V3 request must not forge the internal Task binding flag",
    )
    task_validate = function_body(task_bridge, "agent_task_bridge_validate")
    require_order(
        task_validate,
        (
            "agent_task_bridge_binding(sqe, input, &op, &binding)",
            "agent_execution_contract_preflight(",
            "preflight.status == AGENT_STATUS_RETRY",
            "validation->output_artifact_type = AGENT_ARTIFACT_NONE",
            "return AGENT_TASK_HOOK_PENDING",
        ),
        "Task validation must remain a pure preflight that queues allowed work",
    )
    for side_effect in (
        "agent_execution_task_submit_sync(",
        "agent_context_append",
        "agent_evidence_",
    ):
        forbid(
            task_validate,
            side_effect,
            "Task validate must remain side-effect free",
        )
    task_submit_sync = function_body(core, "agent_execution_task_submit_sync")
    require_order(
        task_submit_sync,
        (
            "binding->internal_flags &",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            ") == 0",
            "agent_execute_one(",
        ),
        "the accepted-Task submit path must require the internal binding flag",
    )
    task_submit = function_body(task_bridge, "agent_task_bridge_submit")
    require_order(
        task_submit,
        (
            "agent_task_bridge_binding(sqe, input, &op, &binding)",
            "agent_execution_task_submit_sync(",
            "agent_task_bridge_execution_completion(",
            "completion->status == AGENT_STATUS_DENIED",
            "AGENT_TASK_HOOK_DENIED",
        ),
        "a RETIRING Task must reach submit and finish as a canonical denial",
    )
    task_completion = function_body(
        task_bridge, "agent_task_bridge_execution_completion"
    )
    require_order(
        task_completion,
        (
            "completion->evidence_ticket = outcome->evidence_ticket",
            "completion->provenance_labels = outcome->output_provenance_labels",
            "completion->terminal_tick = outcome->terminal_tick",
            "completion->completion_tick = agent_task_bridge_now()",
        ),
        "Task completion must keep kernel terminal time separate from publication time",
    )
    task_completion_canonical = function_body(
        task_bridge, "agent_task_bridge_submit_completion_canonical"
    )
    require_order(
        task_completion_canonical,
        (
            "completion->status == AGENT_STATUS_TIMEOUT",
            "AGENT_TASK_SQE_F_HARD_DEADLINE",
            "sqe->deadline_tick != 0",
            "completion->terminal_tick >= sqe->deadline_tick",
            "completion->flags == AGENT_TASK_CQE_F_DEADLINE",
        ),
        "Task deadline validation must use the kernel execution terminal tick",
    )
    force_cancel_sync = function_body(core, "agent_execution_force_cancel_sync")
    require_order(
        force_cancel_sync,
        (
            "agent_execution_contract_force_cancel(",
            "AGENT_EXECUTION_ADMISSION_CACHED",
            "outcome->evidence_ticket == 0",
            "AGENT_EXECUTION_ADMISSION_CANCEL_PENDING",
            "agent_execution_security_denial(",
            "outcome->evidence_ticket != 0",
        ),
        "force cancel must return only a pending claim or an evidenced terminal",
    )
    timeout_sync = function_body(core, "agent_execution_timeout_sync")
    require_order(
        timeout_sync,
        (
            "agent_lifecycle_context_lane_enter(p)",
            "agent_context_append_prepare(",
            "agent_evidence_context_reserve(",
            "agent_execution_contract_timeout(",
            "AGENT_EXECUTION_ADMISSION_CACHED",
            "outcome->evidence_ticket == 0",
            "AGENT_EXECUTION_ADMISSION_EXECUTE",
            "res->status = AGENT_STATUS_TIMEOUT",
            "outcome->terminal_tick = now",
            "outcome->output_provenance_labels =",
            "agent_execution_append_terminal(",
            "p, &op, res, now",
            'panic("reserved timeout terminal evidence")',
        ),
        "deadline completion must reserve and publish exactly one evidenced terminal",
    )
    execute_one = function_body(core, "agent_execute_one")
    require_order(
        execute_one,
        (
            "agent_execute_op(p, op, res)",
            "agent_provenance_commit_tool_output(",
            "terminal_tick = agent_ticks()",
            "outcome->terminal_tick = terminal_tick",
            "terminal_tick >= claim.deadline_tick",
            "AGENT_EXECUTION_REASON_DEADLINE_EXPIRED",
            "agent_execution_append_terminal(",
            "p, op, res, tick, status",
        ),
        "post-effect outcome/deadline must share a fresh terminal tick while Context keeps the service-start tick",
    )

    require_regex(
        phase_header,
        r"RESOURCE_PHASE_LEASE_EMPTY\s*=\s*0,\s*"
        r"RESOURCE_PHASE_LEASE_ADMITTED,\s*RESOURCE_PHASE_LEASE_ACTIVE,\s*"
        r"RESOURCE_PHASE_LEASE_DEACTIVATED,\s*RESOURCE_PHASE_LEASE_SETTLED",
        "phase lease lifecycle changed",
    )
    begin = function_body(phase, "resource_phase_lease_begin")
    require_order(
        begin,
        (
            "memmove(amounts[RESOURCE_PHASE_EXEC], exec_amounts",
            "memmove(amounts[RESOURCE_PHASE_STORAGE], storage_amounts",
            "union_mask = masks[RESOURCE_PHASE_EXEC]",
            "if (union_mask == 0)",
            "owner->process->resource_slot_reserved ?",
            "resource_phase_pair_admissible_locked(",
            "resource_phase_pair_commit_locked(",
            "RESOURCE_PHASE_LEASE_ADMITTED",
        ),
        "phase begin must atomically validate and lock the exact envelope/class",
    )
    pair_commit = function_body(phase, "resource_phase_pair_commit_locked")
    require_order(
        pair_commit,
        (
            "resource_credit_free_take(",
            "counter->used += amount",
            "lock->locked[charge_class][kind] += amount",
        ),
        "phase admission must move the exact amount into locked used credit",
    )
    settle = function_body(phase, "resource_phase_lease_settle_entry_locked")
    require_order(
        settle,
        (
            "counter->used -= remaining",
            "lock->locked[lease->charge_class][kind] -=",
            "resource_credit_free_add(",
            "lease->account[role].remaining[kind] = 0",
            "memset(entry, 0, sizeof(*entry))",
        ),
        "settle must return all unused credit before forgetting the lease",
    )
    for cleanup in (
        "resource_phase_lease_abort_entry_locked(",
        "resource_phase_thread_cleanup_locked(thread)",
        "resource_phase_thread_cleanup_locked(\n\t\t\t    &process->threads[tid])",
    ):
        require(phase, cleanup, "phase cleanup path is incomplete")
    if proc.count("resource_phase_thread_cleanup(") < 3:
        raise ContractError("all thread teardown variants must settle phase leases")
    require(
        proc,
        "resource_phase_process_cleanup(p)",
        "process teardown must prove that no phase lease survives",
    )

    execute_one = function_body(core, "agent_execute_one")
    main_reserve = execute_one.find(
        "if (bound && agent_evidence_context_reserve(",
        execute_one.find("AGENT_TOOL_F_SYSCALL_ONLY"),
    )
    phase_begin = execute_one.find("resource_phase_lease_begin(", main_reserve)
    effect_begin = execute_one.find(
        "agent_execution_contract_effect_begin(&claim)", phase_begin
    )
    tool_effect = execute_one.find("agent_execute_op(p, op, res)", effect_begin)
    if min(main_reserve, phase_begin, effect_begin, tool_effect) < 0 or not (
        main_reserve < phase_begin < effect_begin < tool_effect
    ):
        raise ContractError(
            "bound calls must reserve terminal evidence and phase credit before effects"
        )
    require_order(
        execute_one[effect_begin:],
        (
            "resource_phase_lease_deactivate(",
            "resource_phase_lease_settle(",
            'agent_result_text(res, "cancelled_before_effect")',
            "agent_execution_append_terminal(",
        ),
        "cancelled calls must settle their phase before terminal publication",
    )
    normal_effect = execute_one.find("agent_execute_op(p, op, res)")
    normal_settle = execute_one.find("resource_phase_lease_settle(", normal_effect)
    normal_terminal = execute_one.find(
        "agent_execution_append_terminal(", normal_settle
    )
    if min(normal_effect, normal_settle, normal_terminal) < 0 or not (
        normal_effect < normal_settle < normal_terminal
    ):
        raise ContractError("normal completion must settle its phase before terminal evidence")

    terminal = function_body(core, "agent_execution_append_terminal")
    require_order(
        terminal,
        (
            "agent_context_append_reserved_ticket(",
            "result < 0 || evidence_ticket == 0",
            "outcome->evidence_ticket = evidence_ticket",
            "agent_execution_contract_complete(claim, res, outcome)",
        ),
        "a contract may become terminal only after a visible evidence ticket",
    )
    denial = function_body(provenance, "agent_provenance_append_security_denial")
    require_order(
        denial,
        (
            "workflow_lifecycle_operation_enter(evidence_lifecycle)",
            "agent_evidence_security_reserve(p, &reservation)",
            "agent_provenance_stage_labels(",
            "agent_context_append_security_denial_record(",
            "ticket == 0",
            "reservation.active",
            "*ticket_out = ticket",
            "workflow_lifecycle_operation_leave(evidence_lifecycle)",
        ),
        "critical denial needs a staged, lifecycle-bound atomic ticket",
    )
    if "agent_evidence_security_commit(" in denial:
        raise ContractError(
            "provenance must not publish Context before a separate Evidence commit"
        )
    append = function_body(context, "agent_context_append_flags")
    require_order(
        append,
        (
            "agent_context_publish_begin(p)",
            "agent_context_write_record(p, slot, &record)",
            "agent_observe_commit_security_reserved_ticket(",
            "agent_context_publish_end(p)",
            "agent_observe_publish_context_ticket(",
        ),
        "security Evidence must commit while Context publication is odd",
    )
    security_commit = function_body(
        observe, "agent_observe_commit_security_reserved_ticket"
    )
    require_order(
        security_commit,
        (
            "agent_context_load_attribution(",
            "agent_observe_alloc_audit_sequence()",
            "agent_evidence_security_commit(",
            "evidence_ticket == 0",
            "return evidence_ticket",
        ),
        "atomic security commit lost attribution or its nonzero Evidence ticket",
    )
    projection = function_body(observe, "agent_observe_publish_context_ticket")
    require_order(
        projection,
        (
            "evidence_ticket == 0",
            "agent_observe_timeline_record_context(p, record)",
            "agent_observe_ledger_record_context(",
        ),
        "timeline/ledger projection must follow atomic Context/Evidence publish",
    )

    wait = function_body(ipc, "sys_agent_wait")
    if wait.count("agent_provenance_merge_current(") != 1:
        raise ContractError("Agent wait must merge reservation provenance exactly once")
    require_order(
        wait,
        (
            "agent_lifecycle_context_lane_enter(p)",
            "agent_provenance_merge_current(",
            "p, reservation.provenance_labels",
            "copyout(p->pagetable, eventaddr, (char *)&event",
            "p->agent_current_cause_sequence = event.cause_sequence",
            "agent_observe_record_event(",
            "agent_context_append_system_causal(",
            "agent_ipc_wait_finish(p, &reservation, 1)",
            "agent_lifecycle_context_lane_leave(p)",
        ),
        "wait provenance must be visible before event payload copyout",
    )


def mutated(path: str, old: str, new: str, *, count: int = 1) -> dict[str, str]:
    sources = dict(SOURCES)
    if old not in sources[path]:
        raise AssertionError(f"mutation anchor missing in {path}: {old!r}")
    sources[path] = sources[path].replace(old, new, count)
    return sources


def mutated_occurrence(
    path: str, old: str, new: str, occurrence: int
) -> dict[str, str]:
    sources = dict(SOURCES)
    parts = sources[path].split(old)
    if occurrence <= 0 or occurrence >= len(parts):
        raise AssertionError(
            f"mutation occurrence missing in {path}: {old!r} #{occurrence}"
        )
    sources[path] = (
        old.join(parts[:occurrence])
        + new
        + old.join(parts[occurrence:])
    )
    return sources


@dataclass
class DagNode:
    predecessors: int
    sequence: int = 0


@dataclass(frozen=True)
class LLMPendingRecord:
    lifecycle: tuple[int, int]
    requester_pid: int
    requester_control: int
    relay_pid: int
    relay_control: int
    corr_id: int


PROV_KERNEL_FACT = 1 << 0
PROV_TRUSTED_CONTROL = 1 << 1
PROV_AGENT_DERIVED = 1 << 2
PROV_UNTRUSTED_FILE = 1 << 3
PROV_UNTRUSTED_TOOL = 1 << 4
PROV_CROSS_AGENT = 1 << 5
PROV_ACCEPT_CONTROL = PROV_KERNEL_FACT | PROV_TRUSTED_CONTROL | PROV_AGENT_DERIVED
PROV_ACCEPT_AGENT_LOOP = (
    PROV_ACCEPT_CONTROL
    | PROV_UNTRUSTED_FILE
    | PROV_UNTRUSTED_TOOL
    | PROV_CROSS_AGENT
)


@dataclass(frozen=True)
class AgentLoopEventModel:
    provenance_labels: int
    cause_sequence: int


class AgentLoopProvenanceModel:
    POLICIES = {
        "llm_request": (PROV_ACCEPT_AGENT_LOOP, PROV_AGENT_DERIVED),
        "llm_response": (PROV_ACCEPT_AGENT_LOOP, PROV_UNTRUSTED_TOOL),
        "send_message": (PROV_ACCEPT_AGENT_LOOP, PROV_AGENT_DERIVED),
        "action_commit": (PROV_ACCEPT_CONTROL, PROV_AGENT_DERIVED),
    }

    def __init__(self) -> None:
        self.labels = PROV_AGENT_DERIVED
        self.call_count = 0

    def _authorize(self, tool: str) -> int:
        accepted, output_add = self.POLICIES[tool]
        if self.labels & ~accepted:
            raise PermissionError(f"{tool} rejects current provenance")
        self.call_count += 1
        return output_add

    def call(self, tool: str) -> int:
        output_add = self._authorize(tool)
        self.labels |= output_add | PROV_AGENT_DERIVED
        return self.call_count

    def deliver(self, tool: str, target: AgentLoopProvenanceModel) -> AgentLoopEventModel:
        output_add = self._authorize(tool)
        event = AgentLoopEventModel(
            self.labels | PROV_AGENT_DERIVED | PROV_CROSS_AGENT,
            self.call_count,
        )
        target.consume(event)
        # The kernel commits the tool's output label after its IPC helper returns.
        self.labels |= output_add | PROV_AGENT_DERIVED
        return event

    def consume(self, event: AgentLoopEventModel) -> None:
        self.labels |= (
            event.provenance_labels | PROV_AGENT_DERIVED | PROV_CROSS_AGENT
        )


class LLMCorrelationModel:
    def __init__(self, *, ttl: int = 120, limit: int = 16) -> None:
        self.ttl = ttl
        self.limit = limit
        self.pending: list[LLMPendingRecord] = []
        self.deadlines: dict[LLMPendingRecord, int] = {}
        self.last_corr: dict[tuple[tuple[int, int], int, int], int] = {}
        self.terminals: dict[
            tuple[tuple[int, int], int, int, int, int, int], str
        ] = {}

    @staticmethod
    def requester_key(
        record: LLMPendingRecord,
    ) -> tuple[tuple[int, int], int, int]:
        return record.lifecycle, record.requester_pid, record.requester_control

    @staticmethod
    def terminal_key(
        record: LLMPendingRecord,
    ) -> tuple[tuple[int, int], int, int, int, int, int]:
        return (
            record.lifecycle,
            record.requester_pid,
            record.requester_control,
            record.relay_pid,
            record.relay_control,
            record.corr_id,
        )

    def reap(self, now: int) -> None:
        for record in list(self.pending):
            if now < self.deadlines[record]:
                continue
            self.pending.remove(record)
            del self.deadlines[record]
            self.terminals[self.terminal_key(record)] = "timeout"

    def request(
        self, record: LLMPendingRecord, *, delivered: bool, now: int = 0
    ) -> None:
        if record.requester_pid <= 0 or record.relay_pid <= 0 or record.corr_id == 0:
            raise ValueError("invalid LLM request identity")
        self.reap(now)
        if any(
            item.lifecycle == record.lifecycle
            and item.requester_pid == record.requester_pid
            and item.requester_control == record.requester_control
            and item.corr_id == record.corr_id
            for item in self.pending
        ):
            raise FileExistsError("duplicate correlation")
        requester = self.requester_key(record)
        if record.corr_id <= self.last_corr.get(requester, 0):
            raise FileExistsError("correlation is not strictly monotonic")
        if sum(self.requester_key(item) == requester for item in self.pending) >= self.limit:
            raise BlockingIOError("LLM pending limit")
        if delivered:
            self.pending.append(record)
            self.deadlines[record] = now + self.ttl
            self.last_corr[requester] = record.corr_id

    def respond(
        self,
        *,
        lifecycle: tuple[int, int],
        requester_pid: int,
        requester_control: int,
        relay_pid: int,
        relay_control: int,
        corr_id: int,
        delivered: bool,
        now: int = 0,
    ) -> None:
        self.reap(now)
        matches = [
            item
            for item in self.pending
            if item.lifecycle == lifecycle
            and item.requester_pid == requester_pid
            and item.requester_control == requester_control
            and item.relay_pid == relay_pid
            and item.relay_control == relay_control
            and item.corr_id == corr_id
        ]
        if len(matches) != 1:
            terminal = self.terminals.get(
                (
                    lifecycle,
                    requester_pid,
                    requester_control,
                    relay_pid,
                    relay_control,
                    corr_id,
                )
            )
            if terminal == "timeout":
                raise TimeoutError("LLM request expired")
            if terminal == "consumed":
                raise RuntimeError("LLM response already consumed")
            raise PermissionError("unmatched LLM response")
        if delivered:
            record = matches[0]
            self.pending.remove(record)
            del self.deadlines[record]
            self.terminals[self.terminal_key(record)] = "consumed"

    def teardown(
        self, *, lifecycle: tuple[int, int], pid: int, control_id: int
    ) -> None:
        self.pending = [
            item
            for item in self.pending
            if item.lifecycle != lifecycle
            or (
                (item.requester_pid != pid or item.requester_control != control_id)
                and (item.relay_pid != pid or item.relay_control != control_id)
            )
        ]
        self.deadlines = {
            record: deadline
            for record, deadline in self.deadlines.items()
            if record in self.pending
        }
        self.last_corr = {
            key: corr
            for key, corr in self.last_corr.items()
            if key != (lifecycle, pid, control_id)
        }
        self.terminals = {
            key: status
            for key, status in self.terminals.items()
            if key[0] != lifecycle
            or (
                (key[1] != pid or key[2] != control_id)
                and (key[3] != pid or key[4] != control_id)
            )
        }


@dataclass
class ExecutionModel:
    nodes: list[DagNode] = field(default_factory=list)
    completed: int = 0

    def add(self, node_id: int, predecessors: int) -> None:
        index = len(self.nodes)
        lower = 0 if index == 0 else (1 << index) - 1
        if node_id != index or predecessors & ~lower:
            raise ValueError("not a declaration-ordered DAG")
        self.nodes.append(DagNode(predecessors))

    def complete(self, node_id: int, sequence: int) -> None:
        if sequence <= 0:
            raise ValueError("terminal sequence must be visible")
        self.nodes[node_id].sequence = sequence
        self.completed |= 1 << node_id

    def admit(self, node_id: int, source_node: int | None, source_sequence: int) -> None:
        node = self.nodes[node_id]
        if node.predecessors & ~self.completed:
            raise BlockingIOError("predecessor pending")
        if node.predecessors == 0:
            if source_node is not None or source_sequence != 0:
                raise PermissionError("illegal root edge")
            return
        if source_node is None or not node.predecessors & (1 << source_node):
            raise PermissionError("undeclared predecessor")
        if self.nodes[source_node].sequence == 0:
            raise PermissionError("unpublished predecessor")
        if source_sequence != self.nodes[source_node].sequence:
            raise PermissionError("stale predecessor sequence")


@dataclass
class PhaseLeaseModel:
    reserved_workflow: bool
    available: list[int]
    state: str = "EMPTY"
    locked: list[int] = field(default_factory=list)

    def begin(self, charge_class: str, envelope: list[int]) -> None:
        expected = "RESERVED" if self.reserved_workflow else "ORDINARY"
        if charge_class != expected or not any(envelope):
            raise PermissionError("bad class or empty envelope")
        if len(envelope) != len(self.available) or any(
            amount < 0 or amount > free
            for amount, free in zip(envelope, self.available)
        ):
            raise BlockingIOError("envelope unavailable")
        self.locked = list(envelope)
        self.available = [
            free - amount for free, amount in zip(self.available, envelope)
        ]
        self.state = "ADMITTED"

    def activate(self) -> None:
        if self.state != "ADMITTED":
            raise RuntimeError("bad lease transition")
        self.state = "ACTIVE"

    def consume(self, kind: int, amount: int) -> None:
        if self.state != "ACTIVE" or amount > self.locked[kind]:
            raise RuntimeError("claim outside envelope")
        self.locked[kind] -= amount

    def settle(self) -> None:
        if self.state not in ("ADMITTED", "ACTIVE", "DEACTIVATED"):
            raise RuntimeError("bad settle")
        self.available = [
            free + remaining for free, remaining in zip(self.available, self.locked)
        ]
        self.locked = [0] * len(self.available)
        self.state = "SETTLED"


@dataclass
class FilePinCutModel:
    state: str = "EMPTY"
    bare_inflight: int = 0

    def pin_enter(self) -> bool:
        if self.state == "BUILDING":
            raise BlockingIOError("contract publication owns the cut")
        if self.state in ("FROZEN", "RETIRING"):
            return False
        if self.state != "EMPTY":
            raise RuntimeError("invalid contract state")
        self.bare_inflight += 1
        return True

    def pin_leave(self, active: bool) -> None:
        if not active:
            return
        if self.bare_inflight == 0:
            raise RuntimeError("unbalanced pin token")
        self.bare_inflight -= 1

    def create_begin(self) -> None:
        if self.state != "EMPTY" or self.bare_inflight != 0:
            raise BlockingIOError("pre-freeze work is still live")
        self.state = "BUILDING"

    def freeze(self) -> None:
        if self.state != "BUILDING" or self.bare_inflight != 0:
            raise RuntimeError("invalid freeze")
        self.state = "FROZEN"

    @staticmethod
    def checkpoint_allowed(
        operation_denied: bool, direct_active: bool, file_pin_active: bool
    ) -> bool:
        return not operation_denied or direct_active or file_pin_active


@dataclass
class ContractRetireModel:
    generation: int = 0
    state: str = "EMPTY"
    bare_inflight: int = 0
    running_count: int = 0

    def create(self) -> int:
        if self.bare_inflight != 0 or self.running_count != 0:
            raise BlockingIOError("contract references are live")
        if self.state not in ("EMPTY", "RECLAIMED"):
            raise FileExistsError("contract generation is still authoritative")
        self.generation += 1
        self.state = "FROZEN"
        return self.generation

    def retire(self, generation: int) -> tuple[str, str]:
        if generation != self.generation:
            return "STALE", self.state
        if self.state == "RECLAIMED":
            return "OK", self.state
        if self.state not in ("FROZEN", "RETIRING"):
            return "NOT_FOUND", self.state
        self.state = "RETIRING"
        if self.bare_inflight != 0 or self.running_count != 0:
            return "RETRY", self.state
        self.state = "RECLAIMED"
        return "OK", self.state

    def direct_allowed(self) -> bool:
        return self.state not in ("FROZEN", "RETIRING")


@dataclass(frozen=True)
class TerminalRecord:
    status: str
    terminal_tick: int
    context_tick: int


@dataclass
class TerminalTickModel:
    deadline_tick: int
    cache: dict[int, TerminalRecord] = field(default_factory=dict)

    def _publish(
        self, attempt_id: int, terminal_tick: int, context_tick: int
    ) -> TerminalRecord:
        if (
            attempt_id <= 0
            or context_tick <= 0
            or terminal_tick < context_tick
            or attempt_id in self.cache
        ):
            raise ValueError("invalid or duplicate terminal publication")
        status = (
            "TIMEOUT"
            if self.deadline_tick != 0 and terminal_tick >= self.deadline_tick
            else "OK"
        )
        record = TerminalRecord(status, terminal_tick, context_tick)
        self.cache[attempt_id] = record
        return record

    def finish_effect(
        self,
        attempt_id: int,
        start_tick: int,
        terminal_tick: int,
        completion_tick: int,
    ) -> TerminalRecord:
        if completion_tick < terminal_tick:
            raise ValueError("publication precedes execution terminal")
        return self._publish(attempt_id, terminal_tick, start_tick)

    def timeout_sync(self, attempt_id: int, now: int) -> TerminalRecord:
        if self.deadline_tick == 0 or now < self.deadline_tick:
            raise BlockingIOError("deadline not due")
        return self._publish(attempt_id, now, now)

    def replay(self, attempt_id: int) -> TerminalRecord:
        return self.cache[attempt_id]


@dataclass
class RetiringTaskModel:
    state: str = "RETIRING"

    TASK_FLAG = 1 << 0
    ALL_FLAGS = TASK_FLAG

    @classmethod
    def _task_binding(cls, internal_flags: int) -> bool:
        if internal_flags & ~cls.ALL_FLAGS:
            raise PermissionError("unknown internal binding flag")
        return (internal_flags & cls.TASK_FLAG) != 0

    def preflight(self, internal_flags: int) -> str:
        is_task = self._task_binding(internal_flags)
        if self.state != "RETIRING":
            return "ALLOW"
        return "ALLOW" if is_task else "DENIED"

    def validate(self, internal_flags: int) -> tuple[str, str, bool]:
        if not self._task_binding(internal_flags):
            raise PermissionError("Task validate requires an internal Task binding")
        return "PENDING", self.preflight(internal_flags), False

    def admit(self, internal_flags: int) -> str:
        is_task = self._task_binding(internal_flags)
        if self.state != "RETIRING":
            return "EXECUTE"
        return "DENIED" if is_task else "CANCELLED"


class ExecutionContractTests(unittest.TestCase):
    def assert_mutation_rejected(
        self, path: str, old: str, new: str, *, count: int = 1
    ) -> None:
        with self.assertRaises(ContractError):
            validate_execution_contract(mutated(path, old, new, count=count))

    def test_current_implementation_satisfies_contract(self) -> None:
        validate_execution_contract(SOURCES)

    def test_llm_pending_publication_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "pending->active = 1;",
            "pending->active = 0;",
        )

    def test_llm_pending_ttl_regression_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "#define AGENT_LLM_PENDING_TTL_TICKS (660ULL * TICKS_PER_SEC)",
            "#define AGENT_LLM_PENDING_TTL_TICKS (120ULL * TICKS_PER_SEC)",
        )

    def test_llm_wrong_relay_match_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "pending->relay_control_id != relay->agent_control_id",
            "pending->relay_control_id == relay->agent_control_id",
        )

    def test_llm_response_consume_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "\tagent_llm_terminal_remember_locked(pending, AGENT_STATUS_OK);\n"
            "\tmemset(pending, 0, sizeof(*pending));",
            "\tagent_llm_terminal_remember_locked(pending, AGENT_STATUS_OK);\n"
            "\t/* Mutation: leave replay authority live after delivery. */",
        )

    def test_llm_monotonic_comparison_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "requester_state != 0 && corr_id <= requester_state->last_corr_id",
            "requester_state != 0 && corr_id < requester_state->last_corr_id",
        )

    def test_llm_monotonic_publication_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "\trequester_state->last_corr_id = corr_id;",
            "\t/* Mutation: successful delivery does not advance last_corr_id. */",
        )

    def test_llm_expiry_comparison_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "now < pending->deadline_tick",
            "now <= pending->deadline_tick",
        )

    def test_llm_expiry_tombstone_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "agent_llm_terminal_remember_locked(pending, AGENT_STATUS_TIMEOUT);",
            "agent_llm_terminal_remember_locked(pending, AGENT_STATUS_OK);",
        )

    def test_llm_tick_reap_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "\tenabled = intr_save();\n"
            "\tagent_llm_pending_reap_locked(now);\n"
            "\tintr_restore(enabled);\n"
            "\tfs_deferred_reclaim_tick(now);",
            "\tenabled = intr_save();\n"
            "\t/* Mutation: silent relays retain pending slots forever. */\n"
            "\tintr_restore(enabled);\n"
            "\tfs_deferred_reclaim_tick(now);",
        )

    def test_llm_pending_limit_diagnostic_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            '"llm_pending_limit"',
            '"event_queue_full"',
        )

    def test_llm_teardown_cleanup_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "\tagent_llm_pending_proc_clear(p);",
            "\t/* Mutation: pending LLM state survives teardown. */",
        )

    def test_llm_event_cause_future_sequence_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "agent_ticks(), p->agent_call_count, op->payload,",
            "agent_ticks(), p->agent_call_count + 1, op->payload,",
        )

    def test_send_message_cause_future_sequence_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "p->agent_call_count, op->payload, 1, &delivered);",
            "p->agent_call_count + 1, op->payload, 1, &delivered);",
        )

    def test_agent_loop_cross_agent_mask_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_tool_protocol.c",
            "\t AGENT_PROVENANCE_CROSS_AGENT_DATA)",
            "\t AGENT_PROVENANCE_AGENT_DERIVED)",
        )

    def test_agent_loop_tool_output_mask_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_tool_protocol.c",
            "\t AGENT_PROVENANCE_UNTRUSTED_TOOL_OUTPUT |",
            "\t AGENT_PROVENANCE_AGENT_DERIVED |",
        )

    def test_agent_loop_message_policy_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_tool_protocol.c",
            "X(AGENT_CAP_MESSAGE_SEND, PROV_ACCEPT_AGENT_LOOP, PROV_DERIVED, AGENT_SIDE_EFFECT_IPC)",
            "X(AGENT_CAP_MESSAGE_SEND, PROV_ACCEPT_CONTROL, PROV_DERIVED, AGENT_SIDE_EFFECT_IPC)",
        )

    def test_agent_loop_llm_response_policy_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_tool_protocol.c",
            "X(AGENT_CAP_LLM_RELAY, PROV_ACCEPT_AGENT_LOOP, PROV_TOOL, AGENT_SIDE_EFFECT_IPC)",
            "X(AGENT_CAP_LLM_RELAY, PROV_ACCEPT_CONTROL, PROV_TOOL, AGENT_SIDE_EFFECT_IPC)",
        )

    def test_agent_loop_does_not_broaden_other_effect_tools(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_tool_protocol.c",
            "X(AGENT_CAP_DEPENDENCY_UPDATE, PROV_ACCEPT_CONTROL, PROV_DERIVED, AGENT_SIDE_EFFECT_METADATA)",
            "X(AGENT_CAP_DEPENDENCY_UPDATE, PROV_ACCEPT_AGENT_LOOP, PROV_DERIVED, AGENT_SIDE_EFFECT_METADATA)",
        )

    def test_agent_loop_provenance_and_cause_model(self) -> None:
        main = AgentLoopProvenanceModel()
        relay = AgentLoopProvenanceModel()

        request1 = main.deliver("llm_request", relay)
        self.assertEqual(request1.cause_sequence, 1)
        self.assertTrue(relay.labels & PROV_CROSS_AGENT)

        response1 = relay.deliver("llm_response", main)
        self.assertEqual(response1.cause_sequence, 1)
        self.assertTrue(main.labels & PROV_CROSS_AGENT)
        self.assertTrue(relay.labels & PROV_UNTRUSTED_TOOL)

        request2 = main.deliver("llm_request", relay)
        self.assertEqual(request2.cause_sequence, 2)
        response2 = relay.deliver("llm_response", main)
        self.assertEqual(response2.cause_sequence, 2)
        self.assertEqual(
            main.labels & (PROV_CROSS_AGENT | PROV_UNTRUSTED_TOOL),
            PROV_CROSS_AGENT | PROV_UNTRUSTED_TOOL,
        )

        request3 = main.deliver("llm_request", relay)
        self.assertEqual(request3.cause_sequence, 3)
        forwarded = main.deliver("send_message", relay)
        self.assertEqual(forwarded.cause_sequence, 4)
        self.assertEqual(
            forwarded.provenance_labels
            & (PROV_CROSS_AGENT | PROV_UNTRUSTED_TOOL),
            PROV_CROSS_AGENT | PROV_UNTRUSTED_TOOL,
        )
        with self.assertRaises(PermissionError):
            main.call("action_commit")

    def test_llm_correlation_model_denies_unsolicited_wrong_and_replayed(self) -> None:
        model = LLMCorrelationModel()
        pending = LLMPendingRecord((4, 17), 21, 101, 22, 102, 7001)

        model.request(pending, delivered=False)
        self.assertEqual(model.pending, [])
        with self.assertRaises(PermissionError):
            model.respond(
                lifecycle=(4, 17),
                requester_pid=21,
                requester_control=101,
                relay_pid=22,
                relay_control=102,
                corr_id=7001,
                delivered=True,
            )

        model.request(pending, delivered=True)
        with self.assertRaises(FileExistsError):
            model.request(
                LLMPendingRecord((4, 17), 21, 101, 23, 103, 7001),
                delivered=True,
            )
        for relay_pid, relay_control, corr_id in (
            (23, 103, 7001),
            (22, 102, 7002),
        ):
            with self.assertRaises(PermissionError):
                model.respond(
                    lifecycle=(4, 17),
                    requester_pid=21,
                    requester_control=101,
                    relay_pid=relay_pid,
                    relay_control=relay_control,
                    corr_id=corr_id,
                    delivered=True,
                )
        self.assertEqual(model.pending, [pending])

        model.respond(
            lifecycle=(4, 17),
            requester_pid=21,
            requester_control=101,
            relay_pid=22,
            relay_control=102,
            corr_id=7001,
            delivered=False,
        )
        self.assertEqual(model.pending, [pending])
        model.respond(
            lifecycle=(4, 17),
            requester_pid=21,
            requester_control=101,
            relay_pid=22,
            relay_control=102,
            corr_id=7001,
            delivered=True,
        )
        self.assertEqual(model.pending, [])
        with self.assertRaises(RuntimeError):
            model.respond(
                lifecycle=(4, 17),
                requester_pid=21,
                requester_control=101,
                relay_pid=22,
                relay_control=102,
                corr_id=7001,
                delivered=True,
            )
        with self.assertRaises(FileExistsError):
            model.request(pending, delivered=True)

        next_pending = LLMPendingRecord((4, 17), 21, 101, 22, 102, 7002)
        model.request(next_pending, delivered=True)
        with self.assertRaises(RuntimeError):
            model.respond(
                lifecycle=(4, 17),
                requester_pid=21,
                requester_control=101,
                relay_pid=22,
                relay_control=102,
                corr_id=7001,
                delivered=True,
            )
        self.assertEqual(model.pending, [next_pending])

    def test_llm_correlation_model_reaps_and_rejects_expired_reuse(self) -> None:
        model = LLMCorrelationModel(ttl=5, limit=1)
        expired = LLMPendingRecord((5, 23), 31, 111, 32, 112, 8001)

        model.request(expired, delivered=False, now=10)
        model.request(expired, delivered=True, now=11)
        with self.assertRaises(BlockingIOError):
            model.request(
                LLMPendingRecord((5, 23), 31, 111, 32, 112, 8002),
                delivered=True,
                now=15,
            )

        model.reap(16)
        self.assertEqual(model.pending, [])
        with self.assertRaises(TimeoutError):
            model.respond(
                lifecycle=(5, 23),
                requester_pid=31,
                requester_control=111,
                relay_pid=32,
                relay_control=112,
                corr_id=8001,
                delivered=True,
                now=16,
            )
        with self.assertRaises(FileExistsError):
            model.request(expired, delivered=True, now=16)

        next_pending = LLMPendingRecord((5, 23), 31, 111, 32, 112, 8002)
        model.request(next_pending, delivered=True, now=16)
        with self.assertRaises(TimeoutError):
            model.respond(
                lifecycle=(5, 23),
                requester_pid=31,
                requester_control=111,
                relay_pid=32,
                relay_control=112,
                corr_id=8001,
                delivered=True,
                now=17,
            )
        self.assertEqual(model.pending, [next_pending])

    def test_llm_correlation_model_clears_requester_and_relay_teardown(self) -> None:
        first = LLMPendingRecord((6, 31), 41, 201, 42, 202, 8001)
        second = LLMPendingRecord((6, 31), 43, 203, 42, 202, 8002)
        model = LLMCorrelationModel()
        model.request(first, delivered=True)
        model.request(second, delivered=True)
        model.teardown(lifecycle=(6, 31), pid=41, control_id=201)
        self.assertEqual(model.pending, [second])
        model.request(first, delivered=True)
        self.assertEqual(model.pending, [second, first])
        model.teardown(lifecycle=(6, 31), pid=42, control_id=202)
        self.assertEqual(model.pending, [])

    def test_digest_width_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "include/agent_execution_contract_abi.h",
            "#define AGENT_EXECUTION_DIGEST_SIZE           32U",
            "#define AGENT_EXECUTION_DIGEST_SIZE           16U",
        )

    def test_forward_edge_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "(node->predecessor_mask & ~lower_mask) != 0",
            "(node->predecessor_mask & ~lower_mask) == 0",
        )

    def test_exact_source_sequence_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "binding->source_context_sequence != source->context_sequence",
            "binding->source_context_sequence == source->context_sequence",
        )

    def test_direct_effect_bypass_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "agent_execution_contract_gate_direct_syscall(",
            "agent_execution_contract_gate_direct_syscall_bypassed(",
        )

    def test_direct_denial_controller_flag_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_evidence_ring.c",
            "controller->is_agent &&\n",
            "controller->is_agent && controller->vfs_scope_controller &&\n",
        )

    def test_file_pin_gate_bypass_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "agent_execution_contract_file_pin_enter(",
            "agent_execution_contract_file_pin_enter_bypassed(",
        )

    def test_file_pin_release_before_settlement_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "\t\tif (syscall_needs_transaction(class)) {",
            "\t\tagent_execution_contract_file_pin_leave(&file_pin_guard);\n"
            "\t\tif (syscall_needs_transaction(class)) {",
        )

    def test_file_pin_building_retry_cannot_dispatch(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "\t\t\t*operation_denied = 1;\n"
            "\t\t\tgoto finish;\n"
            "\t\t}\n"
            "\t}\n"
            "\tdirect_side_effects = syscall_direct_agent_side_effects(",
            "\t\t\t*operation_denied = 1;\n"
            "\t\t}\n"
            "\t}\n"
            "\tdirect_side_effects = syscall_direct_agent_side_effects(",
        )

    def test_pipe_read_cannot_pin_contract_publication_cut(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "transaction->file->type == FD_INODE",
            "transaction->file->type == FD_PIPE",
        )

    def test_frozen_file_pin_denial_mutation_is_rejected(self) -> None:
        old = (
            "if (agent_execution_record_enforced(record, lifecycle)) {\n"
            "\t\tintr_restore(enabled);\n"
            "\t\treturn AGENT_STATUS_OK;\n"
            "\t}"
        )
        new = old.replace("AGENT_STATUS_OK", "AGENT_STATUS_DENIED")
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c", old, new
        )

    def test_retire_reclaimed_transition_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "record->state =\n\t\t\t\t\tAGENT_EXECUTION_CONTRACT_RECLAIMED;",
            "record->state = AGENT_EXECUTION_CONTRACT_RETIRING;",
        )

    def test_reclaimed_create_recycle_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "record->state == AGENT_EXECUTION_CONTRACT_RECLAIMED",
            "record->state == AGENT_EXECUTION_CONTRACT_RETIRING",
        )

    def test_reclaimed_query_pin_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "query_pinned = 1;",
            "query_pinned = 0;",
        )

    def test_fstat_removed_from_outer_pin_cut_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "id == SYS_read || id == SYS_write || id == SYS_fstat",
            "id == SYS_read || id == SYS_write",
        )

    def test_background_release_before_checkpoint_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "\tif (!background_done && !operation_denied &&",
            "\tagent_execution_contract_file_pin_leave(&file_pin_guard);\n"
            "\tif (!background_done && !operation_denied &&",
        )

    def test_denied_active_guard_checkpoint_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/syscall.c",
            "!operation_denied || direct_guard.active ||\n"
            "\t\t\t     file_pin_guard.active",
            "!operation_denied",
        )

    def test_phase_class_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "uint expected_class = p->resource_slot_reserved ?",
            "uint expected_class = node->charge_class ?",
        )

    def test_empty_envelope_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/resource_controller.c",
            "if (union_mask == 0)\n\t\treturn -1;",
            "if (union_mask != 0)\n\t\treturn -1;",
        )

    def test_phase_cleanup_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/proc.c",
            "resource_phase_process_cleanup(p)",
            "resource_phase_process_cleanup_bypassed(p)",
        )

    def test_pre_effect_evidence_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "agent_evidence_context_reserve(",
            "agent_evidence_context_reserve_bypassed(",
            count=3,
        )

    def test_critical_reservation_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_provenance.c",
            "agent_evidence_security_reserve(p, &reservation)",
            "agent_evidence_security_reserve_bypassed(p, &reservation)",
        )

    def test_context_release_before_security_evidence_is_rejected(self) -> None:
        old = (
            "\telse if (security_reservation != 0)\n"
            "\t\t*evidence_ticket_out =\n"
            "\t\t\tagent_observe_commit_security_reserved_ticket(\n"
            "\t\t\t\tp, &record, security_reservation);\n"
            "\tagent_context_publish_end(p);"
        )
        new = (
            "\tagent_context_publish_end(p);\n"
            "\telse if (security_reservation != 0)\n"
            "\t\t*evidence_ticket_out =\n"
            "\t\t\tagent_observe_commit_security_reserved_ticket(\n"
            "\t\t\t\tp, &record, security_reservation);"
        )
        self.assert_mutation_rejected("os/agent_context.c", old, new)

    def test_wait_payload_before_provenance_merge_is_rejected(self) -> None:
        source = SOURCES["os/agent_ipc.c"]
        body = function_body(source, "sys_agent_wait")
        merge = body.index("\t\tif (agent_provenance_merge_current(")
        copyout = body.index("\t\tif (eventaddr &&", merge)
        attribution = body.index("\t\tif (event.span_id", copyout)
        mutated_body = (
            body[:merge]
            + body[copyout:attribution]
            + body[merge:copyout]
            + body[attribution:]
        )
        mutated_sources = dict(SOURCES)
        mutated_sources["os/agent_ipc.c"] = source.replace(body, mutated_body, 1)
        with self.assertRaises(ContractError):
            validate_execution_contract(mutated_sources)

    def test_zero_denial_ticket_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "status != AGENT_STATUS_DENIED || ticket == 0",
            "status != AGENT_STATUS_DENIED",
        )

    def test_terminal_before_ticket_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "if (result < 0 || evidence_ticket == 0)",
            "if (result < 0)",
        )

    def test_v3_binding_cannot_be_rebound_to_current_lifecycle(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "binding.lifecycle.id = req.contract.lifecycle.id;",
            "binding.lifecycle.id = lifecycle.id;",
        )

    def test_v3_response_cannot_echo_requested_lifecycle(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "resp.contract.lifecycle.id = lifecycle.id;",
            "resp.contract.lifecycle.id = req.contract.lifecycle.id;",
        )

    def test_v3_denial_without_ticket_mutation_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            validate_execution_contract(
                mutated_occurrence(
                    "os/agent_core.c",
                    "status != AGENT_STATUS_STALE) || ticket == 0",
                    "status != AGENT_STATUS_STALE)",
                    2,
                )
            )

    def test_v3_zero_request_id_denial_bypass_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "if (enforced && op.tool_id > 0) {",
            "if (enforced && req.request_id != 0 && op.tool_id > 0) {",
        )

    def test_v3_copyout_ticket_invariant_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "resp.evidence_ticket == 0) {",
            "resp.evidence_ticket != 0) {",
        )

    def test_preflight_denial_provenance_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "result->output_provenance_labels = decision.output_labels;",
            "result->output_provenance_labels = 0;",
        )

    def test_kernel_outcome_terminal_tick_removal_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.h",
            "\tuint64 terminal_tick;",
            "\tuint64 provider_completion_tick;",
        )

    def test_completion_cache_outcome_drop_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "memmove(&cached->outcome, outcome, sizeof(cached->outcome));",
            "memset(&cached->outcome, 0, sizeof(cached->outcome));",
        )

    def test_cached_replay_outcome_drop_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "memmove(outcome, &cached->outcome,",
            "memset(outcome, 0,",
        )

    def test_timeout_sync_terminal_tick_substitution_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            validate_execution_contract(
                mutated_occurrence(
                    "os/agent_core.c",
                    "outcome->terminal_tick = now;",
                    "outcome->terminal_tick = request_deadline_tick;",
                    4,
                )
            )

    def test_post_effect_deadline_uses_terminal_tick(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "terminal_tick >= claim.deadline_tick",
            "tick >= claim.deadline_tick",
        )

    def test_post_effect_outcome_cannot_reuse_start_tick(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "terminal_tick = agent_ticks();\n"
            "\tif (outcome != 0)\n"
            "\t\toutcome->terminal_tick = terminal_tick;",
            "terminal_tick = agent_ticks();\n"
            "\tif (outcome != 0)\n"
            "\t\toutcome->terminal_tick = tick;",
        )

    def test_post_effect_context_retains_service_start_tick(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "p, op, res, tick, status",
            "p, op, res, terminal_tick, status",
        )

    def test_task_completion_cannot_use_publication_as_terminal_time(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "completion->terminal_tick = outcome->terminal_tick;",
            "completion->terminal_tick = agent_task_bridge_now();",
        )

    def test_output_none_only_bypass_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "node.output_artifact_type != AGENT_ARTIFACT_NONE",
            "node.output_artifact_type == AGENT_ARTIFACT_NONE",
        )

    def test_task_binding_flag_stamp_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "binding->internal_flags =\n"
            "\t\tAGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL;",
            "binding->internal_flags = 0;",
        )

    def test_scalar_v3_cannot_forge_task_binding_flag(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "memset(&binding, 0, sizeof(binding));",
            "memset(&binding, 0, sizeof(binding));\n"
            "\tbinding.internal_flags =\n"
            "\t\tAGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL;",
        )

    def test_unknown_internal_binding_flag_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "~AGENT_EXECUTION_BINDING_INTERNAL_F_ALL) != 0",
            "~AGENT_EXECUTION_BINDING_INTERNAL_F_ALL) == 0",
        )

    def test_retiring_task_validate_allow_mutation_is_rejected(self) -> None:
        old = (
            "if ((binding->internal_flags &\n"
            "\t\t     AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0) {\n"
            "\t\t\tagent_execution_preflight_decide(\n"
            "\t\t\t\tresult, AGENT_STATUS_OK,\n"
            "\t\t\t\tAGENT_EXECUTION_REASON_NONE);\n"
            "\t\t\tgoto out_locked;\n"
            "\t\t}"
        )
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            old,
            old.replace("AGENT_STATUS_OK", "AGENT_STATUS_DENIED"),
        )

    def test_retiring_task_validate_must_remain_pending(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "validation->output_provenance_labels = 0;\n"
            "\treturn AGENT_TASK_HOOK_PENDING;",
            "validation->output_provenance_labels = 0;\n"
            "\treturn AGENT_TASK_HOOK_DENIED;",
        )

    def test_retiring_task_admit_status_swap_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0 ?\n"
            "\t\t\t\tAGENT_STATUS_DENIED : AGENT_STATUS_CANCELLED,",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0 ?\n"
            "\t\t\t\tAGENT_STATUS_CANCELLED : AGENT_STATUS_DENIED,",
        )

    def test_task_submit_internal_flag_gate_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) == 0)",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0)",
        )

    def test_task_validate_terminal_evidence_route_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "status = agent_execution_contract_preflight(",
            "status = agent_execution_task_submit_sync(",
        )

    def test_retiring_task_preflight_cancel_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "result, AGENT_STATUS_DENIED,\n"
            "\t\t\tAGENT_EXECUTION_REASON_CONTRACT_RETIRING);",
            "result, AGENT_STATUS_CANCELLED,\n"
            "\t\t\tAGENT_EXECUTION_REASON_CONTRACT_RETIRING);",
        )

    def test_completion_eviction_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "cache_index = agent_execution_cache_index(\n"
            "\t\trecord, claim->node_id, claim->attempt_id);",
            "cache_index = claim->node_id % 4;",
        )

    def test_retryable_failure_poison_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "if (terminal_failure) {",
            "if (1) {",
        )

    def test_private_dependency_poison_cannot_publish_failed_mask(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "\t\t\tpoisoned |= 1ULL << i;",
            "\t\t\trecord->failed_mask |= 1ULL << i;\n"
            "\t\t\tpoisoned |= 1ULL << i;",
        )

    def test_create_replay_requires_same_request_id(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "record->create_request_id != control->request_id ||",
            "record->create_request_id == control->request_id ||",
            count=2,
        )

    def test_create_replay_rehashes_canonical_nodes(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "agent_execution_contract_hash_node(&fingerprint, &node);",
            "agent_execution_hash_u64(&fingerprint, node.node_id);",
        )

    def test_create_replay_normalizes_zero_schema_digest(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "if (agent_execution_digest_zero(node.schema_digest))",
            "if (!agent_execution_digest_zero(node.schema_digest))",
        )

    def test_force_cancel_cannot_steal_timeout_winner(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED) != 0) {",
            "AGENT_EXECUTION_RUNTIME_F_TIMEOUT_REQUESTED) == 0) {",
        )

    def test_timeout_cannot_steal_cancel_winner(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED)) != 0) {",
            "AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED)) == 0) {",
        )

    def test_cancel_effect_fence_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "AGENT_EXECUTION_RUNTIME_F_CANCEL_REQUESTED;",
            "AGENT_EXECUTION_RUNTIME_F_EFFECT_STARTED;",
        )

    def test_dag_model_requires_all_predecessors_and_exact_edge_sequence(self) -> None:
        model = ExecutionModel()
        model.add(0, 0)
        model.add(1, 1 << 0)
        model.add(2, (1 << 0) | (1 << 1))
        model.complete(0, 41)
        model.admit(1, 0, 41)
        with self.assertRaises(BlockingIOError):
            model.admit(2, 0, 41)
        model.complete(1, 73)
        model.admit(2, 1, 73)
        with self.assertRaises(PermissionError):
            model.admit(2, 1, 72)
        with self.assertRaises(PermissionError):
            model.admit(2, None, 0)

    def test_dag_model_rejects_self_forward_and_sparse_declarations(self) -> None:
        model = ExecutionModel()
        model.add(0, 0)
        with self.assertRaises(ValueError):
            model.add(2, 1)
        with self.assertRaises(ValueError):
            model.add(1, 1 << 1)

    def test_sha256_model_binds_all_bytes(self) -> None:
        first = hashlib.sha256(b"agentos.execution.request.v1\x00payload").digest()
        second = hashlib.sha256(b"agentos.execution.request.v1\x00payloae").digest()
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first[16:], second[16:])

    def test_terminal_tick_model_uses_one_post_effect_deadline_tick(self) -> None:
        before_deadline = TerminalTickModel(deadline_tick=100)
        success = before_deadline.finish_effect(
            attempt_id=1, start_tick=90, terminal_tick=99, completion_tick=140
        )
        self.assertEqual(
            success, TerminalRecord("OK", terminal_tick=99, context_tick=90)
        )

        after_deadline = TerminalTickModel(deadline_tick=100)
        timeout = after_deadline.finish_effect(
            attempt_id=1, start_tick=90, terminal_tick=105, completion_tick=140
        )
        self.assertEqual(
            timeout,
            TerminalRecord("TIMEOUT", terminal_tick=105, context_tick=90),
        )

    def test_terminal_tick_model_replay_preserves_kernel_decision_time(self) -> None:
        model = TerminalTickModel(deadline_tick=100)
        published = model.finish_effect(
            attempt_id=1, start_tick=80, terminal_tick=101, completion_tick=130
        )
        replayed = model.replay(1)
        self.assertIs(replayed, published)
        self.assertEqual(replayed.terminal_tick, 101)
        self.assertNotEqual(replayed.terminal_tick, 130)

    def test_timeout_sync_model_records_now_not_requested_deadline(self) -> None:
        model = TerminalTickModel(deadline_tick=100)
        with self.assertRaises(BlockingIOError):
            model.timeout_sync(attempt_id=1, now=99)
        timeout = model.timeout_sync(attempt_id=1, now=107)
        self.assertEqual(
            timeout,
            TerminalRecord("TIMEOUT", terminal_tick=107, context_tick=107),
        )
        self.assertEqual(model.replay(1), timeout)

    def test_retiring_task_model_validate_is_pure_then_admit_is_denied(self) -> None:
        model = RetiringTaskModel()
        self.assertEqual(
            model.validate(RetiringTaskModel.TASK_FLAG),
            ("PENDING", "ALLOW", False),
        )
        self.assertEqual(model.admit(RetiringTaskModel.TASK_FLAG), "DENIED")

    def test_retiring_task_model_keeps_scalar_cancel_semantics(self) -> None:
        model = RetiringTaskModel()
        self.assertEqual(model.preflight(0), "DENIED")
        self.assertEqual(model.admit(0), "CANCELLED")
        with self.assertRaises(PermissionError):
            model.preflight(1 << 1)

    def test_phase_model_derives_class_and_refunds_unused_credit(self) -> None:
        lease = PhaseLeaseModel(True, [4, 8, 2])
        with self.assertRaises(PermissionError):
            lease.begin("ORDINARY", [1, 0, 0])
        lease.begin("RESERVED", [2, 3, 0])
        self.assertEqual(lease.available, [2, 5, 2])
        lease.activate()
        lease.consume(0, 1)
        lease.settle()
        self.assertEqual(lease.available, [3, 8, 2])
        self.assertEqual(lease.state, "SETTLED")

    def test_phase_model_rejects_empty_or_over_envelope(self) -> None:
        lease = PhaseLeaseModel(False, [1, 1])
        with self.assertRaises(PermissionError):
            lease.begin("ORDINARY", [0, 0])
        with self.assertRaises(BlockingIOError):
            lease.begin("ORDINARY", [2, 0])

    def test_file_pin_model_blocks_create_until_last_settlement(self) -> None:
        cut = FilePinCutModel()
        first = cut.pin_enter()
        second = cut.pin_enter()
        with self.assertRaises(BlockingIOError):
            cut.create_begin()
        cut.pin_leave(first)
        with self.assertRaises(BlockingIOError):
            cut.create_begin()
        cut.pin_leave(second)
        cut.create_begin()
        cut.freeze()
        self.assertEqual((cut.state, cut.bare_inflight), ("FROZEN", 0))

    def test_file_pin_model_building_retries_and_frozen_is_non_authorizing(self) -> None:
        cut = FilePinCutModel()
        cut.create_begin()
        with self.assertRaises(BlockingIOError):
            cut.pin_enter()
        cut.freeze()
        active = cut.pin_enter()
        self.assertFalse(active)
        self.assertEqual((cut.state, cut.bare_inflight), ("FROZEN", 0))
        cut.pin_leave(active)
        self.assertEqual((cut.state, cut.bare_inflight), ("FROZEN", 0))

    def test_file_pin_model_denied_checkpoint_requires_an_active_guard(self) -> None:
        allowed = FilePinCutModel.checkpoint_allowed
        self.assertFalse(allowed(True, False, False))
        self.assertTrue(allowed(True, True, False))
        self.assertTrue(allowed(True, False, True))
        self.assertTrue(allowed(False, False, False))

    def test_contract_retire_model_drains_replays_and_recycles_generation(self) -> None:
        model = ContractRetireModel()
        first = model.create()
        model.running_count = 1
        self.assertEqual(model.retire(first), ("RETRY", "RETIRING"))
        self.assertFalse(model.direct_allowed())
        model.running_count = 0
        self.assertEqual(model.retire(first), ("OK", "RECLAIMED"))
        self.assertEqual(model.retire(first), ("OK", "RECLAIMED"))
        self.assertTrue(model.direct_allowed())
        second = model.create()
        self.assertGreater(second, first)
        self.assertEqual(model.retire(first)[0], "STALE")

    def test_contract_retire_model_waits_for_direct_cut(self) -> None:
        model = ContractRetireModel()
        generation = model.create()
        model.bare_inflight = 1
        self.assertEqual(model.retire(generation), ("RETRY", "RETIRING"))
        model.bare_inflight = 0
        self.assertEqual(model.retire(generation), ("OK", "RECLAIMED"))

    def test_fail_closed_denial_model_requires_ticket(self) -> None:
        def direct_effect(enforced: bool, ticket: int) -> str:
            if not enforced:
                return "OK"
            return "DENIED" if ticket > 0 else "RETRY"

        self.assertEqual(direct_effect(False, 0), "OK")
        self.assertEqual(direct_effect(True, 0), "RETRY")
        self.assertEqual(direct_effect(True, 19), "DENIED")


if __name__ == "__main__":
    unittest.main()
