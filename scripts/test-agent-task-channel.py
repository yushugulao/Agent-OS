#!/usr/bin/env python3
"""Static, mutation, and model contracts for the asynchronous Task Channel."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "agent_task_channel_abi.h",
    "os/agent.c",
    "os/agent_core.c",
    "os/agent_execution_contract.c",
    "os/agent_execution_contract.h",
    "os/agent_evidence_ring.c",
    "os/agent_evidence_ring.h",
    "os/agent_internal.h",
    "os/agent_lifecycle.c",
    "os/agent_lifecycle.h",
    "os/agent_task_bridge.h",
    "os/agent_task_bridge.c",
    "os/agent_task_channel.h",
    "os/agent_task_channel.c",
    "os/proc.c",
    "os/syscall.c",
    "os/trap.c",
    "user/src/agenttask_ucore.c",
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


def validate_task_channel(sources: dict[str, str]) -> None:
    abi = sources["agent_task_channel_abi.h"]
    facade = sources["os/agent.c"]
    core = sources["os/agent_core.c"]
    execution = sources["os/agent_execution_contract.c"]
    execution_header = sources["os/agent_execution_contract.h"]
    evidence = sources["os/agent_evidence_ring.c"]
    evidence_header = sources["os/agent_evidence_ring.h"]
    internal = sources["os/agent_internal.h"]
    lifecycle_source = sources["os/agent_lifecycle.c"]
    lifecycle_header = sources["os/agent_lifecycle.h"]
    bridge_header = sources["os/agent_task_bridge.h"]
    bridge = sources["os/agent_task_bridge.c"]
    header = sources["os/agent_task_channel.h"]
    channel = sources["os/agent_task_channel.c"]
    proc = sources["os/proc.c"]
    syscall = sources["os/syscall.c"]
    trap = sources["os/trap.c"]
    guest = sources["user/src/agenttask_ucore.c"]

    for token in (
        "#define PERF_OPERATION_COUNT 16U",
        "_Static_assert(PERF_OPERATION_COUNT == AGENT_TASK_CHANNEL_CAPACITY",
        "quantiles=nearest_rank",
        "sample_semantics=pre_effect_context_service_start",
        "interval_origin=sequence_start_boundary",
        "service_metric=service_start_tick_intervals",
        "sequence_metric=agent_info_boundary_elapsed_ticks",
        "boundary_overhead=start_return+end_entry_included",
        "post_sequence_excluded=1",
        "wall_clock=unavailable raw_cycles=not_claimed",
        "syscall_source=guest_call_sites",
        "kernel_path_syscall_counter=unavailable",
        "provider=synchronous_echo",
        "running_cancel_latency=unavailable",
        "terminal_pending_saturation=unavailable",
        'report_ablation(\n\t\t"batch", 1,',
        'report_ablation(\n\t\t"scalar_v3", PERF_OPERATION_COUNT,',
        'report_ablation(\n\t\t"sq_cq", 2,',
        "PERF_OPERATION_COUNT * TOOL_V3_DISPATCH_HEADER_BYTES",
        "2 * (sizeof(struct agent_task_channel_enter) +\n"
        "\t\t\t 2 * sizeof(struct agent_task_channel_enter_result))",
        "enter_result.submitted == 0",
        "enter_result.sq_head == PERF_OPERATION_COUNT",
        "enter_result.backpressure == 1",
        "pending_preserved=1",
        "recovery_enter_calls=2 resync_recovery=1",
        "scope=retained_terminal",
        "pending_provider=unavailable",
        "agent_workflow_create(AGENT_ROLE_ORCHESTRATOR)",
        "for (int attempt = 0; attempt < 2000; attempt++)",
        "sleep(1);",
        "pid = create_isolated_workflow();",
    ):
        require(guest, token, f"Task Guest performance contract missing: {token}")
    forbid(
        guest,
        "pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR)",
        "ablation paths share one execution-contract lifecycle",
    )
    forbid(
        guest,
        "completion_span_ticks",
        "Task Guest must not describe service-start samples as completions",
    )
    guest_exercise = function_body(guest, "exercise_task_channel")
    require_order(
        guest_exercise,
        (
            '"acknowledge the sole target completion"',
            "before_stale = enter_result",
            "request_id = ++next_request_id",
            "acked_cancel = make_cancel_sqe(&target, request_id",
            "write_sqe(view.sq, view.sq_tail, &acked_cancel)",
            "view.sq_tail++",
            "enter_channel(0, 1, 0, view.generation, view.sq_tail, view.cq_head",
            "enter_result.status == AGENT_TASK_CHANNEL_STALE",
            "enter_result.submitted == 1",
            "enter_result.completed == 0",
            "enter_result.sq_head == before_stale.sq_head + 1",
            "enter_result.sq_head == view.sq_tail",
            "request_id > before_stale.last_accepted_request_id",
            "enter_result.last_accepted_request_id == request_id",
            "enter_result.cq_head == before_stale.cq_head",
            "enter_result.cq_tail == before_stale.cq_tail",
            "enter_result.generation == before_stale.generation",
            "enter_result.protocol_faults == before_stale.protocol_faults",
            "enter_result.resync_count == before_stale.resync_count",
            "enter_result.backpressure == before_stale.backpressure",
            "enter_result.in_flight == 0",
            "enter_result.terminal_pending == 0",
            "enter_result.flags == before_stale.flags",
            "(enter_result.flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) == 0",
            '"ACKed target cancel is consumed stale without resync or CQE"',
            '"hard deadline reaches a schedulable Task safe point"',
            '"channel remains usable after resync, cancel, and deadline"',
        ),
        "Guest must consume an ACKed-target cancel as STALE without corrupting the channel",
    )
    require(
        guest,
        "#define ECHO_PARAM_COUNT 3U",
        "scalar V3 ECHO must carry its three required typed parameters",
    )
    require_regex(
        guest,
        r"scalar_params\s*\[PERF_OPERATION_COUNT\]\[ECHO_PARAM_COUNT\]",
        "scalar V3 needs one three-parameter array per measured operation",
    )
    scalar_ablation = function_body(guest, "run_scalar_ablation")
    require_order(
        scalar_ablation,
        (
            "params[0].version = AGENT_PARAM_VERSION",
            "params[0].size = sizeof(params[0])",
            "params[0].type = AGENT_PARAM_STRING",
            "params[0].value_size = 1",
            'memcpy(params[0].key, "payload", sizeof("payload"))',
            "params[0].value.string_value[0] = 0",
            "params[1].version = AGENT_PARAM_VERSION",
            "params[1].size = sizeof(params[1])",
            "params[1].type = AGENT_PARAM_UINT64",
            "params[1].value_size = sizeof(params[1].value.uint64_value)",
            'memcpy(params[1].key, "arg0", sizeof("arg0"))',
            "params[2].version = AGENT_PARAM_VERSION",
            "params[2].size = sizeof(params[2])",
            "params[2].type = AGENT_PARAM_UINT64",
            "params[2].value_size = sizeof(params[2].value.uint64_value)",
            'memcpy(params[2].key, "arg1", sizeof("arg1"))',
            "request->param_count = ECHO_PARAM_COUNT",
            "request->params = (uint64)params",
            "tool_call_v3(&scalar_requests[i], &scalar_responses[i])",
        ),
        "scalar V3 must marshal payload:string, arg0:uint64, and arg1:uint64",
    )
    if scalar_ablation.count(
        "ECHO_PARAM_COUNT * sizeof(struct agent_param_v2)"
    ) != 2:
        raise ContractError(
            "scalar ABI and copied-byte accounting must include all three params"
        )
    require_regex(
        function_body(core, "sys_agent_run"),
        r"agent_execute_one\(p,\s*&op,\s*&res,\s*agent_ticks\(\),"
        r"\s*0,\s*0,\s*0,\s*0\)",
        "legacy batch must sample a fresh pre-effect tick per operation",
    )
    fingerprint = function_body(guest, "semantic_fingerprint")
    require(
        fingerprint,
        "*service_tick = context_ok ? record.tick : 0",
        "all performance paths must derive service start from Context",
    )
    ablation_end = function_body(guest, "ablation_end")
    require(
        ablation_end,
        "metrics->sequence_elapsed_ticks = info.current_tick - metrics->start_tick",
        "sequence elapsed time must use the common post-call agent_info boundary",
    )
    for path_function in (
        "run_batch_ablation",
        "run_scalar_ablation",
        "run_task_ablation",
    ):
        require_order(
            function_body(guest, path_function),
            (
                "ablation_end(&metrics)",
                "semantic_fingerprint(",
                "ablation_record_service_start(",
                "report_ablation(",
            ),
            f"{path_function} must exclude Context observers from sequence elapsed time",
        )
    task_ablation = function_body(guest, "run_task_ablation")
    forbid(
        task_ablation,
        "ablation_record_service_start(&metrics, i, cqe->completion_tick)",
        "Task performance must not reinterpret the public CQE completion tick",
    )
    require_regex(
        task_ablation,
        r"semantic_fingerprint\(.*?&service_start_tick\).*?"
        r"ablation_record_service_start\(\s*&metrics,\s*i,\s*service_start_tick\)",
        "Task performance must record the queried Context service-start tick",
    )
    execution_completion = function_body(
        bridge, "agent_task_bridge_execution_completion"
    )
    require(
        execution_completion,
        "(outcome->completion_flags & AGENT_RESPONSE_V3_F_CACHED) != 0",
        "cached marker must come only from an affirmative execution outcome",
    )
    require_order(
        execution_completion,
        (
            "outcome->completion_flags & AGENT_RESPONSE_V3_F_CACHED",
            "completion->internal_flags |=",
            "AGENT_TASK_COMPLETION_INTERNAL_F_CACHED",
            "completion->context_sequence = result->sequence",
            "completion->terminal_tick = outcome->terminal_tick",
            "completion->completion_tick = agent_task_bridge_now()",
        ),
        "Task completion must preserve cached authority and sample publication last",
    )
    forbid(
        execution_completion,
        "completion->terminal_tick = agent_task_bridge_now()",
        "the bridge must copy, not resample, the authoritative terminal tick",
    )
    bridge_flags = function_body(bridge, "agent_task_bridge_completion_flags")
    require_order(
        bridge_flags,
        (
            "status == AGENT_STATUS_CANCELLED",
            "flags |= AGENT_TASK_CQE_F_CANCELLED",
            "linked &&",
            "decision_reason == AGENT_EXECUTION_REASON_DEPENDENCY_FAILED",
            "flags |= AGENT_TASK_CQE_F_LINK_FAILED",
        ),
        "LINK_FAILED must decorate only a linked dependency cancellation",
    )
    submit_canonical = function_body(
        bridge, "agent_task_bridge_submit_completion_canonical"
    )
    require_order(
        submit_canonical,
        (
            "completion->status == AGENT_STATUS_CANCELLED",
            "AGENT_EXECUTION_REASON_DEPENDENCY_FAILED",
            "AGENT_TASK_CQE_F_CANCELLED |",
            "sqe->flags & AGENT_TASK_SQE_F_LINK",
            "AGENT_TASK_CQE_F_LINK_FAILED : 0",
            "completion->status == AGENT_STATUS_TIMEOUT",
            "completion->terminal_tick >= sqe->deadline_tick",
        ),
        "bridge canonicalization must distinguish linked dependency and deadline truth",
    )
    require_order(
        function_body(bridge, "agent_task_bridge_submit"),
        (
            "status = agent_execution_task_submit_sync(",
            "agent_task_bridge_execution_completion(",
        ),
        "Task bridge must sample the public completion tick after execution",
    )

    deadline_helper = function_body(bridge, "agent_task_bridge_request_deadline")
    require_order(
        deadline_helper,
        (
            "AGENT_TASK_SQE_F_HARD_DEADLINE) != 0",
            "sqe->deadline_tick : 0",
        ),
        "Task deadline extraction must be exact and shared",
    )
    bridge_validate = function_body(bridge, "agent_task_bridge_validate")
    bridge_submit = function_body(bridge, "agent_task_bridge_submit")
    bridge_binding = function_body(bridge, "agent_task_bridge_binding")
    require_order(
        bridge_binding,
        (
            "memset(binding, 0, sizeof(*binding))",
            "binding->internal_flags =",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            "binding->lifecycle.id = sqe->contract.lifecycle.id",
        ),
        "only the Task bridge may mint the internal Task binding flag",
    )
    for body, stage in ((bridge_validate, "preflight"), (bridge_submit, "submit")):
        require(
            body,
            "agent_task_bridge_request_deadline(sqe)",
            f"Task {stage} lost the copied hard deadline",
        )
    require_order(
        bridge_submit,
        (
            'panic("Task bridge submit validation")',
            "status = agent_execution_task_submit_sync(",
            'panic("Task bridge submit outcome")',
            "agent_task_bridge_execution_completion(",
            "agent_task_bridge_completion_valid(completion)",
            "agent_task_bridge_submit_completion_canonical(sqe, completion)",
            'panic("Task bridge submit completion")',
            "AGENT_TASK_HOOK_DENIED : AGENT_TASK_HOOK_COMPLETE",
        ),
        "an accepted bridge submit must finish or fail an internal invariant",
    )
    forbid(
        bridge_submit,
        "AGENT_TASK_CHANNEL_RETRY",
        "an accepted Task must never return to the Channel as sticky RUNNING",
    )
    require_order(
        bridge_validate,
        (
            "agent_execution_contract_preflight(",
            "preflight.status == AGENT_STATUS_RETRY",
            "preflight.status == AGENT_STATUS_NO_SPACE",
            "preflight.status == AGENT_STATUS_NOT_AGENT",
            "validation->output_artifact_type = AGENT_ARTIFACT_NONE",
            "return AGENT_TASK_HOOK_PENDING",
        ),
        "Task validate must stay pure and defer terminal authority to submit",
    )
    forbid(
        bridge_validate,
        "AGENT_EXECUTION_REASON_CONTRACT_RETIRING",
        "RETIRING is an authoritative Task admission, not a validate retry",
    )
    for side_effect in (
        "agent_execution_task_submit_sync(",
        "agent_context_append",
        "agent_evidence_",
    ):
        forbid(
            bridge_validate,
            side_effect,
            "Task validate must not publish pre-admission side effects",
        )

    request_digest = function_body(execution, "agent_execution_request_digest")
    require_order(
        request_digest,
        (
            "agent_execution_hash_u64(&ctx, (uint)binding->source_pid)",
            "agent_execution_hash_u64(&ctx, request_deadline_tick)",
            "agent_execution_hash_u64(&ctx, (uint)op->version)",
            "agent_sha256_final(&ctx, digest)",
        ),
        "hard deadline must be part of the canonical replay identity",
    )
    if execution.count(
        "binding, op, request_deadline_tick, request_digest"
    ) != 2:
        raise ContractError("preflight and admission must hash the same deadline")
    require_regex(
        execution_header,
        r"agent_execution_contract_admit\(\s*struct proc \*,\s*"
        r"const struct agent_execution_binding \*,\s*const struct agent_op \*,"
        r"\s*uint64,\s*uint,\s*uint64,",
        "execution admission API lost request deadline, policy, or admission time",
    )
    require_regex(
        internal,
        r"agent_execution_task_submit_sync\(\s*struct proc \*,\s*"
        r"struct agent_op \*,\s*const struct agent_execution_binding \*,"
        r"\s*uint64,\s*uint,",
        "Task submit API lost its request deadline or admission policy",
    )
    admit = function_body(execution, "agent_execution_contract_admit")
    preflight = function_body(execution, "agent_execution_contract_preflight")
    preflight_retiring = preflight[
        preflight.find("record->state == AGENT_EXECUTION_CONTRACT_RETIRING") :
        preflight.find("binding->contract_generation", preflight.find(
            "record->state == AGENT_EXECUTION_CONTRACT_RETIRING"
        ))
    ]
    require_order(
        preflight_retiring,
        (
            "binding->internal_flags &",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            "result, AGENT_STATUS_OK",
            "AGENT_EXECUTION_REASON_NONE",
            "result, AGENT_STATUS_DENIED",
            "AGENT_EXECUTION_REASON_CONTRACT_RETIRING",
        ),
        "pure RETIRING preflight must defer Task authority but deny scalar calls",
    )
    admit_retiring = admit[
        admit.find("record->state == AGENT_EXECUTION_CONTRACT_RETIRING") :
        admit.find("binding->contract_generation", admit.find(
            "record->state == AGENT_EXECUTION_CONTRACT_RETIRING"
        ))
    ]
    require_order(
        admit_retiring,
        (
            "binding->internal_flags &",
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            "AGENT_STATUS_DENIED : AGENT_STATUS_CANCELLED",
            '"contract_retiring"',
            "AGENT_EXECUTION_REASON_CONTRACT_RETIRING",
        ),
        "authoritative RETIRING admission must deny Task and cancel scalar calls",
    )
    require_order(
        admit,
        (
            "contract_deadline = node->deadline_tick != 0",
            "claim->deadline_tick = request_deadline_tick != 0 ?",
            "uint expected_policy =",
            "AGENT_EXECUTION_PREFLIGHT_F_OUTPUT_NONE_ONLY",
            "AGENT_EXECUTION_PREFLIGHT_F_HARD_DEADLINE",
            "task_admission_policy",
            "request_deadline_tick > contract_deadline",
            "cache_index = agent_execution_cache_index(",
            "now >= claim->deadline_tick",
            "runtime->state = AGENT_EXECUTION_NODE_RUNNING",
        ),
        "admission must bind Task policy and deadline before cache or execution",
    )
    require_order(
        preflight,
        (
            "result->effective_deadline_tick = request_deadline_tick != 0 ?",
            "request_deadline_tick > contract_deadline",
            "cache_index = agent_execution_cache_index(",
            "now >= result->effective_deadline_tick",
        ),
        "preflight and admission deadline/min/replay order diverged",
    )
    execute_one = function_body(core, "agent_execute_one")
    require_order(
        execute_one,
        (
            "agent_execution_contract_admit(",
            "p, binding, op, request_deadline_tick, admission_policy_flags",
            "agent_execution_contract_effect_begin(&claim)",
            "agent_execute_op(p, op, res)",
            "terminal_tick = agent_ticks()",
            "outcome->terminal_tick = terminal_tick",
            "claim.deadline_tick != 0",
            "terminal_tick >= claim.deadline_tick",
            "res->status = AGENT_STATUS_TIMEOUT",
            "agent_execution_append_terminal(",
        ),
        "one authoritative post-effect tick must drive deadline and replay metadata",
    )
    forbid(
        execute_one,
        "res->status == AGENT_STATUS_OK && claim.deadline_tick",
        "a non-OK tool result must not escape an inclusive hard deadline",
    )
    task_submit = function_body(core, "agent_execution_task_submit_sync")
    require_order(
        task_submit,
        (
            "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
            "agent_evidence_context_preallocated(p, binding->lifecycle)",
            'panic("accepted Task Evidence pages")',
            "for (;;)",
            "agent_lifecycle_context_lane_enter_accepted_task(p)",
            'panic("accepted Task context lane")',
            "agent_execute_one(",
            "p, op, result, agent_ticks(), binding",
            "request_deadline_tick, admission_policy_flags, outcome",
            "agent_lifecycle_context_lane_leave(p)",
            "result->status != AGENT_STATUS_RETRY",
            "result->status != AGENT_STATUS_NO_SPACE",
            "outcome->evidence_ticket != 0",
            "workflow_lifecycle_key_equal(",
            "workflow_lifecycle_active(binding->lifecycle)",
            "yield()",
            "outcome->evidence_ticket == 0",
            'panic("accepted Task without terminal evidence")',
        ),
        "accepted Task submit must retry transient admission until one evidenced terminal",
    )
    require_regex(
        task_submit,
        r"\(binding->internal_flags\s*&\s*"
        r"AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL\)\s*==\s*0.*?"
        r"for\s*\(;;\)\s*\{\s*if\s*\("
        r"agent_lifecycle_context_lane_enter_accepted_task\(p\)\s*<\s*0\)",
        "Task-only retry must use the teardown-tolerant accepted-Task lane",
    )
    require_regex(
        task_submit,
        r"if\s*\(!agent_evidence_context_preallocated\("
        r"p,\s*binding->lifecycle\)\)\s*"
        r"panic\(\"accepted Task Evidence pages\"\)",
        "accepted Task must prove CREATE-time Evidence allocation before retrying",
    )
    require(
        evidence_header,
        "int agent_evidence_context_preallocated(",
        "accepted Task submit needs an allocation-free Evidence readiness check",
    )
    evidence_preallocated = function_body(
        evidence, "agent_evidence_context_preallocated"
    )
    require_order(
        evidence_preallocated,
        (
            "p == 0 || !p->is_agent",
            "workflow_lifecycle_key_equal(vfs_proc_lifecycle(p), key)",
            "agent_evidence_pages_ready(state)",
            "resource_account_handle_equal(state->page_account",
            "state->page_charge_class == charge_class",
        ),
        "preallocated Evidence must match lifecycle, account, and charge class",
    )
    contract_control = function_body(execution, "sys_agent_execution_contract")
    require_order(
        contract_control,
        (
            "control.operation == AGENT_EXECUTION_CONTRACT_CREATE",
            "agent_evidence_prepare_direct_denials(p, lifecycle)",
            "record->state = AGENT_EXECUTION_CONTRACT_FROZEN",
        ),
        "CREATE must provision Evidence pages before publishing a frozen contract",
    )
    require(
        lifecycle_header,
        "int agent_lifecycle_context_lane_enter_accepted_task(struct proc *);",
        "the accepted-Task lane must remain an internal lifecycle API",
    )
    accepted_lane = function_body(
        lifecycle_source, "agent_lifecycle_context_lane_enter_accepted_task"
    )
    ordinary_lane = function_body(
        lifecycle_source, "agent_lifecycle_context_lane_enter"
    )
    task_live = function_body(
        lifecycle_source, "agent_lifecycle_context_lane_task_live"
    )
    enter_mode = function_body(
        lifecycle_source, "agent_lifecycle_context_lane_enter_mode"
    )
    require(
        accepted_lane,
        "agent_lifecycle_context_lane_enter_mode(p, 1)",
        "accepted Task lane lost its privileged mode bit",
    )
    require(
        ordinary_lane,
        "agent_lifecycle_context_lane_enter_mode(p, 0)",
        "ordinary Context admission must remain teardown interruptible",
    )
    require_order(
        task_live,
        (
            "p->state == P_USED",
            "p->teardown_state <= PROC_TEARDOWN_QUIESCING",
        ),
        "accepted Tasks may progress only through the QUIESCING teardown phase",
    )
    require_order(
        enter_mode,
        (
            "wait_status = accepted_task ?",
            "wait_queue_sleep_irq_uninterruptible(",
            "wait_queue_sleep_irq(&p->agent_context_lane_waiters)",
        ),
        "only accepted Tasks may wait uninterruptibly for the Context lane",
    )
    if core.count("agent_lifecycle_context_lane_enter_accepted_task(") != 1:
        raise ContractError("accepted-Task lane must have exactly one core call site")
    for name, source in sources.items():
        if not name.endswith(".c") or name in (
            "os/agent_core.c",
            "os/agent_lifecycle.c",
        ):
            continue
        forbid(
            source,
            "agent_lifecycle_context_lane_enter_accepted_task(",
            f"accepted-Task lane leaked into {name}",
        )
    forbid(
        task_submit,
        "p->killed",
        "process exit cannot abandon an already accepted Task request",
    )
    scalar_v3 = function_body(core, "sys_tool_call_v3")
    require_order(
        scalar_v3,
        (
            "memset(&binding, 0, sizeof(binding))",
            "binding.lifecycle.id = req.contract.lifecycle.id",
        ),
        "scalar V3 bindings must start with all internal flags clear",
    )
    forbid(
        scalar_v3,
        "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL",
        "user-controlled scalar V3 must never mint the internal Task flag",
    )
    for caller, pattern in (
        (
            "sys_agent_run",
            r"agent_execute_one\(p,\s*&op,\s*&res,\s*agent_ticks\(\),"
            r"\s*0,\s*0,\s*0,\s*0\)",
        ),
        (
            "sys_agent_call",
            r"agent_execute_one\(p,\s*&op,\s*&res,\s*agent_ticks\(\),"
            r"\s*0,\s*0,\s*0,\s*0\)",
        ),
        (
            "sys_tool_call_v2",
            r"agent_execute_one\(p,\s*&op,\s*&result,\s*agent_ticks\(\),"
            r"\s*0,\s*0,\s*0,\s*0\)",
        ),
        (
            "sys_tool_call_v3",
            r"agent_execute_one\(p,\s*&op,\s*&result,\s*agent_ticks\(\),"
            r"\s*&binding,\s*0,\s*0,\s*&outcome\)",
        ),
    ):
        require_regex(
            function_body(core, caller),
            pattern,
            f"{caller} must use zero request deadline and policy outside Task Channel",
        )
    require_order(
        execute_one,
        (
            "if (claim.deadline_expired || claim.dependency_failed)",
            "res->status = claim.deadline_expired ?",
            "AGENT_STATUS_TIMEOUT : AGENT_STATUS_CANCELLED",
            "agent_execution_append_terminal(",
        ),
        "deadline must win dependency cancellation at the authoritative submit",
    )
    terminal_append = function_body(core, "agent_execution_append_terminal")
    require_order(
        terminal_append,
        (
            "agent_context_append_reserved_ticket(",
            "evidence_ticket == 0",
            "outcome->evidence_ticket = evidence_ticket",
            "agent_execution_contract_complete(claim, res, outcome)",
        ),
        "one evidence-bound terminal must commit the execution contract",
    )
    contract_complete = function_body(
        execution, "agent_execution_contract_complete"
    )
    require_order(
        contract_complete,
        (
            "runtime->state != AGENT_EXECUTION_NODE_RUNNING",
            "record->failed_mask |= 1ULL << claim->node_id",
            "agent_execution_propagate_dependency_failure(record)",
            "cached->valid = 1",
            "memmove(cached->request_digest, claim->request_digest",
            "claim->active = 0",
        ),
        "due/dependency terminal must update DAG state and immutable replay cache",
    )
    cache_copy = function_body(execution, "agent_execution_cache_copy")
    require_order(
        cache_copy,
        (
            "memmove(result, &cached->result, sizeof(*result))",
            "memmove(outcome, &cached->outcome",
            "sizeof(*outcome)",
        ),
        "cached replay must preserve the original authoritative terminal tick",
    )
    cancel_common = function_body(
        execution, "agent_execution_contract_cancel_common"
    )
    timeout_contract = function_body(
        execution, "agent_execution_contract_timeout"
    )
    for body, path in (
        (cancel_common, "cancel"),
        (timeout_contract, "timeout"),
    ):
        require_order(
            body,
            (
                "memset(outcome, 0, sizeof(*outcome))",
                "outcome->terminal_tick = now",
            ),
            f"{path} outcomes must bind their authoritative decision tick",
        )
    timeout_sync = function_body(core, "agent_execution_timeout_sync")
    require_order(
        timeout_sync,
        (
            "agent_execution_contract_timeout(",
            "outcome->terminal_tick = now",
            "agent_execution_append_terminal(",
        ),
        "synchronous expiry must commit the same tick used for its decision",
    )
    require_regex(
        execution_header,
        r"struct agent_execution_outcome\s*\{.*?uint64\s+evidence_ticket;.*?"
        r"uint64\s+terminal_tick;\s*\};",
        "execution outcome lost its kernel-authoritative terminal tick",
    )
    require(
        execution_header,
        "#define AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL (1U << 0)",
        "execution bindings lost the internal Task provenance bit",
    )
    require_regex(
        execution_header,
        r"struct agent_execution_binding\s*\{.*?uint\s+resource_slot;\s*"
        r"uint\s+internal_flags;",
        "Task provenance must remain an internal execution-binding field",
    )

    for constant in (
        "#define AGENT_TASK_CHANNEL_SETUP_SYSCALL    563U",
        "#define AGENT_TASK_CHANNEL_ENTER_SYSCALL    564U",
        "#define AGENT_TASK_CHANNEL_RESOURCE_SYSCALL 565U",
        "#define AGENT_TASK_CHANNEL_CAPACITY      16U",
        "#define AGENT_TASK_CHANNEL_SCHEMA_SIZE   32U",
        "#define AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER (1U << 0)",
        "#define AGENT_TASK_CHANNEL_RING_F_RESYNC       (1U << 1)",
        "#define AGENT_TASK_CHANNEL_RING_F_CQ_FULL      (1U << 2)",
        "#define AGENT_TASK_CHANNEL_RING_F_RECLAIMING   (1U << 3)",
        "#define AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE (1U << 4)",
        "#define AGENT_TASK_HANDLE_F_OWNED    (1U << 0)",
        "#define AGENT_TASK_HANDLE_F_BORROWED (1U << 1)",
    ):
        require(abi, constant, f"frozen Task Channel constant changed: {constant}")
    for layout in (
        "sizeof(struct agent_task_resource_handle) == 16",
        "sizeof(struct agent_task_ring_header) == 128",
        "sizeof(struct agent_task_sqe) == 128",
        "__builtin_offsetof(struct agent_task_sqe, schema_digest) == 96",
        "sizeof(struct agent_task_cqe) == 128",
        "__builtin_offsetof(struct agent_task_cqe, result) == 72",
        "sizeof(struct agent_task_channel_setup_result) == 96",
        "sizeof(struct agent_task_channel_enter_result) == 104",
        "sizeof(struct agent_task_channel_resource) == 72",
        "sizeof(struct agent_task_channel_resource_result) == 80",
    ):
        require(abi, layout, f"frozen Task Channel layout changed: {layout}")
    require(header, "#define AGENT_TASK_CHANNEL_MAPPED_PAGES  2U", "SQ/CQ page count changed")
    require(header, "#define AGENT_TASK_CHANNEL_PRIVATE_PAGES 2U", "private page count changed")
    require(
        header,
        "#define AGENT_TASK_COMPLETION_INTERNAL_F_CACHED (1U << 0)",
        "kernel completion lost its authenticated cached marker",
    )
    require_regex(
        header,
        r"struct agent_task_completion\s*\{.*?uint64\s+completion_tick;.*?"
        r"Kernel-only execution decision time.*?uint64\s+terminal_tick;\s*\};",
        "terminal tick must remain kernel-only metadata beside the public tick",
    )
    forbid(
        abi,
        "terminal_tick",
        "the authoritative terminal tick must not become user-writable UAPI",
    )
    require(
        bridge,
        "static const struct agent_task_channel_ops agent_task_bridge_ops",
        "Task completion metadata must come from the pinned kernel bridge ops",
    )
    require(
        header,
        "AGENT_TASK_REQUEST_CQ_VISIBLE",
        "request state machine lost CQ visibility",
    )
    require_regex(
        header,
        r"AGENT_TASK_REQUEST_FREE\s*=\s*0,\s*"
        r"AGENT_TASK_REQUEST_ACCEPTED,\s*AGENT_TASK_REQUEST_RUNNING,\s*"
        r"AGENT_TASK_REQUEST_EVIDENCE_PENDING,\s*"
        r"AGENT_TASK_REQUEST_TERMINAL,\s*AGENT_TASK_REQUEST_CQ_VISIBLE",
        "request state sequence changed",
    )

    setup_result = function_body(channel, "agent_task_channel_setup_result_fill")
    require_order(
        setup_result,
        (
            "result->mapped_page_count = AGENT_TASK_CHANNEL_MAPPED_PAGES",
            "result->private_page_count = AGENT_TASK_CHANNEL_PRIVATE_PAGES",
        ),
        "setup result must report two mapped and two private pages",
    )
    setup = function_body(channel, "agent_task_channel_setup")
    require_order(
        setup,
        (
            "setup->flags != AGENT_TASK_CHANNEL_SETUP_F_SINGLE_ISSUER",
            "issuer != curr_thread()",
            "issuer != &p->threads[0]",
            "issuer->identity_generation == 0",
            "agent_task_lifecycle_matches(p, setup->lifecycle)",
        ),
        "setup must bind one live main-thread issuer and full lifecycle",
    )
    require_order(
        setup,
        (
            "workflow_lifecycle_operation_enter(lifecycle)",
            "resource_reserve_many(",
            "kalloc_account_page(",
            "resource_reservation_commit(&reservation)",
            "state->private->header.issuer_generation = issuer->identity_generation",
            "state->state = AGENT_TASK_CHANNEL_OWNER_LIVE",
            "workflow_lifecycle_operation_leave(lifecycle)",
        ),
        "setup allocation/publication is not lifecycle and resource atomic",
    )
    require_regex(
        setup,
        r"AGENT_TASK_CHANNEL_SQ_BASE.*?PTE_U\s*\|\s*PTE_R\s*\|\s*PTE_W"
        r".*?AGENT_TASK_CHANNEL_CQ_BASE.*?PTE_U\s*\|\s*PTE_R\)\s*<\s*0",
        "SQ must be writable and CQ must be read-only",
    )
    setup_cq = setup.find("AGENT_TASK_CHANNEL_CQ_BASE")
    setup_cq_end = setup.find("mapped = 2", setup_cq)
    if setup_cq < 0 or setup_cq_end < 0 or "PTE_W" in setup[setup_cq:setup_cq_end]:
        raise ContractError("CQ setup mapping became writable")

    issuer = function_body(channel, "agent_task_channel_issuer_valid_locked")
    require_order(
        issuer,
        (
            "state->state == AGENT_TASK_CHANNEL_OWNER_LIVE",
            "proc_teardown_live(p)",
            "workflow_lifecycle_active(state->lifecycle)",
            "issuer == curr_thread()",
            "issuer->process == p",
            "issuer->identity_generation != 0",
            "issuer->tid == state->private->header.issuer_tid",
            "issuer->identity_generation ==",
            "state->private->header.issuer_generation",
        ),
        "single issuer validation lost identity-generation or lifecycle binding",
    )

    consume = function_body(channel, "agent_task_channel_consume_one")
    if consume.count("state->sq->entries") != 1:
        raise ContractError("an SQE must be read from shared memory exactly once")
    shared_read = consume.find("state->sq->entries")
    validate = consume.find("agent_task_sqe_shape_valid_locked", shared_read)
    if shared_read < 0 or validate < 0 or "state->sq->entries" in consume[validate:]:
        raise ContractError("shared SQE was reread after validation")
    require_order(
        consume,
        (
            "memmove(&sqe,",
            "&state->sq->entries[position % AGENT_TASK_CHANNEL_CAPACITY]",
            "sizeof(sqe));",
            "__sync_synchronize()",
            "agent_task_sqe_shape_valid_locked(p, private, &sqe, position)",
        ),
        "descriptor must be copied completely before any validation",
    )
    shape = function_body(channel, "agent_task_sqe_shape_valid_locked")
    require_order(
        shape,
        (
            "position / AGENT_TASK_CHANNEL_CAPACITY + 1",
            "sqe->request_id <= private->header.last_accepted_request_id",
            "sqe->ring_generation != private->header.generation",
            "sqe->slot_generation != slot_generation",
            "sqe->opcode != AGENT_TASK_CHANNEL_OP_SUBMIT",
            "sqe->opcode != AGENT_TASK_CHANNEL_OP_CANCEL",
            "AGENT_TASK_SQE_F_HARD_DEADLINE) != 0) !=",
            "(sqe->deadline_tick != 0)",
            "agent_task_lifecycle_matches(p, sqe->contract.lifecycle)",
            "sqe->contract.generation == 0",
            "agent_task_handle_shape_valid(sqe->input)",
            "agent_task_schema_present(sqe->schema_digest)",
        ),
        "SQE shape lost opcode, exact deadline, generation, or lifecycle checks",
    )

    ack = function_body(channel, "agent_task_channel_ack_locked")
    require_regex(
        ack,
        r"acknowledged\s*<\s*private->header\.cq_head\s*\|\|\s*"
        r"acknowledged\s*>\s*private->header\.cq_tail\s*\|\|\s*"
        r"acknowledged\s*-\s*private->header\.cq_head\s*>\s*"
        r"AGENT_TASK_CHANNEL_CAPACITY",
        "CQ acknowledgement range is not bounded by authoritative counters",
    )
    require_order(
        ack,
        (
            "found = 0",
            "request->state == AGENT_TASK_REQUEST_CQ_VISIBLE",
            "request->cq_position == position",
            "found++",
            "if (found != 1)",
            "result->owner_request_id = 0",
            "memset(request, 0, sizeof(*request))",
            "private->header.cq_head = acknowledged",
        ),
        "CQ ack must prevalidate one visible completion before ownership handoff",
    )
    enter = function_body(channel, "agent_task_channel_enter")
    require_order(
        enter,
        (
            "agent_task_channel_ack_locked(state, enter->cq_head)",
            "AGENT_TASK_CHANNEL_RING_F_RESYNC",
            "AGENT_TASK_CHANNEL_ENTER_F_RESYNC",
            "enter->sq_tail != 0",
            "header->flags &= ~AGENT_TASK_CHANNEL_RING_F_RESYNC",
            "enter->sq_tail < header->sq_tail",
            "enter->sq_tail - header->sq_head > AGENT_TASK_CHANNEL_CAPACITY",
        ),
        "enter must validate CQ ack and perform the exact sticky resync handshake",
    )

    fault = function_body(channel, "agent_task_channel_protocol_fault_locked")
    require_order(
        fault,
        (
            "header->protocol_faults++",
            "(header->flags & AGENT_TASK_CHANNEL_RING_F_RESYNC) == 0",
            "header->flags |= AGENT_TASK_CHANNEL_RING_F_RESYNC",
            "header->resync_count++",
            "header->generation++",
            "header->sq_head = 0",
            "header->sq_tail = 0",
            "memset(state->sq->entries, 0, sizeof(state->sq->entries))",
        ),
        "protocol faults need sticky generation-bumping resync",
    )
    flush = function_body(channel, "agent_task_channel_flush_locked")
    require_order(
        flush,
        (
            "private->header.cq_tail - private->header.cq_head >=",
            "AGENT_TASK_CHANNEL_CAPACITY",
            "private->header.flags |= AGENT_TASK_CHANNEL_RING_F_CQ_FULL",
            "private->header.backpressure++",
            "break",
            "AGENT_TASK_REQUEST_TERMINAL",
            "memmove(&state->cq->entries",
            "__sync_synchronize()",
            "request->state = AGENT_TASK_REQUEST_CQ_VISIBLE",
            "private->header.cq_tail++",
            "private->header.terminal_pending--",
        ),
        "CQ full must retain terminal work and publish each CQE once",
    )

    require_order(
        consume,
        (
            "memset(request, 0, sizeof(*request))",
            "request->sqe = sqe",
            "request->accepted_tick = agent_task_now()",
            "request->state = AGENT_TASK_REQUEST_ACCEPTED",
            "request->state = AGENT_TASK_REQUEST_RUNNING",
            "ops->submit(",
        ),
        "accepted time must be captured before an accepted request becomes RUNNING",
    )
    complete_locked = function_body(channel, "agent_task_request_complete_locked")
    require_order(
        complete_locked,
        (
            "request->state != AGENT_TASK_REQUEST_RUNNING",
            "agent_task_completion_valid_locked(",
            "request->state = AGENT_TASK_REQUEST_EVIDENCE_PENDING",
            "request->context_sequence = completion->context_sequence",
            "request->evidence_ticket = completion->evidence_ticket",
            "request->provenance_labels = completion->provenance_labels",
        ),
        "completion must be an evidence-bound RUNNING-to-pending transition",
    )
    completion_valid = function_body(channel, "agent_task_completion_valid_locked")
    require_order(
        completion_valid,
        (
            "~AGENT_TASK_COMPLETION_INTERNAL_F_ALL",
            "completion->context_sequence == 0",
            "completion->evidence_ticket == 0",
            "completion->provenance_labels == 0",
            "cached =",
            "AGENT_TASK_COMPLETION_INTERNAL_F_CACHED",
            "dependency_reason = completion->decision_reason ==",
            "AGENT_EXECUTION_REASON_DEPENDENCY_FAILED",
            "dependency_failed =",
            "completion->status == AGENT_STATUS_CANCELLED",
            "linked_dependency_failed = dependency_failed &&",
            "request->sqe.flags & AGENT_TASK_SQE_F_LINK",
            "cancelled != (completion->status == AGENT_STATUS_CANCELLED)",
            "deadline != (completion->status == AGENT_STATUS_TIMEOUT)",
            "dependency_reason != dependency_failed",
            "link_failed != linked_dependency_failed",
            "completion->terminal_tick > completion->completion_tick",
            "!cached && completion->terminal_tick < request->accepted_tick",
            "deadline_due =",
            "completion->terminal_tick >= request->sqe.deadline_tick",
            "completion->status != AGENT_STATUS_DENIED",
            "completion->status != AGENT_STATUS_STALE",
            "cancel_due =",
            "AGENT_TASK_REQUEST_F_CANCEL_REQUESTED",
            "dependency_failed",
            "!deadline_due",
            "deadline != deadline_due || cancelled != cancel_due",
        ),
        "terminal chronology, dependency flags, and deadline precedence are not exact",
    )
    require_regex(
        completion_valid,
        r"dependency_failed\s*=\s*completion->status\s*==\s*"
        r"AGENT_STATUS_CANCELLED\s*&&\s*dependency_reason;\s*"
        r"linked_dependency_failed\s*=\s*dependency_failed\s*&&\s*"
        r"\(request->sqe\.flags\s*&\s*AGENT_TASK_SQE_F_LINK\)\s*!=\s*0;",
        "dependency cancellation semantics must not depend on the LINK bit",
    )
    require_regex(
        completion_valid,
        r"cancel_due\s*=\s*\(\(request->flags\s*&\s*"
        r"AGENT_TASK_REQUEST_F_CANCEL_REQUESTED\)\s*!=\s*0\s*\|\|\s*"
        r"dependency_failed\)\s*&&\s*!deadline_due;",
        "unlinked dependency cancellation must still be terminal",
    )
    forbid(
        completion_valid,
        "agent_task_now()",
        "completion validation must never resample time after execution",
    )
    complete_finish = function_body(channel, "agent_task_channel_complete_finish")
    require_order(
        complete_finish,
        (
            "request->state != AGENT_TASK_REQUEST_RUNNING",
            "agent_task_request_complete_locked(",
            "agent_task_callback_get_locked(state)",
            "agent_task_release_invoke(",
            "agent_task_callback_put_locked(state)",
            "request->flags &= ~AGENT_TASK_REQUEST_F_LIFECYCLE_HELD",
            "workflow_lifecycle_operation_leave(state->lifecycle)",
            "request->state = AGENT_TASK_REQUEST_TERMINAL",
            "header.terminal_pending++",
            "agent_task_channel_flush_locked(state)",
        ),
        "terminalization must release pins/token once and then flush one CQE",
    )
    complete = function_body(channel, "agent_task_channel_complete")
    require_order(
        complete,
        (
            "agent_task_channel_complete_finish(",
            "agent_task_reclaim_deferred(p, ops)",
        ),
        "public completion must terminalize before deferred reclaim",
    )
    reclaim = function_body(channel, "agent_task_channel_reclaim")
    require(
        reclaim,
        "agent_task_channel_complete_finish(",
        "reclaim must use the non-recursive completion helper",
    )
    forbid(
        reclaim,
        "agent_task_channel_complete(",
        "reclaim must not recurse through public completion/deferred reclaim",
    )

    cancel_start = consume.find("if (sqe.opcode == AGENT_TASK_CHANNEL_OP_CANCEL)")
    cancel_end = consume.find("if ((sqe.flags & AGENT_TASK_SQE_F_LINK) != 0)", cancel_start)
    if cancel_start < 0 or cancel_end < 0:
        raise ContractError("missing target-only cancel path")
    cancel_path = consume[cancel_start:cancel_end]
    require_order(
        cancel_path,
        (
            "target = agent_task_request_find_locked(",
            "if (target != 0 &&",
            "!agent_task_cancel_descriptor_matches(&sqe, target)",
            "agent_task_channel_protocol_fault_locked(state)",
            "private->header.sq_head++",
            "private->header.submitted++",
            "private->header.last_accepted_request_id = sqe.request_id",
            "*consumed = 1",
            "if (target == 0)",
            "agent_task_channel_publish_locked(state)",
            "return AGENT_TASK_CHANNEL_STALE",
            "target->state == AGENT_TASK_REQUEST_TERMINAL",
            "target->state == AGENT_TASK_REQUEST_CQ_VISIBLE",
            "target->state == AGENT_TASK_REQUEST_EVIDENCE_PENDING",
            "return 1",
            "target->flags |= AGENT_TASK_REQUEST_F_CANCEL_REQUESTED",
            "agent_task_callback_get_locked(state)",
            "ops->cancel(p, &sqe, &completion)",
            "agent_task_callback_put_locked(state)",
            "agent_task_reclaim_deferred(p, ops)",
        ),
        "cancel ID consumption and callback pin/deferred reclaim are not exact",
    )
    forbid(
        cancel_path,
        "target == 0 ||",
        "an ACKed cancel target must be consumed stale, not trigger resync",
    )
    if "agent_task_request_alloc_locked" in cancel_path or "agent_task_cqe_build" in cancel_path:
        raise ContractError("a cancel command must never create its own request/CQE")
    require_regex(
        cancel_path,
        r"hook_status\s*==\s*AGENT_TASK_HOOK_DENIED.*?"
        r"~AGENT_TASK_REQUEST_F_CANCEL_REQUESTED.*?"
        r"AGENT_TASK_REQUEST_F_CANCEL_DENIED.*?"
        r"agent_task_reclaim_deferred\(p, ops\).*?"
        r"return\s+AGENT_TASK_CHANNEL_DENIED",
        "synchronous cancel denial must consume its ID and leave target running",
    )
    if cancel_path.count("agent_task_reclaim_deferred(p, ops)") < 2:
        raise ContractError(
            "every cancel callback outcome must resume deferred reclaim"
        )
    require_order(
        enter,
        (
            "uint consumed = 0",
            "agent_task_channel_consume_one(",
            "&consumed",
            "submitted += consumed",
            "if (status <= 0)",
        ),
        "consumed STALE/DENIED commands must appear in enter submitted accounting",
    )
    if consume.count("*consumed = 1") != 2:
        raise ContractError("both cancel and submit SQ consumption must be reported")

    tick = function_body(channel, "agent_task_channel_tick")
    require(tick, "agent_task_deadlines_mark_locked(", "tick must mark deadline work")
    if "ops->" in tick or "agent_task_channel_complete(" in tick:
        raise ContractError("IRQ tick must not invoke callbacks or complete requests")
    deadline_due = function_body(channel, "agent_task_channel_deadline_due")
    require_order(
        deadline_due,
        (
            "agent_task_channel_owner_valid_locked(state, p)",
            "AGENT_TASK_CHANNEL_OWNER_LIVE",
            "AGENT_TASK_CHANNEL_OWNER_RECLAIMING",
            "AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE",
        ),
        "safe-point deadline lookup must use the current channel's sticky bit",
    )
    if "for (" in deadline_due or "ops->" in deadline_due:
        raise ContractError("deadline_due must remain an O(1), callback-free lookup")
    expire = function_body(channel, "agent_task_channel_expire")
    require_order(
        expire,
        (
            "agent_task_callback_get_locked(state)",
            "agent_task_deadline_completion(",
            "agent_task_callback_put_locked(state)",
            "agent_task_reclaim_deferred(p, ops)",
            "agent_task_channel_complete(",
        ),
        "deadline callback must be pinned and resume deferred reclaim",
    )
    submit_path = consume[cancel_end:]
    require_order(
        submit_path,
        (
            "ops->validate(",
            "hook_status != AGENT_TASK_HOOK_PENDING",
            "request->state = AGENT_TASK_REQUEST_ACCEPTED",
            "request->state = AGENT_TASK_REQUEST_RUNNING",
            "private->header.sq_head++",
            "agent_task_callback_get_locked(state)",
            "ops->submit(",
        ),
        "validate must be pure before one post-accept authoritative submit",
    )
    submit_call = submit_path.find("ops->submit(")
    if submit_call < 0 or "agent_task_channel_expire(" in submit_path[:submit_call]:
        raise ContractError("a due descriptor was expired before execution admission")
    if "agent_task_deadline_completion(" in submit_path:
        raise ContractError("pre-submit code must not publish deadline evidence")
    if submit_path.count("ops->submit(") != 1:
        raise ContractError("each accepted descriptor needs one authoritative submit")

    callback_get = function_body(channel, "agent_task_callback_get_locked")
    callback_put = function_body(channel, "agent_task_callback_put_locked")
    require(callback_get, "header.callback_refs++", "callback get lost its pin")
    require(callback_put, "header.callback_refs--", "callback put lost its pin")
    validation_abort = function_body(channel, "agent_task_validation_abort")
    require_order(
        validation_abort,
        (
            "agent_task_callback_put_locked(state)",
            "workflow_lifecycle_operation_leave(state->lifecycle)",
            "agent_task_reclaim_deferred(p, ops)",
        ),
        "failed validation must drop both callback and lifecycle pins",
    )
    resource_finish = function_body(channel, "agent_task_resource_op_finish_locked")
    require_order(
        resource_finish,
        (
            "agent_task_callback_put_locked(state)",
            "workflow_lifecycle_operation_leave(state->lifecycle)",
        ),
        "resource callback must drop callback and lifecycle pins together",
    )
    reclaim = function_body(channel, "agent_task_channel_reclaim")
    require_order(
        reclaim,
        (
            "workflow_lifecycle_departure_enter(state->lifecycle)",
            "state->departure_held = 1",
            "state->state = AGENT_TASK_CHANNEL_OWNER_RECLAIMING",
            "header.callback_refs != 0",
            "header.exec_alias_refs != 0",
            "AGENT_TASK_REQUEST_F_CANCEL_DENIED",
            "agent_task_callback_get_locked(state)",
            "ops->cancel(p, &cancel, &completion)",
            "agent_task_callback_put_locked(state)",
            "state->state = AGENT_TASK_CHANNEL_OWNER_FINALIZING",
            "agent_task_release_invoke(",
            "agent_task_channel_reset_locked(state)",
            "workflow_lifecycle_departure_leave(lifecycle)",
        ),
        "reclaim must hold departure, reject live pins, and pin provider callbacks",
    )

    handle_shape = function_body(channel, "agent_task_handle_shape_valid")
    require_order(
        handle_shape,
        (
            "handle.slot == 0",
            "agent_task_handle_null(handle)",
            "handle.slot <= AGENT_TASK_CHANNEL_RESOURCE_CAPACITY",
            "handle.type > AGENT_ARTIFACT_NONE",
            "handle.generation != 0",
            "ownership == AGENT_TASK_HANDLE_F_OWNED",
            "ownership == AGENT_TASK_HANDLE_F_BORROWED",
        ),
        "typed handles must include slot/type/generation and one ownership mode",
    )
    resource_find = function_body(channel, "agent_task_resource_find_locked")
    require_order(
        resource_find,
        (
            "slot->generation != handle.generation",
            "slot->type != handle.type",
            "slot->flags != AGENT_TASK_HANDLE_F_OWNED",
        ),
        "resource lookup must reject stale generation/type and non-owned base slots",
    )
    resource_allocate = function_body(channel, "agent_task_resource_allocate_locked")
    require_order(
        resource_allocate,
        (
            "state->resource_generation_highwater[i] + 1",
            "state->resource_generation_highwater[i] = generation",
            "slot->generation = generation",
            "slot->flags = (ushort)imported->resource_flags",
            "handle->generation = generation",
        ),
        "resource slot reuse must advance an authoritative generation",
    )
    resource_import = function_body(channel, "agent_task_resource_import_valid")
    require_order(
        resource_import,
        (
            "imported->producer_control_id != 0",
            "imported->producer_pid > 0",
            "imported->resource_flags == AGENT_TASK_HANDLE_F_OWNED",
        ),
        "imports need positive producer identity and owned kernel objects",
    )
    resource_view = function_body(channel, "agent_task_resource_view_locked")
    require_order(
        resource_view,
        (
            "view->producer_context_sequence = slot->producer_context_sequence",
            "view->producer_control_id = slot->producer_control_id",
            "view->producer_node_id = slot->producer_node_id",
            "view->producer_pid = slot->producer_pid",
        ),
        "kernel-owned resource views must preserve full producer identity",
    )
    require_order(
        resource_allocate,
        (
            "slot->producer_context_sequence =",
            "slot->producer_control_id = imported->producer_control_id",
            "slot->producer_node_id = imported->producer_node_id",
            "slot->producer_pid = imported->producer_pid",
        ),
        "resource slots must symmetrically copy producer identity",
    )
    input_acquire = function_body(channel, "agent_task_request_input_acquire_locked")
    require_order(
        input_acquire,
        (
            "handle.flags == AGENT_TASK_HANDLE_F_BORROWED",
            "resource->references++",
            "resource->state = AGENT_TASK_RESOURCE_STATE_IN_FLIGHT",
            "resource->owner_request_id = request->sqe.request_id",
        ),
        "borrowed inputs need refs while owned inputs transfer into flight",
    )

    require_order(
        consume,
        (
            "workflow_lifecycle_operation_enter(lifecycle)",
            "agent_task_callback_get_locked(state)",
            "ops->validate(",
            "request->flags = AGENT_TASK_REQUEST_F_LIFECYCLE_HELD",
            "request->state = AGENT_TASK_REQUEST_RUNNING",
        ),
        "accepted requests must retain a full lifecycle operation token",
    )
    alias = function_body(channel, "agent_task_channel_alias_exec")
    for transition in (
        "#define AGENT_TASK_EXEC_ALIAS_NONE      0U",
        "#define AGENT_TASK_EXEC_ALIAS_STAGED    1U",
        "#define AGENT_TASK_EXEC_ALIAS_COMMITTED 2U",
    ):
        require(channel, transition, "exec alias transaction states changed")
    require_order(
        alias,
        (
            "state->state != AGENT_TASK_CHANNEL_OWNER_LIVE",
            "header.callback_refs != 0",
            "header.exec_alias_refs !=",
            "AGENT_TASK_EXEC_ALIAS_NONE",
            "header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_STAGED",
            "AGENT_TASK_CHANNEL_SQ_BASE",
            "PTE_U | PTE_R | PTE_W",
            "AGENT_TASK_CHANNEL_CQ_BASE",
            "PTE_U | PTE_R",
        ),
        "exec aliasing must pin a quiescent live channel and keep CQ read-only",
    )
    abort_alias = function_body(channel, "agent_task_channel_abort_exec_alias")
    require_order(
        abort_alias,
        (
            "header.exec_alias_refs ==",
            "AGENT_TASK_EXEC_ALIAS_STAGED",
            "uvmunmap(",
            "header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE",
            "agent_task_reclaim_deferred(p, ops)",
        ),
        "an aborted exec must roll a staged alias back to NONE",
    )
    rebind = function_body(channel, "agent_task_channel_rebind_exec")
    require_order(
        rebind,
        (
            "header->exec_alias_refs != AGENT_TASK_EXEC_ALIAS_STAGED",
            "header->generation = generation",
            "header->issuer_generation = issuer->identity_generation",
            "header->exec_alias_refs = AGENT_TASK_EXEC_ALIAS_COMMITTED",
        ),
        "exec commit must atomically move STAGED to COMMITTED",
    )
    unmap = function_body(channel, "agent_task_channel_unmap_exec")
    require_order(
        unmap,
        (
            "header.exec_alias_refs !=",
            "AGENT_TASK_EXEC_ALIAS_COMMITTED",
            "uvmunmap(",
            "header.exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE",
            "agent_task_reclaim_deferred(p, ops)",
        ),
        "committed exec aliases must be explicitly unpinned before reclaim",
    )

    for declaration in (
        "void agent_task_bridge_init(void);",
        "uint agent_task_bridge_tick(uint64 now);",
        "int agent_task_bridge_current_deadline_due(void);",
        "int agent_task_bridge_current_deadline_safe_point(void);",
        "int agent_task_bridge_reclaim(struct proc *p);",
    ):
        require(
            bridge_header,
            declaration,
            f"Task bridge declaration changed: {declaration}",
        )
    agent_init = function_body(facade, "agentinit")
    require_order(
        agent_init,
        ("agent_core_init()", "agent_task_bridge_init()"),
        "Task Channel must initialize after its execution provider",
    )
    for callback in (
        ".validate = agent_task_bridge_validate",
        ".submit = agent_task_bridge_submit",
        ".cancel = agent_task_bridge_cancel",
        ".expire = agent_task_bridge_expire",
        ".resource_import = agent_task_bridge_resource_import",
        ".resource_release = agent_task_bridge_resource_release",
    ):
        require(bridge, callback, f"Task bridge callback missing: {callback}")

    setup_wrapper = function_body(bridge, "sys_agent_task_channel_setup")
    require_order(
        setup_wrapper,
        (
            "copyin(p->pagetable, (char *)&setup",
            "agent_task_bridge_setup_placeholder(&result)",
            "copyout(p->pagetable, resultaddr",
            "agent_task_channel_setup(",
            "copyout(p->pagetable, resultaddr",
            "status == AGENT_TASK_CHANNEL_OK",
            "agent_task_channel_reclaim(p, &agent_task_bridge_ops)",
        ),
        "setup must probe copyout before commit and reclaim a lost result",
    )
    enter_wrapper = function_body(bridge, "sys_agent_task_channel_enter")
    require_order(
        enter_wrapper,
        (
            "copyin(p->pagetable, (char *)&enter",
            "agent_task_bridge_enter_placeholder(&result)",
            "copyout(p->pagetable, resultaddr",
            "agent_task_channel_enter(",
            "copyout(p->pagetable, resultaddr",
        ),
        "enter wrapper lost copied control or result probing",
    )
    resource_wrapper = function_body(bridge, "sys_agent_task_channel_resource")
    require_order(
        resource_wrapper,
        (
            "copyin(p->pagetable, (char *)&control",
            "agent_task_bridge_resource_placeholder(&result)",
            "copyout(p->pagetable, resultaddr",
            "agent_task_channel_resource(",
            "copyout(p->pagetable, resultaddr",
        ),
        "resource wrapper lost copied control or result probing",
    )
    syscall_dispatch = function_body(syscall, "syscall_dispatch")
    require_order(
        syscall_dispatch,
        (
            "case SYS_agent_task_channel_setup:",
            "sys_agent_task_channel_setup(trapframe->a0",
            "case SYS_agent_task_channel_enter:",
            "sys_agent_task_channel_enter(trapframe->a0",
            "case SYS_agent_task_channel_resource:",
            "sys_agent_task_channel_resource(trapframe->a0",
        ),
        "Task syscalls 563-565 are not fully dispatched",
    )

    deadline_mark = function_body(channel, "agent_task_deadlines_mark_locked")
    require_order(
        deadline_mark,
        (
            "request->state != AGENT_TASK_REQUEST_RUNNING",
            "AGENT_TASK_SQE_F_HARD_DEADLINE) == 0",
            "now >= request->sqe.deadline_tick",
            "AGENT_TASK_REQUEST_F_DEADLINE_DUE) == 0",
            "request->flags |= AGENT_TASK_REQUEST_F_DEADLINE_DUE",
            "marked++",
            "due++",
            "AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE",
        ),
        "IRQ tick must mark exactly newly due running hard deadlines",
    )
    facade_tick = function_body(facade, "agent_tick")
    require_order(
        facade_tick,
        (
            "agent_core_tick()",
            "agent_task_bridge_tick(get_cycle() / (CPU_FREQ / TICKS_PER_SEC))",
            "agent_background_request()",
        ),
        "timer tick no longer publishes Task deadline work",
    )
    bridge_tick = function_body(bridge, "agent_task_bridge_tick")
    require(
        bridge_tick,
        "return agent_task_channel_tick(now)",
        "Task bridge tick must preserve the core due count",
    )
    bridge_due = function_body(
        bridge, "agent_task_bridge_current_deadline_due"
    )
    require(
        bridge_due,
        "agent_task_channel_deadline_due(curr_proc())",
        "deadline fast path must inspect only the current process",
    )
    bridge_safe_point = function_body(
        bridge, "agent_task_bridge_current_deadline_safe_point"
    )
    require_order(
        bridge_safe_point,
        (
            "struct proc *p = curr_proc()",
            "while (agent_task_channel_deadline_due(p))",
            "agent_task_channel_expire(",
            "p, agent_task_bridge_now(), &agent_task_bridge_ops",
            "status == 0 && agent_task_channel_deadline_due(p)",
            "AGENT_TASK_CHANNEL_EVIDENCE",
        ),
        "safe point must drain only the scheduled current process",
    )
    usertrapret = function_body(trap, "usertrapret")
    require(
        usertrapret,
        "if (agent_task_deadline_due_current())",
        "user return may not bypass a due Task deadline",
    )
    require_order(
        usertrapret,
        (
            "proc_thread_exit_requested()",
            "agent_task_deadline_due_current()",
            "kernel_work_begin_background()",
            "agent_task_deadline_checkpoint()",
            "kernel_work_end_background()",
            "kernel_stack_check(curr_thread())",
        ),
        "user return must run the current-process deadline safe point",
    )

    facade_teardown = function_body(facade, "agent_proc_teardown")
    require_order(
        facade_teardown,
        (
            "p->teardown_state == PROC_TEARDOWN_QUIESCING",
            "agent_task_bridge_reclaim(p)",
            "status == AGENT_TASK_CHANNEL_RETRY",
            "agent_task_bridge_active(p)",
            'panic("Task Channel teardown phase")',
            "agent_core_proc_teardown(p)",
        ),
        "Task reclaim must complete only in QUIESCING",
    )
    exit_body = function_body(proc, "exit")
    require_order(
        exit_body,
        (
            "proc_teardown_claim_locked(p, t->tid)",
            "while (agent_proc_teardown(p) < 0)",
            "mutex_release_thread_locks(t)",
            "proc_interrupt_siblings(p, t)",
            "proc_teardown_run(p, t, 1)",
        ),
        "QUIESCING Task reclaim must precede locks, FDs, VM, and lifecycle",
    )
    freeproc = function_body(proc, "freeproc")
    require_order(
        freeproc,
        (
            "proc_teardown_claim_locked(p, PROC_TEARDOWN_OWNER_KERNEL)",
            "agent_proc_teardown(p)",
            "proc_child_unbind(p)",
            "proc_teardown_run(p, 0, 0)",
        ),
        "rollback must reclaim Task state before process resources",
    )
    public_commit = function_body(facade, "agent_exec_public_identity_commit")
    require(
        public_commit,
        "if (agent_task_bridge_active(p))",
        "PUBLIC exec must test the active channel without a bypass predicate",
    )
    require_order(
        public_commit,
        (
            "agent_task_bridge_active(p)",
            "return -1",
            "agent_core_exec_public_commit(p)",
        ),
        "PUBLIC exec must reject an active Task Channel before identity commit",
    )
    image_install = function_body(proc, "proc_install_user_image")
    require_order(
        image_install,
        (
            "transition.identity_policy == VFS_EXEC_IDENTITY_PUBLIC",
            "agent_exec_public_identity_commit(p)",
            "vfs_proc_exec_commit(p, &transition)",
            "agent_process_image_install_locked(p)",
        ),
        "PUBLIC rejection must precede credential and VM publication",
    )

    bridge_cancel = function_body(bridge, "agent_task_bridge_cancel")
    require_order(
        bridge_cancel,
        (
            "sqe->request_id == 0",
            "agent_execution_force_cancel_sync(",
            "AGENT_EXECUTION_FORCE_CANCEL_PENDING",
            "AGENT_EXECUTION_FORCE_CANCEL_DENIED",
            "status != AGENT_EXECUTION_FORCE_CANCEL_COMPLETE",
            "status != AGENT_EXECUTION_FORCE_CANCEL_CACHED",
            "agent_task_bridge_execution_completion(",
            "agent_task_bridge_completion_valid(completion)",
            "return AGENT_TASK_HOOK_COMPLETE",
        ),
        "force-cancel cached terminals must become one COMPLETE hook",
    )
    bridge_import = function_body(bridge, "agent_task_bridge_resource_import")
    require_order(
        bridge_import,
        (
            "if (imported != 0)",
            "memset(imported, 0, sizeof(*imported))",
            "return AGENT_TASK_CHANNEL_DENIED",
        ),
        "unsupported resource imports must fail closed with zero metadata",
    )
    forbid(
        bridge_import,
        "AGENT_TASK_CHANNEL_OK",
        "the bridge must not fabricate support for an unowned resource type",
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


@dataclass(frozen=True)
class Handle:
    slot: int
    kind: int
    ownership: str
    generation: int


@dataclass
class Resource:
    kind: int
    generation: int
    references: int = 0
    owner_request: int = 0


@dataclass
class ResourceTableModel:
    capacity: int = 2
    highwater: list[int] = field(default_factory=lambda: [0, 0])
    slots: list[Resource | None] = field(default_factory=lambda: [None, None])

    def import_resource(self, kind: int, ownership: str = "OWNED") -> Handle:
        if ownership != "OWNED" or kind <= 0:
            raise PermissionError("imports transfer ownership")
        for index, slot in enumerate(self.slots):
            if slot is None:
                self.highwater[index] += 1
                self.slots[index] = Resource(kind, self.highwater[index])
                return Handle(index + 1, kind, "OWNED", self.highwater[index])
        raise BlockingIOError("resource table full")

    def lookup(self, handle: Handle) -> Resource:
        if handle.slot <= 0 or handle.slot > self.capacity:
            raise KeyError("bad slot")
        slot = self.slots[handle.slot - 1]
        if slot is None or slot.kind != handle.kind or slot.generation != handle.generation:
            raise KeyError("stale typed handle")
        if handle.ownership not in ("OWNED", "BORROWED"):
            raise PermissionError("bad ownership")
        return slot

    def acquire(self, handle: Handle, request_id: int) -> None:
        slot = self.lookup(handle)
        if handle.ownership == "BORROWED":
            if slot.owner_request:
                raise BlockingIOError("owned object is in flight")
            slot.references += 1
        else:
            if slot.references or slot.owner_request:
                raise BlockingIOError("owned move is not exclusive")
            slot.owner_request = request_id

    def finish(self, handle: Handle, request_id: int, returned: bool = False) -> None:
        slot = self.lookup(handle)
        if handle.ownership == "BORROWED":
            if slot.references == 0:
                raise RuntimeError("borrow underflow")
            slot.references -= 1
        elif returned:
            if slot.owner_request != request_id:
                raise RuntimeError("wrong owned return")
            slot.owner_request = 0
        else:
            self.slots[handle.slot - 1] = None

    def release(self, handle: Handle) -> None:
        slot = self.lookup(handle)
        if handle.ownership != "OWNED" or slot.references or slot.owner_request:
            raise BlockingIOError("resource still pinned")
        self.slots[handle.slot - 1] = None


@dataclass
class Request:
    descriptor: dict[str, int | str]
    accepted_tick: int = 0
    state: str = "RUNNING"
    cancel_requested: bool = False
    lifecycle_held: bool = True
    terminal: str = ""
    cq_position: int = -1
    terminal_tick: int = 0
    completion_tick: int = 0
    cqe_flags: frozenset[str] = frozenset()


@dataclass
class TaskChannelModel:
    issuer: tuple[int, int]
    lifecycle: tuple[int, int]
    capacity: int = 2
    generation: int = 1
    sq_head: int = 0
    submitted: int = 0
    last_request_id: int = 0
    cq_head: int = 0
    cq_tail: int = 0
    resync: bool = False
    cq_full: bool = False
    backpressure: int = 0
    resync_count: int = 0
    lifecycle_tokens: int = 0
    requests: dict[int, Request] = field(default_factory=dict)
    terminal_pending: list[int] = field(default_factory=list)
    visible: dict[int, int] = field(default_factory=dict)

    def submit(
        self,
        shared: dict[str, int | str],
        issuer: tuple[int, int],
        *,
        accepted_tick: int = 1,
    ) -> dict[str, int | str]:
        if issuer != self.issuer:
            raise PermissionError("not the single issuer")
        copied = deepcopy(shared)
        expected_slot_generation = self.sq_head // self.capacity + 1
        if copied["request_id"] <= self.last_request_id:
            raise ValueError("request IDs are not monotonic")
        if copied["ring_generation"] != self.generation:
            raise ValueError("stale ring")
        if copied["slot_generation"] != expected_slot_generation:
            raise ValueError("stale SQ slot")
        if copied["lifecycle"] != self.lifecycle:
            raise PermissionError("stale lifecycle")
        if copied.get("opcode") != "SUBMIT":
            raise ValueError("invalid opcode")
        if bool(copied.get("hard_deadline")) != (
            int(copied.get("deadline", 0)) != 0
        ):
            raise ValueError("deadline flag/value mismatch")
        request_id = int(copied["request_id"])
        self.last_request_id = request_id
        self.sq_head += 1
        self.submitted += 1
        self.lifecycle_tokens += 1
        self.requests[request_id] = Request(copied, accepted_tick=accepted_tick)
        return copied

    def cancel(self, cancel_id: int, target_id: int, denied: bool = False) -> str:
        if cancel_id <= self.last_request_id:
            raise ValueError("cancel ID is not monotonic")
        target = self.requests.get(target_id)
        self.last_request_id = cancel_id
        self.sq_head += 1
        self.submitted += 1
        if target is None:
            return "STALE"
        if target.state in ("TERMINAL", "VISIBLE"):
            return "IDEMPOTENT"
        target.cancel_requested = True
        if denied:
            target.cancel_requested = False
            return "DENIED"
        return "PENDING"

    def complete(
        self,
        request_id: int,
        terminal_tick: int,
        status: str = "OK",
        *,
        completion_tick: int | None = None,
        cached: bool = False,
        dependency_failed: bool = False,
    ) -> str:
        request = self.requests[request_id]
        if request.state != "RUNNING":
            raise KeyError("stale completion")
        published_tick = terminal_tick if completion_tick is None else completion_tick
        if terminal_tick > published_tick:
            raise ValueError("terminal decision follows completion publication")
        if not cached and terminal_tick < request.accepted_tick:
            raise ValueError("fresh terminal predates acceptance")
        hard_deadline = int(request.descriptor.get("deadline", 0))
        if hard_deadline and terminal_tick >= hard_deadline and status not in (
            "DENIED",
            "STALE",
        ):
            terminal = "TIMEOUT"
        elif request.cancel_requested or dependency_failed:
            terminal = "CANCELLED"
        else:
            terminal = status
        flags: set[str] = set()
        if terminal == "TIMEOUT":
            flags.add("DEADLINE")
        elif terminal == "CANCELLED":
            flags.add("CANCELLED")
            if dependency_failed and bool(request.descriptor.get("link")):
                flags.add("LINK_FAILED")
        elif terminal == "DENIED":
            flags.add("DENIED")
        request.terminal = terminal
        request.terminal_tick = terminal_tick
        request.completion_tick = published_tick
        request.cqe_flags = frozenset(flags)
        request.state = "TERMINAL"
        request.lifecycle_held = False
        self.lifecycle_tokens -= 1
        self.terminal_pending.append(request_id)
        self.flush()
        return terminal

    def flush(self) -> None:
        while self.terminal_pending:
            if self.cq_tail - self.cq_head >= self.capacity:
                self.cq_full = True
                self.backpressure += 1
                return
            request_id = self.terminal_pending.pop(0)
            self.visible[self.cq_tail] = request_id
            self.requests[request_id].state = "VISIBLE"
            self.requests[request_id].cq_position = self.cq_tail
            self.cq_tail += 1
        self.cq_full = self.cq_tail - self.cq_head == self.capacity

    def ack(self, acknowledged: int) -> None:
        if not self.cq_head <= acknowledged <= self.cq_tail:
            self.protocol_fault()
            raise ValueError("invalid CQ ack")
        for position in range(self.cq_head, acknowledged):
            if position not in self.visible:
                self.protocol_fault()
                raise ValueError("CQ hole")
        for position in range(self.cq_head, acknowledged):
            request_id = self.visible.pop(position)
            del self.requests[request_id]
        self.cq_head = acknowledged
        self.cq_full = False
        self.flush()

    def protocol_fault(self) -> None:
        if not self.resync:
            self.resync = True
            self.resync_count += 1
            self.generation += 1
            self.sq_head = 0

    def resynchronize(self, flag: bool, sq_tail: int) -> None:
        if not self.resync or not flag or sq_tail != 0:
            raise ValueError("bad resync handshake")
        self.resync = False


@dataclass(frozen=True)
class ContractOutcomeModel:
    status: str
    evidence_ticket: int
    terminal_tick: int


@dataclass
class ExecutionContractModel:
    cache: dict[tuple[int, int, int], ContractOutcomeModel] = field(
        default_factory=dict
    )
    evidence_records: int = 0
    context_records: int = 0
    effects: int = 0
    failed_nodes: set[int] = field(default_factory=set)
    dependency_failed: set[int] = field(default_factory=set)

    def submit(
        self,
        node_id: int,
        attempt_id: int,
        request_deadline: int,
        now: int,
        *,
        predecessor_failed: bool = False,
        effect_terminal_tick: int | None = None,
    ) -> ContractOutcomeModel:
        identity = (node_id, attempt_id, request_deadline)
        if identity in self.cache:
            return self.cache[identity]
        if any(key[:2] == identity[:2] for key in self.cache):
            raise ValueError("attempt replay conflict")
        terminal_tick = now if effect_terminal_tick is None else effect_terminal_tick
        if terminal_tick < now:
            raise ValueError("terminal tick predates admission")
        if request_deadline and now >= request_deadline:
            status = "TIMEOUT"
        elif predecessor_failed:
            status = "CANCELLED"
        else:
            self.effects += 1
            status = (
                "TIMEOUT"
                if request_deadline and terminal_tick >= request_deadline
                else "OK"
            )
        self.context_records += 1
        self.evidence_records += 1
        outcome = ContractOutcomeModel(
            status, self.evidence_records, terminal_tick
        )
        self.cache[identity] = outcome
        if status != "OK":
            self.failed_nodes.add(node_id)
            self.dependency_failed.add(node_id + 1)
        return outcome


@dataclass
class AcceptedTaskSubmitModel:
    accepted_tick: int
    sampled_ticks: list[int] = field(default_factory=list)
    yields: int = 0
    state: str = "RUNNING"

    def run(
        self,
        attempts: list[tuple[str, int]],
        *,
        killed: bool = False,
        lifecycle_active: bool = True,
    ) -> ContractOutcomeModel:
        del killed  # Exit requests cannot abandon an accepted Task.
        for offset, (status, ticket) in enumerate(attempts):
            tick = self.accepted_tick + offset
            self.sampled_ticks.append(tick)
            if status in ("RETRY", "NO_SPACE") and ticket == 0:
                if not lifecycle_active:
                    raise RuntimeError("accepted Task lost lifecycle")
                self.yields += 1
                continue
            if ticket == 0:
                raise RuntimeError("accepted Task without terminal evidence")
            self.state = "TERMINAL"
            return ContractOutcomeModel(status, ticket, tick)
        raise RuntimeError("accepted Task remained nonterminal")


class RetiringContractModel:
    @staticmethod
    def preflight(*, task_binding: bool) -> str:
        return "PENDING" if task_binding else "DENIED"

    @staticmethod
    def admit(*, task_binding: bool) -> str:
        return "DENIED" if task_binding else "CANCELLED"


class TaskChannelTests(unittest.TestCase):
    def assert_mutation_rejected(
        self, path: str, old: str, new: str, *, count: int = 1
    ) -> None:
        with self.assertRaises(ContractError):
            validate_task_channel(mutated(path, old, new, count=count))

    def test_current_implementation_satisfies_contract(self) -> None:
        validate_task_channel(SOURCES)

    def test_sqe_width_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "agent_task_channel_abi.h",
            "sizeof(struct agent_task_sqe) == 128",
            "sizeof(struct agent_task_sqe) == 64",
        )

    def test_partial_copy_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c", "sizeof(sqe));", "sizeof(sqe) - 1);"
        )

    def test_single_issuer_generation_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "issuer->identity_generation ==\n\t\t       state->private->header.issuer_generation",
            "issuer->identity_generation !=\n\t\t       state->private->header.issuer_generation",
        )

    def test_nonmonotonic_request_id_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "sqe->request_id <= private->header.last_accepted_request_id",
            "sqe->request_id < private->header.last_accepted_request_id",
        )

    def test_writable_cq_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "(uint64)pages[1], PTE_U | PTE_R) < 0",
            "(uint64)pages[1], PTE_U | PTE_R | PTE_W) < 0",
        )

    def test_cq_ack_hole_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c", "if (found != 1)", "if (found == 0)"
        )

    def test_resync_handshake_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c", "enter->sq_tail != 0", "enter->sq_tail == 0"
        )

    def test_cq_backpressure_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "private->header.cq_tail - private->header.cq_head >=",
            "private->header.cq_tail - private->header.cq_head >",
        )

    def test_duplicate_terminal_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "request->state != AGENT_TASK_REQUEST_RUNNING ||\n\t    !agent_task_completion_valid_locked(",
            "request->state != AGENT_TASK_REQUEST_ACCEPTED ||\n\t    !agent_task_completion_valid_locked(",
        )

    def test_reclaim_completion_recursion_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "complete_status = agent_task_channel_complete_finish(\n\t\t\t\tp,",
            "complete_status = agent_task_channel_complete(\n\t\t\t\tp,",
        )

    def test_cancel_id_accounting_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "private->header.last_accepted_request_id = sqe.request_id;",
            "private->header.last_accepted_request_id = sqe.link_request_id;",
        )

    def test_deadline_shape_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "(sqe->opcode != AGENT_TASK_CHANNEL_OP_SUBMIT &&\n"
                "\t     sqe->opcode != AGENT_TASK_CHANNEL_OP_CANCEL)",
                "(sqe->opcode != AGENT_TASK_CHANNEL_OP_SUBMIT &&\n"
                "\t     sqe->opcode == AGENT_TASK_CHANNEL_OP_CANCEL)",
            ),
            (
                "(((sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0) !=\n"
                "\t     (sqe->deadline_tick != 0))",
                "((sqe->flags & AGENT_TASK_SQE_F_HARD_DEADLINE) != 0 &&\n"
                "\t     sqe->deadline_tick == 0)",
            ),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                self.assert_mutation_rejected(
                    "os/agent_task_channel.c", old, new
                )

    def test_acked_cancel_stale_mutations_are_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "if (target != 0 &&\n"
            "\t\t    !agent_task_cancel_descriptor_matches(&sqe, target))",
            "if (target == 0 ||\n"
            "\t\t    !agent_task_cancel_descriptor_matches(&sqe, target))",
        )
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "if (target == 0) {\n"
            "\t\t\tagent_task_channel_publish_locked(state);\n"
            "\t\t\tintr_restore(enabled);\n"
            "\t\t\treturn AGENT_TASK_CHANNEL_STALE;\n"
            "\t\t}",
            "if (target == 0) {\n"
            "\t\t\tstatus = agent_task_channel_protocol_fault_locked(state);\n"
            "\t\t\tintr_restore(enabled);\n"
            "\t\t\treturn status;\n"
            "\t\t}",
        )

    def test_consumed_error_accounting_mutations_are_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "submitted += consumed;",
            "submitted += status > 0;",
        )
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "private->header.last_accepted_request_id = sqe.request_id;\n"
            "\t\t*consumed = 1;",
            "private->header.last_accepted_request_id = sqe.request_id;\n"
            "\t\t*consumed = 0;",
        )

    def test_deadline_binding_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "agent_execution_hash_u64(&ctx, request_deadline_tick);",
                "agent_execution_hash_u64(&ctx, 0);",
            ),
            (
                "p, &op, &binding, agent_task_bridge_request_deadline(sqe),",
                "p, &op, &binding, 0,",
            ),
            (
                "claim->deadline_tick = request_deadline_tick != 0 ?\n"
                "\t\trequest_deadline_tick : contract_deadline;",
                "claim->deadline_tick = contract_deadline;",
            ),
            (
                "if (claim->deadline_tick != 0 && now >= claim->deadline_tick)",
                "if (claim->deadline_tick != 0 && now > claim->deadline_tick)",
            ),
            (
                "if (claim.deadline_tick != 0 &&\n"
                "\t    terminal_tick >= claim.deadline_tick)",
                "if (claim.deadline_tick != 0 &&\n"
                "\t    terminal_tick > claim.deadline_tick)",
            ),
        )
        paths = (
            "os/agent_execution_contract.c",
            "os/agent_task_bridge.c",
            "os/agent_execution_contract.c",
            "os/agent_execution_contract.c",
            "os/agent_core.c",
        )
        for path, (old, new) in zip(paths, mutations):
            with self.subTest(path=path, old=old):
                self.assert_mutation_rejected(path, old, new)

    def test_cached_deadline_replay_mutations_are_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "(outcome->completion_flags & AGENT_RESPONSE_V3_F_CACHED) != 0",
            "(outcome->completion_flags & AGENT_RESPONSE_V3_F_CACHED) == 0",
        )
        self.assert_mutation_rejected(
            "os/agent_execution_contract.c",
            "memmove(outcome, &cached->outcome,\n"
            "\t\t\t\t\tsizeof(*outcome));",
            "memset(outcome, 0, sizeof(*outcome));",
        )
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "completion->terminal_tick >= request->sqe.deadline_tick",
            "agent_task_now() >= request->sqe.deadline_tick",
        )

    def test_terminal_tick_provenance_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "os/agent_task_bridge.c",
                "completion->terminal_tick = outcome->terminal_tick;",
                "completion->terminal_tick = agent_task_bridge_now();",
            ),
            (
                "os/agent_task_channel.c",
                "completion->terminal_tick > completion->completion_tick",
                "completion->terminal_tick < completion->completion_tick",
            ),
            (
                "os/agent_task_channel.c",
                "!cached && completion->terminal_tick < request->accepted_tick",
                "cached && completion->terminal_tick < request->accepted_tick",
            ),
            (
                "os/agent_task_channel.c",
                "request->accepted_tick = agent_task_now();",
                "request->accepted_tick = 0;",
            ),
            (
                "os/agent_core.c",
                "terminal_tick = agent_ticks();",
                "terminal_tick = tick;",
            ),
            (
                "os/agent_core.c",
                "outcome->terminal_tick = now;",
                "outcome->terminal_tick = request_deadline_tick;",
            ),
        )
        for path, old, new in mutations:
            with self.subTest(path=path, old=old):
                self.assert_mutation_rejected(path, old, new)

    def test_dependency_link_truth_table_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "os/agent_task_channel.c",
                "completion->status == AGENT_STATUS_CANCELLED &&\n"
                "\t\tdependency_reason;",
                "completion->status == AGENT_STATUS_CANCELLED &&\n"
                "\t\tdependency_reason &&\n"
                "\t\t(request->sqe.flags & AGENT_TASK_SQE_F_LINK) != 0;",
            ),
            (
                "os/agent_task_channel.c",
                "link_failed != linked_dependency_failed",
                "link_failed != dependency_failed",
            ),
            (
                "os/agent_task_channel.c",
                "\t\t dependency_failed) &&",
                "\t\t linked_dependency_failed) &&",
            ),
            (
                "os/agent_task_bridge.c",
                "if (linked &&\n"
                "\t\t    decision_reason == AGENT_EXECUTION_REASON_DEPENDENCY_FAILED)",
                "if (decision_reason == AGENT_EXECUTION_REASON_DEPENDENCY_FAILED)",
            ),
            (
                "os/agent_task_bridge.c",
                "AGENT_TASK_CQE_F_LINK_FAILED : 0)",
                "AGENT_TASK_CQE_F_LINK_FAILED : AGENT_TASK_CQE_F_LINK_FAILED)",
            ),
        )
        for path, old, new in mutations:
            with self.subTest(path=path, old=old):
                self.assert_mutation_rejected(path, old, new)

    def test_accepted_task_retry_loop_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) == 0",
                "AGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL) != 0",
            ),
            ("for (;;) {", "if (1) {"),
            (
                "p, op, result, agent_ticks(), binding,",
                "p, op, result, request_deadline_tick, binding,",
            ),
            (
                "outcome->evidence_ticket != 0)",
                "outcome->evidence_ticket == 0)",
            ),
            ("\t\tyield();", "\t\tbreak;"),
            (
                "!workflow_lifecycle_active(binding->lifecycle)",
                "p->killed",
            ),
            (
                "agent_lifecycle_context_lane_enter_accepted_task(p) < 0",
                "agent_lifecycle_context_lane_enter_accepted_task(p) == 0",
            ),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                self.assert_mutation_rejected("os/agent_core.c", old, new)

    def test_accepted_task_lane_and_preallocation_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "os/agent_core.c",
                "if (!agent_evidence_context_preallocated(p, binding->lifecycle))",
                "if (agent_evidence_context_preallocated(p, binding->lifecycle))",
            ),
            (
                "os/agent_core.c",
                "request_deadline_tick, admission_policy_flags, outcome);\n"
                "\t\tagent_lifecycle_context_lane_leave(p);",
                "request_deadline_tick, admission_policy_flags, outcome);",
            ),
            (
                "os/agent_core.c",
                "agent_lifecycle_context_lane_enter(p) < 0",
                "agent_lifecycle_context_lane_enter_accepted_task(p) < 0",
            ),
            (
                "os/agent_lifecycle.c",
                "p->teardown_state <= PROC_TEARDOWN_QUIESCING",
                "p->teardown_state < PROC_TEARDOWN_QUIESCING",
            ),
            (
                "os/agent_lifecycle.c",
                "wait_queue_sleep_irq_uninterruptible(\n"
                "\t\t\t\t&p->agent_context_lane_waiters)",
                "wait_queue_sleep_irq(&p->agent_context_lane_waiters)",
            ),
            (
                "os/agent_lifecycle.c",
                "agent_lifecycle_context_lane_enter_mode(p, 1)",
                "agent_lifecycle_context_lane_enter_mode(p, 0)",
            ),
            (
                "os/agent_lifecycle.c",
                "agent_lifecycle_context_lane_enter_mode(p, 0)",
                "agent_lifecycle_context_lane_enter_mode(p, 1)",
            ),
            (
                "os/agent_evidence_ring.c",
                "agent_evidence_pages_ready(state) &&",
                "state != 0 &&",
            ),
            (
                "os/agent_execution_contract.c",
                "agent_evidence_prepare_direct_denials(p, lifecycle)",
                "agent_evidence_context_preallocated(p, lifecycle)",
            ),
        )
        for path, old, new in mutations:
            with self.subTest(path=path, old=old):
                self.assert_mutation_rejected(path, old, new)

    def test_accepted_bridge_submit_cannot_retry_mutations_are_rejected(self) -> None:
        for invariant in (
            "Task bridge submit validation",
            "Task bridge submit outcome",
            "Task bridge submit completion",
        ):
            with self.subTest(invariant=invariant):
                self.assert_mutation_rejected(
                    "os/agent_task_bridge.c",
                    f'panic("{invariant}");',
                    "return AGENT_TASK_CHANNEL_RETRY;",
                )

    def test_retiring_task_binding_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "os/agent_task_bridge.c",
                "binding->internal_flags =\n"
                "\t\tAGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL;",
                "binding->internal_flags = 0;",
            ),
            (
                "os/agent_execution_contract.c",
                "result, AGENT_STATUS_OK,\n"
                "\t\t\t\tAGENT_EXECUTION_REASON_NONE",
                "result, AGENT_STATUS_RETRY,\n"
                "\t\t\t\tAGENT_EXECUTION_REASON_CONTRACT_RETIRING",
            ),
            (
                "os/agent_execution_contract.c",
                "AGENT_STATUS_DENIED : AGENT_STATUS_CANCELLED",
                "AGENT_STATUS_CANCELLED : AGENT_STATUS_CANCELLED",
            ),
            (
                "os/agent_core.c",
                "memset(&binding, 0, sizeof(binding));",
                "memset(&binding, 0, sizeof(binding));\n"
                "\tbinding.internal_flags =\n"
                "\t\tAGENT_EXECUTION_BINDING_INTERNAL_F_TASK_CHANNEL;",
            ),
        )
        for path, old, new in mutations:
            with self.subTest(path=path, old=old):
                self.assert_mutation_rejected(path, old, new)

    def test_security_denial_deadline_precedence_mutations_are_rejected(self) -> None:
        for status in ("DENIED", "STALE"):
            with self.subTest(status=status):
                self.assert_mutation_rejected(
                    "os/agent_task_channel.c",
                    f"completion->status != AGENT_STATUS_{status}",
                    f"completion->status == AGENT_STATUS_{status}",
                )

    def test_deadline_precedence_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c", "\t\t!deadline_due;", "\t\tdeadline_due;"
        )

    def test_validate_terminal_is_deferred_to_submit(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "status = agent_execution_contract_preflight(",
            "status = agent_execution_task_submit_sync(",
        )
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "return AGENT_TASK_HOOK_PENDING;",
            "return AGENT_TASK_HOOK_COMPLETE;",
        )

    def test_due_dependency_terminal_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "res->status = claim.deadline_expired ?\n"
            "\t\t\tAGENT_STATUS_TIMEOUT : AGENT_STATUS_CANCELLED;",
            "res->status = claim.dependency_failed ?\n"
            "\t\t\tAGENT_STATUS_CANCELLED : AGENT_STATUS_TIMEOUT;",
        )
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "agent_execution_contract_complete(claim, res, outcome);",
            "agent_execution_contract_release(claim);",
        )

    def test_deadline_sticky_lookup_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "(state->private->header.flags &\n\t     AGENT_TASK_CHANNEL_RING_F_DEADLINE_DUE) != 0",
            "(state->private->header.flags &\n\t     AGENT_TASK_CHANNEL_RING_F_CQ_FULL) != 0",
        )

    def test_callback_pin_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "agent_task_callback_get_locked(state);",
            "agent_task_channel_publish_locked(state);",
        )

    def test_reclaim_pin_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "state->private->header.callback_refs != 0 ||\n\t    state->private->header.exec_alias_refs != 0",
            "state->private->header.callback_refs == 0 ||\n\t    state->private->header.exec_alias_refs != 0",
        )

    def test_exec_alias_transaction_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "header->exec_alias_refs = AGENT_TASK_EXEC_ALIAS_COMMITTED;",
            "header->exec_alias_refs = AGENT_TASK_EXEC_ALIAS_NONE;",
        )

    def test_handle_generation_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "slot->generation != handle.generation",
            "slot->generation == handle.generation",
        )

    def test_borrowed_import_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "imported->resource_flags == AGENT_TASK_HANDLE_F_OWNED",
            "imported->resource_flags == AGENT_TASK_HANDLE_F_BORROWED",
        )

    def test_zero_producer_identity_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "imported->producer_control_id != 0",
            "imported->producer_control_id == 0",
        )

    def test_lifecycle_transfer_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "request->flags = AGENT_TASK_REQUEST_F_LIFECYCLE_HELD;",
            "request->flags = 0;",
        )

    def test_bridge_init_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent.c",
            "agent_task_bridge_init();",
            "agent_task_channel_init_bypassed();",
        )

    def test_task_syscall_dispatch_mutations_are_rejected(self) -> None:
        for name in ("setup", "enter", "resource"):
            with self.subTest(syscall=name):
                self.assert_mutation_rejected(
                    "os/syscall.c",
                    f"ret = sys_agent_task_channel_{name}(trapframe->a0,",
                    f"ret = sys_agent_task_channel_{name}_bypassed(trapframe->a0,",
                )

    def test_setup_wrapper_commit_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "status = agent_task_channel_setup(",
            "status = agent_task_channel_setup_bypassed(",
        )

    def test_timer_bridge_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent.c",
            "agent_task_bridge_tick(get_cycle() / (CPU_FREQ / TICKS_PER_SEC))",
            "agent_task_bridge_tick(0)",
        )

    def test_tick_exact_deadline_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_channel.c",
            "if (now >= request->sqe.deadline_tick &&\n"
            "\t\t    (request->flags &",
            "if (now > request->sqe.deadline_tick &&\n"
            "\t\t    (request->flags &",
        )

    def test_usertrapret_safe_point_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/trap.c",
            "if (agent_task_deadline_due_current()) {",
            "if (0 && agent_task_deadline_due_current()) {",
        )

    def test_quiescing_reclaim_order_mutation_is_rejected(self) -> None:
        old = (
            "\twhile (agent_proc_teardown(p) < 0)\n"
            "\t\tyield();\n"
            "\tmutex_release_thread_locks(t);"
        )
        new = (
            "\tmutex_release_thread_locks(t);\n"
            "\twhile (agent_proc_teardown(p) < 0)\n"
            "\t\tyield();"
        )
        self.assert_mutation_rejected("os/proc.c", old, new)

    def test_public_exec_active_channel_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent.c",
            "if (agent_task_bridge_active(p))\n"
            "\t\treturn -1;\n"
            "\treturn agent_core_exec_public_commit(p);",
            "if (0 && agent_task_bridge_active(p))\n"
            "\t\treturn -1;\n"
            "\treturn agent_core_exec_public_commit(p);",
        )

    def test_force_cached_completion_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "status != AGENT_EXECUTION_FORCE_CANCEL_CACHED",
            "status == AGENT_EXECUTION_FORCE_CANCEL_CACHED",
        )

    def test_resource_import_fail_closed_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "if (imported != 0)\n"
            "\t\tmemset(imported, 0, sizeof(*imported));\n"
            "\treturn AGENT_TASK_CHANNEL_DENIED;",
            "if (imported != 0)\n"
            "\t\tmemset(imported, 0, sizeof(*imported));\n"
            "\treturn AGENT_TASK_CHANNEL_OK;",
        )

    def test_guest_performance_sample_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "user/src/agenttask_ucore.c",
            "#define PERF_OPERATION_COUNT 16U",
            "#define PERF_OPERATION_COUNT 15U",
        )

    def test_guest_acked_cancel_stale_mutations_are_rejected(self) -> None:
        mutations = (
            (
                "enter_result.status == AGENT_TASK_CHANNEL_STALE &&",
                "enter_result.status == AGENT_TASK_CHANNEL_OK &&",
            ),
            (
                "enter_result.protocol_faults == before_stale.protocol_faults &&",
                "enter_result.protocol_faults != before_stale.protocol_faults &&",
            ),
            (
                "enter_result.flags == before_stale.flags &&",
                "enter_result.flags != before_stale.flags &&",
            ),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                self.assert_mutation_rejected(
                    "user/src/agenttask_ucore.c", old, new
                )

    def test_scalar_v3_typed_echo_param_mutations_are_rejected(self) -> None:
        mutations = (
            ("#define ECHO_PARAM_COUNT 3U", "#define ECHO_PARAM_COUNT 2U"),
            (
                "params[0].type = AGENT_PARAM_STRING;",
                "params[0].type = AGENT_PARAM_UINT64;",
            ),
            (
                'memcpy(params[1].key, "arg0", sizeof("arg0"));',
                'memcpy(params[1].key, "arg1", sizeof("arg1"));',
            ),
            (
                "request->param_count = ECHO_PARAM_COUNT;",
                "request->param_count = 0;",
            ),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                self.assert_mutation_rejected(
                    "user/src/agenttask_ucore.c", old, new
                )

    def test_guest_wall_clock_claim_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "user/src/agenttask_ucore.c",
            "wall_clock=unavailable raw_cycles=not_claimed",
            "wall_clock=measured raw_cycles=measured",
        )

    def test_guest_shared_lifecycle_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "user/src/agenttask_ucore.c",
            "pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);",
            "pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);",
        )

    def test_guest_task_control_copy_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "user/src/agenttask_ucore.c",
            "2 * (sizeof(struct agent_task_channel_enter) +\n"
            "\t\t\t 2 * sizeof(struct agent_task_channel_enter_result))",
            "2 * (sizeof(struct agent_task_channel_enter) +\n"
            "\t\t\t sizeof(struct agent_task_channel_enter_result))",
        )

    def test_batch_reused_service_tick_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_core.c",
            "agent_execute_one(p, &op, &res, agent_ticks(), 0, 0, 0, 0)",
            "agent_execute_one(p, &op, &res, 1, 0, 0, 0, 0)",
        )

    def test_task_context_service_tick_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "user/src/agenttask_ucore.c",
            "\t\t\t&service_start_tick);",
            "\t\t\t0);",
        )

    def test_public_completion_tick_mutation_is_rejected(self) -> None:
        self.assert_mutation_rejected(
            "os/agent_task_bridge.c",
            "completion->completion_tick = agent_task_bridge_now();",
            "completion->completion_tick = 1;",
            count=2,
        )

    def descriptor(
        self,
        request_id: int,
        position: int = 0,
        deadline: int = 0,
        *,
        linked: bool = False,
    ) -> dict[str, int | str | tuple[int, int]]:
        return {
            "opcode": "SUBMIT",
            "request_id": request_id,
            "ring_generation": 1,
            "slot_generation": position // 2 + 1,
            "lifecycle": (9, 4),
            "deadline": deadline,
            "hard_deadline": deadline != 0,
            "link": linked,
            "payload": "original",
        }

    def test_model_copies_before_validation_and_binds_single_issuer(self) -> None:
        model = TaskChannelModel((7, 12), (9, 4))
        shared = self.descriptor(1)
        copied = model.submit(shared, (7, 12))
        shared["payload"] = "attacker mutation"
        self.assertEqual(copied["payload"], "original")
        self.assertEqual(model.requests[1].descriptor["payload"], "original")
        with self.assertRaises(PermissionError):
            model.submit(self.descriptor(2, 1), (7, 13))

    def test_model_rejects_nonmonotonic_ids_and_stale_slot_generation(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.submit(self.descriptor(4), (1, 1))
        with self.assertRaises(ValueError):
            model.submit(self.descriptor(4, 1), (1, 1))
        stale = self.descriptor(5, 1)
        stale["slot_generation"] = 2
        with self.assertRaises(ValueError):
            model.submit(stale, (1, 1))

    def test_model_cancel_has_no_cqe_and_deadline_wins(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.submit(self.descriptor(1, deadline=10), (1, 1))
        self.assertEqual(model.cancel(2, 1), "PENDING")
        self.assertEqual(model.cq_tail, 0)
        self.assertEqual(model.complete(1, terminal_tick=10), "TIMEOUT")
        self.assertEqual(model.cq_tail, 1)
        with self.assertRaises(KeyError):
            model.complete(1, terminal_tick=11)
        self.assertEqual(model.lifecycle_tokens, 0)

    def test_model_denied_cancel_consumes_id_but_leaves_target_running(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.submit(self.descriptor(3), (1, 1))
        submitted = model.submitted
        self.assertEqual(model.cancel(4, 3, denied=True), "DENIED")
        self.assertEqual(model.submitted, submitted + 1)
        self.assertEqual(model.last_request_id, 4)
        self.assertEqual(model.requests[3].state, "RUNNING")
        self.assertFalse(model.requests[3].cancel_requested)
        self.assertEqual(model.cq_tail, 0)

    def test_model_acked_cancel_is_consumed_stale_without_resync(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.submit(self.descriptor(1), (1, 1))
        model.complete(1, terminal_tick=1)
        retained_submitted = model.submitted
        self.assertEqual(model.cancel(2, 1), "IDEMPOTENT")
        self.assertEqual(model.submitted, retained_submitted + 1)
        model.ack(1)
        submitted = model.submitted
        self.assertEqual(model.cancel(3, 1), "STALE")
        self.assertEqual(model.submitted, submitted + 1)
        self.assertEqual(model.sq_head, 3)
        self.assertFalse(model.resync)
        self.assertEqual(model.cq_tail, 1)

    def test_model_deadline_shape_and_inclusive_completion(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        malformed = self.descriptor(1, deadline=10)
        malformed["hard_deadline"] = False
        with self.assertRaises(ValueError):
            model.submit(malformed, (1, 1))
        model.submit(self.descriptor(1, deadline=10), (1, 1))
        self.assertEqual(
            model.complete(1, terminal_tick=10, status="DENIED"),
            "DENIED",
        )

    def test_model_terminal_tick_is_authoritative_not_delivery_time(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.submit(
            self.descriptor(1, deadline=10),
            (1, 1),
            accepted_tick=8,
        )
        self.assertEqual(
            model.complete(
                1,
                terminal_tick=9,
                completion_tick=20,
                status="OK",
            ),
            "OK",
        )
        self.assertEqual(model.requests[1].terminal_tick, 9)
        self.assertEqual(model.requests[1].completion_tick, 20)

        cached = TaskChannelModel((1, 1), (9, 4))
        cached.submit(
            self.descriptor(1, deadline=10),
            (1, 1),
            accepted_tick=12,
        )
        self.assertEqual(
            cached.complete(
                1,
                terminal_tick=9,
                completion_tick=20,
                status="OK",
                cached=True,
            ),
            "OK",
        )

        invalid = TaskChannelModel((1, 1), (9, 4))
        invalid.submit(self.descriptor(1), (1, 1), accepted_tick=8)
        with self.assertRaises(ValueError):
            invalid.complete(1, terminal_tick=7, completion_tick=20)
        with self.assertRaises(ValueError):
            invalid.complete(
                1,
                terminal_tick=21,
                completion_tick=20,
                cached=True,
            )

    def test_model_dependency_link_flag_truth_table(self) -> None:
        unlinked = TaskChannelModel((1, 1), (9, 4))
        unlinked.submit(self.descriptor(1), (1, 1), accepted_tick=5)
        self.assertEqual(
            unlinked.complete(
                1,
                terminal_tick=5,
                status="CANCELLED",
                dependency_failed=True,
            ),
            "CANCELLED",
        )
        self.assertEqual(
            unlinked.requests[1].cqe_flags,
            frozenset(("CANCELLED",)),
        )

        linked = TaskChannelModel((1, 1), (9, 4))
        linked.submit(
            self.descriptor(1, linked=True),
            (1, 1),
            accepted_tick=5,
        )
        linked.complete(
            1,
            terminal_tick=5,
            status="CANCELLED",
            dependency_failed=True,
        )
        self.assertEqual(
            linked.requests[1].cqe_flags,
            frozenset(("CANCELLED", "LINK_FAILED")),
        )

    def test_model_accepted_task_retries_until_evidenced_terminal(self) -> None:
        submit = AcceptedTaskSubmitModel(accepted_tick=40)
        outcome = submit.run(
            (("RETRY", 0), ("NO_SPACE", 0), ("OK", 71)),
            killed=True,
        )
        self.assertEqual(outcome.status, "OK")
        self.assertEqual(outcome.evidence_ticket, 71)
        self.assertEqual(outcome.terminal_tick, 42)
        self.assertEqual(submit.sampled_ticks, [40, 41, 42])
        self.assertEqual(submit.yields, 2)
        self.assertEqual(submit.state, "TERMINAL")

        with self.assertRaises(RuntimeError):
            AcceptedTaskSubmitModel(1).run((("RETRY", 0),), lifecycle_active=False)
        with self.assertRaises(RuntimeError):
            AcceptedTaskSubmitModel(1).run((("OK", 0),))

    def test_model_retiring_validate_and_admit_are_task_aware(self) -> None:
        self.assertEqual(
            RetiringContractModel.preflight(task_binding=True),
            "PENDING",
        )
        self.assertEqual(
            RetiringContractModel.preflight(task_binding=False),
            "DENIED",
        )
        self.assertEqual(
            RetiringContractModel.admit(task_binding=True),
            "DENIED",
        )
        self.assertEqual(
            RetiringContractModel.admit(task_binding=False),
            "CANCELLED",
        )

    def test_model_due_terminal_replay_and_dependency_are_authoritative(self) -> None:
        contract = ExecutionContractModel()
        first = contract.submit(0, 1, 10, 10)
        self.assertEqual(first.status, "TIMEOUT")
        self.assertEqual(contract.context_records, 1)
        self.assertEqual(contract.evidence_records, 1)
        self.assertEqual(contract.effects, 0)
        self.assertIn(0, contract.failed_nodes)
        self.assertIn(1, contract.dependency_failed)
        replay = contract.submit(0, 1, 10, 20)
        self.assertEqual(replay, first)
        self.assertEqual(contract.context_records, 1)
        self.assertEqual(contract.evidence_records, 1)
        self.assertEqual(contract.effects, 0)
        with self.assertRaises(ValueError):
            contract.submit(0, 1, 11, 20)
        dependent = contract.submit(
            1, 1, 0, 20, predecessor_failed=True
        )
        self.assertEqual(dependent.status, "CANCELLED")
        self.assertEqual(contract.effects, 0)

    def test_model_cached_success_replays_after_deadline_without_new_effect(self) -> None:
        contract = ExecutionContractModel()
        first = contract.submit(0, 1, 10, 9)
        self.assertEqual(first.status, "OK")
        self.assertEqual(contract.effects, 1)
        replay = contract.submit(0, 1, 10, 12)
        self.assertEqual(replay, first)
        self.assertEqual(replay.evidence_ticket, first.evidence_ticket)
        self.assertEqual(replay.terminal_tick, first.terminal_tick)
        self.assertEqual(contract.effects, 1)
        self.assertEqual(contract.context_records, 1)
        self.assertEqual(contract.evidence_records, 1)

    def test_model_post_effect_tick_drives_inclusive_timeout(self) -> None:
        contract = ExecutionContractModel()
        outcome = contract.submit(
            0,
            1,
            10,
            9,
            effect_terminal_tick=10,
        )
        self.assertEqual(outcome.status, "TIMEOUT")
        self.assertEqual(outcome.terminal_tick, 10)
        self.assertEqual(contract.effects, 1)

    def test_model_cq_full_retains_terminal_and_ack_recovers(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.submit(self.descriptor(1, 0), (1, 1))
        model.submit(self.descriptor(2, 1), (1, 1))
        model.complete(1, 1)
        model.complete(2, 1)
        model.submit(self.descriptor(3, 2), (1, 1))
        model.complete(3, 1)
        self.assertTrue(model.cq_full)
        self.assertEqual(model.terminal_pending, [3])
        self.assertGreater(model.backpressure, 0)
        model.ack(1)
        self.assertEqual(model.terminal_pending, [])
        self.assertEqual(model.cq_tail, 3)
        self.assertEqual(model.visible[2], 3)

    def test_model_sticky_resync_requires_exact_handshake(self) -> None:
        model = TaskChannelModel((1, 1), (9, 4))
        model.protocol_fault()
        first_generation = model.generation
        model.protocol_fault()
        self.assertTrue(model.resync)
        self.assertEqual(model.resync_count, 1)
        self.assertEqual(model.generation, first_generation)
        with self.assertRaises(ValueError):
            model.resynchronize(True, 1)
        model.resynchronize(True, 0)
        self.assertFalse(model.resync)

    def test_model_typed_resource_generation_and_ownership(self) -> None:
        table = ResourceTableModel()
        owned = table.import_resource(3)
        borrowed = Handle(owned.slot, owned.kind, "BORROWED", owned.generation)
        table.acquire(borrowed, 10)
        with self.assertRaises(BlockingIOError):
            table.acquire(owned, 10)
        table.finish(borrowed, 10)
        table.acquire(owned, 10)
        table.finish(owned, 10, returned=True)
        table.release(owned)
        replacement = table.import_resource(3)
        self.assertEqual(replacement.slot, owned.slot)
        self.assertGreater(replacement.generation, owned.generation)
        with self.assertRaises(KeyError):
            table.lookup(owned)
        with self.assertRaises(PermissionError):
            table.import_resource(3, "BORROWED")


if __name__ == "__main__":
    unittest.main()
