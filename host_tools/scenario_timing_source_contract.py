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
import hashlib
import os
import re
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
CONTRACT_VERSION = "scenario-timing-source-v15"
SOURCE_PATHS = (
    "baseline_ucore/user/src/rp_seed_orch.c",
    "user/src/rp_agentos_orch.c",
    "user/src/rp_orch.c",
    "user/src/rp_package.c",
    "user/include/rp_program_manifest.h",
    "user/include/rp_worker_batch.h",
    "user/src/rp_wbatch0.c",
    "user/src/rp_wbatch1.c",
    "user/src/rp_wbatch2.c",
    "user/Makefile",
    "user/lib/main.c",
    "user/include/rp_launch_attestation.h",
    "user/include/rp_evidence.h",
    "user/src/rp_resource_probe.c",
    "user/include/rp_resource_stability.h",
    "user/include/exec_policy_manifest.h",
    "agent_lifecycle_abi.h",
    "os/agent_lifecycle.c",
    "agent_performance_abi.h",
    "agent_resource_abi.h",
    "os/agent_core.c",
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
    "os/workflow_scheduler.c",
    "os/workflow_scheduler.h",
    "os/syscall.c",
    "os/syscall_ids.h",
    "user/lib/arch/riscv/syscall_ids.h.in",
    "user/lib/syscall_ids.h",
    "baseline_ucore/user/lib/syscall.c",
    "user/lib/syscall.c",
    "user/include/labdemo_workload.h",
    "user/src/labdemo_ucore.c",
    "user/src/agent_eevdf_ucore.c",
)

EXPECTED_PLATFORM_PROGRAMS = (
    "rp_catalog", "rp_state_catalog", "rp_object_store", "rp_object_query",
    "rp_lineage", "rp_site_export", "rp_planner", "rp_portability",
    "rp_retriever", "rp_analyst", "rp_reviewer", "rp_lab",
    "rp_governance", "rp_writer", "rp_repair", "rp_auditor", "rp_query",
    "rp_evidence", "rp_llm_bridge", "rp_llm_relay", "rp_privacy",
    "rp_runconf", "rp_execobs", "rp_invoke", "rp_complete",
    "rp_artifact_ops", "rp_data_pipeline", "rp_workflow_runner",
    "rp_workbench", "rp_agent_collab", "rp_package", "rp_calculation",
    "rp_realtask", "rp_analysisres", "rp_campaign", "rp_delta",
    "rp_release", "rp_dossier", "rp_service_surface", "rp_startup_doctor",
    "rp_notebook_export", "rp_backend", "rp_consistency", "rp_metrics",
    "rp_ui_export", "rp_web_export", "rp_revdash", "rp_modelreg",
    "rp_sysreview", "rp_expsched", "rp_traincomp", "rp_publication",
    "rp_runbooks", "rp_projectrel", "rp_studyproto", "rp_stdesign",
    "rp_opsboard", "rp_reviewboard", "rp_controlplane",
    "rp_integrityplane", "rp_coherenceplane", "rp_mature", "rp_prov_view",
    "rp_prov_query", "rp_reldossier", "rp_decsupport", "rp_usable",
    "rp_usableproject", "rp_compare_plain", "rp_test_suite",
)

EXPECTED_ROLE_PROGRAMS = (
    ("rp_query", "artifact"),
    ("rp_repair", "recovery"),
    ("rp_execobs", "artifact"),
    ("rp_agent_collab", "orchestrator"),
    ("rp_auditor", "orchestrator"),
    ("rp_workbench", "artifact"),
    ("rp_package", "orchestrator"),
    ("rp_realtask", "orchestrator"),
    ("rp_service_surface", "artifact"),
    ("rp_backend", "orchestrator"),
)

EXPECTED_BATCH_PROGRAMS = (
    (
        "rp_catalog", "rp_state_catalog", "rp_object_store",
        "rp_object_query", "rp_lineage", "rp_site_export", "rp_planner",
        "rp_portability", "rp_retriever", "rp_analyst", "rp_reviewer",
        "rp_lab", "rp_governance", "rp_writer", "rp_evidence",
        "rp_llm_bridge", "rp_llm_relay", "rp_privacy", "rp_runconf",
        "rp_invoke", "rp_complete", "rp_artifact_ops", "rp_data_pipeline",
        "rp_workflow_runner", "rp_calculation", "rp_analysisres",
        "rp_campaign", "rp_delta", "rp_release", "rp_dossier",
        "rp_startup_doctor", "rp_notebook_export",
    ),
    (
        "rp_consistency", "rp_metrics", "rp_ui_export", "rp_web_export",
        "rp_revdash", "rp_modelreg", "rp_sysreview", "rp_expsched",
        "rp_traincomp",
    ),
    (
        "rp_publication", "rp_runbooks", "rp_projectrel", "rp_studyproto",
        "rp_stdesign", "rp_opsboard", "rp_reviewboard", "rp_controlplane",
        "rp_integrityplane", "rp_coherenceplane", "rp_mature",
        "rp_prov_view", "rp_prov_query", "rp_reldossier", "rp_decsupport",
        "rp_usable", "rp_usableproject",
    ),
)

EXPECTED_BATCH_GROUPS = (
    ("0", "rp_wbatch0", "32"),
    ("1", "rp_wbatch1", "9"),
    ("2", "rp_wbatch2", "17"),
)
EXPECTED_DIRECT_PROGRAMS = ("rp_compare_plain", "rp_test_suite")


def _tokens(text: str) -> list[str]:
    return _lex(text.replace("\\\n", " "))


def _token_fingerprint(tokens: list[str]) -> str:
    return hashlib.sha256("\0".join(tokens).encode("ascii")).hexdigest()


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


def _macro_apply_entries(
    text: str, name: str, arity: int
) -> tuple[tuple[str, ...], ...]:
    """Read one canonical APPLY-list macro from preprocessor tokens."""

    tokens = _tokens(text)
    marker = ("#", "define", name, "(", "APPLY", ")")
    cursor = _require_once(tokens, marker, f"{name} definition") + len(marker)
    entries: list[tuple[str, ...]] = []
    while cursor < len(tokens) and tokens[cursor] == "APPLY":
        if cursor + 1 >= len(tokens) or tokens[cursor + 1] != "(":
            raise ValueError(f"{name} has a malformed APPLY entry")
        cursor += 2
        fields: list[str] = []
        while cursor < len(tokens) and tokens[cursor] != ")":
            token = tokens[cursor]
            if token == ",":
                cursor += 1
                continue
            if token.startswith('"') and token.endswith('"'):
                token = token[1:-1]
            fields.append(token)
            cursor += 1
        if cursor >= len(tokens) or len(fields) != arity:
            raise ValueError(f"{name} has a wrong-arity APPLY entry")
        entries.append(tuple(fields))
        cursor += 1
    if not entries or cursor >= len(tokens) or tokens[cursor] != "#":
        raise ValueError(f"{name} does not end at the next directive")
    return tuple(entries)


def _require_token_fingerprint(
    body: list[str], expected: str, label: str
) -> None:
    if _token_fingerprint(body) != expected:
        raise ValueError(f"{label} reviewed token body differs")


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


def _validate_worker_batch_manifest(text: str) -> None:
    platform = tuple(
        entry[0]
        for entry in _macro_apply_entries(text, "RP_PLATFORM_PROGRAMS", 1)
    )
    roles = _macro_apply_entries(text, "RP_AGENTOS_ROLE_PROGRAMS", 2)
    groups = _macro_apply_entries(text, "RP_WORKER_BATCH_GROUPS", 3)
    direct = tuple(
        entry[0]
        for entry in _macro_apply_entries(text, "RP_WORKER_DIRECT_PROGRAMS", 1)
    )
    if platform != EXPECTED_PLATFORM_PROGRAMS or len(set(platform)) != 70:
        raise ValueError("platform program manifest is not the reviewed 70-program order")
    if roles != EXPECTED_ROLE_PROGRAMS or len({name for name, _ in roles}) != 10:
        raise ValueError("AgentOS role manifest is not the reviewed 10-program set")
    if groups != EXPECTED_BATCH_GROUPS:
        raise ValueError("persistent worker runner descriptors differ")
    if direct != EXPECTED_DIRECT_PROGRAMS:
        raise ValueError("direct worker tail differs")

    indexed_groups = tuple(
        _macro_apply_entries(text, f"RP_WORKER_BATCH_{group}_PROGRAMS", 2)
        for group, _runner, _count in groups
    )
    expected_indexed = tuple(
        tuple((str(index), program) for index, program in enumerate(programs))
        for programs in EXPECTED_BATCH_PROGRAMS
    )
    if indexed_groups != expected_indexed:
        raise ValueError("persistent worker membership, order, or index differs")
    flattened = tuple(
        program for group in indexed_groups for _index, program in group
    )
    role_names = {program for program, _role in roles}
    expected_flattened = tuple(
        program for program in platform
        if program not in role_names and program not in set(direct)
    )
    if (
        flattened != expected_flattened
        or len(flattened) != 58
        or len(set(flattened)) != 58
        or set(flattened) & role_names
        or set(flattened) & set(direct)
        or role_names & set(direct)
        or set(flattened) | role_names | set(direct) != set(platform)
    ):
        raise ValueError("10 role + 58 batch + 2 direct sets do not close exactly")

    tokens = _tokens(text)
    for name, value in (
        ("RP_WORKER_BATCH_GROUP_COUNT", "3"),
        ("RP_WORKER_BATCH_PROGRAM_COUNT", "58"),
        ("RP_WORKER_DIRECT_PROGRAM_COUNT", "2"),
    ):
        _require_once(
            tokens, ("#", "define", name, value), f"canonical {name}"
        )


def _validate_worker_batch_protocol(text: str) -> None:
    tokens = _tokens(text)
    for name, value in (
        ("RP_WORKER_BATCH_MAGIC", "0x52505742U"),
        ("RP_WORKER_BATCH_VERSION", "1U"),
        ("RP_WORKER_BATCH_READY_INDEX", "0xffffffffU"),
        ("RP_WORKER_BATCH_MAX_FD", "15"),
        ("RP_WORKER_BATCH_ARG_READ_FD", "2"),
        ("RP_WORKER_BATCH_ARG_WRITE_FD", "3"),
        ("RP_WORKER_BATCH_ARG_NONCE", "4"),
        ("RP_WORKER_BATCH_ARGC", "5"),
    ):
        _require_once(tokens, ("#", "define", name, value), f"worker wire {name}")
    _require_once(
        tokens,
        ("#", "define", "RP_WORKER_BATCH_NEXT_STOP", "(", "-", "1", ")"),
        "worker wire stop sentinel",
    )
    _require_once(
        tokens,
        (
            "enum", "rp_worker_batch_kind", "{",
            "RP_WORKER_BATCH_READY", "=", "1", ",",
            "RP_WORKER_BATCH_RUN", "=", "2", ",",
            "RP_WORKER_BATCH_RESULT", "=", "3", ",",
            "RP_WORKER_BATCH_STOP", "=", "4", ",",
            "RP_WORKER_BATCH_STOPPED", "=", "5", ",", "}", ";",
        ),
        "worker wire kind namespace",
    )
    _require_once(
        tokens,
        (
            "struct", "rp_worker_batch_frame", "{", "uint32", "magic", ";",
            "uint16", "version", ";", "uint8", "kind", ";", "uint8",
            "group", ";", "uint32", "index", ";", "int32", "status", ";",
            "uint64", "nonce", ";", "uint64", "guard", ";", "}", ";",
        ),
        "padding-free worker frame layout",
    )
    for assertion in (
        (
            "_Static_assert", "(", "sizeof", "(", "struct",
            "rp_worker_batch_frame", ")", "==", "32",
        ),
        (
            "_Static_assert", "(", "__builtin_offsetof", "(", "struct",
            "rp_worker_batch_frame", ",", "guard", ")", "==", "24",
        ),
    ):
        _require_once(tokens, assertion, "worker frame compile-time layout")

    # These compact bodies are the wire trust boundary. Token fingerprints retain
    # exact I/O, overflow, guard, sequencing, cleanup, and fail-closed behavior.
    fingerprints = {
        "rp_worker_batch_guard": "69a57b19c768cf9ec7615176935a6f9e42b6fcc860a732e421489b71f05ca126",
        "rp_worker_batch_frame_init": "718c3e897ef92635d0c4bee8bcd8dc8c219148e16477b3dbe765993f70c7deda",
        "rp_worker_batch_read_exact": "f159e0e57ccaba5eb42e806c1f9ccc88fba543b2ad7c04c2cf49e623a082a749",
        "rp_worker_batch_write_exact": "80ed09bbd003ea6cef11eb9df1b7ea97147dfd9a1e49339ea768e7677f13cc7f",
        "rp_worker_batch_parse_fd": "30846e4ed0ba8a671a0b53faaf30d299ec6be135c85bdd7671f617ad21194458",
        "rp_worker_batch_parse_nonce": "242a2f87a8b8c3e1d490e3a2621f0450dc811b80c95d3dd540bcff7414822825",
        "rp_worker_batch_frame_guard_valid": "b29d13f715ef20c6e5377cb43c2a6d8c871c9e0ce7805b439529509e42d9b379",
        "rp_worker_batch_command_valid": "835aa1ec64f36fbaff9f3a736f68615de199f0d13ad1978dba9f1245ba973fb5",
        "rp_worker_batch_finish": "4c4fa2dded4a63b140c9e2a27a7c987a2c7c897b4c2a3dd4a9aba3d733d419df",
        "rp_worker_batch_start": "0892f8e1af43b670b26664b32bef6355a22b095d70743ee56805f7cb4d2d69c3",
        "rp_worker_batch_next": "ad7e05ad64409a8da5cf57e2d86008d92f4ef92029644f38a39b3760d93341a8",
        "rp_worker_batch_report": "3e9183a4beb3b42576386b7da357850de99f8535601f7fd2ccad52c66f911d9b",
    }
    for name, fingerprint in fingerprints.items():
        _require_token_fingerprint(
            _function_tokens(tokens, name), fingerprint,
            f"fail-closed worker protocol {name}",
        )
    if (
        "rp_host_seed_buf" in tokens
        or "(*run)" in text
        or re.search(r"\bagent_[A-Za-z0-9_]*\s*\(", text)
    ):
        raise ValueError("worker dispatcher protocol bypasses its non-Agent boundary")


def _validate_worker_batch_runners(texts: tuple[str, str, str]) -> None:
    fingerprints = (
        (
            "ada5c1ceb6cd898613d710f6cb8bb0d1baf96fc5fcce0edcdd1604c338180ea9",
            "4cacabd34fab2d0205c1595645684316eb9af10be50003309b7bdbef791aaeaa",
        ),
        (
            "ae0373ca0be9925ea1b66cc57da8511a5d323fba1955125950e1ef3be94935e1",
            "840e6eea497a5179e6f8e8d3e3ae3517eca9e178ff156630338cb39edb417040",
        ),
        (
            "2183663ce7046b001f5a51be826104647ffc096a68ba4237d56608061e89639e",
            "49a9f6d021464e21e56ecd041ac599198df63c1ae85d0b318d481545409ed3bb",
        ),
    )
    for group, text in enumerate(texts):
        tokens = _tokens(text)
        _require_once(
            tokens,
            (
                "#", "define", "RP_WORKER_BATCH_DISPATCHER", "1", "#",
                "include", "<", "rp_worker_batch", ".", "h", ">",
            ),
            f"worker runner {group} dispatcher include boundary",
        )
        _require_token_fingerprint(
            _function_tokens(tokens, "rp_worker_run"), fingerprints[group][0],
            f"worker runner {group} direct switch",
        )
        _require_token_fingerprint(
            _function_tokens(tokens, "main"), fingerprints[group][1],
            f"worker runner {group} sequential main loop",
        )


def _make_words(text: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"^{re.escape(name)}\s*:=\s*(.*)$", text, re.MULTILINE)
    if match is None:
        raise ValueError(f"worker build variable is missing: {name}")
    return tuple(match.group(1).split())


def _validate_worker_batch_build(text: str) -> None:
    for group, programs in enumerate(EXPECTED_BATCH_PROGRAMS):
        if _make_words(text, f"WORKER_BATCH_{group}_PROGRAMS") != programs:
            raise ValueError(f"worker build group {group} differs from manifest")
    if _make_words(text, "WORKER_BATCH_APPS") != tuple(
        runner for _group, runner, _count in EXPECTED_BATCH_GROUPS
    ):
        raise ValueError("worker build runner inventory differs")
    if _make_words(text, "WORKER_BATCH_DIRECT_PROGRAMS") != EXPECTED_DIRECT_PROGRAMS:
        raise ValueError("worker build direct inventory differs")
    for fragment, label in (
        ("WORKER_BATCH_FLAT_MAX := 258048", "16 KiB flat-image reserve"),
        (
            "$(CC_CMD) $(CFLAGS) -Dmain=$*_worker_entry -c $< -o $@",
            "unique worker entry compilation",
        ),
        (
            "$(foreach group,0 1 2,$(eval $(call RP_WORKER_BATCH_LINK_RULE,$(group))))",
            "three-runner link instantiation",
        ),
        (
            "CH_TESTS := rp_agentos_orch rp_resource_probe $(PLATFORM_TESTS) $(WORKER_BATCH_APPS)",
            "AgentOS image runner closure",
        ),
        (
            "worker-batch-check:\n\t@$(PYTHON_CMD) $(WORKER_BATCH_CHECKER) --root ..",
            "build-time static contract",
        ),
        (
            "worker-batch-selftest:\n\t@$(PYTHON_CMD) $(WORKER_BATCH_SELFTEST)",
            "worker mutation self-test target",
        ),
        (
            "binary: worker-batch-check $(addprefix $(elf_dir)/,$(SELECTED_APPS))",
            "mandatory worker build gate",
        ),
        (
            'if test "$$$$size" -gt $$(WORKER_BATCH_FLAT_MAX); then',
            "per-runner size gate",
        ),
    ):
        if text.count(fragment) != 1:
            raise ValueError(f"worker build {label} differs")
    if "rp_wbatch3" in text or "rp_wbatch4" in text:
        raise ValueError("one-shot tail runners re-entered the AgentOS image")


def _validate_orchestrator_worker_batches(tokens: list[str]) -> None:
    fingerprints = {
        "launch_manifest_valid": "f35a38d96534dbe934083480b6d9be65083860e77997ea24bbe2650ba60f58d9",
        "worker_batch_session_reset": "a5af28498391bec72e8a66809c52459feeedcea6cda8cfbb831809f263e09903",
        "worker_batch_reap": "d5636877513c3b15eb835279eb68dec09d55b05effb05b9bdde5330b83f676f4",
        "worker_batch_frame_is": "2c80fc42c67015470852d5fc4bfe116ca35ac4869686d48804b13cda05fbb076",
        "worker_batch_start": "4e45d2eea714c34af8940a79a5e486fdd7da1bf84ad84b7c421f3bb2b01a4059",
        "run_worker_batch": "c90d1240659f6c549f2253b888966fd1967f8b98f71f37ad40f7b69658555f11",
    }
    for name, fingerprint in fingerprints.items():
        _require_token_fingerprint(
            _function_tokens(tokens, name), fingerprint,
            f"reviewed orchestrator batch path {name}",
        )

    manifest = _function_tokens(tokens, "launch_manifest_valid")
    for sequence, label in (
        (
            (
                "binding", "->", "index", "!=", "group_entries", "[",
                "binding", "->", "group", "]", "||", "binding", "->",
                "index", ">=", "group", "->", "count",
            ),
            "contiguous group index",
        ),
        (
            (
                "int", "categories", "=", "(", "role", "!=", "0", ")",
                "+", "(", "worker_batch_binding_for_program", "(",
                "PROGRAMS", "[", "i", "]", ".", "program", ")", "!=", "0",
                ")", "+", "worker_direct_program", "(", "PROGRAMS", "[",
                "i", "]", ".", "program", ")", ";",
            ),
            "disjoint launch category",
        ),
        (
            (
                "return", "trusted", "==", "10", "&&", "trusted", "==",
                "declared", ";",
            ),
            "exact role count",
        ),
    ):
        _require_once(manifest, sequence, f"launch manifest {label}")
    if _locations(manifest, ("return", "1", ";")):
        raise ValueError("launch manifest has an unconditional success bypass")

    reap = _function_tokens(tokens, "worker_batch_reap")
    _ordered(
        reap,
        (
            (
                "close", "(", "WORKER_BATCH_SESSION", ".", "command_fd", ")",
                ";",
            ),
            (
                "close", "(", "WORKER_BATCH_SESSION", ".", "result_fd", ")",
                ";",
            ),
            ("got", "=", "waitpid", "(", "pid", ",", "&", "code", ")", ";"),
            ("worker_batch_session_reset", "(", ")", ";"),
            ("return", "pid", ">", "0", "&&", "got", "==", "pid", ";"),
        ),
        "batch failure close-command close-result exact-wait reset",
    )
    if reap.count("close") != 2 or reap.count("waitpid") != 1:
        raise ValueError("batch reap does not have one exact cleanup path")

    launch = _function_tokens(tokens, "worker_batch_start")
    _ordered(
        launch,
        (
            ("pipe", "(", "command_pipe", ")", "!=", "0"),
            ("pipe", "(", "result_pipe", ")", "!=", "0"),
            (
                "exec_manifest_worker_image", "(", "group", "->", "runner", ",",
                "WORKER_BATCH_SESSION", ".", "image", ")", ";",
            ),
            (
                "agent_scope_delegate_fd", "(", "command_pipe", "[", "0", "]",
                ")", "!=", "AGENT_STATUS_OK",
            ),
            (
                "agent_scope_delegate_fd", "(", "result_pipe", "[", "1", "]",
                ")", "!=", "AGENT_STATUS_OK",
            ),
            (
                "pid", "=", "agent_worker_create", "(",
                "WORKER_BATCH_SESSION", ".", "image", ",", "launch", "->",
                "worker_capabilities", ")", ";",
            ),
            (
                "rp_worker_batch_read_exact", "(", "WORKER_BATCH_SESSION", ".",
                "result_fd", ",", "&", "ready", ",", "sizeof", "(", "ready",
                ")", ")",
            ),
            (
                "&", "ready", ",", "RP_WORKER_BATCH_READY", ",", "group", "->",
                "group", ",", "RP_WORKER_BATCH_READY_INDEX", ",",
                "WORKER_BATCH_SESSION", ".", "nonce", ",", "1", ")",
            ),
            ("WORKER_BATCH_SESSION", ".", "active", "=", "1", ";"),
        ),
        "batch start delegation and READY proof",
    )
    if (
        launch.count("pipe") != 2
        or launch.count("agent_scope_delegate_fd") != 2
        or launch.count("agent_worker_create") != 1
        or launch.count("exec") != 1
        or launch.count("waitpid") != 0
    ):
        raise ValueError("batch runner is not one delegated spawn per group")

    run = _function_tokens(tokens, "run_worker_batch")
    _ordered(
        run,
        (
            ("int64", "start", "=", "get_mtime", "(", ")", ";"),
            ("worker_batch_start", "(", "launch", ",", "binding", ",", "orchestrator_identity", ")"),
            (
                "rp_worker_batch_frame_init", "(", "&", "frame", ",",
                "RP_WORKER_BATCH_RUN",
            ),
            (
                "&", "frame", ",", "RP_WORKER_BATCH_RESULT", ",", "binding",
                "->", "group", ",", "binding", "->", "index", ",",
                "WORKER_BATCH_SESSION", ".", "nonce", ",", "0", ")",
            ),
            ("code", "=", "frame", ".", "status", ";"),
            ("WORKER_BATCH_SESSION", ".", "next_index", "++", ";"),
            (
                "rp_worker_batch_frame_init", "(", "&", "frame", ",",
                "RP_WORKER_BATCH_STOP",
            ),
            (
                "&", "frame", ",", "RP_WORKER_BATCH_STOPPED", ",", "binding",
                "->", "group", ",", "WORKER_BATCH_SESSION", ".", "next_index",
                ",", "WORKER_BATCH_SESSION", ".", "nonce", ",", "1", ")",
            ),
            (
                "if", "(", "!", "worker_batch_reap", "(", "&",
                "child_code", ")", "||", "child_code", "!=", "0", ")",
            ),
            (
                "record_timing", "(", "launch", "->", "program", ",",
                '"agent_worker_batch"', ",", "pid", ",", "&", "expected", ",",
                "RP_LAUNCH_BATCH_IDENTITY_SOURCE", ",", "1", ",", "0", ",",
                "elapsed", ")", ";",
            ),
        ),
        "batch RUN RESULT STOP STOPPED and success ledger",
    )
    if run.count("agent_worker_create") or run.count("exec") or run.count("waitpid"):
        raise ValueError("per-program batch dispatch re-enters process launch")

    main = _function_tokens(tokens, "main")
    receipt_literals = (
        '"launcher=agentos-orchestrator\\n"',
        '"stage_launch=agent_create_role\\n"',
        '"support_launch=agent_worker_batch\\n"',
        '"support_spawn=agent_worker_create\\n"',
        '"support_role=delegated_non_agent_worker\\n"',
        '"worker_batch_groups=3\\n"',
        '"worker_batch_programs=58\\n"',
        '"worker_direct_launch=agent_worker_create\\n"',
        '"worker_direct_programs=2\\n"',
        '"worker_batch_identity=trusted_crt_batch_dispatch\\n"',
        '"worker_direct_identity=trusted_crt_self_check\\n"',
        '"role_policy=program_specific\\n"',
        '"launch_policy=kernel_bound_roles_and_delegated_workers\\n"',
        '"agent_bound_programs=rp_query,rp_repair,rp_execobs,rp_agent_collab,rp_auditor,rp_workbench,rp_package,rp_realtask,rp_service_surface,rp_backend\\n"',
        '"execution_ledger=rp_orch_timing\\n"',
        '"status=ready\\n"',
    )
    _ordered(main, tuple((literal,) for literal in receipt_literals), "batch role receipt")
    for literal in receipt_literals:
        _require_once(main, (literal,), "exact batch role receipt field")


def _validate_delegated_workflow(text: str) -> None:
    tokens = _tokens(text)
    _validate_orchestrator_worker_batches(tokens)
    for sequence, label in (
        (
            (
                "_Static_assert", "(", "EXEC_MANIFEST_VFS_CONTENT_READ", "==",
                "AGENT_CAP_CONTENT_READ",
            ),
            "content-read capability namespace",
        ),
        (
            (
                "_Static_assert", "(", "EXEC_MANIFEST_VFS_ARTIFACT_WRITE", "==",
                "AGENT_CAP_ARTIFACT_WRITE",
            ),
            "artifact-write capability namespace",
        ),
    ):
        _require_once(tokens, sequence, f"delegated {label}")
    main = _function_tokens(tokens, "main")
    start = ("int64", "steady_clock", "=", "get_mtime", "(", ")", ";")
    loop = (
        "for", "(", "int", "i", "=", "0", ";", "i", "<", "total", ";",
        "i", "++", ")", "{",
    )
    manifest_gate = (
        "if", "(", "!", "launch_manifest_valid", "(", ")", ")", "{",
        "printf", "(", '"rp_orch: launch_manifest_invalid\\n"', ")", ";",
        "return", "1", ";", "}",
    )
    batch_reset = ("worker_batch_session_reset", "(", ")", ";")
    production = (
        "int", "passed", "=", "in_orchestrator", "&&", "binding", "?",
        "run_worker_batch", "(", "&", "PROGRAMS", "[", "i", "]", ",",
        "binding", ",", "&", "orchestrator_identity", ")", ":", "run_child",
        "(", "&", "PROGRAMS", "[", "i", "]", ",", "in_orchestrator", ",",
        "&", "orchestrator_identity", ")", ";",
    )
    failure_break = (
        "if", "(", "!", "passed", ")", "{", "if", "(",
        "WORKER_BATCH_SESSION", ".", "active", "||", "WORKER_BATCH_SESSION",
        ".", "pid", ">", "0", ")", "worker_batch_reap", "(", "0", ")",
        ";", "break", ";", "}",
    )
    final_reap = (
        "if", "(", "WORKER_BATCH_SESSION", ".", "active", "||",
        "WORKER_BATCH_SESSION", ".", "pid", ">", "0", ")", "{",
        "printf", "(", '"rp_orch: batch_session_left_active\\n"', ")", ";",
        "worker_batch_reap", "(", "0", ")", ";", "ok", "=", "0", ";", "}",
    )
    ledger_write = (
        "rp_write_file", "(", '"rp_orch_timing"', ",", "rp_state_buf", ")",
    )
    inventory = (
        "append_program_inventory_evidence", "(", "in_orchestrator", ")",
    )
    completion = (
        "write_workflow_completion", "(", "completion_fd", ",", "&",
        "timing_handoff", ",", "(", "uint64", ")", "steady_clock", ")",
    )
    _ordered(
        main,
        (
            start, manifest_gate, batch_reset, loop, production,
            failure_break, final_reap, ledger_write, inventory, completion,
        ),
        "delegated workflow steady window",
    )
    _require_top_level(main, start, "delegated workflow start clock")
    _require_top_level(main, manifest_gate, "closed launch manifest gate")
    _require_top_level(main, batch_reset, "initial batch session reset")
    _require_top_level(main, loop, "ordered 70-program execution loop")
    if (
        main.count("run_worker_batch") != 1
        or main.count("run_child") != 1
        or main.count("worker_batch_session_reset") != 1
        or main.count("worker_batch_reap") != 2
        or main.count("break") != 1
    ):
        raise ValueError("delegated loop can bypass persistent-worker cleanup")
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

    context = _function_tokens(tokens, "orchestrator_context")
    _ordered(
        context,
        (
            ("pid", "=", "agent_launch_info", "(", "info", ")", ";"),
            ("if", "(", "pid", "<=", "0", ")", "return", "-", "1", ";"),
            ("if", "(", "!", "info", "->", "is_agent", ")", "return", "0", ";"),
            (
                "return", "info", "->", "agent_role", "==",
                "AGENT_ROLE_ORCHESTRATOR", "&&", "info", "->",
                "filesystem_domain", "!=", "0", "&&", "info", "->",
                "filesystem_capability_mask", "!=", "0", "?", "1", ":",
                "-", "1", ";",
            ),
        ),
        "delegated orchestrator launch identity",
    )
    if (
        context.count("agent_launch_info") != 1
        or _locations(context, ("agent_info", "("))
        or "getpid" in context
    ):
        raise ValueError("delegated orchestrator identity bypasses compact launch info")

    role_capabilities = _function_tokens(tokens, "role_filesystem_capabilities")
    for sequence, label in (
        (
            (
                "case", "AGENT_ROLE_INVESTIGATOR", ":", "return",
                "AGENT_CAP_CONTENT_READ", ";",
            ),
            "investigator",
        ),
        (
            (
                "case", "AGENT_ROLE_RECOVERY", ":", "case",
                "AGENT_ROLE_ORCHESTRATOR", ":", "case", "AGENT_ROLE_ARTIFACT",
                ":", "return", "RP_WORKFLOW_WORKER", ";",
            ),
            "workflow writer roles",
        ),
        (
            (
                "case", "AGENT_ROLE_SENTINEL", ":", "default", ":", "return",
                "0", ";",
            ),
            "sentinel",
        ),
    ):
        _require_once(role_capabilities, sequence, f"role VFS capability {label}")

    profile_capabilities = _function_tokens(
        tokens, "exec_profile_filesystem_capabilities"
    )
    for profile, capabilities in (
        ("EXEC_MANIFEST_VFS_PROFILE_WORKFLOW", "EXEC_MANIFEST_VFS_WORKFLOW_CAPS"),
        ("EXEC_MANIFEST_VFS_PROFILE_CONTENT_READ", "EXEC_MANIFEST_VFS_CONTENT_READ"),
        (
            "EXEC_MANIFEST_VFS_PROFILE_ARTIFACT_WRITE",
            "EXEC_MANIFEST_VFS_ARTIFACT_WRITE",
        ),
    ):
        _require_once(
            profile_capabilities,
            ("if", "(", "profile", "==", profile, ")", "return", capabilities, ";"),
            f"exec profile VFS capability {profile}",
        )

    expectation = _function_tokens(tokens, "launch_expectation_for")
    for sequence, label in (
        (
            (
                "orchestrator", "==", "0", "||", "!", "orchestrator", "->",
                "is_agent", "||", "orchestrator", "->", "agent_role", "!=",
                "AGENT_ROLE_ORCHESTRATOR",
            ),
            "trusted orchestrator",
        ),
        (
            (
                "orchestrator", "->", "filesystem_capability_mask", "&",
                "launch", "->", "worker_capabilities", ")", "!=", "launch",
                "->", "worker_capabilities",
            ),
            "delegated capability ceiling",
        ),
        (
            (
                "expected", "->", "is_agent", "=", "policy", "!=", "0", ";",
                "expected", "->", "agent_role", "=", "policy", "?", "policy",
                "->", "role", ":", "0", ";",
            ),
            "expected role identity",
        ),
        (
            (
                "if", "(", "policy", "!=", "0", ")", "{",
                "expected_capabilities", "=", "role_filesystem_capabilities", "(",
                "policy", "->", "role", ")", "&",
                "exec_profile_filesystem_capabilities", "(", "policy", "->",
                "vfs_profile", ")", "&", "orchestrator", "->",
                "filesystem_capability_mask", ";", "}", "else", "{",
                "expected_capabilities", "=", "launch", "->",
                "worker_capabilities", ";", "}",
            ),
            "role/profile VFS identity",
        ),
        (
            (
                "expected", "->", "filesystem_domain", "=", "orchestrator",
                "->", "filesystem_domain", ";", "expected", "->",
                "filesystem_capability_mask", "=", "expected_capabilities", ";",
            ),
            "expected VFS identity",
        ),
    ):
        _require_once(expectation, sequence, f"launch expectation {label}")

    formatter = _function_tokens(tokens, "format_launch_expectation")
    _ordered(
        formatter,
        (
            (
                "rp_copy_text", "(", "argument", ",", "RP_LAUNCH_EXPECT_ARG_SIZE",
                ",", "RP_LAUNCH_EXPECT_PREFIX", ")", ";",
            ),
            (
                "rp_append_uint_text", "(", "argument", ",",
                "RP_LAUNCH_EXPECT_ARG_SIZE", ",", "expected", "->", "is_agent",
                ")", ";",
            ),
            (
                "rp_append_uint_text", "(", "argument", ",",
                "RP_LAUNCH_EXPECT_ARG_SIZE", ",", "expected", "->", "agent_role",
                ")", ";",
            ),
            (
                "rp_append_uint_text", "(", "argument", ",",
                "RP_LAUNCH_EXPECT_ARG_SIZE", ",", "expected", "->",
                "filesystem_domain", ")", ";",
            ),
            (
                "rp_append_uint_text", "(", "argument", ",",
                "RP_LAUNCH_EXPECT_ARG_SIZE", ",", "expected", "->",
                "filesystem_capability_mask", ")", ";",
            ),
        ),
        "canonical launch expectation serialization",
    )

    child = _function_tokens(tokens, "run_child")
    for forbidden in (
        "pipe", "agent_scope_delegate_fd", "read_launch_attestation",
        "launch_attestation_valid", "attest_pipe", "read", "write", "close",
    ):
        if forbidden in child:
            raise ValueError(f"delegated child identity still uses {forbidden}")
    success_record = (
        "record_timing", "(", "program", ",", "launcher", ",", "pid",
        ",", "expectation_ready", "?", "&", "expected", ":", "0",
        ",", "RP_LAUNCH_IDENTITY_SOURCE", ",", "1", ",", "code", ",",
        "elapsed", ")", ";",
    )
    _ordered(
        child,
        (
            (
                "expectation_ready", "=", "launch_expectation_for", "(",
                "launch", ",", "policy", ",", "launcher", ",",
                "orchestrator_identity", ",", "&", "expected", ")", ";",
            ),
            (
                "!", "expectation_ready", "||", "!",
                "format_launch_expectation", "(", "identity_arg", ",", "&",
                "expected", ")",
            ),
            (
                "expectation_ready", "?", "identity_arg", ":", "0", ",", "0",
                ",", "}", ";", "if", "(", "exec", "(", "image", ",", "argv",
                ")", "<", "0", ")",
            ),
            ("got", "=", "waitpid", "(", "pid", ",", "&", "code", ")", ";"),
            ("if", "(", "got", "!=", "pid", ")"),
            ("if", "(", "code", "!=", "0", ")"),
            success_record,
        ),
        "exec child trusted CRT identity proof",
    )
    _require_top_level(
        child, success_record, "successful trusted CRT identity record"
    )
    if child.count("waitpid") != 1 or child.count("exec") != 1:
        raise ValueError("delegated child identity is not bound to one exec/wait pair")

    timing = _function_tokens(tokens, "record_timing")
    _require_once(
        timing,
        (
            "identity_ready", "=", "pid", ">", "0", "&&",
            "rp_launch_expectation_valid", "(", "expected", ")", ";",
        ),
        "successful child identity readiness",
    )
    _require_once(
        timing,
        (
            "identity_ready", "&&", "identity_source", "?", "identity_source",
            ":", '"unavailable"',
        ),
        "caller-bound launch identity source",
    )
    if timing.count("identity_source") != 2:
        raise ValueError("timing ledger identity source can be substituted")

    _ordered(
        timing,
        (
            (
                "if", "(", "strcmp", "(", "launcher", ",", '"fork"', ")",
                "==", "0", ")", "{", "rp_append_text", "(", "line", ",",
                "sizeof", "(", "line", ")", ",", '";launcher=fork"', ")",
                ";", "goto", "append_outcome", ";", "}",
            ),
            ("rp_append_text", "(", "line", ",", "sizeof", "(", "line", ")", ",", '";role="', ")", ";"),
            ("append_outcome", ":", "rp_append_text", "(", "line", ",", "sizeof", "(", "line", ")", ",", '";ok="', ")", ";"),
            ("rp_state_append_line", "(", "rp_state_buf", ",", "RP_STATE_BUFFER_SIZE", ",", '"rp_orch_timing"', ",", "line", ")", ";"),
        ),
        "plain five-field versus Agent attested timing schema",
    )

    inventory_writer = _function_tokens(tokens, "append_program_inventory_evidence")
    _ordered(
        inventory_writer,
        (
            (
                "const", "char", "*", "launcher", "=", "in_orchestrator", "?",
                '"mixed_attested"', ":", '"fork"', ";",
            ),
            (
                "PROGRAM_NAMES", ",", "expected_programs", ",", "launcher", ",",
                "in_orchestrator", ",", "&", "inventory", ")",
            ),
        ),
        "mode-bound program ledger profile",
    )
    header = (
        "rp_copy_text", "(", "rp_state_buf", ",", "RP_STATE_BUFFER_SIZE", ",",
        "in_orchestrator", "?",
        '"orchestrator=rp_orch\\nlauncher=mixed_attested\\n"', ":",
        '"orchestrator=rp_orch\\nlauncher=fork\\n"', ")", ";",
    )
    _require_once(main, header, "mode-bound program ledger header")


def _validate_worker_batch_evidence(evidence: str) -> None:
    tokens = _tokens(evidence)
    batch = _function_tokens(tokens, "rp_evidence_worker_batch_program")
    _ordered(
        batch,
        (
            ("RP_WORKER_BATCH_0_PROGRAMS", "(", "RP_EVIDENCE_BATCH_MATCH", ")"),
            ("RP_WORKER_BATCH_1_PROGRAMS", "(", "RP_EVIDENCE_BATCH_MATCH", ")"),
            ("RP_WORKER_BATCH_2_PROGRAMS", "(", "RP_EVIDENCE_BATCH_MATCH", ")"),
            ("return", "0", ";"),
        ),
        "evidence batch membership",
    )
    direct = _function_tokens(tokens, "rp_evidence_worker_direct_program")
    _require_once(
        direct,
        ("RP_WORKER_DIRECT_PROGRAMS", "(", "RP_EVIDENCE_DIRECT_MATCH", ")"),
        "evidence direct membership",
    )
    declared = _function_tokens(tokens, "rp_evidence_declared_role")
    _require_once(
        declared,
        ("RP_AGENTOS_ROLE_PROGRAMS", "(", "RP_EVIDENCE_ROLE_MATCH", ")"),
        "evidence role membership",
    )

    parser = _function_tokens(tokens, "rp_evidence_parse_program_record")
    _ordered(
        parser,
        (
            (
                "if", "(", "plain", "&&", "rp_evidence_worker_batch_program",
                "(", "expected_program", ")", ")", "{", "expected_launcher",
                "=", '"agent_worker_batch"', ";", "expected_identity_source",
                "=", '"trusted_crt_batch_dispatch"', ";", "}",
            ),
            (
                "else", "if", "(", "plain", "&&",
                "rp_evidence_worker_direct_program", "(", "expected_program", ")",
                ")", "{", "expected_launcher", "=", '"agent_worker_create"',
                ";", "expected_identity_source", "=",
                '"trusted_crt_self_check"', ";", "}",
            ),
            (
                "else", "if", "(", "!", "plain", "&&", "declared_role",
                "!=", "0", "&&", "rp_evidence_field_equal", "(", "role", ",",
                "role_len", ",", "declared_role", ")", ")", "{",
                "expected_launcher", "=", '"agent_create_role"', ";",
                "expected_identity_source", "=", '"trusted_crt_self_check"',
                ";", "}", "else", "{", "return", "0", ";", "}",
            ),
            (
                "rp_evidence_consume_field", "(", "line", ",", "len", ",",
                "&", "pos", ",", '"identity_source"', ",", "&", "value", ",",
                "&", "value_len", ")",
            ),
            (
                "rp_evidence_field_equal", "(", "value", ",", "value_len", ",",
                "expected_identity_source", ")",
            ),
        ),
        "launcher and identity evidence classification",
    )


def _validate_package_publish_order(text: str) -> None:
    main = _function_tokens(_tokens(text), "main")
    commit = (
        "if", "(", "package_state", ".", "append_active", "&&", "!",
        "rp_state_buffer_commit", "(", "&", "package_state", ")", ")",
        "return", "1", ";",
    )
    ack = (
        "rp_append_file", "(", '"rp_ack"', ",",
        '"ack=package;msg=11;status=ready"', ")",
    )
    tool = (
        "rp_append_file", "(", '"rp_tool"', ",", "package_tool_lines", ")",
    )
    status = ("rp_append_status", "(", '"package=ready\\n"')
    _ordered(
        main, (commit, ack, tool, status),
        "package authoritative commit before ready publications",
    )
    _require_top_level(main, commit, "package authoritative transaction commit")
    for sequence, label in (
        (commit, "package commit"), (ack, "package ack"),
        (tool, "package tool ledger"), (status, "package ready status"),
    ):
        _require_once(main, sequence, label)


def _validate_launch_self_check(main_text: str, header: str, evidence: str) -> None:
    for declaration in (
        '#define RP_LAUNCH_EXPECT_PREFIX "--rp-launch-expect="',
        '#define RP_LAUNCH_IDENTITY_SOURCE "trusted_crt_self_check"',
        '#define RP_LAUNCH_BATCH_IDENTITY_SOURCE "trusted_crt_batch_dispatch"',
        "#define RP_LAUNCH_SELF_CHECK_EXIT 125",
        "struct rp_launch_expectation",
        "int is_agent;",
        "int agent_role;",
        "uint64 filesystem_domain;",
        "uint64 filesystem_capability_mask;",
    ):
        if declaration not in header:
            raise ValueError(f"trusted CRT launch contract differs: {declaration}")
    if "child_after_exec" in header or "child_after_exec" in evidence:
        raise ValueError("legacy pipe identity source remains in the Guest contract")
    if '"trusted_crt_self_check"' not in evidence:
        raise ValueError("Guest evidence parser lacks trusted CRT identity source")
    _validate_worker_batch_evidence(evidence)

    validity = _function_tokens(_tokens(header), "rp_launch_expectation_valid")
    _ordered(
        validity,
        (
            (
                "expected", "==", "0", "||", "expected", "->",
                "filesystem_domain", "==", "0", "||", "expected", "->",
                "filesystem_capability_mask", "==", "0", "||", "(",
                "expected", "->", "is_agent", "!=", "0", "&&", "expected",
                "->", "is_agent", "!=", "1", ")",
            ),
            (
                "if", "(", "expected", "->", "is_agent", ")", "return",
                "expected", "->", "agent_role", ">=", "AGENT_ROLE_SENTINEL",
                "&&", "expected", "->", "agent_role", "<=",
                "AGENT_ROLE_ARTIFACT", ";",
            ),
            (
                "return", "expected", "->", "agent_role", "==", "0", ";",
            ),
        ),
        "launch expectation fail-closed validity",
    )

    tokens = _tokens(main_text)
    parser = _function_tokens(tokens, "rp_parse_launch_uint")
    for sequence, label in (
        (
            (
                "*", "digits", "==", "'0'", "&&", "digits", "[", "1", "]",
                ">=", "'0'", "&&", "digits", "[", "1", "]", "<=", "'9'",
            ),
            "leading zero rejection",
        ),
        (
            (
                "parsed", ">", "(", "~", "0ULL", "-", "digit", ")", "/",
                "10",
            ),
            "uint64 overflow rejection",
        ),
        (
            (
                "delimiter", "!=", "0", "&&", "*", "digits", "!=",
                "delimiter",
            ),
            "field delimiter rejection",
        ),
    ):
        _require_once(parser, sequence, f"launch expectation {label}")

    expectation = _function_tokens(tokens, "rp_parse_launch_expectation")
    _ordered(
        expectation,
        (
            (
                "argc", "<", "2", "||", "argv", "==", "0", "||", "argv",
                "[", "1", "]", "==", "0",
            ),
            (
                "cursor", "=", "argv", "[", "1", "]", "+", "prefix_len", ";",
            ),
            (
                "rp_parse_launch_uint", "(", "&", "cursor", ",", "','", ",",
                "&", "is_agent", ")",
            ),
            (
                "rp_parse_launch_uint", "(", "&", "cursor", ",", "','", ",",
                "&", "role", ")",
            ),
            (
                "rp_parse_launch_uint", "(", "&", "cursor", ",", "','", ",",
                "&", "expected", "->", "filesystem_domain", ")",
            ),
            (
                "rp_parse_launch_uint", "(", "&", "cursor", ",", "0", ",",
                "&", "expected", "->", "filesystem_capability_mask", ")",
            ),
            (
                "return", "rp_launch_expectation_valid", "(", "expected", ")",
                "?", "1", ":", "-", "1", ";",
            ),
        ),
        "strict launch expectation parser",
    )

    self_check = _function_tokens(tokens, "rp_launch_identity_self_check")
    _ordered(
        self_check,
        (
            (
                "parsed", "=", "rp_parse_launch_expectation", "(", "argc", ",",
                "argv", ",", "&", "expected", ")", ";",
            ),
            ("if", "(", "parsed", "<", "0", ")", "return", "0", ";"),
            ("pid", "=", "agent_launch_info", "(", "&", "info", ")", ";"),
            (
                "return", "pid", ">", "0", "&&", "info", ".", "is_agent",
                "==", "expected", ".", "is_agent", "&&", "info", ".",
                "agent_role", "==", "expected", ".", "agent_role", "&&", "info",
                ".", "filesystem_domain", "==", "expected", ".",
                "filesystem_domain", "&&", "info", ".",
                "filesystem_capability_mask", "==", "expected", ".",
                "filesystem_capability_mask", ";",
            ),
        ),
        "trusted CRT launch identity self-check",
    )
    if (
        self_check.count("agent_launch_info") != 1
        or _locations(self_check, ("agent_info", "("))
        or any(
            token in self_check
            for token in ("getpid", "pipe", "read", "write", "close")
        )
    ):
        raise ValueError("trusted CRT launch self-check performs extra identity I/O")

    start = _function_tokens(tokens, "__start_main")
    guard = (
        "if", "(", "!", "rp_launch_identity_self_check", "(", "argc", ",",
        "argv", ")", ")", "exit", "(", "RP_LAUNCH_SELF_CHECK_EXIT", ")", ";",
    )
    invoke = ("exit", "(", "main", "(", "argc", ",", "argv", ")", ")", ";")
    _ordered(start, (guard, invoke), "trusted CRT pre-main launch identity guard")
    _require_top_level(start, guard, "trusted CRT launch identity guard")


def _validate_launch_info_kernel(core: str) -> None:
    body = _function_tokens(_tokens(core), "sys_agent_info")
    _require_once(
        body,
        ("struct", "proc", "*", "p", "=", "curr_proc", "(", ")", ";"),
        "compact launch identity current process",
    )
    compact_start = _require_once(
        body,
        ("if", "(", "launch_identity", ")", "{"),
        "compact launch identity branch",
    )
    compact_end = _require_once(
        body,
        ("}", "else", "{"),
        "compact/full identity branch boundary",
    )
    if compact_start >= compact_end:
        raise ValueError("compact launch identity branch is not before full diagnostics")
    compact = body[compact_start:compact_end]
    _ordered(
        compact,
        (
            ("memset", "(", "&", "info", ",", "0", ",", "sizeof", "(", "info", ")", ")", ";"),
            ("info", ".", "is_agent", "=", "p", "->", "is_agent", ";"),
            ("info", ".", "agent_id", "=", "p", "->", "agent_id", ";"),
            ("info", ".", "agent_role", "=", "p", "->", "agent_role", ";"),
            (
                "info", ".", "capability_mask", "=",
                "agent_identity_proc_scope", "(", "p", ")", "!=",
                "VFS_SCOPE_NONE", "?", "p", "->", "agent_capability_mask", ":",
                "0", ";",
            ),
            (
                "info", ".", "filesystem_domain", "=", "p", "->",
                "vfs_scope_id", ";",
            ),
            (
                "info", ".", "filesystem_capability_mask", "=",
                "vfs_scope_active", "(", "p", "->", "vfs_scope_id", ")", "?",
                "p", "->", "vfs_effective_caps", ":", "0", ";",
            ),
        ),
        "compact current-process launch identity fill",
    )
    if any(
        forbidden in compact
        for forbidden in (
            "agent_info_fill", "agent_metadata_fill_info", "agent_ticks",
            "agent_observe_scope_epoch",
        )
    ):
        raise ValueError("compact launch identity branch reads full diagnostics")
    _require_once(
        body,
        (
            "return", "result", "<", "0", "||", "!", "launch_identity", "?",
            "result", ":", "p", "->", "pid", ";",
        ),
        "compact launch identity current PID return",
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
    _require_once(
        agentos_tokens,
        ("#", "define", "RP_RESOURCE_STABILITY_ADMISSION_TIMEOUT_MS", "30000"),
        "30-second resource stability retirement deadline",
    )
    retirement = _function_tokens(agentos_tokens, "stability_scope_retired")
    if _token_fingerprint(retirement) != (
        "0c8edc739bc80fd7b2bf9bbf1bd18b82bff6ad81cd7eb18fd2037ae06eb4b658"
    ):
        raise ValueError("resource stability scope-retirement retry body differs")
    _ordered(
        retirement,
        (
            ("int64", "now", "=", "get_mtime", "(", ")", ";"),
            (
                "deadline", "=", "now", "<", "0", "?", "-", "1", ":",
                "now", "+", "RP_RESOURCE_STABILITY_ADMISSION_TIMEOUT_MS", ";",
            ),
            (
                "status", "=", "agent_workflow_close", "(", "report", "->",
                "scope_id", ")", ";",
            ),
            (
                "if", "(", "status", "==", "AGENT_STATUS_NOT_FOUND", ")",
                "return", "1", ";",
            ),
            ("return", "1", ";", "now", "=", "get_mtime", "(", ")", ";"),
            (
                "if", "(", "status", "!=", "AGENT_STATUS_RETRY", "||",
                "now", "<", "0", "||", "deadline", "<", "0", "||",
                "now", ">=", "deadline", "||", "sleep", "(", "10", ")",
                "<", "0", ")", "return", "0", ";",
            ),
        ),
        "resource stability scope retirement",
    )
    if (
        retirement.count("agent_workflow_close") != 1
        or retirement.count("get_mtime") != 2
        or retirement.count("sleep") != 1
        or retirement.count("return") != 2
        or retirement.count("break") != 0
        or retirement.count("goto") != 0
        or retirement.count("continue") != 0
    ):
        raise ValueError("resource stability scope-retirement control flow differs")

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
            (
                "wait_status", "=", "pid", "==", "AGENT_STATUS_RETRY", "?",
                "sched_yield", "(", ")", ":", "sleep", "(", "1", ")", ";",
            ),
            ("get_mtime", "(", ")", ">=", "admission_deadline"),
            ("wait_status", "<", "0"),
            ("rp_copy_text", "(", "nonce_arg", ",", "sizeof", "(", "nonce_arg", ")", ",", "RP_RESOURCE_STABILITY_NONCE_PREFIX", ")"),
            ("rp_append_uint_text", "(", "nonce_arg", ",", "sizeof", "(", "nonce_arg", ")", ",", "challenge_nonce", ")"),
            ("nonce_arg", ",", "0", ","),
            ("exec", "(", '"rp_resprobe"', ",", "argv", ")"),
            ("read_stability_report", "(", "report_pipe", "[", "0", "]", ",", "report", ")"),
            ("read", "(", "report_pipe", "[", "0", "]", ",", "&", "extra", ",", "1", ")"),
            ("waitpid", "(", "pid", ",", "&", "code", ")"),
            (
                "if", "(", "!", "stability_report_valid", "(", "report", ",",
                "index", ",", "mode", ",", "challenge_nonce", ")", ")",
                "mismatch", "|", "=", "1U", "<<", "3", ";",
            ),
            (
                "if", "(", "mismatch", "==", "0", "&&", "!",
                "stability_scope_retired", "(", "report", ")", ")",
                "mismatch", "|", "=", "1U", "<<", "6", ";",
            ),
            ("agent_resource_snapshot", "(", "global_after", ")"),
            ("stability_identity_unique", "(", "index", "+", "1", ")"),
            ("verify_global", "&&"),
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
            (
                "run_stability_workflow", "(", "0", ",",
                "RP_RESOURCE_STABILITY_MODE_TERMINAL", ",", "0", ")",
            ),
            ("memset", "(", "orch_stability_reports", ",", "0", ",", "sizeof", "(", "orch_stability_reports", ")", ")"),
            ("memset", "(", "orch_stability_global_before", ",", "0", ",", "sizeof", "(", "orch_stability_global_before", ")", ")"),
            ("memset", "(", "orch_stability_global_after", ",", "0", ",", "sizeof", "(", "orch_stability_global_after", ")", ")"),
            ("run_stability_workflow", "(", "index", ",", "mode", ",", "1", ")"),
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
    _require_once(
        report_valid,
        (
            "report", "->", "final_completion_sequence", "<",
            "report", "->", "initial_completion_sequence",
        ),
        "monotonic settlement receipt",
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
    _require_once(
        final_state,
        (
            "final_io", ".", "completion_sequence", "<",
            "initial_io", ".", "completion_sequence",
        ),
        "monotonic settlement I/O sequence",
    )
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
    _only_references(global_pair, "bound", 2, "per-workflow growth bound")
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
    _only_references(global_sequence, "bound", 3, "sequence growth bound")
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
    settle = _function_tokens(probe_tokens, "settle_current_workflow")
    _ordered(
        settle,
        (
            ("started", "=", "get_mtime", "(", ")", ";"),
            ("for", "(", ";", ";", ")", "{"),
            ("status", "=", "sync", "(", ")", ";"),
            ("status", "==", "0"),
            ("get_mtime", "(", ")", ">=", "deadline"),
            ("sleep", "(", "1", ")", "<", "0"),
        ),
        "workflow resource fence",
    )
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
            ("fence_status", "=", "settle_current_workflow", "(", ")", ";"),
            ("fence_status", "<", "0"),
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
            ("memset", "(", "snapshots", ",", "0", ",", "sizeof", "(", "*", "snapshots", ")", "*", "count", ")", ";"),
            ("enabled", "=", "intr_save", "(", ")", ";"),
            ("resource_account_trim_locked", "(", "account", ")", ";"),
            ("counter", "=", "resource_account_counter_const", "("),
            ("snapshot", "->", "used", "+", "=", "counter", "->", "used", ";"),
            ("snapshot", "->", "pending", "+", "=", "counter", "->", "pending", ";"),
            ("snapshot", "->", "ordinary_used", "+", "=", "counter", "->", "used", ";"),
            ("snapshot", "->", "reserved_pending", "+", "=", "counter", "->", "pending", ";"),
            ("snapshot", "->", "capacity", "=", "policy", "->", "capacity", ";"),
            ("policy", "->", "held", "!=", "snapshot", "->", "used", "+", "snapshot", "->", "pending"),
            ("panic", "(", '"resource exact snapshot invariant"', ")", ";"),
            ("intr_restore", "(", "enabled", ")", ";"),
        ),
        "rstat-style exact resource controller snapshot",
    )
    _only_references(policy, "intr_save", 1, "resource aggregation IRQ cut")
    _only_references(policy, "intr_restore", 1, "resource aggregation IRQ cut")

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
    if authorization != [
        "return", "p", "!=", "0", "&&", "exec_policy_process_bootstrap",
        "(", "p", ")", "&&", "(", "p", "->",
        "resource_domain_admin", "||", "(", "p", "->", "agent_role",
        "==", "AGENT_ROLE_ORCHESTRATOR", "&&", "p", "->",
        "agent_controller_id", "==", "0", "&&", "agent_identity_has_cap",
        "(", "p", ",", "AGENT_CAP_ORCHESTRATE", ")", ")", ")", ";",
    ]:
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
    identity_tokens = _tokens(identity_source)
    has_cap = _function_tokens(identity_tokens, "agent_identity_has_cap")
    if has_cap != [
        "return", "p", "!=", "0", "&&", "p", "->", "is_agent", "&&",
        "agent_identity_proc_scope", "(", "p", ")", "!=", "VFS_SCOPE_NONE",
        "&&", "exec_policy_process_allows_role", "(", "p", ",", "p", "->",
        "agent_role", ")", "&&", "(", "p", "->", "agent_capability_mask",
        "&", "cap", ")", "==", "cap", ";",
    ]:
        raise ValueError("performance observer capability authority differs")
    identity = _function_tokens(
        identity_tokens, "agent_identity_authority_bootstrap"
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


def _validate_performance_pair_consumer(source: str, workload_header: str) -> None:
    """Bind showcase receipts to one observer's raw performance snapshot."""

    workload_tokens = _tokens(workload_header)
    _require_once(
        workload_tokens,
        ("#", "define", "LABDEMO_RETRY_TIMEOUT_MS", "30000"),
        "showcase retry timeout",
    )
    if workload_tokens.count("LABDEMO_RETRY_TIMEOUT_MS") != 1:
        raise ValueError("showcase retry timeout differs")

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
    fence_pairs = (
        ("fs_epoch_commits", "epoch_commits"),
        ("fs_epoch_buffers_staged", "epoch_buffers_staged"),
        ("block_physical_writes", "physical_writes"),
        ("block_physical_reads", "physical_reads"),
        ("block_durable_flushes", "durable_flushes"),
        ("fs_epoch_deduplicated_stages", "deduplicated_stages"),
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
    retired_metadata_fields = (
        "metadata_dirty",
        "metadata_durable",
        "metadata_requests",
        "metadata_coalesced",
        "metadata_commits",
        "metadata_pending",
    )
    raw_names = tuple(name for _field, name in snapshot_pairs)
    storage_fields = tuple(
        field for field, _name in fence_pairs
        if field != "observer_workload_syscalls"
    )
    tokens = _tokens(source)
    _require_once(
        workload_tokens,
        (
            "struct", "labdemo_performance_receipt", "{",
            "uint64", "observer_pid", ";",
            "struct", "agent_performance_snapshot", "snapshot", ";",
            "}", ";",
        ),
        "showcase snapshot-only performance receipt",
    )
    _require_once(
        workload_tokens,
        (
            "struct", "labdemo_fence_receipt", "{",
            "uint64", "tick_us", ";",
            "uint64", "attempts", ";",
            "uint64", "stable_rounds", ";",
            "struct", "labdemo_performance_receipt", "performance", ";",
            "}", ";",
        ),
        "showcase snapshot-only fence receipt",
    )
    for field in retired_metadata_fields:
        if field in workload_header or field in source:
            raise ValueError(
                f"showcase retired synthetic metadata field remains: {field}"
            )

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
    if (
        take.count("agent_performance_snapshot") != 1
        or take.count("agent_info") != 0
        or take.count("return") != 0
    ):
        raise ValueError("showcase performance snapshot control flow differs")

    storage_equal = _function_tokens(tokens, "performance_storage_equal")
    expected_storage_equal = ["return"]
    for index, field in enumerate(storage_fields):
        expected_storage_equal.extend(
            ("left", "->", field, "==", "right", "->", field)
        )
        expected_storage_equal.append(
            ";" if index + 1 == len(storage_fields) else "&&"
        )
    if storage_equal != expected_storage_equal:
        raise ValueError("showcase storage-only fence stability fields differ")

    sync_fence = _function_tokens(tokens, "demo_quiescence_flush")
    if _token_fingerprint(sync_fence) != (
        "a73e61508aea1a449b09e71eedf7279417c62bb0dc9851c416a77df333f77dbc"
    ):
        raise ValueError("showcase quiescence retry body differs")
    _ordered(
        sync_fence,
        (
            ("deadline", "=", "get_mtime", "(", ")", ";"),
            ("if", "(", "deadline", "<", "0", ")", "return", "status", ";"),
            ("deadline", "+", "=", "LABDEMO_RETRY_TIMEOUT_MS", ";"),
            ("now", "=", "get_mtime", "(", ")", ";"),
            (
                "if", "(", "now", "<", "0", "||", "now", ">=", "deadline",
                "||", "sleep", "(", "10", ")", "<", "0", ")", "return",
                "status", ";",
            ),
        ),
        "showcase bounded quiescence retry",
    )
    flush_call = (
        "status", "=", "fd", "<", "0", "?", "sync", "(", ")", ":",
        "fsync", "(", "fd", ")", ";",
    )
    flushes = _locations(sync_fence, flush_call)
    successes = _locations(
        sync_fence,
        ("if", "(", "status", ">=", "0", ")", "return", "status", ";"),
    )
    deadline_at = _require_once(
        sync_fence, ("deadline", "=", "get_mtime", "(", ")", ";"),
        "showcase quiescence deadline",
    )
    sleep_at = _require_once(
        sync_fence, ("sleep", "(", "10", ")", "<", "0"),
        "showcase quiescence sleep",
    )
    status_assignments = _locations(sync_fence, ("status", "="))
    if (
        len(flushes) != 2 or len(successes) != 2
        or not flushes[0] < successes[0] < deadline_at < sleep_at
        < flushes[1] < successes[1]
        or sync_fence.count("sync") != 2 or sync_fence.count("fsync") != 2
        or sync_fence.count("sleep") != 1 or sync_fence.count("return") != 4
        or sync_fence.count("break") != 0 or sync_fence.count("goto") != 0
        or sync_fence.count("continue") != 0 or status_assignments != flushes
    ):
        raise ValueError("showcase quiescence retry control flow differs")
    if _function_tokens(tokens, "demo_quiescence_sync") != [
        "return", "demo_quiescence_flush", "(", "-", "1", ")", ";",
    ] or _function_tokens(tokens, "demo_quiescence_fsync") != [
        "return", "demo_quiescence_flush", "(", "fd", ")", ";",
    ]:
        raise ValueError("showcase quiescence wrappers differ")

    fence = _function_tokens(tokens, "demo_quiescence_fence")
    fence_sync_at = _require_once(
        fence,
        (
            "check", "(", "demo_quiescence_sync", "(", ")", "==", "0", ",",
            '"quiescence sync"', ")", ";",
        ),
        "showcase quiescence fence sync",
    )
    _require_once(
        fence,
        (
            "memset", "(", "&", "previous", ",", "0", ",", "sizeof", "(",
            "previous", ")", ")", ";", "for", "(", "int", "attempt", "=", "1",
            ";", "attempt", "<=", "LABDEMO_FENCE_MAX_ATTEMPTS", ";", "attempt",
            "++", ")", "{", "check", "(", "demo_quiescence_sync", "(", ")",
        ),
        "showcase quiescence fence unconditional loop",
    )
    if (
        fence.count("demo_quiescence_sync") != 1 or fence.count("sync") != 0
        or _depth_at(fence, fence_sync_at) != 1
    ):
        raise ValueError("showcase quiescence fence bypasses bounded sync")
    _require_once(
        fence,
        (
            "if", "(", "attempt", ">", "1", "&&",
            "current", ".", "observer_pid", "==", "previous", ".",
            "observer_pid", "&&", "current", ".", "snapshot", ".",
            "observer_lifecycle_id", "==", "previous", ".", "snapshot", ".",
            "observer_lifecycle_id", "&&", "current", ".", "snapshot", ".",
            "observer_lifecycle_generation", "==", "previous", ".",
            "snapshot", ".", "observer_lifecycle_generation", "&&",
            "current", ".", "snapshot", ".", "sample_tick", ">",
            "previous", ".", "snapshot", ".", "sample_tick", "&&",
            "performance_storage_equal", "(", "&", "previous", ".",
            "snapshot", ",", "&", "current", ".", "snapshot", ")", ")",
            "stable_rounds", "++", ";", "else", "stable_rounds", "=", "0", ";",
        ),
        "showcase storage-only fence stability",
    )
    if fence.count("performance_storage_equal") != 1:
        raise ValueError("showcase storage-only fence comparison differs")

    fence_records = tuple(
        token for token in fence
        if token.startswith('"') and "kind=fence" in token
    )
    if len(fence_records) != 1:
        raise ValueError("showcase fence record structure differs")
    expected_fence_record = (
        '"agentos:demo schema=2 nonce=%llu kind=fence mode=%s seq=%d '
        "point=%s tick_us=%llu attempts=%llu stable_rounds=%llu "
        "observer_pid=%llu observer_tick=%llu observer_lifecycle_id=%llu "
        "observer_lifecycle_generation=%llu counter_scope=global"
        + "".join(f" {name}=%llu" for _field, name in fence_pairs)
        + '\\n"'
    )
    if fence_records[0] != expected_fence_record:
        raise ValueError("showcase storage-only fence serialization differs")
    fence_values = [
        "(", "uint64", ")", "LABDEMO_RUN_NONCE", ",",
        "mode", ",", "sequence", ",", "point", ",",
        "receipt", "->", "tick_us", ",",
        "receipt", "->", "attempts", ",",
        "receipt", "->", "stable_rounds", ",",
        "current", ".", "observer_pid", ",",
        "current", ".", "snapshot", ".", "sample_tick", ",",
        "current", ".", "snapshot", ".", "observer_lifecycle_id", ",",
        "current", ".", "snapshot", ".", "observer_lifecycle_generation", ",",
    ]
    for index, (field, _name) in enumerate(fence_pairs):
        fence_values.extend(("current", ".", "snapshot", ".", field))
        if index + 1 != len(fence_pairs):
            fence_values.append(",")
    _require_once(
        fence, tuple(fence_values), "showcase storage-only fence value bindings"
    )
    if fence.count("printf") != 1:
        raise ValueError("showcase fence emission control flow differs")

    run_one = _function_tokens(tokens, "run_one")
    if _token_fingerprint(run_one) != (
        "c0e4cbcfaf16cfd8049220f0e94cb0feb346f525247ebae55b930ae5f1fae39d"
    ):
        raise ValueError("showcase metadata action retry body differs")
    _ordered(
        run_one,
        (
            ("deadline", "=", "get_mtime", "(", ")", ";"),
            (
                "if", "(", "deadline", ">=", "0", ")", "deadline", "+", "=",
                "LABDEMO_RETRY_TIMEOUT_MS", ";",
            ),
            ("n", "=", "agent_run", "(", "op", ",", "res", ",", "1", ",", "0", ")", ";"),
            (
                "if", "(", "n", "!=", "1", "||", "status", "!=",
                "AGENT_STATUS_OK", "||", "res", "->", "status", "!=",
                "AGENT_STATUS_RETRY", "||", "(", "op", "->", "tool_id", "!=",
                "AGENT_TOOL_ACTION_COMMIT", "&&", "op", "->", "tool_id", "!=",
                "AGENT_TOOL_ARTIFACT_UPDATE", ")", ")", "break", ";",
            ),
            ("now", "=", "get_mtime", "(", ")", ";"),
            (
                "check", "(", "deadline", ">=", "0", "&&", "now", ">=", "0",
                "&&", "now", "<", "deadline", "&&", "sleep", "(", "10", ")",
                "==", "0", ",", '"agent run retry wait"', ")", ";",
            ),
        ),
        "showcase metadata action retry",
    )
    if (
        run_one.count("agent_run") != 1 or run_one.count("sleep") != 1
        or run_one.count("return") != 0 or run_one.count("break") != 1
        or run_one.count("goto") != 0 or run_one.count("continue") != 0
        or _locations(run_one, ("status", "="))
        or _locations(run_one, ("res", "->", "status", "="))
        or _locations(run_one, ("op", "->", "tool_id", "="))
        or _locations(run_one, ("op", "->", "request_id", "="))
    ):
        raise ValueError("showcase metadata action retry control flow differs")

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
    if pair.count("performance_delta") != len(snapshot_pairs):
        raise ValueError("showcase performance pair has extra delta producers")
    string_literals = tuple(
        token for token in pair if token.startswith('"') and token.endswith('"')
    )
    if len(string_literals) != 3:
        raise ValueError("showcase performance pair record structure differs")
    record = string_literals[-1]
    expected_record = (
        '"agentos:demo schema=2 nonce=%llu kind=mechanism mode=%s scope=%s '
        "observer_pid=%llu before_tick=%llu after_tick=%llu "
        "observer_lifecycle_id=%llu observer_lifecycle_generation=%llu "
        "counter_scope=global"
        + "".join(
            f" before_{name}=%llu after_{name}=%llu"
            for _field, name in snapshot_pairs
        )
        + '\\n"'
    )
    if record != expected_record:
        raise ValueError("showcase snapshot-only mechanism serialization differs")
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
                "check", "(", "demo_quiescence_sync", "(", ")", "==", "0", ",",
                '"workflow setup boundary"', ")", ";",
            ),
            (
                "take_performance_snapshot", "(", "&",
                "workflow_perf_before", ")", ";",
            ),
            (
                "check", "(", "demo_quiescence_sync", "(", ")", "==", "0", ",",
                '"workflow completion sync"', ")", ";",
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
    if orchestrator.count("demo_quiescence_sync") != 2 or orchestrator.count("sync") != 0:
        raise ValueError("showcase workflow boundaries bypass bounded sync")
    setup_at = _require_once(
        orchestrator,
        (
            "seed_demo_metadata", "(", ")", ";", "check", "(",
            "demo_quiescence_sync", "(", ")", "==", "0", ",",
            '"workflow setup boundary"', ")", ";", "take_performance_snapshot",
            "(", "&", "workflow_perf_before", ")", ";",
        ),
        "showcase unconditional workflow setup boundary",
    )
    durable_at = _require_once(
        orchestrator,
        (
            "check_provenance_graph", "(", "sentinel_pid", ")", ";", "check", "(",
            "demo_quiescence_sync", "(", ")", "==", "0", ",",
            '"workflow completion sync"', ")", ";", "workflow_finished_us", "=",
            "demo_now_us", "(", ")", ";",
        ),
        "showcase unconditional workflow completion boundary",
    )
    if _depth_at(orchestrator, setup_at) != 0 or _depth_at(orchestrator, durable_at) != 0:
        raise ValueError("showcase workflow boundary is conditional")
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
        primary_at = _require_once(
            lane,
            (
                "check", "(", "demo_quiescence_fsync", "(", "fd", ")", "==", "0", ",",
                f'"{mode} recovery primary ack"', ")", ";",
            ),
            f"{mode} bounded primary acknowledgement",
        )
        previous = (
            ('"compat recovery write"', ")", ";") if mode == "compat" else
            ('"native recovery metadata stage"', ")", ";")
        )
        _require_once(
            lane,
            previous + (
                "check", "(", "demo_quiescence_fsync", "(", "fd", ")", "==", "0",
                ",", f'"{mode} recovery primary ack"', ")", ";", "check", "(",
                "close", "(", "fd", ")", "==", "0", ",",
            ),
            f"{mode} unconditional primary acknowledgement",
        )
        if (
            lane.count("demo_quiescence_fsync") != 1 or lane.count("fsync") != 0
            or _depth_at(lane, primary_at) != 1
        ):
            raise ValueError(f"{mode} primary acknowledgement bypasses bounded fsync")


def _validate_lifecycle_identity(abi: str, implementation: str) -> None:
    for declaration in (
        "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 3U",
        "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION 2U",
        "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE 64U",
        "unsigned int resource_account_valid;",
        "unsigned int resource_account_slot;",
        "unsigned long long resource_account_generation;",
        "unsigned int scheduler_mode;",
        "signed long long scheduler_lag_cycles;",
        "unsigned long long scheduler_wakeup_latency_buckets[",
        "sizeof(struct agent_workflow_lifecycle_info) == 216",
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
        (
            "__builtin_offsetof", "(", "struct", "agent_workflow_lifecycle_info", ",",
            "scheduler_mode", ")", "==",
            "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE",
        ),
        (
            "__builtin_offsetof", "(", "struct", "agent_workflow_lifecycle_info", ",",
            "scheduler_wakeup_latency_buckets", ")", "==", "184",
        ),
    ):
        _require_once(abi_tokens, sequence, "workflow lifecycle ABI layout")

    lifecycle = _function_tokens(
        _tokens(implementation), "sys_agent_workflow_lifecycle_info"
    )
    _ordered(
        lifecycle,
        (
            (
                "project_v3", "=", "user_size", ">",
                "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE", ";",
            ),
            (
                "copy_size", "=", "MIN", "(", "user_size", ",",
                "project_v3", "?", "sizeof", "(", "info", ")", ":",
                "(", "uint64", ")", "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_SIZE",
                ")", ";",
            ),
            (
                "info", ".", "version", "=", "project_v3", "?",
                "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION", ":",
                "AGENT_WORKFLOW_LIFECYCLE_INFO_V2_VERSION", ";",
            ),
            ("enabled", "=", "intr_save", "(", ")", ";"),
            (
                "current_account", "=", "p", "->", "resource_account", ";",
            ),
            ("resource_account_handle_valid", "(", "current_account", ")"),
            ("info", ".", "resource_account_valid", "=", "1", ";"),
            (
                "info", ".", "resource_account_slot", "=",
                "current_account", ".", "slot", ";",
            ),
            (
                "info", ".", "resource_account_generation", "=",
                "current_account", ".", "generation", ";",
            ),
            ("intr_restore", "(", "enabled", ")", ";"),
            (
                "if", "(", "project_v3", "&&", "info", ".", "charged", "&&",
                "workflow_scheduler_snapshot_get", "(", "current", ",",
                "current_account", ",", "current_domain", ",", "&",
                "scheduler_snapshot", ")", "==", "0", ")",
            ),
            (
                "copyout", "(", "p", "->", "pagetable", ",", "addr", ",",
                "(", "char", "*", ")", "&", "info", ",", "copy_size", ")",
            ),
        ),
        "versioned read-only lifecycle identity",
    )
    scheduler_fields = (
        "mode", "flags", "latency_class", "weight", "runnable",
        "request_ticks", "remaining_cycles", "lag_cycles", "vruntime",
        "virtual_deadline", "dispatches", "service_cycles", "sleep_decays",
        "eligibility_misses", "fallbacks", "max_wakeup_ticks",
        "deadline_misses", "wakeup_samples",
    )
    for field in scheduler_fields:
        _require_once(
            lifecycle,
            (
                "info", ".", f"scheduler_{field}", "=",
                "scheduler_snapshot", ".", field, ";",
            ),
            f"workflow scheduler snapshot field {field}",
        )
    _require_once(
        lifecycle,
        (
            "info", ".", "scheduler_wakeup_latency_buckets", "[", "i", "]",
            "=", "scheduler_snapshot", ".", "wakeup_latency_buckets", "[",
            "i", "]", ";",
        ),
        "workflow scheduler wake histogram",
    )
    allowed_resource_tokens = {
        "resource_account", "resource_account_handle_valid",
        "resource_account_handle", "resource_account_none",
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
    _validate_worker_batch_manifest(
        sources["user/include/rp_program_manifest.h"]
    )
    _validate_worker_batch_protocol(sources["user/include/rp_worker_batch.h"])
    _validate_worker_batch_runners(
        (
            sources["user/src/rp_wbatch0.c"],
            sources["user/src/rp_wbatch1.c"],
            sources["user/src/rp_wbatch2.c"],
        )
    )
    _validate_worker_batch_build(sources["user/Makefile"])
    _validate_delegated_workflow(sources["user/src/rp_orch.c"])
    _validate_package_publish_order(sources["user/src/rp_package.c"])
    _validate_launch_self_check(
        sources["user/lib/main.c"],
        sources["user/include/rp_launch_attestation.h"],
        sources["user/include/rp_evidence.h"],
    )
    _validate_launch_info_kernel(sources["os/agent_core.c"])
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
        sources["user/src/labdemo_ucore.c"],
        sources["user/include/labdemo_workload.h"],
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
