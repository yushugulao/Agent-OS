#!/usr/bin/env python3
"""任务 6 makespan 并非来自真实 Guest 时钟窗口时按失败关闭。"""
from __future__ import annotations

import sys as _entry_sys


def _isolate_direct_entry_imports() -> None:
    """顶层导入解析仅使用解释器自身管理的路径。"""

    if __name__ != "__main__":
        return
    prefixes = {
        value.replace("\\", "/").rstrip("/").casefold()
        for value in (
            _entry_sys.base_prefix, _entry_sys.base_exec_prefix,
            _entry_sys.prefix, _entry_sys.exec_prefix,
        )
        if value
    }
    _entry_sys.path[:] = [
        value for value in _entry_sys.path
        if value and any(
            (normalized := value.replace("\\", "/").rstrip("/").casefold())
            == prefix or normalized.startswith(f"{prefix}/")
            for prefix in prefixes
        )
    ]


_isolate_direct_entry_imports()

import argparse
import os
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        Path(tempfile.gettempdir()) / f"agentos-pycache-{os.urandom(16).hex()}"
    )
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.append(str(Path(__file__).resolve().parent))

if __package__:
    from .benchmark_source_contract import (
        _depth_at, _function_tokens, _lex, _locations, _require_once,
        _require_top_level,
    )
else:
    from benchmark_source_contract import (
        _depth_at, _function_tokens, _lex, _locations, _require_once,
        _require_top_level,
    )


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_VERSION = "scenario-timing-source-v9"
SOURCE_PATHS = (
    "baseline_ucore/user/src/rp_seed_orch.c",
    "user/src/rp_agentos_orch.c",
    "user/src/rp_orch.c",
    "user/src/rp_resource_probe.c",
    "user/include/rp_resource_stability.h",
    "user/include/exec_policy_manifest.h",
    "agent_lifecycle_abi.h",
    "os/agent_lifecycle.c",
    "agent_performance_abi.h",
    "agent_resource_abi.h",
    "os/agent_resource.c",
    "os/performance_stats.c",
    "os/performance_stats.h",
    "os/agent_identity.c",
    "os/exec_policy.c",
    "os/bio.c",
    "os/virtio_disk.c",
    "os/fs_epoch.c",
    "os/vm.c",
    "os/loader.c",
    "os/resource_controller.c",
    "os/resource_controller.h",
    "os/syscall.c",
    "os/syscall_ids.h",
    "user/lib/arch/riscv/syscall_ids.h.in",
    "user/lib/syscall_ids.h",
    "baseline_ucore/user/lib/syscall.c",
    "user/lib/syscall.c",
    "user/src/labdemo_ucore.c",
)


def _tokens(text: str) -> list[str]:
    return _lex(text.replace("\\\n", " "))


def _ordered(body: list[str], sequences: tuple[tuple[str, ...], ...], label: str) -> None:
    positions = [_require_once(body, sequence, f"{label} step") for sequence in sequences]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ValueError(f"{label} steps are not in production order")


def _only_references(
    body: list[str], name: str, expected: int, label: str
) -> None:
    observed = sum(token == name for token in body)
    if observed != expected:
        raise ValueError(f"{label} has {observed} references, expected {expected}")


def _validate_snapshot_dispatch(
    syscall_source: str, syscall_id: str, callee: str, label: str
) -> None:
    """校验一个 observer 用例，不复制调度器布局。"""

    dispatch = _function_tokens(_tokens(syscall_source), "syscall_dispatch")
    case_at = _require_once(
        dispatch, ("case", syscall_id, ":"), f"{label} case"
    )
    case_depth = _depth_at(dispatch, case_at)
    case_end = next(
        (
            position
            for position in range(case_at + 3, len(dispatch))
            if dispatch[position] in {"case", "default"}
            and _depth_at(dispatch, position) == case_depth
        ),
        len(dispatch),
    )
    case_body = dispatch[case_at + 3:case_end]
    call_at = _require_once(
        case_body,
        (
            "ret", "=", callee, "(", "trapframe", "->", "a0", ",",
            "trapframe", "->", "a1", ")", ";",
        ),
        f"{label} call",
    )
    break_at = _require_once(case_body, ("break", ";"), f"{label} break")
    if call_at >= break_at:
        raise ValueError(f"{label} does not return through its own case")
    if _depth_at(case_body, call_at) != 0:
        raise ValueError(f"{label} call is conditional or nested")
    if _depth_at(case_body, break_at) != 0:
        raise ValueError(f"{label} break is conditional or nested")


def _validate_clock_source(text: str, label: str) -> None:
    tokens = _tokens(text)
    get_mtime = _function_tokens(tokens, "get_mtime")
    _require_once(
        get_mtime,
        ("int", "err", "=", "sys_get_time", "(", "&", "time", ",", "0", ")", ";"),
        f"{label} get_mtime syscall",
    )
    _require_once(
        get_mtime,
        (
            "return", "(", "time", ".", "sec", "*", "1000", "+",
            "time", ".", "usec", "/", "1000", ")", ";",
        ),
        f"{label} millisecond conversion",
    )
    _only_references(get_mtime, "time", 4, f"{label} clock sample")
    sys_get_time = _function_tokens(tokens, "sys_get_time")
    _require_top_level(
        sys_get_time,
        (
            "return", "syscall", "(", "SYS_gettimeofday", ",", "ts", ",",
            "tz", ")", ";",
        ),
        f"{label} clock syscall binding",
    )


def _validate_plain_timing(text: str) -> None:
    tokens = _tokens(text)
    main = _function_tokens(tokens, "main")
    loop = (
        "for", "(", "int", "i", "=", "0", ";", "i", "<", "total", ";",
        "i", "++", ")", "{",
    )
    _ordered(
        main,
        (
            ("workflow_start", "=", "get_mtime", "(", ")", ";"),
            ("steady_start", "=", "get_mtime", "(", ")", ";"),
            loop,
            ("ok", "+", "=", "run_child", "(", "PROGRAMS", "[", "i", "]", ")", ";"),
            ("append_program_inventory_evidence", "(", ")"),
            (
                "record_workflow_timing", "(", "workflow_start", ",",
                "steady_start", ")",
            ),
        ),
        "plain workflow window",
    )
    loop_at = _require_once(main, loop, "plain production loop")
    if _depth_at(main, loop_at) != 0:
        raise ValueError("plain production loop is not top level")
    _only_references(main, "workflow_start", 3, "plain workflow start")
    _only_references(main, "steady_start", 3, "plain steady start")

    record = _function_tokens(tokens, "record_workflow_timing")
    end_sample = (
        "int64", "workflow_end", "=", "get_mtime", "(", ")", ";",
    )
    elapsed = (
        "workflow_elapsed", "=", "(", "unsigned", "long", "long", ")", "(",
        "workflow_end", "-", "workflow_start", ")", ";",
    )
    serialize = (
        "rp_append_uint_text", "(", "line", ",", "sizeof", "(", "line", ")",
        ",", "workflow_elapsed", ")", ";",
    )
    write = ("return", "rp_write_file", "(", '"rp_workflow_timing"', ",", "line", ")", ";")
    _ordered(record, (end_sample, elapsed, serialize, write), "plain timing receipt")
    _require_top_level(record, end_sample, "plain workflow end clock")
    _require_top_level(record, elapsed, "plain raw makespan")
    _require_top_level(record, serialize, "plain direct makespan serialization")
    _require_top_level(
        record,
        (
            "if", "(", "workflow_start", "<", "0", "||", "steady_start",
            "<", "workflow_start", "||", "workflow_end", "<",
            "steady_start", ")", "return", "0", ";",
        ),
        "plain timing clock-order guard",
    )
    write_at = _require_top_level(record, write, "plain timing receipt write")
    if record[write_at:] != list(write):
        raise ValueError("plain timing receipt write must be the final statement")
    if record.count("return") != 2 or any(
        token in {"goto", "break", "continue"} for token in record
    ):
        raise ValueError("plain timing receipt control flow differs")
    _only_references(record, "workflow_elapsed", 3, "plain makespan")
    _only_references(record, "workflow_end", 5, "plain workflow end")
    _only_references(record, "workflow_start", 5, "plain receipt start")
    _only_references(record, "steady_start", 6, "plain receipt steady start")


def _validate_agentos_timing(text: str) -> None:
    tokens = _tokens(text)
    main = _function_tokens(tokens, "main")
    start = ("int64", "workflow_start", "=", "get_mtime", "(", ")", ";")
    create = ("int", "pid", "=", "agent_create_role", "(", "AGENT_ROLE_ORCHESTRATOR", ")", ";")
    child = (
        "return", "run_research_orchestrator", "(", "timing_pipe", "[", "0", "]",
        ",", "timing_pipe", "[", "1", "]", ",", "completion_pipe", "[", "1",
        "]", ",", "(", "uint64", ")", "workflow_start", ")", ";",
    )
    wait = ("int", "got", "=", "waitpid", "(", "pid", ",", "&", "code", ")", ";")
    completion = ("read_workflow_completion", "(", "completion_pipe", "[", "0", "]", ",", "&", "completion", ")")
    final_state = ("rp_file_contains", "(", '"rp_agentos_acceptance"', ",")
    end = ("uint64", "workflow_end", "=", "get_mtime", "(", ")", ";")
    receipt = ("record_workflow_timing", "(", "&", "completion", ",", "workflow_end", ")")
    stability = ("run_resource_stability_acceptance", "(", ")")
    _ordered(
        main,
        (start, create, child, wait, completion, final_state, end, receipt, stability),
        "AgentOS workflow window",
    )
    _require_top_level(main, start, "AgentOS workflow start clock")
    _require_top_level(main, create, "AgentOS production launcher")
    _require_top_level(main, wait, "AgentOS workflow completion wait")
    _require_top_level(main, end, "AgentOS workflow end clock")
    _require_top_level(main, stability, "post-makespan resource acceptance")
    _only_references(main, "workflow_end", 3, "AgentOS workflow end")
    _only_references(main, "workflow_start", 4, "AgentOS workflow start")

    record = _function_tokens(tokens, "record_workflow_timing")
    elapsed = (
        "workflow_elapsed", "=", "workflow_end", "-", "record", "->",
        "start_ms", ";",
    )
    serialize = (
        "rp_append_uint_text", "(", "line", ",", "sizeof", "(", "line", ")",
        ",", "workflow_elapsed", ")", ";",
    )
    write = ("return", "rp_write_file", "(", '"rp_workflow_timing"', ",", "line", ")", ";")
    _ordered(record, (elapsed, serialize, write), "AgentOS timing receipt")
    _require_top_level(record, elapsed, "AgentOS raw makespan")
    _require_top_level(record, serialize, "AgentOS direct makespan serialization")
    _require_top_level(
        record,
        (
            "if", "(", "workflow_end", "<", "record", "->",
            "steady_start_ms", ")", "return", "0", ";",
        ),
        "AgentOS timing clock-order guard",
    )
    write_at = _require_top_level(record, write, "AgentOS timing receipt write")
    if record[write_at:] != list(write):
        raise ValueError("AgentOS timing receipt write must be the final statement")
    if record.count("return") != 2 or any(
        token in {"goto", "break", "continue"} for token in record
    ):
        raise ValueError("AgentOS timing receipt control flow differs")
    _only_references(record, "workflow_elapsed", 3, "AgentOS makespan")
    _only_references(record, "workflow_end", 4, "AgentOS receipt end")

    handoff_writer = _function_tokens(tokens, "write_workflow_handoff")
    start_assignment = ("record", ".", "start_ms", "=", "start_ms", ";")
    ready_assignment = ("record", ".", "ready_ms", "=", "ready_ms", ";")
    handoff_guard = (
        "record", ".", "guard", "=", "workflow_handoff_guard", "(",
        "&", "record", ")", ";",
    )
    _ordered(
        handoff_writer,
        (start_assignment, ready_assignment, handoff_guard),
        "AgentOS timing handoff writer",
    )
    _require_top_level(
        handoff_writer, start_assignment, "AgentOS direct workflow start handoff"
    )
    _require_top_level(
        handoff_writer, ready_assignment, "AgentOS direct ready clock handoff"
    )

    orchestrator = _function_tokens(tokens, "run_research_orchestrator")
    ready = ("uint64", "ready_ms", "=", "get_mtime", "(", ")", ";")
    handoff = ("write_workflow_handoff", "(", "timing_write_fd", ",", "workflow_start", ",", "ready_ms", ",")
    execute = ("exec", "(", '"rp_orch"', ",", "argv", ")")
    _ordered(orchestrator, (ready, handoff, execute), "AgentOS delegated handoff")
    _only_references(
        orchestrator, "workflow_start", 2, "AgentOS delegated workflow start"
    )
    _only_references(orchestrator, "ready_ms", 3, "AgentOS delegated ready clock")


def _validate_delegated_workflow(text: str) -> None:
    tokens = _tokens(text)
    main = _function_tokens(tokens, "main")
    start = ("int64", "steady_clock", "=", "get_mtime", "(", ")", ";")
    loop = (
        "for", "(", "int", "i", "=", "0", ";", "i", "<", "total", ";",
        "i", "++", ")", "{",
    )
    production = (
        "ok", "+", "=", "run_child", "(", "&", "PROGRAMS", "[", "i", "]", ",",
        "in_orchestrator", ")", ";",
    )
    inventory = ("append_program_inventory_evidence", "(", ")")
    completion = (
        "write_workflow_completion", "(", "completion_fd", ",", "&",
        "timing_handoff", ",", "(", "uint64", ")", "steady_clock", ")",
    )
    _ordered(
        main, (start, loop, production, inventory, completion),
        "delegated workflow steady window",
    )
    _require_top_level(main, start, "delegated workflow start clock")
    _only_references(main, "steady_clock", 7, "delegated steady clock")
    _only_references(main, "timing_handoff", 7, "delegated timing handoff")

    completion_writer = _function_tokens(tokens, "write_workflow_completion")
    steady_assignment = (
        "record", ".", "steady_start_ms", "=", "steady_start_ms", ";",
    )
    completion_guard = (
        "record", ".", "guard", "=", "workflow_handoff_guard", "(",
        "&", "record", ")", ";",
    )
    _ordered(
        completion_writer,
        (steady_assignment, completion_guard),
        "delegated workflow completion writer",
    )
    _require_top_level(
        completion_writer,
        steady_assignment,
        "delegated direct steady clock handoff",
    )
    _only_references(
        completion_writer, "steady_start_ms", 2,
        "delegated completion steady clock",
    )


def _validate_resource_stability(
    agentos_text: str, probe_text: str, header: str, manifest: str
) -> None:
    for token in (
        "RP_RESOURCE_STABILITY_VERSION 2U",
        "RP_RESOURCE_STABILITY_REPORT_SIZE 224U",
        "RP_RESOURCE_STABILITY_LOAD_WORKFLOWS 4U",
        "RP_RESOURCE_STABILITY_TERMINAL_WORKFLOWS 1U",
        "RP_RESOURCE_STABILITY_CHILD_ROUNDS 12U",
        "RP_RESOURCE_STABILITY_MEMORY_PAGES 128U",
        "RP_RESOURCE_STABILITY_FILE_OBJECTS 12U",
        "RP_RESOURCE_STABILITY_METADATA_OPS 3U",
        "RP_RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND 1U",
        "RP_RESOURCE_STABILITY_FS_BLOCK_GROWTH_BOUND 32U",
        "RP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND 16U",
        "RP_RESOURCE_STABILITY_NONCE_PREFIX \"--rp-stability-nonce=\"",
        "unsigned long long challenge_nonce;",
        "unsigned int resource_account_slot;",
        "unsigned long long resource_account_generation;",
        "sizeof(struct rp_resource_stability_report) ==",
        "RP_RESOURCE_STABILITY_REPORT_SIZE",
    ):
        if token not in header:
            raise ValueError(f"resource stability policy differs: {token}")

    header_tokens = _tokens(header)
    nonce = _function_tokens(header_tokens, "rp_resource_stability_nonce")
    _ordered(
        nonce,
        (
            ("rp_resource_stability_mix", "(", "hash", ",", "RP_RESOURCE_STABILITY_MAGIC", ")"),
            ("rp_resource_stability_mix", "(", "hash", ",", "RP_RESOURCE_STABILITY_VERSION", ")"),
            ("rp_resource_stability_mix", "(", "hash", ",", "challenge_request_id", ")"),
            ("rp_resource_stability_mix", "(", "hash", ",", "workflow_index", ")"),
            ("rp_resource_stability_mix", "(", "hash", ",", "mode", ")"),
            ("return", "hash", "!=", "0", "?", "hash", ":", "1", ";"),
        ),
        "challenge-derived resource nonce",
    )
    guard = _function_tokens(header_tokens, "rp_resource_stability_guard")
    for field in (
        "challenge_nonce", "resource_account_slot",
        "resource_account_generation",
    ):
        _require_once(
            guard,
            ("RP_RESOURCE_STABILITY_GUARD_FIELD", "(", field, ")", ";"),
            f"resource report {field} guard binding",
        )

    manifest_tokens = _tokens(manifest)
    _require_once(
        manifest_tokens,
        (
            "X", "(", '"rp_resource_probe"', ",", '"rp_resprobe"', ",",
            "EXEC_MANIFEST_F_SEALED", ",", "EXEC_MANIFEST_ROLE_BIT", "(",
            "EXEC_MANIFEST_ROLE_ORCHESTRATOR", ")", "|",
            "EXEC_MANIFEST_ROLE_BIT", "(", "EXEC_MANIFEST_ROLE_ARTIFACT", ")",
            ",", "0", ",", "EXEC_MANIFEST_VFS_PROFILE_WORKFLOW", ")",
        ),
        "resource stability trusted role binding",
    )

    agentos_tokens = _tokens(agentos_text)
    resource_growth_registry = (
        "static", "const", "uint64", "orch_resource_growth_bounds", "[",
        "AGENT_RESOURCE_KIND_COUNT", "]", "=", "{",
        "[", "AGENT_RESOURCE_FS_BLOCK", "]", "=",
        "RP_RESOURCE_STABILITY_FS_BLOCK_GROWTH_BOUND", ",",
        "[", "AGENT_RESOURCE_BUFFER_CACHE", "]", "=",
        "RP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND", ",", "}", ";",
    )
    _require_once(
        agentos_tokens,
        resource_growth_registry,
        "resource growth-bound registry",
    )
    _only_references(
        agentos_tokens, "orch_resource_growth_bounds", 5,
        "resource growth-bound registry",
    )
    launch = _function_tokens(agentos_tokens, "run_stability_workflow")
    _ordered(
        launch,
        (
            (
                "challenge_nonce", "=", "rp_resource_stability_nonce", "(",
                "orch_stability_workflow", ".", "request_id", ",", "index",
                ",", "mode", ")", ";",
            ),
            ("agent_resource_snapshot", "(", "global_before", ")"),
            ("admission_start", "=", "get_mtime", "(", ")", ";"),
            (
                "admission_deadline", "=", "admission_start", "<", "0", "?", "-", "1", ":",
                "admission_start", "+", "RP_RESOURCE_STABILITY_ADMISSION_TIMEOUT_MS", ";",
            ),
            ("agent_scope_delegate_fd", "(", "report_pipe", "[", "1", "]", ")"),
            ("agent_workflow_create", "(", "AGENT_ROLE_ORCHESTRATOR", ")"),
            ("get_mtime", "(", ")", ">=", "admission_deadline"),
            ("sleep", "(", "1", ")", "<", "0"),
            ("rp_copy_text", "(", "nonce_arg", ",", "sizeof", "(", "nonce_arg", ")", ",", "RP_RESOURCE_STABILITY_NONCE_PREFIX", ")"),
            ("rp_append_uint_text", "(", "nonce_arg", ",", "sizeof", "(", "nonce_arg", ")", ",", "challenge_nonce", ")"),
            ("nonce_arg", ",", "0", ","),
            ("exec", "(", '"rp_resprobe"', ",", "argv", ")"),
            ("read_stability_report", "(", "report_pipe", "[", "0", "]", ",", "report", ")"),
            ("read", "(", "report_pipe", "[", "0", "]", ",", "&", "extra", ",", "1", ")"),
            ("waitpid", "(", "pid", ",", "&", "code", ")"),
            ("agent_resource_snapshot", "(", "global_after", ")"),
            ("stability_report_valid", "(", "report", ",", "index", ",", "mode", ",", "challenge_nonce", ")"),
            ("stability_identity_unique", "(", "index", "+", "1", ")"),
            ("stability_global_pair_valid", "(", "global_before", ",", "global_after", ",", "mode", ")"),
        ),
        "resource stability workflow replacement",
    )
    acceptance = _function_tokens(
        agentos_tokens, "run_resource_stability_acceptance"
    )
    _ordered(
        acceptance,
        (
            ("load_challenge_oracle", "(", "&", "orch_stability_workflow", ")"),
            ("run_stability_workflow", "(", "index", ",", "mode", ")"),
            ("stability_global_sequence_valid", "(", ")"),
            ("append_stability_global_policy", "(", "body", ",", "measured_mask", ")"),
            ("append_stability_report", "(", "body", ",", "&", "orch_stability_reports", "[", "index", "]", ","),
            ("rp_write_file", "(", '"rp_resource_stability"', ",", "body", ")"),
        ),
        "resource stability acceptance receipt",
    )

    report_valid = _function_tokens(agentos_tokens, "stability_report_valid")
    _ordered(
        report_valid,
        (
            (
                "expected_observations", "=", "(", "uint64", ")",
                "expected_rounds", "*",
                "RP_RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND", ";",
            ),
            (
                "call_delta", "==", "expected_observations", "&&",
                "context_delta", "==", "expected_observations",
            ),
        ),
        "bounded resource-probe observation footprint",
    )

    probe_tokens = _tokens(probe_text)
    probe_snapshot = _function_tokens(probe_tokens, "snapshot_state")
    _ordered(
        probe_snapshot,
        (
            ("agent_workflow_lifecycle_info", "(", "lifecycle", ",", "0", ")"),
            ("lifecycle", "->", "version", "==", "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION"),
            ("lifecycle", "->", "struct_size", "==", "sizeof", "(", "*", "lifecycle", ")"),
            ("lifecycle", "->", "resource_account_valid", "==", "1"),
            ("lifecycle", "->", "resource_account_generation", "!=", "0"),
            ("io_policy_info", "(", "io", ")"),
        ),
        "self resource-account identity snapshot",
    )
    final_state = _function_tokens(probe_tokens, "final_state_mismatch")
    for sequence in (
        (
            "final_lifecycle", ".", "resource_account_slot", "!=",
            "initial_lifecycle", ".", "resource_account_slot",
        ),
        (
            "final_lifecycle", ".", "resource_account_generation", "!=",
            "initial_lifecycle", ".", "resource_account_generation",
        ),
    ):
        _require_once(final_state, sequence, "stable self resource-account identity")
    for sequence in (
        (
            "expected_observations", "=", "(", "uint64", ")",
            "expected_rounds", "*",
            "RP_RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND", ";",
        ),
        (
            "final_agent", ".", "agent_call_count", "!=",
            "initial_agent", ".", "agent_call_count", "+",
            "expected_observations",
        ),
        (
            "final_agent", ".", "context_path_count", "!=",
            "initial_agent", ".", "context_path_count", "+",
            "expected_observations",
        ),
    ):
        _require_once(final_state, sequence, "bounded observation growth")
    child = _function_tokens(probe_tokens, "transient_resource_child")
    _ordered(
        child,
        (
            ("sbrk", "(", "RP_RESOURCE_STABILITY_MEMORY_PAGES", "*", "PAGE_BYTES", ")"),
            ("challenge_nonce", ">>", "(", "(", "page", "&", "7U", ")", "*", "8U", ")"),
            ("challenge_nonce", ">>", "(", "(", "byte", "&", "7U", ")", "*", "8U", ")"),
            ("challenge_nonce", ">>", "(", "index", "*", "4U", ")"),
            ("pipe", "(", "pipes", "[", "index", "]", ")"),
            ("open", "(", "names", "[", "index", "]", ",", "O_CREATE", "|", "O_WRONLY", "|", "O_TRUNC", ")"),
            ("write", "(", "files", "[", "index", "]", ",", "resource_file_data"),
            ("unlink", "(", "names", "[", "index", "]", ")"),
        ),
        "transient resource exit workload",
    )
    _only_references(child, "sbrk", 1, "exit-owned heap allocation")
    _only_references(child, "close", 0, "exit-owned file cleanup")
    metadata = _function_tokens(probe_tokens, "run_metadata_round")
    _ordered(
        metadata,
        (
            ("challenge_nonce", "%", "1000ULL"),
            ("challenge_nonce", ">>", "60"),
            ("challenge_nonce", ">>", "(", "(", "7U", "-", "index", ")", "*", "4U", ")"),
            ("open", "(", "name", ",", "O_CREATE", "|", "O_WRONLY", "|", "O_TRUNC", ")"),
            ("if", "(", "agent_file_meta_set", "(", "&", "metadata_file", ")", "!=", "AGENT_STATUS_OK", ")"),
            ("agent_file_query", "(", "&", "metadata_query", ",", "&", "metadata_query_result", ")"),
            ("metadata_file", ".", "flags", "=", "AGENT_FILE_META_F_DELETE", ";"),
            ("return", "agent_file_meta_set", "(", "&", "metadata_file", ")"),
            ("unlink", "(", "name", ")"),
        ),
        "durable metadata lifecycle round",
    )
    _only_references(metadata, "agent_file_meta_set", 2, "metadata mutation")
    _only_references(metadata, "agent_file_query", 1, "metadata query")
    _only_references(metadata, "agent_run", 0, "metadata echo substitute")
    positive_delta = _function_tokens(
        agentos_tokens, "stability_positive_delta"
    )
    expected_positive_delta = (
        "return", "after", ">", "before", "?", "after", "-",
        "before", ":", "0", ";",
    )
    if tuple(positive_delta) != expected_positive_delta:
        raise ValueError("class-local positive resource delta is not exact")
    global_pair = _function_tokens(agentos_tokens, "stability_global_pair_valid")
    class_growth = (
        "uint64", "positive_growth", "=",
        "stability_positive_delta", "(", "left", "->", "ordinary_used",
        ",", "right", "->", "ordinary_used", ")", "+",
        "stability_positive_delta", "(", "left", "->", "reserved_used",
        ",", "right", "->", "reserved_used", ")", ";",
    )
    _require_once(
        global_pair,
        class_growth,
        "per-workflow summed class-positive growth",
    )
    _require_once(
        global_pair,
        (
            "uint64", "bound", "=", "mode", "==",
            "RP_RESOURCE_STABILITY_MODE_TERMINAL", "?", "0", ":",
            "orch_resource_growth_bounds", "[", "kind", "]", ";",
        ),
        "per-workflow registered growth bound",
    )
    _only_references(global_pair, "positive_growth", 2, "per-workflow growth value")
    _only_references(global_pair, "bound", 4, "per-workflow growth bound")
    _only_references(
        global_pair, "stability_positive_delta", 2,
        "per-workflow class delta helper",
    )
    for sequence in (
        ("before", "->", "ordinary_free_pages", "!=", "after", "->", "ordinary_free_pages"),
        ("left", "->", "ordinary_pending", "!=", "0"),
        ("right", "->", "ordinary_pending", "!=", "0"),
        ("left", "->", "reserved_pending", "!=", "0"),
        ("right", "->", "reserved_pending", "!=", "0"),
        ("left", "->", "ordinary_used", "!=", "right", "->", "ordinary_used"),
        ("left", "->", "reserved_used", "!=", "right", "->", "reserved_used"),
        ("positive_growth", ">", "bound", ")"),
    ):
        _require_once(global_pair, sequence, "per-workflow class reclamation bound")
    for charge_class in ("ordinary", "reserved"):
        _require_once(
            global_pair,
            (
                "stability_positive_delta", "(", "left", "->",
                f"{charge_class}_used", ",", "right", "->",
                f"{charge_class}_used", ")",
            ),
            f"per-workflow {charge_class} positive growth",
        )

    identity = _function_tokens(agentos_tokens, "stability_identity_unique")
    for sequence in (
        ("prior", "->", "lifecycle_id", "==", "latest", "->", "lifecycle_id"),
        ("prior", "->", "lifecycle_generation", "==", "latest", "->", "lifecycle_generation"),
        ("prior", "->", "scope_id", "==", "latest", "->", "scope_id"),
        ("prior", "->", "io_owner", "==", "latest", "->", "io_owner"),
        ("prior", "->", "resource_account_slot", "==", "latest", "->", "resource_account_slot"),
        ("prior", "->", "resource_account_generation", "==", "latest", "->", "resource_account_generation"),
    ):
        _require_once(identity, sequence, "fresh workflow identity")

    global_sequence = _function_tokens(
        agentos_tokens, "stability_global_sequence_valid"
    )
    _require_once(
        global_sequence,
        class_growth,
        "whole-sequence summed class-positive growth",
    )
    _require_once(
        global_sequence,
        (
            "uint64", "bound", "=", "orch_resource_growth_bounds", "[",
            "kind", "]", ";",
        ),
        "whole-sequence registered growth bound",
    )
    _only_references(global_sequence, "positive_growth", 2, "sequence growth value")
    _only_references(global_sequence, "bound", 5, "sequence growth bound")
    _only_references(
        global_sequence, "stability_positive_delta", 2,
        "whole-sequence class delta helper",
    )
    for sequence in (
        ("&", "orch_stability_global_before", "[", "0", "]"),
        (
            "&", "orch_stability_global_after", "[",
            "RP_RESOURCE_STABILITY_WORKFLOWS", "-", "1", "]",
        ),
        ("first", "->", "ordinary_free_pages", "!=", "terminal", "->", "ordinary_free_pages"),
        ("left", "->", "ordinary_pending", "!=", "0"),
        ("right", "->", "reserved_pending", "!=", "0"),
        ("positive_growth", ">", "bound", ")"),
        (
            "index", "<", "RP_RESOURCE_STABILITY_LOAD_WORKFLOWS",
        ),
        ("current", "->", "used", "<=", "prior", "->", "used"),
        ("right", "->", "used", "<", "last_load", "->", "used"),
        ("if", "(", "!", "plateau", ")", "return", "0", ";"),
    ):
        _require_once(global_sequence, sequence, "whole-sequence terminal bound")
    for charge_class in ("ordinary", "reserved"):
        _require_once(
            global_sequence,
            (
                "stability_positive_delta", "(", "left", "->",
                f"{charge_class}_used", ",", "right", "->",
                f"{charge_class}_used", ")",
            ),
            f"whole-sequence {charge_class} positive growth",
        )

    append_report = _function_tokens(agentos_tokens, "append_stability_report")
    for field in (
        "challenge_nonce=", "resource_account_slot=",
        "resource_account_generation=", "_ordinary_used_before=",
        "_ordinary_used_after=", "_ordinary_pending_before=",
        "_ordinary_pending_after=", "_reserved_used_before=",
        "_reserved_used_after=", "_reserved_pending_before=",
        "_reserved_pending_after=", "report_guard=",
    ):
        literal = f'"{field}"' if field.startswith("_") else f'";{field}"'
        _require_once(
            append_report, (literal,),
            f"serialized resource evidence {field}",
        )
    for side in ("before", "after"):
        for member in (
            "ordinary_used", "ordinary_pending",
            "reserved_used", "reserved_pending",
        ):
            _require_once(
                append_report,
                (
                    "rp_append_uint_text", "(", "body", ",", "sizeof", "(",
                    "orch_stability_body", ")", ",", side, "->", member,
                    ")", ";",
                ),
                f"resource {side} {member} value serialization",
            )
    for boundary in (
        "schema=agentos_resource_stability_v5",
        "claim_scope=configured_global_counter_reclamation",
        "configured_kind_coverage=measured_mask_only",
        "account_coverage=self_identity_only",
        "measured_mask_semantics=configured_global_resource_kind_counters_only",
        "coverage=configured_global_kind_counters",
        "account_counter_coverage=not_measured",
        "rate_budget_coverage=not_measured",
        "snapshot_consistency=single_core_irq_coherent",
        "growth_bound_semantics=per_class_positive_delta_sum",
        "decrease_semantics=reclamation_allowed",
        "global_leak_freedom=not_claimed",
    ):
        if boundary not in agentos_text:
            raise ValueError(f"resource acceptance boundary differs: {boundary}")
    for overclaim in (
        "global_leak_freedom=verified",
        "account_counter_coverage=measured",
        "rate_budget_coverage=measured",
        "measured_mask_semantics=all_resources",
    ):
        if overclaim in agentos_text:
            raise ValueError(f"resource acceptance overclaims coverage: {overclaim}")
    global_policy = _function_tokens(
        agentos_tokens, "append_stability_global_policy"
    )
    for suffix in (
        '"_per_workflow_growth_bound="',
        '"_terminal_growth_bound="',
    ):
        _require_once(
            global_policy, (suffix,), "resource growth-bound serialization"
        )
    child_round = _function_tokens(probe_tokens, "run_child_round")
    _ordered(
        child_round,
        (
            ("agent_create_role", "(", "AGENT_ROLE_ARTIFACT", ")"),
            ("transient_resource_child", "(", "workflow_index", ",", "round", ",", "challenge_nonce", ")"),
            ("waitpid", "(", "pid", ",", "&", "status", ")"),
        ),
        "process resource reuse round",
    )
    main = _function_tokens(probe_tokens, "main")
    _ordered(
        main,
        (
            ("parse_u64_argument", "(", "argv", "[", "4", "]", ",", "RP_RESOURCE_STABILITY_NONCE_PREFIX", ",", "&", "challenge_nonce", ")"),
            ("snapshot_state", "(", "&", "initial_lifecycle", ",", "&", "initial_io", ",", "&", "initial_agent", ")"),
            (
                "!", "initial_state_is_fresh", "(", ")", ")", "{",
                "printf", "(",
                '"rp_resource_probe: initial_state_not_fresh\\n"', ")", ";",
                "return", "1", ";", "}",
            ),
            ("run_child_round", "(", "workflow_index", ",", "round", ",", "challenge_nonce", ")"),
            ("run_metadata_round", "(", "workflow_index", ",", "round", ",", "challenge_nonce", ")"),
            ("snapshot_state", "(", "&", "final_lifecycle", ",", "&", "final_io", ",", "&", "final_agent", ")"),
            (
                "mismatch", "=", "final_state_mismatch", "(",
                "expected_rounds", ",", "mode", ")", ";",
            ),
            (
                "if", "(", "mismatch", "!=", "0", ")", "{",
                "printf", "(",
                '"rp_resource_probe: final_state_mismatch mask=%u calls=%llu/%llu context=%llu/%llu completion=%llu/%llu\\n"',
            ),
            (
                "final_io", ".", "completion_sequence", ")", ";",
                "return", "1", ";", "}",
            ),
            ("report", ".", "challenge_nonce", "=", "challenge_nonce", ";"),
            ("report", ".", "resource_account_slot", "=", "initial_lifecycle", ".", "resource_account_slot", ";"),
            ("report", ".", "resource_account_generation", "=", "initial_lifecycle", ".", "resource_account_generation", ";"),
            ("report", ".", "guard", "=", "rp_resource_stability_guard", "(", "&", "report", ")", ";"),
            ("write_exact", "(", "(", "int", ")", "report_fd", ",", "&", "report", ",", "sizeof", "(", "report", ")", ")"),
        ),
        "resource stability Guest receipt",
    )


def _validate_resource_observer(
    abi: str,
    observer: str,
    controller: str,
    controller_header: str,
    syscall_source: str,
    kernel_ids: str,
    user_ids: str,
    user_syscall: str,
) -> None:
    for declaration in (
        "#define AGENT_RESOURCE_SNAPSHOT_VERSION 1U",
        "#define AGENT_RESOURCE_KIND_COUNT 8U",
        "struct agent_resource_kind_snapshot",
        "struct agent_resource_snapshot",
        "sizeof(struct agent_resource_snapshot) == 488",
    ):
        if declaration not in abi:
            raise ValueError(f"resource snapshot ABI differs: {declaration}")

    observer_tokens = _tokens(observer)
    authorization = _function_tokens(
        observer_tokens, "agent_resource_snapshot_authorized"
    )
    _require_top_level(
        authorization,
        (
            "return", "p", "!=", "0", "&&", "!", "p", "->", "is_agent",
            "&&", "p", "->", "resource_domain_admin", "&&",
            "exec_policy_process_bootstrap", "(", "p", ")", ";",
        ),
        "resource observer bootstrap authorization",
    )
    snapshot = _function_tokens(observer_tokens, "sys_agent_resource_snapshot")
    _ordered(
        snapshot,
        (
            ("agent_resource_snapshot_authorized", "(", "p", ")"),
            ("user_range_check", "(", "p", "->", "pagetable", ",", "addr", ",", "copy_size", ",", "PTE_W", ")"),
            ("enabled", "=", "intr_save", "(", ")", ";"),
            ("resource_policy_snapshot_all", "(", "policies", ",", "RESOURCE_KIND_COUNT", ")"),
            ("kalloc_free_pages", "(", ")"),
            ("kalloc_physical_reserved_free_pages", "(", ")"),
            ("kalloc_stack_reserved_free_pages", "(", ")"),
            ("intr_restore", "(", "enabled", ")", ";"),
            ("copyout", "(", "p", "->", "pagetable", ",", "addr", ",", "(", "char", "*", ")", "&", "snapshot", ",", "copy_size", ")"),
        ),
        "single-core coherent global resource snapshot",
    )
    _only_references(snapshot, "intr_save", 1, "outer resource snapshot IRQ cut")
    _only_references(snapshot, "intr_restore", 1, "outer resource snapshot IRQ cut")
    for sequence in (
        ("enabled", "=", "intr_save", "(", ")", ";"),
        ("resource_policy_snapshot_all", "(", "policies", ",", "RESOURCE_KIND_COUNT", ")"),
        ("kalloc_free_pages", "(", ")"),
        ("kalloc_physical_reserved_free_pages", "(", ")"),
        ("kalloc_stack_reserved_free_pages", "(", ")"),
        ("intr_restore", "(", "enabled", ")", ";"),
    ):
        _require_top_level(snapshot, sequence, "coherent resource snapshot cut")
    for kind in (
        "PROCESS", "THREAD", "FILE_OBJECT", "FS_BLOCK", "FS_INODE",
        "BUFFER_CACHE", "AGENT_STATE_PAGE", "PHYSICAL_PAGE", "KIND_COUNT",
    ):
        if f"AGENT_RESOURCE_{kind}" not in observer or f"RESOURCE_{kind}" not in observer:
            raise ValueError(f"resource snapshot kind binding differs: {kind}")

    if "struct resource_policy_snapshot" not in controller_header:
        raise ValueError("resource controller snapshot interface is missing")
    controller_tokens = _tokens(controller)
    policy = _function_tokens(controller_tokens, "resource_policy_snapshot_all")
    _ordered(
        policy,
        (
            ("enabled", "=", "intr_save", "(", ")", ";"),
            ("snapshot", "->", "capacity", "=", "policy", "->", "capacity", ";"),
            ("snapshot", "->", "ordinary_used", "=", "policy", "->", "ordinary_used", ";"),
            ("snapshot", "->", "reserved_pending", "=", "policy", "->", "reserved_pending", ";"),
            ("intr_restore", "(", "enabled", ")", ";"),
        ),
        "one-lock resource controller snapshot",
    )

    for ids, label in ((kernel_ids, "kernel"), (user_ids, "AgentOS user")):
        if ids.count("agent_resource_snapshot 559") != 1:
            raise ValueError(f"{label} resource snapshot syscall ID differs")
    _validate_snapshot_dispatch(
        syscall_source,
        "SYS_agent_resource_snapshot",
        "sys_agent_resource_snapshot",
        "resource snapshot syscall dispatch",
    )
    wrapper = _function_tokens(_tokens(user_syscall), "agent_resource_snapshot")
    _require_top_level(
        wrapper,
        (
            "return", "syscall", "(", "SYS_agent_resource_snapshot", ",",
            "snapshot", ",", "sizeof", "(", "*", "snapshot", ")", ")", ";",
        ),
        "resource snapshot user ABI binding",
    )


def _validate_performance_observer(
    abi: str,
    observer: str,
    performance_source: str,
    performance_header: str,
    syscall_source: str,
    kernel_ids: str,
    arch_ids: str,
    user_ids: str,
    user_syscall: str,
) -> None:
    counter_fields = (
        "fs_epoch_commits",
        "fs_epoch_buffers_staged",
        "block_physical_writes",
        "block_physical_reads",
        "block_durable_flushes",
        "fs_epoch_deduplicated_stages",
        "cow_pages_shared",
        "cow_pages_copied",
        "cow_fault_promotions",
        "exec_cache_hits",
        "exec_cache_misses",
        "exec_cache_shared_pages",
        "exec_cache_evictions",
        "observer_workload_syscalls",
        "directory_block_probes",
        "directory_entries_examined",
        "virtio_notifications",
        "virtio_submitted_requests",
        "virtio_write_batch_calls",
        "virtio_batched_write_requests",
        "virtio_indirect_write_batch_calls",
        "virtio_read_batch_calls",
        "virtio_batched_read_requests",
        "overwrite_prereads_skipped",
    )
    for declaration in (
        "#define AGENT_PERFORMANCE_SNAPSHOT_VERSION 3U",
        "#define AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL 1U",
        "struct agent_performance_snapshot",
        "sizeof(struct agent_performance_snapshot) == 256",
        "unsigned long long observer_lifecycle_id;",
        "unsigned long long observer_lifecycle_generation;",
        *(f"unsigned long long {field};" for field in counter_fields),
    ):
        if declaration not in abi:
            raise ValueError(f"performance snapshot ABI differs: {declaration}")

    observer_tokens = _tokens(observer)
    authorization = _function_tokens(
        observer_tokens, "agent_performance_snapshot_authorized"
    )
    _require_top_level(
        authorization,
        (
            "return", "p", "!=", "0", "&&", "p", "->",
            "resource_domain_admin", "&&", "exec_policy_process_bootstrap",
            "(", "p", ")", ";",
        ),
        "performance observer signed bootstrap authorization",
    )
    if authorization.count("return") != 1 or any(
        token in {"if", "goto", "break", "continue"} for token in authorization
    ):
        raise ValueError("performance observer authorization control flow differs")

    snapshot = _function_tokens(observer_tokens, "sys_agent_performance_snapshot")
    _require_top_level(
        snapshot,
        (
            "if", "(", "!", "agent_performance_snapshot_authorized", "(",
            "p", ")", ")", "return", "AGENT_STATUS_DENIED", ";",
        ),
        "performance snapshot authorization guard",
    )
    _require_top_level(
        snapshot,
        (
            "if", "(", "user_size", "<", "2", "*", "sizeof", "(",
            "unsigned", "int", ")", ")", "return",
            "AGENT_STATUS_BAD_PARAM", ";",
        ),
        "performance snapshot sized-prefix minimum",
    )
    _require_top_level(
        snapshot,
        (
            "copy_size", "=", "MIN", "(", "user_size", ",", "sizeof",
            "(", "snapshot", ")", ")", ";",
        ),
        "performance snapshot bounded copy size",
    )
    _require_top_level(
        snapshot,
        (
            "if", "(", "user_range_check", "(", "p", "->", "pagetable",
            ",", "addr", ",", "copy_size", ",", "PTE_W", ")", "<", "0",
            ")", "return", "-", "1", ";",
        ),
        "performance snapshot writable range guard",
    )
    _require_top_level(
        snapshot,
        (
            "if", "(", "copyout", "(", "p", "->", "pagetable", ",",
            "addr", ",", "(", "char", "*", ")", "&", "snapshot", ",",
            "copy_size", ")", "<", "0", ")", "return", "-", "1", ";",
        ),
        "performance snapshot copyout guard",
    )
    _ordered(
        snapshot,
        (
            ("agent_performance_snapshot_authorized", "(", "p", ")"),
            (
                "user_range_check", "(", "p", "->", "pagetable", ",",
                "addr", ",", "copy_size", ",", "PTE_W", ")",
            ),
            (
                "memset", "(", "&", "snapshot", ",", "0", ",",
                "sizeof", "(", "snapshot", ")", ")",
            ),
            ("bio_physical_snapshot", "(", "&", "io", ")"),
            ("fs_epoch_stats_snapshot", "(", "&", "epoch", ")"),
            ("uvm_cow_stats_snapshot", "(", "&", "cow", ")"),
            (
                "user_image_rx_cache_stats_snapshot", "(", "&",
                "exec_cache", ")",
            ),
            ("kernel_performance_stats_snapshot", "(", "&", "kernel", ")"),
            (
                "snapshot", ".", "counter_scope", "=",
                "AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL", ";",
            ),
            (
                "copyout", "(", "p", "->", "pagetable", ",", "addr", ",",
                "(", "char", "*", ")", "&", "snapshot", ",", "copy_size",
                ")",
            ),
        ),
        "global performance snapshot",
    )
    for source, target in (
        ("epoch.successful_commits", "fs_epoch_commits"),
        ("epoch.staged_buffers", "fs_epoch_buffers_staged"),
        ("io.successful_writes", "block_physical_writes"),
        ("io.reads", "block_physical_reads"),
        ("io.successful_flushes", "block_durable_flushes"),
        ("epoch.deduplicated_stages", "fs_epoch_deduplicated_stages"),
        ("cow.cow_shared_mappings", "cow_pages_shared"),
        ("cow.cow_fault_copies", "cow_pages_copied"),
        ("cow.cow_fault_promotions", "cow_fault_promotions"),
        ("exec_cache.exec_cache_hits", "exec_cache_hits"),
        ("exec_cache.exec_cache_misses", "exec_cache_misses"),
        ("exec_cache.exec_cache_shared_pages", "exec_cache_shared_pages"),
        ("exec_cache.exec_cache_evictions", "exec_cache_evictions"),
        ("kernel.directory_block_probes", "directory_block_probes"),
        ("kernel.directory_entries_examined", "directory_entries_examined"),
        ("kernel.virtio_notifications", "virtio_notifications"),
        ("kernel.virtio_submitted_requests", "virtio_submitted_requests"),
        ("kernel.virtio_write_batch_calls", "virtio_write_batch_calls"),
        ("kernel.virtio_batched_write_requests", "virtio_batched_write_requests"),
        ("kernel.virtio_indirect_write_batch_calls", "virtio_indirect_write_batch_calls"),
        ("kernel.virtio_read_batch_calls", "virtio_read_batch_calls"),
        ("kernel.virtio_batched_read_requests", "virtio_batched_read_requests"),
        ("kernel.overwrite_prereads_skipped", "overwrite_prereads_skipped"),
    ):
        sequence = (
            "snapshot", ".", target, "=", *source.replace(".", " . ").split(), ";"
        )
        _require_top_level(snapshot, sequence, f"performance counter {target}")
    for sequence, label in (
        (
            ("snapshot", ".", "version", "=", "AGENT_PERFORMANCE_SNAPSHOT_VERSION", ";"),
            "performance snapshot version",
        ),
        (
            ("snapshot", ".", "struct_size", "=", "sizeof", "(", "snapshot", ")", ";"),
            "performance snapshot sized prefix",
        ),
        (
            (
                "snapshot", ".", "sample_tick", "=", "get_cycle", "(",
                ")", ";",
            ),
            "performance snapshot monotonic tick",
        ),
        (
            (
                "snapshot", ".", "observer_lifecycle_id", "=", "p", "->",
                "workflow_lifecycle_id", ";",
            ),
            "performance observer lifecycle label",
        ),
        (
            (
                "snapshot", ".", "observer_lifecycle_generation", "=", "p",
                "->", "workflow_lifecycle_generation", ";",
            ),
            "performance observer lifecycle generation",
        ),
    ):
        _require_top_level(snapshot, sequence, label)
    _require_top_level(
        snapshot,
        (
            "snapshot", ".", "observer_workload_syscalls", "=",
            "agent_performance_workload_syscalls", "(", "p", ")", ";",
        ),
        "observer workload syscall counter",
    )
    for declaration in (
        "KERNEL_PERFORMANCE_VIRTIO_SINGLE = 0",
        "KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH = 1",
        "KERNEL_PERFORMANCE_VIRTIO_READ_BATCH = 2",
        "void kernel_performance_overwrite_preread_skipped(uint);",
    ):
        if declaration not in performance_header:
            raise ValueError(f"performance stats interface differs: {declaration}")
    performance_tokens = _tokens(performance_source)
    preread = _function_tokens(
        performance_tokens, "kernel_performance_overwrite_preread_skipped"
    )
    _require_top_level(
        preread,
        (
            "performance_stats", ".", "overwrite_prereads_skipped", "+", "=",
            "blocks", ";",
        ),
        "overwrite preread counter",
    )
    success = ("return", "AGENT_STATUS_OK", ";")
    success_at = _require_top_level(
        snapshot, success, "performance snapshot success return"
    )
    if snapshot[success_at:] != list(success):
        raise ValueError("performance snapshot success must be the final statement")
    if any(token in {"goto", "break", "continue"} for token in snapshot):
        raise ValueError("performance snapshot contains an early control-flow escape")
    if snapshot.count("return") != 5:
        raise ValueError("performance snapshot return structure differs")

    for ids, label in ((kernel_ids, "kernel"), (user_ids, "AgentOS user")):
        if ids.count("agent_performance_snapshot 560") != 1:
            raise ValueError(f"{label} performance snapshot syscall ID differs")
    if arch_ids.count("__NR_agent_performance_snapshot 560") != 1:
        raise ValueError("RISC-V performance snapshot syscall ID differs")
    _validate_snapshot_dispatch(
        syscall_source,
        "SYS_agent_performance_snapshot",
        "sys_agent_performance_snapshot",
        "performance snapshot syscall dispatch",
    )
    wrapper = _function_tokens(_tokens(user_syscall), "agent_performance_snapshot")
    _require_top_level(
        wrapper,
        (
            "return", "syscall", "(", "SYS_agent_performance_snapshot", ",",
            "snapshot", ",", "sizeof", "(", "*", "snapshot", ")", ")", ";",
        ),
        "performance snapshot user ABI binding",
    )


def _validate_performance_producers(
    identity_source: str,
    exec_policy_source: str,
    bio_source: str,
    virtio_source: str,
    epoch_source: str,
    vm_source: str,
    loader_source: str,
) -> None:
    identity = _function_tokens(
        _tokens(identity_source), "agent_identity_authority_bootstrap"
    )
    _require_once(
        identity,
        ("if", "(", "exec_policy_process_bootstrap", "(", "p", ")", ")"),
        "signed bootstrap identity authority",
    )

    bootstrap = _function_tokens(
        _tokens(exec_policy_source), "exec_policy_process_bootstrap"
    )
    _require_top_level(
        bootstrap,
        (
            "if", "(", "p", "==", "0", "||", "!", "exec_policy_valid",
            "(", "p", "->", "exec_dev", ",", "p", "->", "exec_inum",
            ",", "p", "->", "exec_flags", ",", "p", "->",
            "exec_generation", ",", "p", "->", "exec_role_mask", ",",
            "p", "->", "exec_layout_version", ",", "p", "->",
            "exec_rw_offset", ")", ")", "return", "0", ";",
        ),
        "performance observer signed image validation",
    )
    _require_top_level(
        bootstrap,
        (
            "return", "(", "p", "->", "exec_flags", "&",
            "EXEC_FLAG_BOOTSTRAP", ")", "!=", "0", ";",
        ),
        "performance observer bootstrap flag",
    )

    bio_tokens = _tokens(bio_source)
    transfers = _function_tokens(bio_tokens, "bio_account_transfer_batch")
    input_guard = (
        "if", "(", "results", "==", "0", "||", "count", "==", "0",
        "||", "(", "transfer", "!=", "BIO_TRANSFER_READ", "&&",
        "transfer", "!=", "BIO_TRANSFER_WRITE", "&&", "transfer", "!=",
        "BIO_TRANSFER_FLUSH", ")", ")", "return", ";",
    )
    failed_scan = (
        "for", "(", "uint", "i", "=", "0", ";", "i", "<", "count",
        ";", "i", "++", ")", "if", "(", "results", "[", "i", "]",
        "<", "0", ")", "failed", "++", ";",
    )
    global_requests = (
        "if", "(", "transfer", "==", "BIO_TRANSFER_READ", ")",
        "io_policy", ".", "physical_reads", "+", "=", "count", ";",
        "else", "if", "(", "transfer", "==", "BIO_TRANSFER_WRITE", ")",
        "io_policy", ".", "physical_writes", "+", "=", "count", ";",
        "else", "io_policy", ".", "physical_flushes", "+", "=", "count",
        ";",
    )
    global_results = (
        "io_policy", ".", "failed_transfers", "+", "=", "failed", ";",
        "if", "(", "transfer", "==", "BIO_TRANSFER_WRITE", ")",
        "io_policy", ".", "successful_writes", "+", "=", "successful",
        ";", "else", "if", "(", "transfer", "==",
        "BIO_TRANSFER_FLUSH", ")", "io_policy", ".", "successful_flushes",
        "+", "=", "successful", ";",
    )
    owner_requests = (
        "if", "(", "transfer", "==", "BIO_TRANSFER_READ", ")", "state",
        "->", "physical_reads", "+", "=", "count", ";", "else", "if",
        "(", "transfer", "==", "BIO_TRANSFER_WRITE", ")", "state", "->",
        "physical_writes", "+", "=", "count", ";", "else", "state", "->",
        "physical_flushes", "+", "=", "count", ";",
    )
    _ordered(
        transfers,
        (
            input_guard,
            failed_scan,
            ("successful", "=", "count", "-", "failed", ";"),
            ("enabled", "=", "intr_save", "(", ")", ";"),
            global_requests,
            global_results,
            (
                "io_policy", ".", "completion_sequence", "+", "=", "count",
                ";",
            ),
            owner_requests,
            ("state", "->", "failed_transfers", "+", "=", "failed", ";"),
            (
                "state", "->", "completion_sequence", "=",
                "completion_sequence", ";",
            ),
        ),
        "batched physical transfer accounting",
    )
    for sequence, label in (
        (input_guard, "physical transfer input guard"),
        (failed_scan, "physical transfer failure scan"),
        (global_requests, "global physical request totals"),
        (global_results, "global physical outcome totals"),
        (owner_requests, "owner physical request totals"),
        (("state", "->", "failed_transfers", "+", "=", "failed", ";"),
         "owner physical failure totals"),
    ):
        _require_top_level(transfers, sequence, label)
    if transfers.count("for") != 1:
        raise ValueError("physical transfer batch has a per-request accounting loop")

    single_transfer = _function_tokens(bio_tokens, "bio_account_transfer")
    if single_transfer != [
        "bio_account_transfer_batch", "(", "owner", ",", "io_class", ",",
        "transfer", ",", "&", "result", ",", "1", ")", ";",
    ]:
        raise ValueError("single physical transfer is not a batch-of-one adapter")

    request_batch = (
        "*", "transfers", "+", "=", "count", ";", "if", "(", "*",
        "transfers", ">=", "IO_RATE_LOCAL_BATCH", ")", "{",
        "io_rate_charge_transfers", "(", "state", ",", "io_class", ",",
        "*", "transfers", ")", ";", "*", "transfers", "=", "0", ";",
        "}",
    )
    _require_once(
        transfers, request_batch, "request-local batched resource charge"
    )
    if transfers.count("io_rate_charge_transfers") != 3 or \
       transfers.count("io_rate_charge_transfer") != 2:
        raise ValueError("physical transfer batch resource-charge topology differs")

    rate_batch = _function_tokens(bio_tokens, "io_rate_charge_transfers")
    _ordered(
        rate_batch,
        (
            (
                "lane", "=", "io_rate_owner_refresh", "(", "state", ",",
                "io_class", ")", ";",
            ),
            (
                "reserved", "=", "MIN", "(", "amount", ",", "lane", "->",
                "tokens", ")", ";",
            ),
            (
                "io_rate_charge_pair", "(", "state", ",", "io_class", ",",
                "0", ",", "0", ",", "1", ",", "reserved", ")",
            ),
            ("state", "->", "reserved_grants", "+", "=", "reserved", ";"),
            ("amount", "-", "=", "reserved", ";"),
            (
                "capacity", "=", "io_rate_shared_refresh", "(", ")", ";",
            ),
            (
                "shared", "=", "MIN", "(", "amount", ",", "capacity", "->",
                "tokens", ")", ";",
            ),
            (
                "io_rate_charge_pair", "(", "state", ",", "io_class", ",",
                "1", ",", "0", ",", "0", ",", "shared", ")",
            ),
            ("state", "->", "shared_grants", "+", "=", "shared", ";"),
            ("amount", "-", "=", "shared", ";"),
            (
                "for", "(", "uint64", "i", "=", "0", ";", "i", "<",
                "amount", ";", "i", "++", ")",
            ),
            (
                "io_rate_charge_pair", "(", "state", ",", "io_class", ",",
                "0", ",", "1", ",", "1", ",", "1", ")",
            ),
            ("state", "->", "throttles", "+", "=", "amount", ";"),
        ),
        "BIO-local batched rate charge",
    )
    if rate_batch.count("io_rate_charge_pair") != 4:
        raise ValueError("BIO-local fast path no longer charges by bounded batches")

    producer_assignments = (
        "io_policy.physical_reads += count;",
        "io_policy.physical_writes += count;",
        "io_policy.physical_flushes += count;",
        "io_policy.failed_transfers += failed;",
        "io_policy.successful_writes += successful;",
        "io_policy.successful_flushes += successful;",
        "io_policy.completion_sequence += count;",
    )
    if any(bio_source.count(assignment) != 1 for assignment in producer_assignments):
        raise ValueError("global batched I/O counter producers differ")
    if bio_source.count("kernel_performance_overwrite_preread_skipped(1)") != 1:
        raise ValueError("overwrite preread skip counter must have one publish producer")
    bio_snapshot = _function_tokens(bio_tokens, "bio_physical_snapshot")
    for sequence, label in (
        (
            (
                "stats", "->", "reads", "=", "io_policy", ".",
                "physical_reads", ";",
            ),
            "physical read request snapshot",
        ),
        (
            (
                "stats", "->", "successful_writes", "=", "io_policy", ".",
                "successful_writes", ";",
            ),
            "successful physical write snapshot",
        ),
        (
            (
                "stats", "->", "successful_flushes", "=", "io_policy", ".",
                "successful_flushes", ";",
            ),
            "successful durable flush snapshot",
        ),
    ):
        _require_top_level(bio_snapshot, sequence, label)
    overwrite_prepare = _function_tokens(bio_tokens, "bprepare_overwrite")
    _ordered(
        overwrite_prepare,
        (
            ("receipt", "->", "skipped_preread", "=", "1", ";"),
            ("return", "VIRTIO_DISK_OK", ";"),
        ),
        "overwrite preread omission receipt",
    )
    overwrite_publish = _function_tokens(bio_tokens, "bpublish_overwrite")
    _ordered(
        overwrite_publish,
        (
            ("b", "->", "valid", "=", "1", ";"),
            ("b", "->", "disk_result", "=", "VIRTIO_DISK_OK", ";"),
            (
                "kernel_performance_overwrite_preread_skipped", "(", "1",
                ")", ";",
            ),
            ("*", "out", "=", "b", ";"),
            (
                "memset", "(", "receipt", ",", "0", ",", "sizeof", "(",
                "*", "receipt", ")", ")", ";",
            ),
            ("return", "VIRTIO_DISK_OK", ";"),
        ),
        "published overwrite preread skip accounting",
    )

    virtio_tokens = _tokens(virtio_source)
    single_submit = _function_tokens(virtio_tokens, "disk_submit")
    _ordered(
        single_submit,
        (
            ("*", "R", "(", "VIRTIO_MMIO_QUEUE_NOTIFY", ")", "=", "0", ";"),
            (
                "kernel_performance_virtio_notify", "(", "1", ",",
                "KERNEL_PERFORMANCE_VIRTIO_SINGLE", ",", "0", ")", ";",
            ),
        ),
        "single VirtIO submission accounting",
    )
    direct_pair = _function_tokens(virtio_tokens, "disk_submit_write_pair")
    _ordered(
        direct_pair,
        (
            ("*", "R", "(", "VIRTIO_MMIO_QUEUE_NOTIFY", ")", "=", "0", ";"),
            (
                "kernel_performance_virtio_notify", "(", "2", ",",
                "KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH", ",", "0", ")", ";",
            ),
        ),
        "direct VirtIO write batch accounting",
    )
    indirect = _function_tokens(virtio_tokens, "disk_submit_indirect")
    _ordered(
        indirect,
        (
            ("*", "R", "(", "VIRTIO_MMIO_QUEUE_NOTIFY", ")", "=", "0", ";"),
            (
                "kernel_performance_virtio_notify", "(", "count", ",",
                "type", "==", "VIRTIO_BLK_T_OUT", "?",
                "KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH", ":",
                "KERNEL_PERFORMANCE_VIRTIO_READ_BATCH", ",", "1", ")", ";",
            ),
        ),
        "indirect VirtIO batch accounting",
    )
    read_batch = _function_tokens(virtio_tokens, "virtio_disk_read_batch")
    _require_once(
        read_batch,
        (
            "disk_submit_indirect", "(", "&", "buffers", "[", "offset", "]",
            ",", "batch", ",", "VIRTIO_BLK_T_IN", ")",
        ),
        "indirect VirtIO read batch producer",
    )

    epoch_tokens = _tokens(epoch_source)
    commit = _function_tokens(epoch_tokens, "fs_epoch_commit")
    _ordered(
        commit,
        (
            ("bio_deferred_sponsor_end", "(", ")", ";"),
            ("epoch", ".", "totals", ".", "successful_commits", "++", ";"),
            ("bio_deferred_owner_release", "(", "commit_owner", ")", ";"),
        ),
        "successful filesystem epoch publication",
    )
    if "bio_request_settle_quiescent_cleanup" in commit:
        raise ValueError(
            "filesystem epoch must release its gate before I/O debt settlement"
        )
    if commit[-3:] != ["return", "0", ";"]:
        raise ValueError("successful filesystem epoch return must be final")
    if epoch_source.count("epoch.totals.successful_commits++") != 1:
        raise ValueError("filesystem epoch success counter has multiple producers")
    epoch_snapshot = _function_tokens(epoch_tokens, "fs_epoch_stats_snapshot")
    _require_top_level(
        epoch_snapshot,
        ("*", "stats", "=", "epoch", ".", "totals", ";"),
        "filesystem epoch totals snapshot",
    )

    vm_tokens = _tokens(vm_source)
    vm_snapshot = _function_tokens(vm_tokens, "uvm_cow_stats_snapshot")
    _require_top_level(
        vm_snapshot,
        ("memmove", "(", "out", ",", "&", "cow_stats", ",", "sizeof", "(", "*", "out", ")", ")", ";"),
        "COW counter snapshot",
    )
    for counter in (
        "cow_shared_mappings",
        "cow_fault_copies",
        "cow_fault_promotions",
    ):
        if vm_source.count(f"cow_stats.{counter}++") + vm_source.count(
            f"cow_stats.{counter} +="
        ) != 1:
            raise ValueError(f"COW counter producer differs: {counter}")
    copyout = _function_tokens(vm_tokens, "copyout")
    _require_once(
        copyout,
        ("uvm_cow_fault", "(", "pagetable", ",", "page", ")"),
        "copyout COW accounting",
    )

    loader_tokens = _tokens(loader_source)
    loader_snapshot = _function_tokens(
        loader_tokens, "user_image_rx_cache_stats_snapshot"
    )
    _require_top_level(
        loader_snapshot,
        (
            "memmove", "(", "out", ",", "&", "user_image_rx_cache_stats",
            ",", "sizeof", "(", "*", "out", ")", ")", ";",
        ),
        "exec cache counter snapshot",
    )
    for counter in (
        "exec_cache_hits",
        "exec_cache_misses",
        "exec_cache_shared_pages",
        "exec_cache_evictions",
    ):
        if loader_source.count(
            f"user_image_rx_cache_stats.{counter}++"
        ) != 1:
            raise ValueError(f"exec cache counter producer differs: {counter}")


def _validate_performance_pair_consumer(source: str) -> None:
    """将展示差值绑定到同一 observer 的原始 Guest 快照。"""

    snapshot_pairs = (
        ("fs_epoch_commits", "epoch_commits"),
        ("fs_epoch_buffers_staged", "epoch_buffers_staged"),
        ("block_physical_writes", "physical_writes"),
        ("block_physical_reads", "physical_reads"),
        ("block_durable_flushes", "durable_flushes"),
        ("fs_epoch_deduplicated_stages", "deduplicated_stages"),
        ("cow_pages_shared", "cow_shared_pages"),
        ("cow_pages_copied", "cow_copied_pages"),
        ("cow_fault_promotions", "cow_fault_promotions"),
        ("exec_cache_hits", "exec_cache_hits"),
        ("exec_cache_misses", "exec_cache_misses"),
        ("exec_cache_shared_pages", "exec_cache_shared_pages"),
        ("exec_cache_evictions", "exec_cache_evictions"),
        ("observer_workload_syscalls", "workload_syscalls"),
        ("directory_block_probes", "directory_block_probes"),
        ("directory_entries_examined", "directory_entries_examined"),
        ("virtio_notifications", "virtio_notifications"),
        ("virtio_submitted_requests", "virtio_submitted_requests"),
        ("virtio_write_batch_calls", "virtio_write_batch_calls"),
        ("virtio_batched_write_requests", "virtio_batched_write_requests"),
        ("virtio_indirect_write_batch_calls", "virtio_indirect_write_batch_calls"),
        ("virtio_read_batch_calls", "virtio_read_batch_calls"),
        ("virtio_batched_read_requests", "virtio_batched_read_requests"),
        ("overwrite_prereads_skipped", "overwrite_prereads_skipped"),
    )
    metadata_pairs = (
        ("metadata_dirty", "metadata_dirty"),
        ("metadata_durable", "metadata_durable"),
        ("metadata_requests", "metadata_requests"),
        ("metadata_coalesced", "metadata_coalesced"),
        ("metadata_commits", "metadata_commits"),
    )
    raw_names = tuple(name for _field, name in snapshot_pairs + metadata_pairs) + (
        "metadata_pending",
    )
    tokens = _tokens(source)
    take = _function_tokens(tokens, "take_performance_snapshot")
    _ordered(
        take,
        (
            ("memset", "(", "receipt", ",", "0", ",", "sizeof", "(", "*", "receipt", ")", ")", ";"),
            ("receipt", "->", "observer_pid", "=", "(", "uint64", ")", "getpid", "(", ")", ";"),
            ("agent_performance_snapshot", "(", "&", "receipt", "->", "snapshot", ")"),
            (
                "receipt", "->", "snapshot", ".", "version", "==",
                "AGENT_PERFORMANCE_SNAPSHOT_VERSION",
            ),
            (
                "receipt", "->", "snapshot", ".", "counter_scope", "==",
                "AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL",
            ),
        ),
        "showcase performance snapshot",
    )
    if take.count("agent_performance_snapshot") != 1 or take.count("return") != 0:
        raise ValueError("showcase performance snapshot control flow differs")

    delta = _function_tokens(tokens, "performance_delta")
    if delta != [
        "check", "(", "after", ">=", "before", ",",
        '"monotonic performance counter"', ")", ";",
        "return", "after", "-", "before", ";",
    ]:
        raise ValueError("showcase performance delta is not a raw monotonic delta")

    pair = _function_tokens(tokens, "print_mechanism_delta")
    for sequence, label in (
        (
            (
                "before", "->", "counter_scope", "==", "after", "->",
                "counter_scope",
            ),
            "showcase pair counter scope",
        ),
        (
            (
                "before_receipt", "->", "observer_pid", "==",
                "after_receipt", "->", "observer_pid",
            ),
            "showcase pair observer pid",
        ),
        (
            (
                "before", "->", "observer_lifecycle_id", "==", "after",
                "->", "observer_lifecycle_id",
            ),
            "showcase pair observer lifecycle",
        ),
        (
            (
                "before", "->", "observer_lifecycle_generation", "==",
                "after", "->", "observer_lifecycle_generation",
            ),
            "showcase pair observer generation",
        ),
        (
            (
                "before", "->", "sample_tick", "<", "after", "->",
                "sample_tick",
            ),
            "showcase pair increasing sample tick",
        ),
    ):
        _require_once(pair, sequence, label)
    for field, _raw_name in snapshot_pairs:
        _require_once(
            pair,
            (
                "performance_delta", "(", "before", "->", field, ",",
                "after", "->", field, ")",
            ),
            f"showcase pair delta {field}",
        )
        if pair.count(field) != 4:
            raise ValueError(f"showcase raw pair serialization differs: {field}")
    for field, _raw_name in metadata_pairs:
        _require_once(
            pair,
            (
                "performance_delta", "(", "before_receipt", "->", field,
                ",", "after_receipt", "->", field, ")",
            ),
            f"showcase metadata pair delta {field}",
        )
        if pair.count(field) != 4:
            raise ValueError(f"showcase metadata serialization differs: {field}")
    if pair.count("performance_delta") != len(snapshot_pairs) + len(metadata_pairs):
        raise ValueError("showcase performance pair has extra delta producers")
    string_literals = tuple(
        token for token in pair if token.startswith('"') and token.endswith('"')
    )
    if len(string_literals) != 3:
        raise ValueError("showcase performance pair record structure differs")
    record = string_literals[-1]
    for name in raw_names:
        if record.count(f"before_{name}=%llu") != 1 or record.count(
            f"after_{name}=%llu"
        ) != 1:
            raise ValueError(f"showcase raw pair field differs: {name}")
    for field in (
        "observer_pid=%llu", "before_tick=%llu", "after_tick=%llu",
        "observer_lifecycle_id=%llu",
        "observer_lifecycle_generation=%llu", "counter_scope=global",
    ):
        if record.count(field) != 1:
            raise ValueError(f"showcase raw pair identity differs: {field}")
    serialized_values = [
        "before_receipt", "->", "observer_pid", ",",
        "before", "->", "sample_tick", ",",
        "after", "->", "sample_tick", ",",
        "before", "->", "observer_lifecycle_id", ",",
        "before", "->", "observer_lifecycle_generation", ",",
    ]
    serialized_pairs = [
        ("before", "after", field) for field, _raw in snapshot_pairs
    ] + [
        ("before_receipt", "after_receipt", field)
        for field, _raw in metadata_pairs
    ] + [
        ("before_receipt", "after_receipt", "metadata_pending")
    ]
    for index, (before_name, after_name, field) in enumerate(serialized_pairs):
        serialized_values.extend(
            (before_name, "->", field, ",", after_name, "->", field)
        )
        if index + 1 != len(serialized_pairs):
            serialized_values.append(",")
    _require_once(
        pair, tuple(serialized_values), "showcase raw before/after serialization"
    )
    if pair.count("printf") != 1 or pair.count("return") != 0:
        raise ValueError("showcase raw pair emission control flow differs")

    orchestrator = _function_tokens(tokens, "run_orchestrator")
    _ordered(
        orchestrator,
        (
            (
                "memset", "(", "&", "measurement_warmup", ",", "0", ",",
                "sizeof", "(", "measurement_warmup", ")", ")", ";",
            ),
            (
                "take_performance_snapshot", "(", "&", "measurement_warmup",
                ")", ";",
            ),
            (
                "take_performance_snapshot", "(", "&",
                "workflow_perf_before", ")", ";",
            ),
            (
                "take_performance_snapshot", "(", "&",
                "workflow_perf_after", ")", ";",
            ),
            (
                "print_mechanism_delta", "(", '"workflow"', ",",
                '"end_to_end"', ",", "&", "workflow_perf_before", ",",
                "&", "workflow_perf_after", ")", ";",
            ),
        ),
        "showcase pre-touched performance interval",
    )
    for function_name, mode in (
        ("run_compat_workload", "compat"),
        ("run_native_workload", "native"),
    ):
        lane = _function_tokens(tokens, function_name)
        _ordered(
            lane,
            (
                (
                    "demo_quiescence_fence", "(", f'"{mode}"', ",",
                    '"CORE_START"', ",", "2", ",", "&", "state", "->",
                    "core_start", ")", ";",
                ),
                (
                    "metrics", "->", "started_us", "=", "demo_now_us",
                    "(", ")", ";",
                ),
                (
                    "metrics", "->", "finished_us", "=", "demo_now_us",
                    "(", ")", ";",
                ),
                (
                    "take_performance_snapshot", "(", "&", "state", "->",
                    "core_ack", ")", ";",
                ),
                (
                    "demo_quiescence_fence", "(", f'"{mode}"', ",",
                    '"ACK_SETTLED"', ",", "3", ",", "&", "state", "->",
                    "ack_settled", ")", ";",
                ),
                (
                    "print_mechanism_delta", "(", f'"{mode}"', ",",
                    '"core"', ",", "&", "state", "->", "core_start", ".",
                    "performance", ",", "&", "state", "->", "core_ack",
                    ")", ";",
                ),
            ),
            f"{mode} core performance interval",
        )


def _validate_lifecycle_identity(abi: str, implementation: str) -> None:
    for declaration in (
        "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 2U",
        "unsigned int resource_account_valid;",
        "unsigned int resource_account_slot;",
        "unsigned long long resource_account_generation;",
        "sizeof(struct agent_workflow_lifecycle_info) == 64",
    ):
        if declaration not in abi:
            raise ValueError(f"workflow lifecycle identity ABI differs: {declaration}")

    abi_tokens = _tokens(abi)
    for sequence in (
        (
            "__builtin_offsetof", "(", "struct", "agent_workflow_lifecycle_info", ",",
            "resource_account_valid", ")", "==", "48",
        ),
        (
            "__builtin_offsetof", "(", "struct", "agent_workflow_lifecycle_info", ",",
            "resource_account_generation", ")", "==", "56",
        ),
    ):
        _require_once(abi_tokens, sequence, "workflow resource-account ABI layout")

    lifecycle = _function_tokens(
        _tokens(implementation), "sys_agent_workflow_lifecycle_info"
    )
    _ordered(
        lifecycle,
        (
            ("enabled", "=", "intr_save", "(", ")", ";"),
            ("resource_account_handle_valid", "(", "p", "->", "resource_account", ")"),
            ("info", ".", "resource_account_valid", "=", "1", ";"),
            ("info", ".", "resource_account_slot", "=", "p", "->", "resource_account", ".", "slot", ";"),
            ("info", ".", "resource_account_generation", "=", "p", "->", "resource_account", ".", "generation", ";"),
            ("intr_restore", "(", "enabled", ")", ";"),
        ),
        "read-only current resource-account identity",
    )
    allowed_resource_tokens = {
        "resource_account", "resource_account_handle_valid",
        "resource_account_valid", "resource_account_slot",
        "resource_account_generation",
    }
    if any(
        token.startswith("resource_account_") and
        token not in allowed_resource_tokens
        for token in lifecycle
    ):
        raise ValueError("lifecycle identity observer mutates resource account")


def validate_source_texts(sources: dict[str, str]) -> None:
    expected = set(SOURCE_PATHS)
    if set(sources) != expected:
        raise ValueError(
            f"scenario timing source set differs: missing={sorted(expected - set(sources))} "
            f"extra={sorted(set(sources) - expected)}"
        )
    _validate_plain_timing(sources["baseline_ucore/user/src/rp_seed_orch.c"])
    _validate_agentos_timing(sources["user/src/rp_agentos_orch.c"])
    _validate_delegated_workflow(sources["user/src/rp_orch.c"])
    _validate_resource_stability(
        sources["user/src/rp_agentos_orch.c"],
        sources["user/src/rp_resource_probe.c"],
        sources["user/include/rp_resource_stability.h"],
        sources["user/include/exec_policy_manifest.h"],
    )
    _validate_lifecycle_identity(
        sources["agent_lifecycle_abi.h"],
        sources["os/agent_lifecycle.c"],
    )
    _validate_resource_observer(
        sources["agent_resource_abi.h"],
        sources["os/agent_resource.c"],
        sources["os/resource_controller.c"],
        sources["os/resource_controller.h"],
        sources["os/syscall.c"],
        sources["os/syscall_ids.h"],
        sources["user/lib/syscall_ids.h"],
        sources["user/lib/syscall.c"],
    )
    _validate_performance_observer(
        sources["agent_performance_abi.h"],
        sources["os/agent_resource.c"],
        sources["os/performance_stats.c"],
        sources["os/performance_stats.h"],
        sources["os/syscall.c"],
        sources["os/syscall_ids.h"],
        sources["user/lib/arch/riscv/syscall_ids.h.in"],
        sources["user/lib/syscall_ids.h"],
        sources["user/lib/syscall.c"],
    )
    _validate_performance_producers(
        sources["os/agent_identity.c"],
        sources["os/exec_policy.c"],
        sources["os/bio.c"],
        sources["os/virtio_disk.c"],
        sources["os/fs_epoch.c"],
        sources["os/vm.c"],
        sources["os/loader.c"],
    )
    _validate_performance_pair_consumer(
        sources["user/src/labdemo_ucore.c"]
    )
    _validate_clock_source(
        sources["baseline_ucore/user/lib/syscall.c"], "plain Guest"
    )
    _validate_clock_source(sources["user/lib/syscall.c"], "AgentOS Guest")


def validate_sources(root: Path = ROOT) -> None:
    values = {
        relative: (root / relative).read_text(encoding="utf-8")
        for relative in SOURCE_PATHS
    }
    validate_source_texts(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        validate_sources(args.repo)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    print("scenario timing source contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
