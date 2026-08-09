#!/usr/bin/env python3
"""任务 6 Guest 总耗时源码来源的变异测试。"""
from __future__ import annotations

import re

from scenario_timing_source_contract import ROOT, SOURCE_PATHS, validate_source_texts


def _function_span(source: str, name: str) -> tuple[int, int]:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S
    )
    if match is None:
        raise AssertionError(f"missing fixture function: {name}")
    opening = match.end() - 1
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"unterminated fixture function: {name}")


def _mutate(source: str, name: str, old: str, new: str) -> str:
    start, end = _function_span(source, name)
    body = source[start:end]
    if body.count(old) != 1:
        raise AssertionError(f"mutation anchor differs in {name}: {old!r}")
    return source[:start] + body.replace(old, new, 1) + source[end:]


def _reject(sources: dict[str, str]) -> None:
    try:
        validate_source_texts(sources)
    except ValueError:
        return
    raise AssertionError("accepted forged Task 6 timing provenance")


def _case(
    sources: dict[str, str], relative: str, name: str, old: str, new: str
) -> dict[str, str]:
    changed = dict(sources)
    changed[relative] = _mutate(changed[relative], name, old, new)
    return changed


def _text_case(
    sources: dict[str, str], relative: str, old: str, new: str
) -> dict[str, str]:
    changed = dict(sources)
    if changed[relative].count(old) != 1:
        raise AssertionError(f"global mutation anchor differs: {old!r}")
    changed[relative] = changed[relative].replace(old, new, 1)
    return changed


def _validate_bounded_acceptance_io() -> None:
    headers = [
        ROOT / "baseline_ucore/user/include/research_platform_state.h",
        ROOT / "user/include/research_platform_state.h",
    ]
    header_texts = [path.read_text(encoding="utf-8") for path in headers]
    if header_texts[0] != header_texts[1]:
        raise AssertionError("Plain and AgentOS state-buffer helpers differ")
    header = header_texts[0]
    for anchor in (
        "struct rp_state_buffer",
        "char body[RP_STATE_BUFFER_SIZE];",
        "rp_state_buffer_contains",
        "strcmp(state->path, path) != 0",
        "rp_state_buffer_begin_append",
        "rp_state_buffer_append",
        "rp_state_buffer_commit",
        "rp_open_bounded_append",
        "rp_write_append_suffix",
        "rp_bytes_equal",
    ):
        if anchor not in header:
            raise AssertionError(f"bounded state-buffer anchor is missing: {anchor}")
    for forbidden in ("O_TRUNC", "rp_write_file", "memcmp"):
        start, end = _function_span(header, "rp_state_buffer_commit")
        if forbidden in header[start:end]:
            raise AssertionError(
                f"state-buffer commit uses unsafe append primitive: {forbidden}"
            )

    for prefix in ("baseline_ucore/", ""):
        suite = (ROOT / f"{prefix}user/src/rp_test_suite.c").read_text(
            encoding="utf-8"
        )
        compare = (ROOT / f"{prefix}user/src/rp_compare_plain.c").read_text(
            encoding="utf-8"
        )
        if "rp_state_buffer_contains(&suite_state, path, token)" not in suite:
            raise AssertionError(f"{prefix or 'AgentOS '}suite bypasses state buffer")
        tool_records = re.findall(
            r'rp_state_buffer_append\(\s*&suite_state,\s*"tool=test_suite\.',
            suite,
        )
        if len(tool_records) != 10:
            raise AssertionError(f"{prefix or 'AgentOS '}suite tool batch is incomplete")
        if suite.count("rp_state_buffer_begin_append(&suite_state, \"rp_tool\")") != 1:
            raise AssertionError(f"{prefix or 'AgentOS '}suite tool batch is not unique")
        if "rp_state_buffer_contains(&compare_state, path, token)" not in compare:
            raise AssertionError(f"{prefix or 'AgentOS '}comparator bypasses state buffer")
        if compare.count(
            'rp_state_buffer_begin_append(&compare_state, "rp_agentcmp")'
        ) != 1 or compare.count("rp_state_buffer_commit(&compare_state)") != 1:
            raise AssertionError(f"{prefix or 'AgentOS '}comparator batch is incomplete")

        literal_paths = re.findall(
            r'(?:require_file_token|rp_file_contains)\("([^"]+)"',
            suite,
        )
        path_runs = sum(
            index == 0 or path != literal_paths[index - 1]
            for index, path in enumerate(literal_paths)
        )
        if len(literal_paths) < 1000 or path_runs * 2 >= len(literal_paths):
            raise AssertionError(
                f"{prefix or 'AgentOS '}suite no longer has bounded snapshot locality"
            )

    manifest = (ROOT / "user/include/rp_program_manifest.h").read_text(
        encoding="utf-8"
    )
    platform = re.search(
        r"#define RP_PLATFORM_PROGRAMS\(APPLY\)(.*?)(?:\n\n|#define)",
        manifest,
        re.S,
    )
    if platform is None or not platform.group(1).rstrip().endswith(
        'APPLY("rp_test_suite")'
    ):
        raise AssertionError("rp_test_suite must remain the final platform verifier")

    orchestrator = (ROOT / "user/src/rp_orch.c").read_text(encoding="utf-8")
    user_main = (ROOT / "user/lib/main.c").read_text(encoding="utf-8")
    user_syscall = (ROOT / "user/lib/syscall.c").read_text(encoding="utf-8")
    kernel_core = (ROOT / "os/agent_core.c").read_text(encoding="utf-8")
    kernel_syscall = (ROOT / "os/syscall.c").read_text(encoding="utf-8")
    self_check_start, self_check_end = _function_span(
        user_main, "rp_launch_identity_self_check"
    )
    self_check = user_main[self_check_start:self_check_end]
    identity_fields = (
        "info.is_agent == expected.is_agent",
        "info.agent_role == expected.agent_role",
        "info.filesystem_domain == expected.filesystem_domain",
        "info.filesystem_capability_mask ==\n\t\t       expected.filesystem_capability_mask",
    )
    if (
        self_check.count("pid = agent_launch_info(&info);") != 1
        or "pid > 0" not in self_check
        or any(field not in self_check for field in identity_fields)
        or re.search(r"\bagent_info\s*\(", self_check)
        or any(call in self_check for call in ("getpid(", "pipe(", "read(", "write("))
    ):
        raise AssertionError("可信 CRT 未用一次紧凑查询精确自检启动身份")
    start_main_start, start_main_end = _function_span(user_main, "__start_main")
    start_main = user_main[start_main_start:start_main_end]
    identity_guard = (
        "if (!rp_launch_identity_self_check(argc, argv))\n"
        "\t\texit(RP_LAUNCH_SELF_CHECK_EXIT);"
    )
    if identity_guard not in start_main or not (
        start_main.index(identity_guard) < start_main.index("main(argc, argv)")
    ):
        raise AssertionError("可信 CRT 启动身份失败未在 main 前关闭")
    if (
        "syscall(SYS_agent_launch_info, info)"
        not in user_syscall
        or "if (launch_identity)" not in kernel_core
        or "case SYS_agent_info:\n\t\tret = sys_agent_info(trapframe->a0, 0);"
        not in kernel_syscall
        or "if (id == SYS_agent_launch_info)\n\t\treturn sys_agent_info(trapframe->a0, 1);"
        not in kernel_syscall
        or "agent_metadata_fill_info" in kernel_core[
            kernel_core.index("int sys_agent_info("):
        ]
    ):
        raise AssertionError("紧凑启动身份路径绕回了完整 metadata 快照")
    timing_start, timing_end = _function_span(orchestrator, "record_timing")
    timing = orchestrator[timing_start:timing_end]
    context_start, context_end = _function_span(orchestrator, "orchestrator_context")
    if "pid = agent_launch_info(info);" not in orchestrator[context_start:context_end]:
        raise AssertionError("编排器启动判定仍会读取整份 Agent 诊断信息")
    main_start, main_end = _function_span(orchestrator, "main")
    orch_main = orchestrator[main_start:main_end]
    buffered_append = (
        "rp_state_append_line(rp_state_buf, RP_STATE_BUFFER_SIZE,\n"
        '\t\t\t     "rp_orch_timing", line);'
    )
    if buffered_append not in timing or 'rp_append_file("rp_orch_timing"' in timing:
        raise AssertionError("编排器计时记录未在内存中批量汇总")
    timing_write = 'rp_write_file("rp_orch_timing", rp_state_buf)'
    if orch_main.count(timing_write) != 1 or not (
        orch_main.index("for (int i = 0; i < total; i++)")
        < orch_main.index(timing_write)
        < orch_main.index("append_program_inventory_evidence(in_orchestrator)")
    ):
        raise AssertionError("编排器计时账本未在执行后一次写出")
    child_start, child_end = _function_span(orchestrator, "run_child")
    child = orchestrator[child_start:child_end]
    for legacy in (
        "pipe(", "agent_scope_delegate_fd(", "read_launch_attestation(",
        "launch_attestation_valid(", "attest_pipe", "read(", "write(",
    ):
        if legacy in child:
            raise AssertionError(f"启动身份热路径仍包含逐子进程 I/O: {legacy}")
    if not (
        child.index("launch_expectation_for(")
        < child.index("format_launch_expectation(")
        < child.index("exec(image, argv)")
        < child.index("waitpid(pid, &code)")
        < child.index("record_timing(program, launcher, pid,")
    ):
        raise AssertionError("父进程未在成功等待后记录可信 CRT 已自检的预期身份")

    agentos_orchestrator = (
        ROOT / "user/src/rp_agentos_orch.c"
    ).read_text(encoding="utf-8")
    stability_start, stability_end = _function_span(
        agentos_orchestrator, "run_stability_workflow"
    )
    stability = agentos_orchestrator[stability_start:stability_end]
    child_branch = stability[
        stability.index("if (pid == 0)") : stability.index("close(report_pipe[1]);")
    ]
    if "close(report_pipe[0])" in child_branch:
        raise AssertionError(
            "workflow child closes a descriptor it did not inherit"
        )
    if "RP_RESOURCE_STABILITY_ADMISSION_TIMEOUT_MS" not in stability:
        raise AssertionError("resource stability admission lacks a time bound")
    if (
        "get_mtime() >= admission_deadline" not in stability
        or "pid == AGENT_STATUS_RETRY ? sched_yield() : sleep(1)" not in stability
        or "wait_status < 0" not in stability
    ):
        raise AssertionError("resource stability admission does not await reclamation")
    if "agent_scope_delegate_fd(report_pipe[1])" not in stability:
        raise AssertionError("workflow report descriptor is not explicitly delegated")


def main() -> int:
    sources = {
        relative: (ROOT / relative).read_text(encoding="utf-8")
        for relative in SOURCE_PATHS
    }
    validate_source_texts(sources)
    _validate_bounded_acceptance_io()

    plain = "baseline_ucore/user/src/rp_seed_orch.c"
    _reject(_case(
        sources,
        plain,
        "record_workflow_timing",
        "char line[512];",
        "return 1;\n\tchar line[512];",
    ))
    _reject(_case(
        sources, plain, "main", "workflow_start = get_mtime();",
        "workflow_start = 7;",
    ))
    _reject(_case(
        sources, plain, "main", "steady_start = get_mtime();",
        "steady_start = get_mtime();\n\tworkflow_start = 7;",
    ))
    _reject(_case(
        sources, plain, "main", "ok += run_child(PROGRAMS[i]);",
        "ok += forged_run_child(PROGRAMS[i]);",
    ))
    plain_elapsed = (
        "workflow_elapsed = (unsigned long long)(workflow_end - workflow_start);"
    )
    _reject(_case(
        sources, plain, "record_workflow_timing", plain_elapsed,
        "workflow_elapsed = 7;",
    ))
    _reject(_case(
        sources, plain, "record_workflow_timing", plain_elapsed,
        plain_elapsed + "\n\tworkflow_elapsed += 1;",
    ))
    _reject(_case(
        sources, plain, "record_workflow_timing",
        "int64 workflow_end = get_mtime();",
        "int64 workflow_end = get_mtime();\n\tworkflow_end = 13;",
    ))
    _reject(_case(
        sources, plain, "record_workflow_timing",
        "rp_append_uint_text(line, sizeof(line), workflow_elapsed);",
        "rp_append_uint_text(line, sizeof(line), workflow_elapsed + 1);",
    ))

    agentos = "user/src/rp_agentos_orch.c"
    _reject(_case(
        sources,
        agentos,
        "record_workflow_timing",
        "char line[640];",
        "return 1;\n\tchar line[640];",
    ))
    _reject(_text_case(
        sources,
        agentos,
        "\t\tRP_RESOURCE_STABILITY_FS_BLOCK_GROWTH_BOUND,",
        "\t\t~0ULL,",
    ))
    _reject(_text_case(
        sources,
        agentos,
        "\t\tRP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND,",
        "\t\t~0ULL,",
    ))
    _reject(_text_case(
        sources,
        agentos,
        "\t[AGENT_RESOURCE_BUFFER_CACHE] =\n"
        "\t\tRP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND,\n};",
        "\t[AGENT_RESOURCE_BUFFER_CACHE] =\n"
        "\t\tRP_RESOURCE_STABILITY_BUFFER_GROWTH_BOUND,\n"
        "\t[AGENT_RESOURCE_PROCESS] = ~0ULL,\n};",
    ))
    _reject(_case(
        sources, agentos, "main", "int64 workflow_start = get_mtime();",
        "int64 workflow_start = 7;",
    ))
    _reject(_case(
        sources, agentos, "main", "int64 workflow_start = get_mtime();",
        "int64 workflow_start = get_mtime();\n\tworkflow_start = 7;",
    ))
    _reject(_case(
        sources, agentos, "main", "int got = waitpid(pid, &code);",
        "int got = forged_waitpid(pid, &code);",
    ))
    _reject(_case(
        sources, agentos, "main", "uint64 workflow_end = get_mtime();",
        "uint64 workflow_end = completion.steady_start_ms;",
    ))
    agentos_elapsed = "workflow_elapsed = workflow_end - record->start_ms;"
    _reject(_case(
        sources, agentos, "record_workflow_timing", agentos_elapsed,
        "workflow_elapsed = 11;",
    ))
    _reject(_case(
        sources, agentos, "record_workflow_timing", agentos_elapsed,
        agentos_elapsed + "\n\tworkflow_elapsed += 1;",
    ))
    _reject(_case(
        sources, agentos, "record_workflow_timing",
        "rp_append_uint_text(line, sizeof(line), workflow_elapsed);",
        "rp_append_uint_text(line, sizeof(line), workflow_elapsed * 2);",
    ))
    _reject(_case(
        sources, agentos, "write_workflow_handoff",
        "record.start_ms = start_ms;", "record.start_ms = 0;",
    ))

    delegated = "user/src/rp_orch.c"
    crt = "user/lib/main.c"
    launch_header = "user/include/rp_launch_attestation.h"
    launch_evidence = "user/include/rp_evidence.h"
    launch_kernel = "os/agent_core.c"
    _reject(_case(
        sources, crt, "__start_main",
        "if (!rp_launch_identity_self_check(argc, argv))",
        "if (0 && !rp_launch_identity_self_check(argc, argv))",
    ))
    _reject(_case(
        sources, crt, "rp_launch_identity_self_check",
        "pid = agent_launch_info(&info);",
        "pid = getpid();",
    ))
    _reject(_case(
        sources, crt, "rp_launch_identity_self_check",
        "info.is_agent == expected.is_agent",
        "info.is_agent == info.is_agent",
    ))
    _reject(_case(
        sources, crt, "rp_launch_identity_self_check",
        "info.filesystem_capability_mask ==\n\t\t       expected.filesystem_capability_mask",
        "info.filesystem_capability_mask != 0",
    ))
    _reject(_case(
        sources, delegated, "run_child",
        "expectation_ready ? identity_arg : 0,",
        "0,",
    ))
    _reject(_case(
        sources, delegated, "run_child",
        "int got = waitpid(pid, &code);",
        "int got = pid;",
    ))
    _reject(_case(
        sources, delegated, "run_child",
        "record_timing(program, launcher, pid,",
        "record_timing(program, launcher, 0,",
    ))
    _reject(_case(
        sources, delegated, "role_filesystem_capabilities",
        "return RP_WORKFLOW_WORKER;",
        "return AGENT_CAP_CONTENT_READ;",
    ))
    _reject(_case(
        sources, delegated, "launch_expectation_for",
        "expected->filesystem_capability_mask = expected_capabilities;",
        "expected->filesystem_capability_mask = launch->worker_capabilities;",
    ))
    _reject(_case(
        sources, delegated, "run_child",
        "if (agent_child) {",
        "int identity_pipe[2];\n\tpipe(identity_pipe);\n\tif (agent_child) {",
    ))
    _reject(_text_case(
        sources, launch_header,
        '#define RP_LAUNCH_IDENTITY_SOURCE "trusted_crt_self_check"',
        '#define RP_LAUNCH_IDENTITY_SOURCE "child_after_exec"',
    ))
    _reject(_case(
        sources, launch_header, "rp_launch_expectation_valid",
        "expected->filesystem_domain == 0 ||",
        "0 ||",
    ))
    _reject(_case(
        sources, launch_header, "rp_launch_expectation_valid",
        "return expected->agent_role == 0;",
        "return 1;",
    ))
    _reject(_text_case(
        sources, launch_evidence,
        '"trusted_crt_batch_dispatch"',
        '"child_after_exec"',
    ))

    batch_manifest = "user/include/rp_program_manifest.h"
    batch_protocol = "user/include/rp_worker_batch.h"
    batch_runner = "user/src/rp_wbatch0.c"
    batch_make = "user/Makefile"
    package = "user/src/rp_package.c"
    _reject(_text_case(
        sources, batch_manifest,
        "APPLY(1, rp_state_catalog)", "APPLY(1, rp_catalog)",
    ))
    _reject(_text_case(
        sources, batch_manifest,
        "APPLY(1, rp_wbatch1, 9)", "APPLY(1, rp_wbatch1, 10)",
    ))
    _reject(_case(
        sources, batch_protocol, "rp_worker_batch_frame_guard_valid",
        "return frame->magic == RP_WORKER_BATCH_MAGIC &&",
        "return 1 || frame->magic == RP_WORKER_BATCH_MAGIC &&",
    ))
    _reject(_case(
        sources, batch_protocol, "rp_worker_batch_next",
        "runtime->expected < runtime->count",
        "runtime->expected <= runtime->count",
    ))
    _reject(_case(
        sources, batch_runner, "rp_worker_run",
        "switch (index)", "switch (0)",
    ))
    _reject(_text_case(
        sources, batch_make,
        "binary: worker-batch-check $(addprefix $(elf_dir)/,$(SELECTED_APPS))",
        "binary: $(addprefix $(elf_dir)/,$(SELECTED_APPS))",
    ))
    _reject(_case(
        sources, delegated, "main",
        "int passed = in_orchestrator && binding ?",
        "int passed = 0 && in_orchestrator && binding ?",
    ))
    _reject(_case(
        sources, delegated, "main",
        '"support_launch=agent_worker_batch\\n"',
        '"support_launch=agent_worker_create\\n"',
    ))
    _reject(_case(
        sources, delegated, "worker_batch_reap",
        "close(WORKER_BATCH_SESSION.command_fd);",
        "close(WORKER_BATCH_SESSION.result_fd);",
    ))
    _reject(_case(
        sources, delegated, "run_worker_batch",
        "RP_LAUNCH_BATCH_IDENTITY_SOURCE, 1, 0, elapsed);",
        "RP_LAUNCH_IDENTITY_SOURCE, 1, 0, elapsed);",
    ))
    _reject(_case(
        sources, delegated, "record_timing",
        'if (strcmp(launcher, "fork") == 0)',
        'if (strcmp(launcher, "fork") != 0)',
    ))
    _reject(_case(
        sources, delegated, "append_program_inventory_evidence",
        "expected_programs,\n\t\t\t\t\t\tlauncher, in_orchestrator,",
        "expected_programs,\n\t\t\t\t\t\tlauncher, 1,",
    ))
    _reject(_case(
        sources, delegated, "main",
        '"orchestrator=rp_orch\\nlauncher=fork\\n"',
        '"orchestrator=rp_orch\\nlauncher=mixed_attested\\n"',
    ))
    package_publish = (
        "if (package_state.append_active &&\n"
        "\t    !rp_state_buffer_commit(&package_state)) return 1;\n"
        "\tif (!rp_append_file(\"rp_ack\", \"ack=package;msg=11;status=ready\")) return 1;"
    )
    package_forged = (
        "if (!rp_append_file(\"rp_ack\", \"ack=package;msg=11;status=ready\")) return 1;\n"
        "\tif (package_state.append_active &&\n"
        "\t    !rp_state_buffer_commit(&package_state)) return 1;"
    )
    _reject(_case(
        sources, package, "main", package_publish, package_forged,
    ))
    _reject(_case(
        sources, launch_kernel, "sys_agent_info",
        "return result < 0 || !launch_identity ? result : p->pid;",
        "return result < 0 || !launch_identity ? result : 1;",
    ))
    _reject(_case(
        sources, launch_kernel, "sys_agent_info",
        "struct proc *p = curr_proc();",
        "struct proc *p = initproc;",
    ))
    _reject(_case(
        sources, launch_kernel, "sys_agent_info",
        "p->vfs_effective_caps : 0;",
        "p->agent_capability_mask : 0;",
    ))
    _reject(_case(
        sources, launch_kernel, "sys_agent_info",
        "if (launch_identity) {",
        "if (launch_identity) {\n\t\tagent_info_fill(p, &info);",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "pid = agent_workflow_create(AGENT_ROLE_ORCHESTRATOR);",
        "pid = agent_create_role(AGENT_ROLE_ORCHESTRATOR);",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "get_mtime() >= admission_deadline",
        "0",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "pid == AGENT_STATUS_RETRY ? sched_yield() : sleep(1)",
        "sleep(1)",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "wait_status < 0",
        "0",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "eof = read(report_pipe[0], &extra, 1) < 0;",
        "eof = 1;",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "if (agent_resource_snapshot(global_before) != AGENT_STATUS_OK) {",
        "if (memset(global_before, 0, sizeof(*global_before)) == 0) {",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "if (agent_resource_snapshot(global_after) != AGENT_STATUS_OK) {",
        "if (memset(global_after, 0, sizeof(*global_after)) == 0) {",
    ))
    _reject(_text_case(
        sources, agentos,
        "#define RP_RESOURCE_STABILITY_ADMISSION_TIMEOUT_MS 30000",
        "#define RP_RESOURCE_STABILITY_ADMISSION_TIMEOUT_MS 1",
    ))
    for old, new in (
        ("int64 now = get_mtime();", "int64 now = 0;"),
        ("status == AGENT_STATUS_NOT_FOUND", "status == AGENT_STATUS_RETRY"),
        ("status != AGENT_STATUS_RETRY", "status != AGENT_STATUS_OK"),
        ("now < 0 || deadline < 0", "deadline < 0"),
        ("sleep(10) < 0", "sleep(1) < 0"),
        ("int64 now = get_mtime();", "return 1;\n\tint64 now = get_mtime();"),
    ):
        _reject(_case(
            sources, agentos, "stability_scope_retired", old, new,
        ))
    _reject(_case(
        sources, agentos, "stability_scope_retired",
        "\t\tnow = get_mtime();",
        "\t\tnow = 0;",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "if (!stability_report_valid(report, index, mode, challenge_nonce))",
        "if (0 && !stability_report_valid(report, index, mode, challenge_nonce))",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "if (mismatch == 0 && !stability_scope_retired(report))",
        "if (0 && !stability_scope_retired(report))",
    ))
    retirement_then_snapshot = (
        "if (mismatch == 0 && !stability_scope_retired(report))\n"
        "\t\tmismatch |= 1U << 6;\n"
        "\tif (agent_resource_snapshot(global_after) != AGENT_STATUS_OK) {\n"
        "\t\tprintf(\"rp_agentos_orch: stability_snapshot_failed index=%u\\n\",\n"
        "\t\t       index);\n"
        "\t\treturn 0;\n"
        "\t}"
    )
    snapshot_then_retirement = (
        "if (agent_resource_snapshot(global_after) != AGENT_STATUS_OK) {\n"
        "\t\tprintf(\"rp_agentos_orch: stability_snapshot_failed index=%u\\n\",\n"
        "\t\t       index);\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tif (mismatch == 0 && !stability_scope_retired(report))\n"
        "\t\tmismatch |= 1U << 6;"
    )
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        retirement_then_snapshot, snapshot_then_retirement,
    ))
    _reject(_case(
        sources, agentos, "stability_positive_delta",
        "return after > before ? after - before : 0;",
        "return 0;",
    ))
    _reject(_case(
        sources, agentos, "stability_positive_delta",
        "return after > before ? after - before : 0;",
        "after = before;\n\treturn after > before ? after - before : 0;",
    ))
    _reject(_case(
        sources, agentos, "stability_global_pair_valid",
        "positive_growth > bound",
        "positive_growth > bound + 1000",
    ))
    _reject(_case(
        sources, agentos, "stability_global_pair_valid",
        "stability_positive_delta(left->reserved_used,",
        "stability_positive_delta(left->ordinary_used,",
    ))
    _reject(_case(
        sources, agentos, "stability_global_pair_valid",
        "right->ordinary_used) +\n\t\t\tstability_positive_delta",
        "right->ordinary_used);\n\t\t(void)stability_positive_delta",
    ))
    _reject(_case(
        sources, agentos, "stability_global_pair_valid",
        "right->reserved_used);",
        "right->reserved_used);\n\t\tpositive_growth = 0;",
    ))
    _reject(_case(
        sources, agentos, "stability_global_pair_valid",
        "uint64 bound = mode == RP_RESOURCE_STABILITY_MODE_TERMINAL ?\n\t\t\t0 : orch_resource_growth_bounds[kind];",
        "uint64 bound = ~0ULL;",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "orch_stability_workflow.request_id, index, mode);",
        "0, index, mode);",
    ))
    _reject(_case(
        sources, agentos, "run_stability_workflow",
        "stability_report_valid(report, index, mode, challenge_nonce)",
        "stability_report_valid(report, index, mode, challenge_nonce + 1)",
    ))
    for old, new in (
        ("run_stability_workflow(0, RP_RESOURCE_STABILITY_MODE_TERMINAL, 0)",
         "run_stability_workflow(0, RP_RESOURCE_STABILITY_MODE_TERMINAL, 1)"),
        ("run_stability_workflow(index, mode, 1)",
         "run_stability_workflow(index, mode, 0)"),
        ("memset(orch_stability_reports, 0, sizeof(orch_stability_reports));",
         "memset(orch_stability_reports, 0, 0);"),
    ):
        _reject(_case(sources, agentos, "run_resource_stability_acceptance",
                      old, new))
    _reject(_case(
        sources, "user/src/rp_resource_probe.c", "main",
        "fence_status = settle_current_workflow();",
        "fence_status = 0;",
    ))
    _reject(_case(
        sources, "user/src/rp_resource_probe.c", "settle_current_workflow",
        "status = sync();",
        "status = 0;",
    ))
    _reject(_case(
        sources, "user/src/rp_resource_probe.c", "final_state_mismatch",
        "final_io.completion_sequence < initial_io.completion_sequence",
        "0",
    ))
    _reject(_case(
        sources, agentos, "stability_report_valid",
        "report->final_completion_sequence <\n\t\t    report->initial_completion_sequence",
        "0",
    ))
    _reject(_case(
        sources, agentos, "stability_identity_unique",
        "prior->scope_id == latest->scope_id",
        "0",
    ))
    _reject(_case(
        sources, agentos, "stability_identity_unique",
        "prior->resource_account_generation ==\n\t\t\t     latest->resource_account_generation",
        "0",
    ))
    _reject(_case(
        sources, agentos, "stability_global_pair_valid",
        "left->ordinary_pending != 0",
        "left->pending != 0",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "&orch_stability_global_before[0]",
        "&orch_stability_global_before[1]",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "positive_growth > bound",
        "positive_growth > bound + 1000",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "stability_positive_delta(left->reserved_used,",
        "stability_positive_delta(left->ordinary_used,",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "right->ordinary_used) +\n\t\t\tstability_positive_delta",
        "right->ordinary_used);\n\t\t(void)stability_positive_delta",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "right->reserved_used);",
        "right->reserved_used);\n\t\tpositive_growth = 0;",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "uint64 bound = orch_resource_growth_bounds[kind];",
        "uint64 bound = ~0ULL;",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "current->used <= prior->used",
        "current->used < prior->used",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "index < RP_RESOURCE_STABILITY_LOAD_WORKFLOWS",
        "index < RP_RESOURCE_STABILITY_WORKFLOWS",
    ))
    _reject(_case(
        sources, agentos, "stability_global_sequence_valid",
        "right->used < last_load->used",
        "right->used <= last_load->used",
    ))
    _reject(_case(
        sources, agentos, "run_resource_stability_acceptance",
        "if (!stability_global_sequence_valid())",
        "if (0)",
    ))
    _reject(_case(
        sources, agentos, "append_stability_report",
        '"_ordinary_used_before="',
        '"_used_before="',
    ))
    _reject(_case(
        sources, agentos, "append_stability_report",
        "before->ordinary_used);",
        "before->used);",
    ))
    _reject(_case(
        sources, agentos, "append_stability_global_policy",
        "measured_mask_semantics=configured_global_resource_kind_counters_only",
        "measured_mask_semantics=all_accounts_and_rates",
    ))
    _reject(_case(
        sources, agentos, "append_stability_global_policy",
        "snapshot_consistency=single_core_irq_coherent",
        "snapshot_consistency=single_core_irq_atomic",
    ))
    _reject(_case(
        sources, agentos, "append_stability_global_policy",
        "growth_bound_semantics=per_class_positive_delta_sum",
        "growth_bound_semantics=net_used_upper_delta",
    ))
    _reject(_case(
        sources, agentos, "append_stability_global_policy",
        "decrease_semantics=reclamation_allowed",
        "decrease_semantics=forbidden",
    ))
    _reject(_case(
        sources, agentos, "run_resource_stability_acceptance",
        "configured_kind_coverage=measured_mask_only",
        "configured_kind_coverage=all_resources",
    ))

    resource_probe = "user/src/rp_resource_probe.c"
    _reject(_case(
        sources, resource_probe, "transient_resource_child",
        "memory = sbrk(RP_RESOURCE_STABILITY_MEMORY_PAGES * PAGE_BYTES);",
        "memory = 4096;",
    ))
    _reject(_case(
        sources, resource_probe, "transient_resource_child",
        "return 0;",
        "close(fd);\n\treturn 0;",
    ))
    _reject(_case(
        sources, resource_probe, "run_child_round",
        "int pid = agent_create_role(AGENT_ROLE_ARTIFACT);",
        "int pid = fork();",
    ))
    _reject(_case(
        sources, resource_probe, "run_metadata_round",
        "queried = agent_file_query(&metadata_query, &metadata_query_result);",
        "queried = 1;",
    ))
    _reject(_case(
        sources, resource_probe, "transient_resource_child",
        "challenge_nonce >> ((page & 7U) * 8U)",
        "0",
    ))
    _reject(_case(
        sources, resource_probe, "run_metadata_round",
        "challenge_nonce % 1000ULL",
        "0",
    ))
    _reject(_case(
        sources, resource_probe, "main",
        "report.challenge_nonce = challenge_nonce;",
        "report.challenge_nonce = 1;",
    ))
    _reject(_case(
        sources, resource_probe, "snapshot_state",
        "lifecycle->resource_account_valid == 1",
        "lifecycle->resource_account_valid == 0",
    ))
    changed = dict(sources)
    header = "user/include/rp_resource_stability.h"
    changed[header] = changed[header].replace(
        "RP_RESOURCE_STABILITY_CHILD_ROUNDS 12U",
        "RP_RESOURCE_STABILITY_CHILD_ROUNDS 1U",
        1,
    )
    _reject(changed)
    changed = dict(sources)
    changed[header] = changed[header].replace(
        "RP_RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND 1U",
        "RP_RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND 0U",
        1,
    )
    _reject(changed)
    _reject(_case(
        sources, resource_probe, "final_state_mismatch",
        "initial_agent.agent_call_count + expected_observations",
        "initial_agent.agent_call_count",
    ))
    _reject(_case(
        sources, resource_probe, "main",
        "!initial_state_is_fresh()",
        "0",
    ))
    _reject(_case(
        sources, resource_probe, "main",
        "!initial_state_is_fresh()",
        "initial_state_is_fresh()",
    ))
    _reject(_case(
        sources, resource_probe, "main",
        "final_state_mismatch(expected_rounds, mode)",
        "0",
    ))
    _reject(_case(
        sources, agentos, "stability_report_valid",
        "call_delta == expected_observations",
        "call_delta == 0",
    ))
    _reject(_case(
        sources, agentos, "stability_report_valid",
        "context_delta == expected_observations",
        "context_delta == 0",
    ))
    changed = dict(sources)
    changed[header] = changed[header].replace(
        "RP_RESOURCE_STABILITY_REPORT_SIZE 224U",
        "RP_RESOURCE_STABILITY_REPORT_SIZE 223U",
        1,
    )
    _reject(changed)
    _reject(_case(
        sources, header, "rp_resource_stability_nonce",
        "hash = rp_resource_stability_mix(hash, challenge_request_id);",
        "hash = rp_resource_stability_mix(hash, 0);",
    ))

    lifecycle_abi = "agent_lifecycle_abi.h"
    changed = dict(sources)
    changed[lifecycle_abi] = changed[lifecycle_abi].replace(
        "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 2U",
        "AGENT_WORKFLOW_LIFECYCLE_INFO_VERSION 1U",
        1,
    )
    _reject(changed)
    lifecycle_observer = "os/agent_lifecycle.c"
    _reject(_case(
        sources, lifecycle_observer, "sys_agent_workflow_lifecycle_info",
        "info.resource_account_slot = p->resource_account.slot;",
        "info.resource_account_slot = 0;",
    ))

    observer = "os/agent_resource.c"
    _reject(_case(
        sources, observer, "agent_resource_snapshot_authorized",
        "exec_policy_process_bootstrap(p)", "1",
    ))
    _reject(_case(
        sources, observer, "sys_agent_resource_snapshot",
        "enabled = intr_save();",
        "enabled = 1;",
    ))
    controller = "os/resource_controller.c"
    _reject(_case(
        sources, controller, "resource_policy_snapshot_all",
        "snapshot->reserved_pending +=\n\t\t\t\t\t\tcounter->pending;",
        "snapshot->reserved_pending +=\n\t\t\t\t\t\t0;",
    ))
    changed = dict(sources)
    kernel_ids = "os/syscall_ids.h"
    user_ids = "user/lib/syscall_ids.h"
    changed[kernel_ids] = changed[kernel_ids].replace(
        "SYS_agent_resource_snapshot 559",
        "SYS_agent_resource_snapshot 558",
        1,
    )
    _reject(changed)
    _reject(_case(
        sources,
        "os/syscall.c",
        "syscall_dispatch",
        "ret = sys_agent_resource_snapshot(trapframe->a0,",
        "ret = forged_resource_snapshot(trapframe->a0,",
    ))
    _reject(_case(
        sources,
        "os/syscall.c",
        "syscall_dispatch",
        "sys_agent_resource_snapshot(trapframe->a0,\n\t\t\t\t\t\t  trapframe->a1)",
        "sys_agent_resource_snapshot(trapframe->a1,\n\t\t\t\t\t\t  trapframe->a0)",
    ))
    _reject(_case(
        sources,
        "os/syscall.c",
        "syscall_dispatch",
        "ret = sys_agent_resource_snapshot(trapframe->a0,\n"
        "\t\t\t\t\t\t  trapframe->a1);\n"
        "\t\tbreak;",
        "if (0) {\n"
        "\t\t\tret = sys_agent_resource_snapshot(trapframe->a0,\n"
        "\t\t\t\t\t\t  trapframe->a1);\n"
        "\t\t}\n"
        "\t\tbreak;",
    ))
    _reject(_case(
        sources,
        "os/syscall.c",
        "syscall_dispatch",
        "ret = sys_agent_resource_snapshot(trapframe->a0,\n"
        "\t\t\t\t\t\t  trapframe->a1);\n"
        "\t\tbreak;",
        "ret = sys_agent_resource_snapshot(trapframe->a0,\n"
        "\t\t\t\t\t\t  trapframe->a1);\n"
        "\t\tif (1) {\n"
        "\t\t\tbreak;\n"
        "\t\t}",
    ))

    performance_abi = "agent_performance_abi.h"
    _reject(_case(
        sources,
        "os/syscall.c",
        "syscall_dispatch",
        "ret = sys_agent_performance_snapshot(trapframe->a0,",
        "ret = forged_performance_snapshot(trapframe->a0,",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "struct agent_performance_snapshot snapshot;",
        "return AGENT_STATUS_OK;\n\tstruct agent_performance_snapshot snapshot;",
    ))
    _reject(_text_case(
        sources,
        performance_abi,
        "AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL 1U",
        "AGENT_PERFORMANCE_COUNTER_SCOPE_GLOBAL 2U",
    ))
    _reject(_text_case(
        sources,
        "user/include/labdemo_workload.h",
        "LABDEMO_RETRY_TIMEOUT_MS 30000",
        "LABDEMO_RETRY_TIMEOUT_MS 0",
    ))
    _reject(_text_case(
        sources,
        "user/include/labdemo_workload.h",
        "#define LABDEMO_RETRY_TIMEOUT_MS 30000",
        "#define LABDEMO_RETRY_TIMEOUT_MS 0\n"
        "// #define LABDEMO_RETRY_TIMEOUT_MS 30000",
    ))
    _reject(_case(
        sources,
        observer,
        "agent_performance_snapshot_authorized",
        "p->agent_role == AGENT_ROLE_ORCHESTRATOR",
        "p->agent_role != AGENT_ROLE_ORCHESTRATOR",
    ))
    _reject(_case(
        sources,
        observer,
        "agent_performance_snapshot_authorized",
        "exec_policy_process_bootstrap(p)",
        "exec_policy_process_allows_role(p, p->agent_role)",
    ))
    _reject(_case(
        sources,
        observer,
        "agent_performance_snapshot_authorized",
        "p->agent_controller_id == 0",
        "p->agent_controller_id != 0",
    ))
    _reject(_case(
        sources,
        observer,
        "agent_performance_snapshot_authorized",
        "agent_identity_has_cap(p, AGENT_CAP_ORCHESTRATE)",
        "p->agent_capability_mask != 0",
    ))
    _reject(_case(
        sources,
        observer,
        "agent_performance_snapshot_authorized",
        "p->resource_domain_admin ||",
        "1 ||",
    ))
    _reject(_case(
        sources,
        observer,
        "agent_performance_snapshot_authorized",
        "return p != 0 && exec_policy_process_bootstrap(p) &&",
        "p->resource_domain_admin = 1;\n\t"
        "return p != 0 && exec_policy_process_bootstrap(p) &&",
    ))
    _reject(_case(
        sources,
        "os/agent_identity.c",
        "agent_identity_has_cap",
        "return p != 0 && p->is_agent &&",
        "return 1;\n\treturn p != 0 && p->is_agent &&",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_flush",
        "int status = fd < 0 ? sync() : fsync(fd);",
        "int status = 0;",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_flush",
        "int status = fd < 0 ? sync() : fsync(fd);",
        "int status = fd < 0 ? sync() : fsync(fd);\n\tstatus = 0;",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_flush",
        "int64 deadline;",
        "return 0;\n\tint64 deadline;",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_flush",
        "now < 0 || now >= deadline || sleep(10) < 0",
        "0",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_flush",
        "for (;;) {",
        "for (;;) {\n\t\tbreak;",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_fence",
        'demo_quiescence_sync() == 0, "quiescence sync"',
        'sync() == 0, "quiescence sync"',
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "demo_quiescence_fence",
        'check(demo_quiescence_sync() == 0, "quiescence sync");',
        'if (0)\n\t\t\tcheck(demo_quiescence_sync() == 0, "quiescence sync");',
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_orchestrator",
        'demo_quiescence_sync() == 0, "workflow completion sync"',
        'sync() == 0, "workflow completion sync"',
    ))
    for label in ("workflow setup boundary", "workflow completion sync"):
        _reject(_case(
            sources,
            "user/src/labdemo_ucore.c",
            "run_orchestrator",
            f'check(demo_quiescence_sync() == 0, "{label}");',
            f'if (0)\n\t\tcheck(demo_quiescence_sync() == 0, "{label}");',
        ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_native_workload",
        "demo_quiescence_fsync(fd) == 0",
        "fsync(fd) == 0",
    ))
    for function_name, mode in (
        ("run_compat_workload", "compat"),
        ("run_native_workload", "native"),
    ):
        primary_ack = (
            "check(demo_quiescence_fsync(fd) == 0,\n"
            f'\t\t      "{mode} recovery primary ack");'
        )
        _reject(_case(
            sources,
            "user/src/labdemo_ucore.c",
            function_name,
            primary_ack,
            f"if (0)\n\t\t\t{primary_ack}",
        ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_one",
        "status != AGENT_STATUS_OK ||",
        "0 ||",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_one",
        "int64 deadline = get_mtime();",
        "return;\n\tint64 deadline = get_mtime();",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_one",
        "res->status != AGENT_STATUS_RETRY ||",
        "0 ||",
    ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_one",
        "n = agent_run(op, res, 1, 0);",
        "n = agent_run(op, res, 1, 0);\n\t\tres->status = AGENT_STATUS_OK;",
    ))
    for assignment in ("op->request_id = 1;", "op->tool_id = 1;"):
        _reject(_case(
            sources,
            "user/src/labdemo_ucore.c",
            "run_one",
            'now = get_mtime();',
            f'{assignment}\n\t\tnow = get_mtime();',
        ))
    _reject(_case(
        sources,
        "user/src/labdemo_ucore.c",
        "run_one",
        "(op->tool_id != AGENT_TOOL_ACTION_COMMIT &&\n"
        "\t\t     op->tool_id != AGENT_TOOL_ARTIFACT_UPDATE)",
        "0",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "user_size < 2 * sizeof(unsigned int)",
        "user_size < sizeof(unsigned int)",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "copy_size = MIN(user_size, sizeof(snapshot));",
        "copy_size = sizeof(snapshot);",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "snapshot.block_physical_writes = io.successful_writes;",
        "snapshot.block_physical_writes = io.writes;",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "snapshot.block_physical_reads = io.reads;",
        "snapshot.block_physical_reads = io.writes;",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "snapshot.overwrite_prereads_skipped =\n\t\tkernel.overwrite_prereads_skipped;",
        "snapshot.overwrite_prereads_skipped = 0;",
    ))
    _reject(_case(
        sources,
        "os/performance_stats.c",
        "kernel_performance_overwrite_preread_skipped",
        "performance_stats.overwrite_prereads_skipped += blocks;",
        "performance_stats.overwrite_prereads_skipped += 0;",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "copyout(p->pagetable, addr, (char *)&snapshot,",
        "copyout_observer(p->pagetable, addr, (char *)&snapshot,",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "io_policy.physical_reads += count;",
        "io_policy.physical_writes += count;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "io_policy.physical_writes += count;",
        "io_policy.physical_writes += 1;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "io_policy.physical_flushes += count;",
        "io_policy.physical_flushes += 1;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "successful = count - failed;",
        "successful = count;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "io_policy.failed_transfers += failed;",
        "io_policy.failed_transfers += count;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_physical_snapshot",
        "stats->reads = io_policy.physical_reads;",
        "stats->reads = io_policy.physical_writes;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bpublish_overwrite",
        "kernel_performance_overwrite_preread_skipped(1);",
        "kernel_performance_overwrite_preread_skipped(0);",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bprepare_overwrite",
        "receipt->skipped_preread = 1;",
        "receipt->skipped_preread = 0;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "io_policy.successful_writes += successful;",
        "io_policy.successful_writes += count;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "io_policy.successful_flushes += successful;",
        "io_policy.successful_flushes += count;",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer",
        "&result, 1);",
        "&result, 0);",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "for (uint i = 0; i < count; i++)\n"
        "\t\tif (results[i] < 0)\n"
        "\t\t\tfailed++;",
        "for (uint i = 0; i < count; i++)\n"
        "\t\tbio_account_transfer(owner, io_class, transfer, results[i]);",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "bio_account_transfer_batch",
        "if (*transfers >= IO_RATE_LOCAL_BATCH)",
        "if (*transfers >= 1)",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "io_rate_charge_transfers",
        "state, io_class, 0, 0, 1, reserved) < 0",
        "state, io_class, 0, 0, 1, 1) < 0",
    ))
    _reject(_case(
        sources,
        "os/bio.c",
        "io_rate_charge_transfers",
        "state, io_class, 1, 0, 0, shared) < 0",
        "state, io_class, 1, 0, 0, 1) < 0",
    ))
    _reject(_case(
        sources,
        "os/virtio_disk.c",
        "disk_submit",
        "1, KERNEL_PERFORMANCE_VIRTIO_SINGLE, 0",
        "1, KERNEL_PERFORMANCE_VIRTIO_READ_BATCH, 0",
    ))
    _reject(_case(
        sources,
        "os/virtio_disk.c",
        "disk_submit_indirect",
        "KERNEL_PERFORMANCE_VIRTIO_READ_BATCH,\n\t\t1",
        "KERNEL_PERFORMANCE_VIRTIO_WRITE_BATCH,\n\t\t1",
    ))
    _reject(_case(
        sources,
        "os/virtio_disk.c",
        "virtio_disk_read_batch",
        "&buffers[offset], batch, VIRTIO_BLK_T_IN",
        "&buffers[offset], batch, VIRTIO_BLK_T_OUT",
    ))
    _reject(_case(
        sources,
        "os/fs_epoch.c",
        "fs_epoch_commit",
        "epoch.totals.successful_commits++;",
        "epoch.totals.failed_commits++;",
    ))
    _reject(_case(
        sources,
        "os/vm.c",
        "uvm_cow_fault",
        "cow_stats.cow_fault_copies++;",
        "cow_stats.cow_fault_promotions++;",
    ))
    _reject(_case(
        sources,
        "os/loader.c",
        "user_image_rx_cache_lookup",
        "user_image_rx_cache_stats.exec_cache_hits++;",
        "user_image_rx_cache_stats.exec_cache_misses++;",
    ))
    _reject(_case(
        sources,
        "os/exec_policy.c",
        "exec_policy_process_bootstrap",
        "EXEC_FLAG_BOOTSTRAP",
        "EXEC_FLAG_TRUSTED",
    ))
    _reject(_case(
        sources,
        observer,
        "sys_agent_performance_snapshot",
        "snapshot.sample_tick = get_cycle();",
        "snapshot.sample_tick = 0;",
    ))
    showcase = "user/src/labdemo_ucore.c"
    showcase_header = "user/include/labdemo_workload.h"
    for retired_field in (
        "metadata_dirty",
        "metadata_durable",
        "metadata_requests",
        "metadata_coalesced",
        "metadata_commits",
        "metadata_pending",
    ):
        _reject(_text_case(
            sources,
            showcase_header,
            "\tstruct agent_performance_snapshot snapshot;\n};",
            "\tstruct agent_performance_snapshot snapshot;\n"
            f"\tuint64 {retired_field};\n"
            "};",
        ))
    _reject(_case(
        sources,
        showcase,
        "take_performance_snapshot",
        "memset(receipt, 0, sizeof(*receipt));",
        "receipt->observer_pid = 0;",
    ))
    _reject(_case(
        sources,
        showcase,
        "take_performance_snapshot",
        "memset(receipt, 0, sizeof(*receipt));",
        "struct agent_info info;\n\n"
        "\tmemset(receipt, 0, sizeof(*receipt));\n"
        "\tcheck(agent_info(&info) == 0, \"forged metadata snapshot\");",
    ))
    _reject(_case(
        sources,
        showcase,
        "performance_storage_equal",
        "left->block_physical_writes == right->block_physical_writes",
        "left->block_physical_reads == right->block_physical_reads",
    ))
    _reject(_case(
        sources,
        showcase,
        "demo_quiescence_fence",
        "epoch_commits=%llu epoch_buffers_staged=%llu",
        "epoch_buffers_staged=%llu epoch_commits=%llu",
    ))
    _reject(_case(
        sources,
        showcase,
        "demo_quiescence_fence",
        "current.snapshot.block_physical_writes,\n"
        "\t\t\t       current.snapshot.block_physical_reads,",
        "current.snapshot.block_physical_reads,\n"
        "\t\t\t       current.snapshot.block_physical_reads,",
    ))
    _reject(_case(
        sources,
        showcase,
        "demo_quiescence_fence",
        "overwrite_prereads_skipped=%llu\\n\"",
        "overwrite_prereads_skipped=%llu metadata_dirty=%llu\\n\"",
    ))
    _reject(_case(
        sources,
        showcase,
        "performance_delta",
        "return after - before;",
        "return 0;",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "before_receipt->observer_pid == after_receipt->observer_pid",
        "before_receipt->observer_pid != after_receipt->observer_pid",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "before->observer_lifecycle_id == after->observer_lifecycle_id",
        "before->observer_lifecycle_id != after->observer_lifecycle_id",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "before->observer_lifecycle_generation ==\n\t\t      after->observer_lifecycle_generation",
        "before->observer_lifecycle_generation !=\n\t\t      after->observer_lifecycle_generation",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "before->sample_tick < after->sample_tick",
        "before->sample_tick <= after->sample_tick",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "before_epoch_commits=%llu after_epoch_commits=%llu",
        "epoch_commits=%llu epoch_commits_delta=%llu",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "before->fs_epoch_commits, after->fs_epoch_commits,",
        "after->fs_epoch_commits, after->fs_epoch_commits,",
    ))
    _reject(_case(
        sources,
        showcase,
        "print_mechanism_delta",
        "after_overwrite_prereads_skipped=%llu\\n\"",
        "after_overwrite_prereads_skipped=%llu "
        "before_metadata_pending=%llu after_metadata_pending=%llu\\n\"",
    ))
    _reject(_case(
        sources,
        showcase,
        "run_orchestrator",
        "take_performance_snapshot(&measurement_warmup);",
        "memset(&measurement_warmup, 0, sizeof(measurement_warmup));",
    ))
    _reject(_case(
        sources,
        showcase,
        "run_compat_workload",
        "take_performance_snapshot(&state->core_ack);\n"
        "\tmetrics->workload_syscalls = performance_delta(\n"
        "\t\tstate->core_start.performance.snapshot.observer_workload_syscalls,\n"
        "\t\tstate->core_ack.snapshot.observer_workload_syscalls);\n"
        "\tcheck(metrics->workload_syscalls != 0, \"compat workload syscall receipt\");\n"
        "\tdemo_quiescence_fence(\"compat\", \"ACK_SETTLED\", 3,",
        "demo_quiescence_fence(\"compat\", \"ACK_SETTLED\", 3,\n"
        "\t\t\t      &state->ack_settled);\n"
        "\ttake_performance_snapshot(&state->core_ack);\n"
        "\t/* forged late core boundary */\n"
        "\tdemo_quiescence_fence(\"compat\", \"ACK_SETTLED_FORGED\", 3,",
    ))
    changed = dict(sources)
    changed[kernel_ids] = changed[kernel_ids].replace(
        "SYS_agent_performance_snapshot 560",
        "SYS_agent_performance_snapshot 559",
        1,
    )
    _reject(changed)
    changed = dict(sources)
    changed[user_ids] = changed[user_ids].replace(
        "SYS_agent_performance_snapshot 560",
        "SYS_agent_performance_snapshot 559",
        1,
    )
    _reject(changed)
    changed = dict(sources)
    arch_ids = "user/lib/arch/riscv/syscall_ids.h.in"
    changed[arch_ids] = changed[arch_ids].replace(
        "__NR_agent_performance_snapshot 560",
        "__NR_agent_performance_snapshot 559",
        1,
    )
    _reject(changed)
    changed = dict(sources)
    manifest = "user/include/exec_policy_manifest.h"
    changed[manifest] = changed[manifest].replace(
        "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ARTIFACT), 0,",
        "0, 0,",
        1,
    )
    _reject(changed)

    delegated = "user/src/rp_orch.c"
    _reject(_case(
        sources, delegated, "main", "int64 steady_clock = get_mtime();",
        "int64 steady_clock = 0;",
    ))
    _reject(_case(
        sources, delegated, "main",
        ":\n\t\t\trun_child(&PROGRAMS[i], in_orchestrator,\n"
        "\t\t\t\t  &orchestrator_identity);",
        ":\n\t\t\tforged_run_child(&PROGRAMS[i], in_orchestrator,\n"
        "\t\t\t\t  &orchestrator_identity);",
    ))
    _reject(_case(
        sources, delegated, "main", "(uint64)steady_clock))",
        "(uint64)(steady_clock + 1)))",
    ))
    _reject(_case(
        sources, delegated, "write_workflow_completion",
        "record.steady_start_ms = steady_start_ms;",
        "record.steady_start_ms = 0;",
    ))

    for clock in ("baseline_ucore/user/lib/syscall.c", "user/lib/syscall.c"):
        _reject(_case(
            sources, clock, "get_mtime", "sys_get_time(&time, 0)",
            "forged_get_time(&time, 0)",
        ))
        _reject(_case(
            sources, clock, "get_mtime", "if (err == 0) {",
            "time.sec = 7;\n\tif (err == 0) {",
        ))
        _reject(_case(
            sources, clock, "sys_get_time", "syscall(SYS_gettimeofday, ts, tz)",
            "syscall(SYS_yield, ts, tz)",
        ))

    print("test_scenario_timing_source: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
