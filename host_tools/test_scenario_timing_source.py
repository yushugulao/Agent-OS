#!/usr/bin/env python3
"""Mutation tests for Task 6 Guest makespan source provenance."""
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
    child_start, child_end = _function_span(orchestrator, "run_child")
    child = orchestrator[child_start:child_end]
    if "if (!in_orchestrator)\n\t\t\tclose(attest_pipe[0]);" not in child:
        raise AssertionError("delegated child closes a descriptor it did not inherit")
    if child.find("read_launch_attestation") > child.find("waitpid"):
        raise AssertionError("child attestation is not consumed before waitpid")

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
    if "get_mtime() >= admission_deadline" not in stability or "sleep(1)" not in stability:
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
        "sleep(1) < 0",
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
        "snapshot->reserved_pending = policy->reserved_pending;",
        "snapshot->reserved_pending = 0;",
    ))
    changed = dict(sources)
    kernel_ids = "os/syscall_ids.h"
    changed[kernel_ids] = changed[kernel_ids].replace(
        "SYS_agent_resource_snapshot 559",
        "SYS_agent_resource_snapshot 558",
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
        "ok += run_child(&PROGRAMS[i], in_orchestrator);",
        "ok += forged_run_child(&PROGRAMS[i], in_orchestrator);",
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
