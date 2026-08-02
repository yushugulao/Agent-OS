#!/usr/bin/env python3
"""Regression tests for the independent-boot evaluation evidence contract."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import tempfile
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

from evaluation_contract import (
    EvaluationError,
    _binding_sha256,
    _expected_result,
    _expected_result_cached,
    _expected_workload,
    _expected_workload_cached,
    _file_operation_targets,
    _file_target_meta,
    _file_target_sequence,
    _fnv_bytes,
    _fnv_u64,
    _format_functional_receipt,
    _functional_semantic,
    _headline_significance_threshold,
    _joint_mcid_sign_test,
    _load_supports_headline_claim,
    _operations_for,
    _semantic_token,
    _task2_schema_fingerprint,
    _task3_semantic,
    _task3_tool_semantic,
    _task4_fixture,
    _task4_query_semantic,
    build,
    load_run_plan,
    load_scenario_report,
    load_suite,
    main as evaluation_contract_main,
    validate_guest_log,
    verify,
    write_json,
    write_jsonl,
)
from render_evaluation_dashboard import DashboardError, validate_summary
from evaluation_scenario import (
    RESOURCE_STABILITY_CHILD_ROUNDS,
    RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND,
    RESOURCE_STABILITY_FILE_OBJECTS,
    RESOURCE_STABILITY_LOAD_WORKFLOWS,
    RESOURCE_STABILITY_MEASUREMENT_SCOPE,
    RESOURCE_STABILITY_MEMORY_PAGES,
    RESOURCE_STABILITY_METADATA_OPS,
    RESOURCE_STABILITY_GROWTH_BOUNDS,
    RESOURCE_STABILITY_RESOURCE_KINDS,
    RESOURCE_STABILITY_RECORD_KEYS,
    RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
    _agentos_challenge_oracle,
    _resource_stability_nonce,
    _resource_stability_report_guard,
    _summarize as summarize_scenario,
)
import agenteval_measurement_source_contract as measurement_source
import scenario_timing_source_contract as scenario_timing_source


ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "ci" / "evaluation-suite.json"
COMMIT = "a" * 40
ENVIRONMENT = "e" * 64


def _scenario_agentos_acceptance(challenge: str, index: int) -> dict[str, object]:
    oracle = _agentos_challenge_oracle(challenge)
    record_hash = 100 + index
    followup_hash = 200 + index
    return {
        "schema": "agentos_task6_acceptance_v3",
        "challenge_binding": {
            "workflow_id": oracle["workflow_id"],
            "workflow_run_id": oracle["workflow_run_id"],
            "input_sha256": oracle["input_sha256"],
            "derived_sha256": oracle["derived_sha256"],
            "workflow_outputs": "verified",
        },
        "required_modules": [
            "context", "structured_tool", "metadata_query", "observation"
        ],
        "modules": [
            {
                "module": "context",
                "operation": "context_snapshot",
                "status": "verified",
                "records": 4,
                "latest_sequence": 10,
                "request_id": oracle["request_id"],
                "tool_id": 1,
                "record_sequence": 9,
                "record_hash": record_hash,
                "payload": oracle["echo_payload"],
                "result": oracle["echo_payload"],
                "followup_sequence": 10,
                "followup_record_hash": followup_hash,
            },
            {
                "module": "structured_tool",
                "operation": "agent_run_echo",
                "status": "verified",
                "request_id": oracle["request_id"],
                "tool_id": 1,
                "request_payload": oracle["echo_payload"],
                "arg0": oracle["request_id"],
                "arg1": oracle["followup_request_id"],
                "result_version": 1,
                "result_status": 0,
                "result_tool_id": 1,
                "result_request_id": oracle["request_id"],
                "result_payload": oracle["echo_payload"],
                "result_value0": len(str(oracle["echo_payload"])),
                "result_value1": oracle["request_id"],
                "result_value2": oracle["followup_request_id"],
                "result_sequence": 9,
            },
            {
                "module": "metadata_query",
                "operation": "file_query_stage_index",
                "status": "verified",
                "project": "lab-gene-x",
                "workflow_id": oracle["workflow_id"],
                "workflow_run_id": oracle["workflow_run_id"],
                "kernel_run_id": oracle["kernel_run_id"],
                "input_sha256": oracle["input_sha256"],
                "derived_sha256": oracle["derived_sha256"],
                "stage": "align",
                "returned": 1,
                "used_index": 1,
                "plan": 2,
                "target_fid": 101,
                "target_physical": oracle["target_physical"],
                "target_stage": "align",
                "target_kind": "artifact",
                "target_status": "ok",
                "target_summary": oracle["target_summary"],
            },
            {
                "module": "observation",
                "operation": "timeline_provenance_ledger",
                "status": "verified",
                "timeline_records": 4,
                "provenance_edges": 1,
                "ledger_records": 9,
                "ledger_hash": 900 + index,
                "edge_kind": 1,
                "edge_tool_id": 1,
                "edge_status": 0,
                "source_sequence": 9,
                "target_sequence": 10,
                "source_record_hash": record_hash,
                "target_record_hash": followup_hash,
                "request_id": oracle["request_id"],
                "workflow_id": oracle["workflow_id"],
            },
        ],
    }


def _scenario_resource_stability(
    challenge: str, boot_index: int
) -> dict[str, object]:
    workflows: list[dict[str, object]] = []
    workflow_count = (
        RESOURCE_STABILITY_LOAD_WORKFLOWS
        + RESOURCE_STABILITY_TERMINAL_WORKFLOWS
    )
    for workflow_index in range(workflow_count):
        load = workflow_index < RESOURCE_STABILITY_LOAD_WORKFLOWS
        rounds = RESOURCE_STABILITY_CHILD_ROUNDS if load else 0
        scope_id = 500 + boot_index * 10 + workflow_index
        workflows.append(
            {
                "workflow_index": workflow_index,
                "mode": "load" if load else "terminal",
                "challenge_nonce": _resource_stability_nonce(
                    challenge, workflow_index, "load" if load else "terminal"
                ),
                "lifecycle_id": 1000 + boot_index * 10 + workflow_index,
                "lifecycle_generation": 100 + boot_index * 10 + workflow_index,
                "scope_id": scope_id,
                "io_owner": 0x80000000 | scope_id,
                "resource_account_slot": 50 + workflow_index,
                "resource_account_reserved": 0,
                "resource_account_generation": 200 + boot_index * 10 + workflow_index,
                "initial_cache_resident": 0,
                "initial_leased": 0,
                "initial_debt": 0,
                "initial_waiters": 0,
                "initial_debt_waiters": 0,
                "initial_admission_waiters": 0,
                "initial_context_lane_depth": 0,
                "initial_context_lane_waiters": 0,
                "initial_metadata_owned": 0,
                "initial_metadata_waiters": 0,
                "initial_agent_calls": 0,
                "initial_context_records": 0,
                "final_cache_resident": 2 if load else 0,
                "final_leased": 0,
                "final_debt": 0,
                "final_waiters": 0,
                "final_debt_waiters": 0,
                "final_admission_waiters": 0,
                "final_context_lane_depth": 0,
                "final_context_lane_waiters": 0,
                "final_metadata_owned": 0,
                "final_metadata_waiters": 0,
                "final_agent_calls": (
                    rounds * RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND
                ),
                "final_context_records": (
                    rounds * RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND
                ),
                "initial_completion_sequence": 1000 + workflow_index * 100,
                "final_completion_sequence": 1000 + workflow_index * 100 + rounds,
                "process_rounds": rounds,
                "file_rounds": rounds,
                "memory_rounds": rounds,
                "metadata_rounds": rounds,
                "ordinary_free_pages_before": 20000,
                "ordinary_free_pages_after": 20000,
                "reserved_free_pages_before": 1500,
                "reserved_free_pages_after": 1500,
                "stack_reserved_free_pages_before": 128,
                "stack_reserved_free_pages_after": 128,
                "per_workflow_bound_status": "verified",
                "reaped": 1,
                "pipe_eof": 1,
                "status": "verified",
            }
        )
        workflows[-1]["report_guard"] = _resource_stability_report_guard(
            workflows[-1]
        )
        for resource_index, kind in enumerate(RESOURCE_STABILITY_RESOURCE_KINDS):
            ordinary_before = 10 + resource_index * 10
            reserved_before = resource_index + 1
            growth = 2 if load and kind in {"fs_block", "buffer_cache"} else 0
            workflows[-1][f"{kind}_ordinary_used_before"] = ordinary_before
            workflows[-1][f"{kind}_ordinary_used_after"] = ordinary_before + growth
            workflows[-1][f"{kind}_ordinary_pending_before"] = 0
            workflows[-1][f"{kind}_ordinary_pending_after"] = 0
            workflows[-1][f"{kind}_reserved_used_before"] = reserved_before
            workflows[-1][f"{kind}_reserved_used_after"] = reserved_before
            workflows[-1][f"{kind}_reserved_pending_before"] = 0
            workflows[-1][f"{kind}_reserved_pending_after"] = 0
        workflows[-1] = {
            key: workflows[-1][key] for key in RESOURCE_STABILITY_RECORD_KEYS
        }
    capacities = {
        "process": 128,
        "thread": 2048,
        "file_object": 2048,
        "fs_block": 10000,
        "fs_inode": 512,
        "buffer_cache": 128,
        "agent_state_page": 4096,
        "physical_page": 32256,
    }
    return {
        "schema": "agentos_resource_stability_v5",
        "measurement_scope": RESOURCE_STABILITY_MEASUREMENT_SCOPE,
        "timed_makespan_included": False,
        "claim_scope": "configured_global_counter_reclamation",
        "configured_kind_coverage": "measured_mask_only",
        "account_coverage": "self_identity_only",
        "rate_budget_coverage": "not_measured",
        "global_leak_freedom": "not_claimed",
        "challenge_suffix": str(int(challenge[3:])),
        "load_workflows": RESOURCE_STABILITY_LOAD_WORKFLOWS,
        "terminal_workflows": RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
        "child_rounds_per_workflow": RESOURCE_STABILITY_CHILD_ROUNDS,
        "memory_pages_per_round": RESOURCE_STABILITY_MEMORY_PAGES,
        "file_objects_per_round": RESOURCE_STABILITY_FILE_OBJECTS,
        "metadata_ops_per_round": RESOURCE_STABILITY_METADATA_OPS,
        "context_records_per_round": (
            RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND
        ),
        "sequence_bound_status": "verified",
        "status": "verified",
        "global_policy": {
            "measured_mask": (1 << len(RESOURCE_STABILITY_RESOURCE_KINDS)) - 1,
            "measured_mask_semantics": "configured_global_resource_kind_counters_only",
            "snapshot_consistency": "single_core_irq_coherent",
            "coverage": "configured_global_kind_counters",
            "account_counter_coverage": "not_measured",
            "rate_budget_coverage": "not_measured",
            "growth_bound_semantics": "per_class_positive_delta_sum",
            "decrease_semantics": "reclamation_allowed",
            "free_pages_status": "measured",
            "terminal_workflow_pair_bound": 0,
            "resources": [
                {
                    "kind": kind,
                    "status": "measured",
                    "capacity": capacities[kind],
                    "per_workflow_growth_bound": RESOURCE_STABILITY_GROWTH_BOUNDS[kind],
                    "terminal_growth_bound": RESOURCE_STABILITY_GROWTH_BOUNDS[kind],
                }
                for kind in RESOURCE_STABILITY_RESOURCE_KINDS
            ],
        },
        "workflows": workflows,
    }


def measurement_source_receipt() -> dict[str, object]:
    return {
        "contract_versions": {
            "functional": measurement_source.FUNCTIONAL_CONTRACT_VERSION,
            "functional_compile": (
                measurement_source.FUNCTIONAL_COMPILE_CONTRACT_VERSION
            ),
            "micro": measurement_source.CONTRACT_VERSION,
            "policy": measurement_source.POLICY_INVENTORY_SCHEMA,
            "scenario": scenario_timing_source.CONTRACT_VERSION,
        },
        "formal_boot_count": measurement_source.FORMAL_BOOT_COUNT,
        "policy_inventory": measurement_source.measurement_source_policy_inventory(),
        "schema": measurement_source.RECEIPT_SCHEMA,
        "source_commit": COMMIT,
        "sources": [
            {"bytes": index, "path": path, "sha256": f"{index:064x}"}
            for index, path in enumerate(
                measurement_source._receipt_source_paths(), 1
            )
        ],
        "stop_rule": measurement_source.STOP_RULE,
    }


def expect_rejected(action, message: str) -> None:
    try:
        action()
    except EvaluationError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid evidence: {message}")


def assert_guest_sample_call_contract() -> None:
    source = (ROOT / "user" / "src" / "agenteval_ucore.c").read_text(
        encoding="utf-8"
    )
    calls = re.findall(r"(?<!void )print_sample\((.*?)\);", source, re.DOTALL)
    assert len(calls) == 16
    for call in calls:
        arguments = [argument.strip() for argument in call.split(",")]
        assert len(arguments) == 10, arguments
        assert arguments[-2] == "workload", arguments
    assert "finalize_path_file_variant(" in source
    assert "finalize_agent_file_variant(" in source
    assert '"path walk accounting uses actual N files"' in source
    assert '"indexed query reports real candidate work"' in source
    assert "index.work_units == EVAL_FILE_QUERIES" not in source
    assert "functional_file.index_work == EVAL_FILE_QUERIES" not in source


def assert_functional_acceptance_source_contract() -> None:
    guest = (ROOT / "user" / "src" / "agenteval_ucore.c").read_text(
        encoding="utf-8"
    )
    host = (ROOT / "host_tools" / "evaluation_contract.py").read_text(
        encoding="utf-8"
    )
    assert "functional_catalog_load" in guest
    assert "agenteval_ucore: catalog schema=%d challenge=" in guest
    assert "tool_list(functional_tools, 0)" in guest
    assert "task2 unique catalog identity" in guest
    assert '0, "eval_missing_tool", 0' in guest
    assert 'AGENT_TOOL_ECHO, "pid_info", 0' in guest
    assert "AGENT_STATUS_DUPLICATE" in guest
    assert "AGENT_STATUS_BAD_TYPE" in guest
    assert "agent_run(&functional_context_ops[i]" in guest
    assert "task3 post-rollback production tool call" in guest
    assert "context_rollback(rollback_sequence)" in guest
    assert "helper_pid = agent_create();" in guest
    assert "TASK5_DELAY_TICKS" in guest
    assert "functional_info_before.current_tick" in guest
    assert "functional_info_after.sched_dispatch_count" in guest
    assert "task5 delayed wait has bounded dispatches" in guest
    assert '"task5-semantic-v2"' in guest
    assert "wait_ticks < 2 * wait_dispatches" in host
    assert "functional_compat_sentinel_pid = agent_create();" in guest
    assert "agent_create_role(AGENT_ROLE_SENTINEL)" not in guest
    assert "tool_count != 25" not in host
    assert "callable_count != 23" not in host
    assert "TASK2_REQUIRED_TOOLS" in host
    assert "_parse_tool_catalog(lines, challenge)" in host


def assert_task4_dynamic_source_contract() -> None:
    source = (ROOT / "user" / "src" / "agenteval_ucore.c").read_text(
        encoding="utf-8"
    )
    start = source.index("static void run_functional_task4(void)")
    end = source.index("\nstatic void run_functional_sentinel", start)
    body = source[start:end]
    assert "functional_file" not in source
    assert "agent_file_meta_set(&file_meta)" in source[start - 5000:end]
    assert body.count("agent_file_query(&file_query, &file_result)") == 4
    assert "file_query.summary_contains" in source[start - 5000:end]
    assert "AGENT_TOOL_READ_FILE_DIGEST" in body
    assert body.count("task4_delete_metadata(") == 4
    assert "TASK4_FUNCTIONAL_FID_BASE" in body
    assert '"task4-semantic-v2"' in body
    assert "duration_us" not in body


def assert_evaluation_image_role_contract() -> None:
    manifest = (ROOT / "user" / "include" / "exec_policy_manifest.h").read_text(
        encoding="utf-8"
    ).replace("\\\n", " ")
    entry = re.search(
        r'X\("agenteval_ucore",\s*"agenteval_ucore",\s*'
        r'EXEC_MANIFEST_F_BOOT_SEALED,\s*(.*?),\s*0,\s*'
        r'EXEC_MANIFEST_VFS_PROFILE_WORKFLOW\)',
        manifest,
        re.DOTALL,
    )
    assert entry is not None
    role_mask = " ".join(entry.group(1).split())
    assert role_mask == (
        "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_ORCHESTRATOR) | "
        "EXEC_MANIFEST_ROLE_BIT(EXEC_MANIFEST_ROLE_SENTINEL)"
    )


def assert_targeted_runner_uses_canonical_contract() -> None:
    runner = (ROOT / "scripts" / "run-agent-tests.sh").read_text(
        encoding="utf-8"
    )
    case_start = runner.index("\tagenteval_ucore)")
    case_end = runner.index("\n\t\t;;", case_start)
    case = runner[case_start:case_end]
    assert runner.count("evaluation_contract.py validate-guest") == 1
    assert "--suite ci/evaluation-suite.json" in case
    assert '--log "${log_file}"' in case
    assert '--challenge "${AGENT_EVAL_CHALLENGE_HEX}"' in case
    assert "<<'PY'" not in case
    assert "_parse_marker" not in runner
    assert "_expected_result" not in runner


def marker(
    experiment: dict,
    load: int,
    pair: int,
    role: str,
    challenge: str,
    boot_number: int,
    improvement: int = 100,
) -> str:
    variant = experiment[role]
    order = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
    baseline = 1000 + load + boot_number
    per_operation_duration = baseline if role == "baseline" else baseline - improvement
    operations = _operations_for(experiment, load)
    duration = (
        per_operation_duration * operations
        if experiment["unit"] == "us/query"
        else per_operation_duration
    )
    workload = _expected_workload(experiment, load, pair, challenge)
    result = _expected_result(experiment, load, pair, challenge)
    file_experiments = {
        "file_query_path_index", "file_query_table_ablation"
    }
    dataset_size = load if experiment["id"] in file_experiments else 0
    if experiment["id"] == "file_query_path_index":
        work_units = load * operations if role == "baseline" else operations
        records_examined = work_units if role == "baseline" else operations
    elif experiment["id"] == "file_query_table_ablation":
        work_units = 512 * operations if role == "baseline" else operations
        records_examined = operations * (load + 2) if role == "baseline" else operations
    else:
        work_units = operations
        records_examined = 0
    return (
        "agenteval_ucore: sample schema=2 "
        f"experiment={experiment['id']} load={load} pair={pair} "
        f"variant={variant['id']} order={order} cache={variant['cache']} "
        f"operations={operations} dataset_size={dataset_size} "
        f"work_units={work_units} records_examined={records_examined} "
        f"result_items={operations} duration_us={duration} "
        "index_rebuild_records=0 result_cache_hits=0 "
        f"workload_fingerprint={workload} result_fingerprint={result} status=measured"
    )


def diagnostic_marker(
    experiment: dict,
    load: int,
    challenge: str,
    *,
    cache: str = "ready",
    duration_us: int = 1,
    index_rebuild_records: int = 0,
) -> str:
    work_units = index_rebuild_records if cache == "cold-rebuild" else 1
    workload = _expected_workload(
        experiment, load, 0, challenge, operations_override=1
    )
    result = _expected_result(
        experiment, load, 0, challenge, operations_override=1
    )
    return (
        "agenteval_ucore: diagnostic schema=2 "
        f"experiment={experiment['id']} "
        f"load={load} cache={cache} operations=1 dataset_size={load} "
        f"work_units={work_units} result_items=1 duration_us={duration_us} "
        f"index_rebuild_records={index_rebuild_records} result_cache_hits=0 "
        f"workload_fingerprint={workload} result_fingerprint={result} "
        "status=measured"
    )


def functional_lines(challenge: str, boot_number: int) -> tuple[str, list[str]]:
    launcher_pid = 100 + boot_number
    agent_pid = 200 + boot_number
    launcher_values = [launcher_pid, 0, 0, 0, 0]
    launcher_semantic = _functional_semantic(
        "task1-launcher-semantic-v1", challenge, launcher_values
    )
    launcher = _format_functional_receipt(
        "launcher", challenge, launcher_values, launcher_semantic, launcher=True
    )

    context_base = 0x3FFFFE8000
    task1_values = [
        agent_pid, launcher_pid, 1, 4, 1000 + boot_number, context_base,
        6 * 4096, 0x4147435458543031, 8, 128, 128, 2, 20480, 4096,
        int(challenge, 16) ^ agent_pid ^ context_base,
        150 + boot_number, 0, 1, 1,
    ]
    catalog = (
        (1, 1, 3, "echo", "payload:string,arg0:uint64,arg1:uint64"),
        (4, 1, 1, "query_process", "type?:uint64"),
        (13, 1, 2, "capability_check", "role:uint64,action:string"),
        (99, 2, 0, "future_probe", "none"),
    )
    catalog_markers = [
        "agenteval_ucore: catalog schema=1 "
        f"challenge={challenge} index={index} total={len(catalog)} abi=2 "
        f"tool_id={tool_id} flags={flags} param_count={param_count} "
        f"name={name} params={params} status=listed"
        for index, (tool_id, flags, param_count, name, params) in enumerate(catalog)
    ]
    catalog_hash = _fnv_bytes(1469598103934665603, b"task2-tool-catalog-v1")
    for value in (int(challenge, 16), 1, 2, len(catalog)):
        catalog_hash = _fnv_u64(catalog_hash, value)
    for tool_id, flags, param_count, name, params in catalog:
        for value in (tool_id, 2, 216, param_count, flags):
            catalog_hash = _fnv_u64(catalog_hash, value)
        catalog_hash = _fnv_bytes(catalog_hash, name.encode("ascii"))
        catalog_hash = _fnv_bytes(catalog_hash, params.encode("ascii"))
    task2_values = [
        1, 2, len(catalog), 3, 3, 7, catalog_hash,
        _task2_schema_fingerprint(), 1001, len("eval-v2"),
        int(challenge, 16) ^ agent_pid,
        int(challenge, 16) ^ 0xA5A5A5A5A5A5A5A5,
        _fnv_bytes(1469598103934665603, b"eval-v2"),
        1002, 1, 1, 1, 1003, 1, 4, (1 << 10) - 1,
        0, -2, _fnv_bytes(1469598103934665603, b"unknown_tool"),
        0, -1, _fnv_bytes(1469598103934665603, b"tool_mismatch"),
        0, -9, _fnv_bytes(1469598103934665603, b"duplicate_param"),
        0, -15, _fnv_bytes(1469598103934665603, b"bad_param_type"),
    ]
    task3_values = [
        6, 6, 6, 1, 6, _task3_tool_semantic(challenge), 3, 3, 9, 10,
        7, 3, 4, 4, 0, 128, 128, 5, 6, 133, 1, 4,
    ]
    fixture = _task4_fixture(challenge)
    fids = fixture["fids"]
    names = fixture["names"]
    summaries = fixture["summaries"]
    bodies = fixture["bodies"]
    dependency = fixture["dependency_mask"]
    common_query = {
        "flags": 2, "max_hits": 8, "physical_name": "",
        "logical_path": "", "project": "eval4",
        "workflow": "query-proof", "run_id": challenge[1:],
        "stage": "memory", "kind": "artifact", "status": "ready",
        "summary_contains": "",
    }
    summary_query = dict(common_query)
    summary_query["summary_contains"] = f"needle {challenge}"

    def task4_hit(index: int, inum: int, incarnation: int,
                  generation: int) -> dict:
        return {
            "fid": fids[index], "physical_name": names[index],
            "logical_path": names[index], "stage": "memory",
            "kind": "artifact" if index < 2 else "report",
            "status": "ready", "summary": summaries[index],
            "dependency_mask": dependency, "dev": 1, "inum": inum,
            "incarnation": incarnation, "size": len(bodies[index]),
            "fs_generation": generation,
        }

    hit_a = task4_hit(
        0, 3000 + boot_number, 4000 + boot_number, 5000 + boot_number
    )
    hit_b = task4_hit(
        1, 3100 + boot_number, 4100 + boot_number, 5100 + boot_number
    )
    query_generation = 6000 + boot_number
    and_result = {
        "total_hits": 2, "returned": 2, "truncated": 0,
        "used_index": 0, "plan": 0, "fs_generation": query_generation,
    }
    summary_result = {
        "total_hits": 1, "returned": 1, "truncated": 0,
        "used_index": 0, "plan": 0, "fs_generation": query_generation,
    }
    after_a_result = {
        "total_hits": 1, "returned": 1, "truncated": 0,
        "used_index": 0, "plan": 0,
        "fs_generation": query_generation + 1,
    }
    after_b_result = {
        "total_hits": 0, "returned": 0, "truncated": 0,
        "used_index": 0, "plan": 0,
        "fs_generation": query_generation + 2,
    }
    and_semantic = _task4_query_semantic(
        "task4-attributes-v2", challenge, common_query, and_result,
        [hit_a, hit_b],
    )
    summary_semantic = _task4_query_semantic(
        "task4-summary-v2", challenge, summary_query, summary_result, [hit_a]
    )
    after_a_semantic = _task4_query_semantic(
        "task4-delete-one-v2", challenge, common_query, after_a_result,
        [hit_b],
    )
    after_b_semantic = _task4_query_semantic(
        "task4-delete-all-v2", challenge, common_query, after_b_result, []
    )
    digest_request = _semantic_token(
        "task4-digest-v2", fixture["code"], 0, 0, challenge
    )
    digest = _fnv_bytes(1469598103934665603, bodies[0].encode("ascii"))
    task4_values = [
        fixture["code"], *fids,
        2, 2, 0, 0, 0, fids[0], fids[1],
        hit_a["dev"], hit_a["inum"], hit_a["incarnation"], hit_a["size"],
        hit_a["fs_generation"], hit_b["dev"], hit_b["inum"],
        hit_b["incarnation"], hit_b["size"], hit_b["fs_generation"],
        query_generation, and_semantic,
        1, 1, 0, 0, 0, fids[0], hit_a["dev"], hit_a["inum"],
        hit_a["incarnation"], hit_a["size"], hit_a["fs_generation"],
        query_generation, summary_semantic,
        digest_request, digest_request, 8000 + boot_number, 0, 20,
        len(bodies[0]), len(bodies[0]), digest, digest,
        0, 1, 1, fids[1], query_generation + 1, after_a_semantic,
        0, 0, 0, query_generation + 2, after_b_semantic,
    ]
    task5_values = [
        agent_pid, 300 + boot_number, 300 + boot_number, agent_pid,
        _semantic_token("task5-event-v1", 2, 0, 0, challenge),
        6000 + boot_number, 7000 + boot_number, 10, 11, 20, 21,
        7100 + boot_number, 7101 + boot_number, 2, 2, -7, 0, 2, 1,
        6990 + boot_number, 7002 + boot_number, 40 + boot_number,
        41 + boot_number, 800 + boot_number * 20,
        820 + boot_number * 20, 5, 7, 50,
    ]
    values_by_task = {
        "task1": task1_values,
        "task2": task2_values,
        "task3": task3_values,
        "task4": task4_values,
        "task5": task5_values,
    }
    semantic_by_task = {
        task: (
            _task3_semantic(challenge, values)
            if task == "task3"
            else _functional_semantic(
                "task4-semantic-v2" if task == "task4" else
                "task5-semantic-v2" if task == "task5" else
                f"{task}-semantic-v1",
                challenge,
                values,
            )
        )
        for task, values in values_by_task.items()
    }
    receipts = {
        task: _format_functional_receipt(
            task, challenge, values_by_task[task], semantic_by_task[task]
        )
        for task in ("task1", "task2", "task3", "task4", "task5")
    }
    return launcher, [
        receipts["task1"], *catalog_markers, receipts["task2"],
        receipts["task3"], receipts["task4"], receipts["task5"],
    ]


def rewrite_task4_receipt(lines: list[str], challenge: str, mutate) -> list[str]:
    rewritten = list(lines)
    for index, line in enumerate(rewritten):
        if not line.startswith(
            "agenteval_ucore: functional schema=1 task=task4 "
        ):
            continue
        value_text = re.search(r" values=([^ ]+) ", line).group(1)
        values = [int(value) for value in value_text.split(",")]
        mutate(values)
        semantic = _functional_semantic("task4-semantic-v2", challenge, values)
        rewritten[index] = _format_functional_receipt(
            "task4", challenge, values, semantic
        )
        return rewritten
    raise AssertionError("Task4 receipt is missing from fixture")


def rewrite_task5_receipt(lines: list[str], challenge: str, mutate) -> list[str]:
    rewritten = list(lines)
    for index, line in enumerate(rewritten):
        if not line.startswith(
            "agenteval_ucore: functional schema=1 task=task5 "
        ):
            continue
        value_text = re.search(r" values=([^ ]+) ", line).group(1)
        values = [int(value) for value in value_text.split(",")]
        mutate(values)
        semantic = _functional_semantic("task5-semantic-v2", challenge, values)
        rewritten[index] = _format_functional_receipt(
            "task5", challenge, values, semantic
        )
        return rewritten
    raise AssertionError("Task5 receipt is missing from fixture")


def make_log(suite: dict, boot_number: int, improvement: int = 100, same_order: bool = False) -> tuple[str, str]:
    challenge = f"{boot_number + 1:016x}"
    launcher, receipts = functional_lines(challenge, boot_number)
    lines = ["boot", f"agenteval_ucore: challenge={challenge}", launcher]
    experiments = {
        experiment["id"]: experiment for experiment in suite["experiments"]
    }
    for scheduled in suite["execution_schedule"]:
        experiment = experiments[scheduled["experiment"]]
        load = scheduled["load"]
        if experiment["id"] in {
            "file_query_path_index", "file_query_table_ablation"
        }:
            lines.append(diagnostic_marker(experiment, load, challenge))
        for pair in range(1, 8):
            order = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
            if same_order:
                order = "AB"
            roles = ("baseline", "treatment") if order == "AB" else ("treatment", "baseline")
            for role in roles:
                value = marker(experiment, load, pair, role, challenge, boot_number, improvement)
                if same_order:
                    expected = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
                    value = value.replace(f"order={expected}", "order=AB")
                lines.append(value)
    lines.extend((*receipts, "agenteval_ucore: worker passed", "agenteval_ucore: parent passed"))
    return challenge, "\n".join(lines) + "\n"


def write_campaign(
    root: Path,
    suite: dict,
    count: int = 7,
    improvement_by_boot: list[int] | None = None,
    same_order: bool = False,
    unavailable_last: bool = False,
    all_status: str | None = None,
) -> Path:
    logs = []
    for index in range(count):
        improvement = improvement_by_boot[index] if improvement_by_boot else 100
        challenge, content = make_log(suite, index, improvement, same_order)
        ref = f"boot-{index + 1:02d}/guest.log"
        path = root / ref
        path.parent.mkdir(parents=True)
        status = all_status or ("unavailable" if unavailable_last and index == count - 1 else "supported")
        if status != "supported":
            content = f"runner: {status} boot={index + 1} challenge={challenge}\n"
        path.write_text(content, encoding="utf-8")
        command = [
            "env",
            f"AGENT_EVAL_CHALLENGE_HEX={challenge}",
            f"AGENT_TEST_GUEST_LOG_FILE=results/evaluation/runs/contract-test/raw/{ref}",
            "bash",
            "scripts/run-agent-tests.sh",
        ]
        command_sha256 = hashlib.sha256(
            json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        logs.append({
            "path": ref,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "boot_id": f"boot-{index + 1:02d}",
            "commit": COMMIT,
            "challenge": challenge,
            "kernel_sha256": "b" * 64,
            "image_input_sha256": hashlib.sha256(
                ("pristine-image-" + challenge).encode("ascii")
            ).hexdigest(),
            "image_final_sha256": "d" * 64,
            "runner_log_sha256": hashlib.sha256(("runner-" + challenge).encode("ascii")).hexdigest(),
            "command_argv": command,
            "command_sha256": command_sha256,
            "status": status,
            "detail": None if status == "supported" else f"capture status: {status}",
        })
    plan = {
        "schema_version": 2,
        "kind": "agentos-evaluation-run-plan",
        "run_id": "contract-test",
        "environment_sha256": ENVIRONMENT,
        "campaign_sha256": "f" * 64,
        "suite_sha256": __import__("hashlib").sha256(SUITE_PATH.read_bytes()).hexdigest(),
        "measurement_source_receipt": measurement_source_receipt(),
        "stop_rule": measurement_source.STOP_RULE,
        "logs": logs,
    }
    path = root / "run-plan.json"
    path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    return path


def refresh_plan_hash(plan_path: Path, source_root: Path) -> None:
    import hashlib
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for item in plan["logs"]:
        item["sha256"] = hashlib.sha256((source_root / item["path"]).read_bytes()).hexdigest()
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")


def make_experiment_slower(
    root: Path, experiment_id: str, treatment_variant: str
) -> None:
    for boot in range(1, 8):
        log_path = root / f"boot-{boot:02d}/guest.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        baselines: dict[tuple[int, int], int] = {}
        for line in lines:
            if (
                line.startswith("agenteval_ucore: sample ")
                and f"experiment={experiment_id} " in line
                and " variant=" + treatment_variant + " " not in line
            ):
                load = int(re.search(r" load=([0-9]+) ", line).group(1))
                pair = int(re.search(r" pair=([0-9]+) ", line).group(1))
                baselines[(load, pair)] = int(
                    re.search(r" duration_us=([0-9]+) ", line).group(1)
                )
        rewritten = []
        for line in lines:
            if (
                line.startswith("agenteval_ucore: sample ")
                and f"experiment={experiment_id} " in line
                and " variant=" + treatment_variant + " " in line
            ):
                load = int(re.search(r" load=([0-9]+) ", line).group(1))
                pair = int(re.search(r" pair=([0-9]+) ", line).group(1))
                operations = int(
                    re.search(r" operations=([0-9]+) ", line).group(1)
                )
                line = re.sub(
                    r"duration_us=[0-9]+",
                    f"duration_us={baselines[(load, pair)] + 100 * operations}",
                    line,
                )
            rewritten.append(line)
        log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def main() -> int:
    assert_guest_sample_call_contract()
    assert_functional_acceptance_source_contract()
    assert_task4_dynamic_source_contract()
    assert_evaluation_image_role_contract()
    assert_targeted_runner_uses_canonical_contract()
    suite = load_suite(SUITE_PATH)
    assert suite["claim_family"] == {
        "familywise_alpha": 0.05,
        "hypotheses": [
            "file_query_path_index", "file_query_table_ablation",
            "tool_batch", "context_access",
        ],
        "id": "agentos-evaluation-headlines-v2",
        "load_gate": "intersection",
        "method": "bonferroni",
    }
    assert suite["competition_claims"] == {
        "task4": {
            "benchmark_id": "file_query_path_index",
            "required_status": "supported",
        }
    }
    assert [
        (item["experiment"], item["load"])
        for item in suite["execution_schedule"]
    ] == [
        ("file_query_path_index", 8),
        ("file_query_path_index", 24),
        ("file_query_table_ablation", 24),
        ("file_query_path_index", 48),
        ("file_query_table_ablation", 64),
        ("file_query_path_index", 96),
        ("file_query_table_ablation", 96),
        ("tool_batch", 24),
        ("tool_batch", 64),
        ("tool_batch", 96),
        ("context_access", 24),
        ("context_access", 64),
        ("context_access", 96),
    ]
    file_experiments = {
        experiment["id"]: experiment
        for experiment in suite["experiments"]
        if experiment["id"].startswith("file_query_")
    }
    assert file_experiments["file_query_path_index"]["loads"] == [
        8, 24, 48, 96,
    ]
    assert file_experiments["file_query_path_index"]["operation_counts"] == [
        8, 6, 4, 4,
    ]
    assert file_experiments["file_query_table_ablation"]["loads"] == [
        24, 64, 96,
    ]
    assert file_experiments["file_query_table_ablation"]["operation_counts"] == [
        16, 16, 16,
    ]
    headline_alpha = _headline_significance_threshold(suite)
    assert abs(headline_alpha - (0.05 / 4)) < 1e-15
    assert {
        experiment["id"]: _expected_workload(experiment, 24, 1, "0000000000000001")
        for experiment in suite["experiments"]
    } == {
        "file_query_path_index": "0e245d6ca9258c40",
        "file_query_table_ablation": "3ab46b7e9e15782e",
        "tool_batch": "03dcdc53ddad7ecf",
        "context_access": "56b98203222b1384",
    }
    tool_experiment = next(
        experiment for experiment in suite["experiments"]
        if experiment["id"] == "tool_batch"
    )
    _expected_workload_cached.cache_clear()
    _expected_result_cached.cache_clear()
    workload = _expected_workload(tool_experiment, 24, 1, "0000000000000001")
    workload_cache = _expected_workload_cached.cache_info()
    assert _expected_workload(
        tool_experiment, 24, 1, "0000000000000001"
    ) == workload
    assert _expected_workload_cached.cache_info().hits == workload_cache.hits + 1
    changed_selector = copy.deepcopy(tool_experiment)
    changed_selector["selector"] += 1
    assert _expected_workload(
        changed_selector, 24, 1, "0000000000000001"
    ) != workload
    result = _expected_result(tool_experiment, 24, 1, "0000000000000001")
    result_cache = _expected_result_cached.cache_info()
    assert _expected_result(
        tool_experiment, 24, 1, "0000000000000001"
    ) == result
    assert _expected_result_cached.cache_info().hits == result_cache.hits + 1
    assert _expected_result(
        tool_experiment,
        24,
        1,
        "0000000000000001",
        operations_override=_operations_for(tool_experiment, 24) + 1,
    ) != result
    _expected_workload_cached.cache_clear()
    _expected_result_cached.cache_clear()
    for experiment in file_experiments.values():
        for load, operations in zip(
            experiment["loads"], experiment["operation_counts"]
        ):
            for challenge_value in (1, 2, 0x123456789ABCDEF0):
                challenge = f"{challenge_value:016x}"
                targets = _file_target_sequence(
                    load, suite["pairing"]["minimum_inner_pairs"], challenge
                )
                assert len(targets) == len(set(targets)) == 7
                for pair in range(1, 8):
                    operation_targets = _file_operation_targets(
                        load, pair, operations, challenge
                    )
                    assert len(operation_targets) == len(set(operation_targets))
                workloads = {
                    _expected_workload(experiment, load, pair, challenge)
                    for pair in range(1, 8)
                }
                results = {
                    _expected_result(experiment, load, pair, challenge)
                    for pair in range(1, 8)
                }
                assert len(workloads) == len(results) == 7
    assert all(
        _expected_result(experiment, 24, 1, "0000000000000001")
        != _expected_result(experiment, 24, 1, "0000000000000002")
        for experiment in suite["experiments"]
    )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        plan_path = write_campaign(root, suite)
        loaded_plan, _ = load_run_plan(plan_path)
        assert loaded_plan["schema_version"] == 2
        assert loaded_plan["stop_rule"] == measurement_source.STOP_RULE
        assert loaded_plan["measurement_source_receipt"] == (
            measurement_source_receipt()
        )
        forged_stop_rule = copy.deepcopy(loaded_plan)
        forged_stop_rule["stop_rule"] = "rerun_until_supported"
        forged_stop_rule_path = root / "forged-stop-rule.json"
        forged_stop_rule_path.write_text(
            json.dumps(forged_stop_rule), encoding="utf-8"
        )
        expect_rejected(
            lambda: load_run_plan(forged_stop_rule_path),
            "run plan header is invalid",
        )
        forged_receipt = copy.deepcopy(loaded_plan)
        forged_receipt["measurement_source_receipt"]["source_commit"] = "b" * 40
        forged_receipt_path = root / "forged-measurement-receipt.json"
        forged_receipt_path.write_text(
            json.dumps(forged_receipt), encoding="utf-8"
        )
        expect_rejected(
            lambda: load_run_plan(forged_receipt_path),
            "measurement source receipt is invalid",
        )
        targeted_log = root / "boot-01/guest.log"
        targeted = validate_guest_log(
            targeted_log, suite, "0000000000000001"
        )
        assert targeted == {
            "schema_version": 1,
            "kind": "agentos-evaluation-guest-validation",
            "challenge": "0000000000000001",
            "samples": 182,
            "diagnostics": 7,
            "functional_receipts": 6,
            "catalog_descriptors": 4,
            "status": "supported",
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert evaluation_contract_main([
                "validate-guest",
                "--suite", str(SUITE_PATH),
                "--log", str(targeted_log),
                "--challenge", "0000000000000001",
            ]) == 0
        assert json.loads(stdout.getvalue()) == targeted
        expect_rejected(
            lambda: validate_guest_log(
                targeted_log, suite, "0000000000000002"
            ),
            "challenge differs",
        )
        summary, rows = build(SUITE_PATH, plan_path, root)
        validate_summary(summary)
        assert summary["run"]["status"] == "measured"
        assert all(
            set(item) == {
                "id", "label", "task", "functional_status",
                "performance_status", "performance", "evidence_ids",
            }
            for item in summary["scenarios"]
        )
        assert [
            item["functional_status"] for item in summary["scenarios"]
        ] == ["pass", "pass", "pass", "pass", "pass", "unavailable"]
        assert all(
            item["performance_status"] == "unavailable"
            and item["performance"] is None
            for item in summary["scenarios"]
        )
        assert len(rows) == 7 * (13 * 7 * 2 + 7)
        benchmark = summary["benchmarks"][0]
        assert benchmark["status"] == "measured"
        assert {estimate["n"] for estimate in benchmark["estimates"]} == {7}
        assert {pair["n"] for pair in benchmark["paired"]} == {7}
        assert all(pair["median"] == 100 for pair in benchmark["paired"])
        assert all(pair["ci_low"] > 0 for pair in benchmark["paired"])
        assert all(pair["sign_test"]["numerator"] == 1 for pair in benchmark["paired"])
        assert all(pair["sign_test"]["denominator"] == 128 for pair in benchmark["paired"])
        assert all(pair["mcid_sign_test"]["wins"] == 7 for pair in benchmark["paired"])
        assert all(pair["mcid_sign_test"]["non_wins"] == 0 for pair in benchmark["paired"])
        assert all(pair["mcid_sign_test"]["numerator"] == 1 for pair in benchmark["paired"])
        assert all(pair["mcid_sign_test"]["denominator"] == 128 for pair in benchmark["paired"])
        assert summary["claims"][0]["status"] == "supported"
        assert summary["acceptance"] == {
            "scientific_evidence": {
                "status": "incomplete",
                "task1_6_functional_status": "not_ready",
                "task4_claim_status": "supported",
            },
            "competition_ready": False,
            "tasks": {
                "task1": "pass",
                "task2": "pass",
                "task3": "pass",
                "task4": "pass",
                "task5": "pass",
                "task6": "not_ready",
            },
            "task4_gate": {
                "benchmark_id": "file_query_path_index",
                "functional_status": "pass",
                "claim_status": "supported",
                "required_status": "supported",
            },
        }
        assert summary["methodology"]["competition_claims"] == (
            suite["competition_claims"]
        )
        assert summary["methodology"]["multiple_testing"] == {
            "family_id": "agentos-evaluation-headlines-v2",
            "method": "Bonferroni",
            "familywise_alpha": 0.05,
            "hypothesis_count": 4,
            "per_claim_alpha": 0.05 / 4,
            "headline_claims": [
                "file_query_path_index", "file_query_table_ablation",
                "tool_batch", "context_access",
            ],
            "load_gate": "intersection (every preregistered load must pass)",
        }
        assert summary["methodology"]["inference_method"].startswith(
            "exact one-sided binomial sign test"
        )
        assert summary["methodology"]["descriptive_interval"] == {
            "method": "percentile bootstrap of the boot-level median",
            "resamples": 2000,
            "role": "descriptive only; never used to support a headline claim",
        }
        assert summary["methodology"]["fwer_mcid"]["per_boot_success"] == (
            "absolute > MCID and relative > MCID"
        )
        assert summary["methodology"]["fwer_mcid"]["per_headline_alpha"] == (
            0.05 / 4
        )
        assert summary["methodology"]["interpretation_boundaries"] == {
            "microbenchmark_design": "same-kernel-paired-comparison",
            "microbenchmark_causal_scope": (
                "task-facing-path-vs-index-and-isolated-ablation-under-preregistered-workloads"
            ),
            "scenario_design": "full-stack",
            "scenario_attribution": "non-single-mechanism",
            "host_page_cache": "uncontrolled",
        }
        forged_boundaries = copy.deepcopy(summary)
        forged_boundaries["methodology"]["interpretation_boundaries"][
            "scenario_attribution"
        ] = "single-mechanism"
        try:
            validate_summary(forged_boundaries)
        except DashboardError as error:
            assert "fixed causal limits" in str(error), error
        else:
            raise AssertionError("accepted forged interpretation boundaries")
        assert "Bonferroni" in summary["claims"][0]["effect"]
        adjusted = copy.deepcopy(benchmark["paired"][0])
        adjusted["ci_low"] = -1_000_000.0
        adjusted["relative_ci_low"] = -1_000_000.0
        adjusted["sign_test"]["p_value"] = 1.0
        assert _load_supports_headline_claim(
            adjusted, benchmark["claim_gate"], [1000.0], headline_alpha
        )
        adjusted["mcid_sign_test"]["p_value"] = 0.02
        assert not _load_supports_headline_claim(
            adjusted, benchmark["claim_gate"], [1000.0], headline_alpha
        )
        adjusted["ci_low"] = 1_000_000.0
        adjusted["relative_ci_low"] = 1_000_000.0
        adjusted["mcid_sign_test"] = _joint_mcid_sign_test(
            [6.0] * 7, [6.0] * 7, benchmark["claim_gate"]
        )
        assert _load_supports_headline_claim(
            adjusted, benchmark["claim_gate"], [1000.0], headline_alpha
        )
        assert not _load_supports_headline_claim(
            adjusted, benchmark["claim_gate"], [19.0], headline_alpha
        )
        gate = benchmark["claim_gate"]
        all_joint_wins = _joint_mcid_sign_test(
            [6.0] * 7, [6.0] * 7, gate
        )
        assert all_joint_wins["wins"] == 7
        assert all_joint_wins["p_value"] == 1 / 128
        for absolute, relative in (
            ([5.0] + [6.0] * 6, [6.0] * 7),
            ([6.0] * 7, [5.0] + [6.0] * 6),
            ([6.0] * 7, [None] + [6.0] * 6),
        ):
            boundary = _joint_mcid_sign_test(absolute, relative, gate)
            assert boundary["wins"] == 6
            assert boundary["non_wins"] == 1
            assert boundary["p_value"] == 1 / 16
            forged = copy.deepcopy(adjusted)
            forged["ci_low"] = 1_000_000.0
            forged["relative_ci_low"] = 1_000_000.0
            forged["mcid_sign_test"] = boundary
            assert not _load_supports_headline_claim(
                forged, gate, [1000.0], headline_alpha
            )
        assert all(pair["relative_ci_low"] > 0 for pair in benchmark["paired"])
        first_paired_sample = benchmark["paired"][0]["samples"][0]
        assert set(first_paired_sample) == {
            "trial", "baseline_value", "treatment_value", "value",
            "relative_percent", "inner_pairs",
        }
        assert len(first_paired_sample["inner_pairs"]) == 7
        assert all(
            set(inner) == {
                "pair", "baseline_value", "treatment_value", "value",
                "relative_percent",
            }
            for inner in first_paired_sample["inner_pairs"]
        )
        assert first_paired_sample["value"] == 100
        assert first_paired_sample["baseline_value"] - first_paired_sample[
            "treatment_value"
        ] == 100
        assert len(benchmark["diagnostics"]) == 4
        assert all(len(item["samples"]) == 7 for item in benchmark["diagnostics"])
        assert all(
            item["result_cache_hits"] == {"median": 0.0, "p95": 0.0, "n": 7}
            for item in benchmark["diagnostics"]
        )
        assert all(
            set(sample) == {
                "boot_id", "cache", "operations", "dataset_size",
                "work_units", "result_items", "duration_us",
                "index_rebuild_records", "result_cache_hits",
                "workload_fingerprint", "result_fingerprint", "evidence_id",
                "source_log", "source_line", "source_log_sha256",
                "source_marker_sha256",
            }
            for item in benchmark["diagnostics"]
            for sample in item["samples"]
        )
        diagnostic_rows = [
            row for row in rows
            if row["kind"] == "agentos-evaluation-diagnostic-row"
        ]
        assert len(diagnostic_rows) == 49
        assert all(
            row["metric"] == "index_readiness_duration"
            and row["source_line"] > 0
            and row["source_marker_sha256"] != "0" * 64
            and row["dataset_size"] == row["load"]
            and row["result_items"] == 1
            and row["result_cache_hits"] == 0
            and int(row["workload_fingerprint"], 16) != 0
            and int(row["result_fingerprint"], 16) != 0
            for row in diagnostic_rows
        )
        assert all(
            sample["index_rebuild_records"] == 0
            and sample["result_cache_hits"] == 0
            and set(sample) == {
                "target_id", "load", "value", "trial", "order", "boot_id",
                "evidence_id", "operations", "dataset_size", "work_units",
                "records_examined", "result_items", "index_rebuild_records",
                "result_cache_hits",
            }
            for measured in summary["benchmarks"]
            for sample in measured["samples"]
        )
        contest_samples = [
            sample for sample in benchmark["samples"]
            if sample["load"] == 24
        ]
        assert all(
            sample["operations"] == 6
            and sample["dataset_size"] == 24
            and sample["result_items"] == 6
            for sample in contest_samples
        )
        assert all(
            sample["work_units"] == 24 * 6
            and sample["records_examined"] == 24 * 6
            for sample in contest_samples
            if sample["target_id"].endswith(":path_walk")
        )
        first_file_pair = {
            row["role"]: row
            for row in rows
            if row["kind"] == "agentos-evaluation-metric-row"
            and row["boot_id"] == "boot-01"
            and row["experiment"] == "file_query_path_index"
            and row["load"] == 24
            and row["inner_pair"] == 1
        }
        assert first_file_pair["baseline"]["work_units"] == 24 * 6
        assert first_file_pair["treatment"]["work_units"] == 6
        assert first_file_pair["baseline"]["records_examined"] == 24 * 6
        assert first_file_pair["treatment"]["records_examined"] == 6
        assert all(
            0 < row["records_examined"] <= row["work_units"]
            for row in first_file_pair.values()
        )
        pair_one_orders = {
            row["boot_id"]: row["order"]
            for row in rows
            if row["kind"] == "agentos-evaluation-metric-row"
            and row["experiment"] == "file_query_path_index"
            and row["load"] == 24
            and row["inner_pair"] == 1
            and row["role"] == "baseline"
        }
        assert pair_one_orders["boot-01"] == "AB"
        assert pair_one_orders["boot-02"] == "BA"
        evidence_paths = {item["id"]: item["path"] for item in summary["evidence"]}
        assert evidence_paths["run-plan"] == "run-plan.json"
        assert all(
            evidence_paths[f"raw-boot-{index:03d}"]
            == f"raw/boot-{index:02d}/guest.log"
            for index in range(1, 8)
        )

        missing_receipt_root = root / "missing-functional-receipt"
        missing_receipt_root.mkdir()
        missing_receipt_plan = write_campaign(missing_receipt_root, suite)
        missing_receipt_log = missing_receipt_root / "boot-01/guest.log"
        missing_receipt_log.write_text(
            "\n".join(
                line for line in missing_receipt_log.read_text(
                    encoding="utf-8"
                ).splitlines()
                if not line.startswith(
                    "agenteval_ucore: functional schema=1 task=task3 "
                )
            ) + "\n",
            encoding="utf-8",
        )
        refresh_plan_hash(missing_receipt_plan, missing_receipt_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, missing_receipt_plan, missing_receipt_root
            ),
            "exactly one receipt for every Task1-5",
        )

        reordered_receipt_root = root / "reordered-functional-receipt"
        reordered_receipt_root.mkdir()
        reordered_receipt_plan = write_campaign(reordered_receipt_root, suite)
        reordered_receipt_log = reordered_receipt_root / "boot-01/guest.log"
        reordered_lines = reordered_receipt_log.read_text(
            encoding="utf-8"
        ).splitlines()
        task2_position = next(
            index for index, line in enumerate(reordered_lines)
            if line.startswith("agenteval_ucore: functional schema=1 task=task2 ")
        )
        task3_position = next(
            index for index, line in enumerate(reordered_lines)
            if line.startswith("agenteval_ucore: functional schema=1 task=task3 ")
        )
        reordered_lines[task2_position], reordered_lines[task3_position] = (
            reordered_lines[task3_position], reordered_lines[task2_position]
        )
        reordered_receipt_log.write_text(
            "\n".join(reordered_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(reordered_receipt_plan, reordered_receipt_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, reordered_receipt_plan, reordered_receipt_root
            ),
            "missing or out of order",
        )

        duplicate_catalog_root = root / "duplicate-tool-catalog"
        duplicate_catalog_root.mkdir()
        duplicate_catalog_plan = write_campaign(duplicate_catalog_root, suite)
        duplicate_catalog_log = duplicate_catalog_root / "boot-01/guest.log"
        duplicate_catalog_log.write_text(
            duplicate_catalog_log.read_text(encoding="utf-8").replace(
                "tool_id=99 flags=2 param_count=0 name=future_probe",
                "tool_id=13 flags=2 param_count=0 name=future_probe",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(duplicate_catalog_plan, duplicate_catalog_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, duplicate_catalog_plan, duplicate_catalog_root
            ),
            "duplicate tool identity",
        )

        bad_catalog_schema_root = root / "bad-tool-catalog-schema"
        bad_catalog_schema_root.mkdir()
        bad_catalog_schema_plan = write_campaign(bad_catalog_schema_root, suite)
        bad_catalog_schema_log = bad_catalog_schema_root / "boot-01/guest.log"
        bad_catalog_schema_log.write_text(
            bad_catalog_schema_log.read_text(encoding="utf-8").replace(
                "param_count=0 name=future_probe params=none",
                "param_count=1 name=future_probe params=broken",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(bad_catalog_schema_plan, bad_catalog_schema_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, bad_catalog_schema_plan, bad_catalog_schema_root
            ),
            "parameter schema is invalid",
        )

        distorted_error_root = root / "distorted-tool-error"
        distorted_error_root.mkdir()
        distorted_error_plan = write_campaign(distorted_error_root, suite)
        distorted_error_log = distorted_error_root / "boot-01/guest.log"
        distorted_lines = distorted_error_log.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(distorted_lines):
            if line.startswith("agenteval_ucore: functional schema=1 task=task2 "):
                challenge = re.search(r" challenge=([0-9a-f]{16}) ", line).group(1)
                values = [
                    int(value) for value in
                    re.search(r" values=([^ ]+) ", line).group(1).split(",")
                ]
                values[22] = -15
                distorted_lines[index] = _format_functional_receipt(
                    "task2", challenge, values,
                    _functional_semantic("task2-semantic-v1", challenge, values),
                )
                break
        distorted_error_log.write_text(
            "\n".join(distorted_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(distorted_error_plan, distorted_error_root)
        expect_rejected(
            lambda: build(SUITE_PATH, distorted_error_plan, distorted_error_root),
            "Task2 structured-tool receipt is inconsistent",
        )

        forged_rollback_root = root / "forged-task3-rollback"
        forged_rollback_root.mkdir()
        forged_rollback_plan = write_campaign(forged_rollback_root, suite)
        forged_rollback_log = forged_rollback_root / "boot-01/guest.log"
        forged_rollback_lines = forged_rollback_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(forged_rollback_lines):
            if line.startswith("agenteval_ucore: functional schema=1 task=task3 "):
                challenge = re.search(r" challenge=([0-9a-f]{16}) ", line).group(1)
                values = [
                    int(value) for value in
                    re.search(r" values=([^ ]+) ", line).group(1).split(",")
                ]
                values[11] = 6
                forged_rollback_lines[index] = _format_functional_receipt(
                    "task3", challenge, values, _task3_semantic(challenge, values)
                )
                break
        forged_rollback_log.write_text(
            "\n".join(forged_rollback_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(forged_rollback_plan, forged_rollback_root)
        expect_rejected(
            lambda: build(SUITE_PATH, forged_rollback_plan, forged_rollback_root),
            "Task3 Context Path receipt is inconsistent",
        )

        forged_receipt_root = root / "forged-functional-receipt"
        forged_receipt_root.mkdir()
        forged_receipt_plan = write_campaign(forged_receipt_root, suite)
        forged_receipt_log = forged_receipt_root / "boot-01/guest.log"
        forged_lines = forged_receipt_log.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(forged_lines):
            if line.startswith("agenteval_ucore: functional schema=1 task=task3 "):
                value_text = re.search(r" values=([^ ]+) ", line).group(1)
                values = [int(value) for value in value_text.split(",")]
                values[17] += 1
                forged_lines[index] = line.replace(
                    f"values={value_text}",
                    "values=" + ",".join(str(value) for value in values),
                )
                break
        forged_receipt_log.write_text(
            "\n".join(forged_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(forged_receipt_plan, forged_receipt_root)
        expect_rejected(
            lambda: build(SUITE_PATH, forged_receipt_plan, forged_receipt_root),
            "functional receipt hash differs",
        )

        forged_semantic_root = root / "forged-functional-semantic"
        forged_semantic_root.mkdir()
        forged_semantic_plan = write_campaign(forged_semantic_root, suite)
        forged_semantic_log = forged_semantic_root / "boot-01/guest.log"
        forged_semantic_lines = forged_semantic_log.read_text(
            encoding="utf-8"
        ).splitlines()
        challenge = "0000000000000001"
        equal_tick_values = [
            200, 300, 300, 200,
            _semantic_token("task5-event-v1", 2, 0, 0, challenge),
            6000, 7000, 10, 11, 20, 21, 7100, 7100, 2, 2, -7, 0, 2, 1,
            6990, 7002, 40, 41, 800, 820, 5, 7, 50,
        ]
        equal_tick_line = _format_functional_receipt(
            "task5", challenge, equal_tick_values,
            _functional_semantic(
                "task5-semantic-v2", challenge, equal_tick_values
            ),
        )
        forged_semantic_lines = [
            equal_tick_line
            if line.startswith(
                "agenteval_ucore: functional schema=1 task=task5 "
            )
            else line
            for line in forged_semantic_lines
        ]
        forged_semantic_log.write_text(
            "\n".join(forged_semantic_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(forged_semantic_plan, forged_semantic_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, forged_semantic_plan, forged_semantic_root
            ),
            "Task5 blocking wait and scheduler-accounting receipt is inconsistent",
        )

        busy_wait_root = root / "forged-task5-busy-wait"
        busy_wait_root.mkdir()
        busy_wait_plan = write_campaign(busy_wait_root, suite)
        busy_wait_log = busy_wait_root / "boot-01/guest.log"
        busy_wait_lines = busy_wait_log.read_text(encoding="utf-8").splitlines()
        busy_wait_lines = rewrite_task5_receipt(
            busy_wait_lines,
            challenge,
            lambda values: values.__setitem__(22, values[21] + 5),
        )
        busy_wait_log.write_text(
            "\n".join(busy_wait_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(busy_wait_plan, busy_wait_root)
        expect_rejected(
            lambda: build(SUITE_PATH, busy_wait_plan, busy_wait_root),
            "Task5 blocking wait and scheduler-accounting receipt is inconsistent",
        )

        task4_order_root = root / "forged-task4-order"
        task4_order_root.mkdir()
        task4_order_plan = write_campaign(task4_order_root, suite)
        task4_order_log = task4_order_root / "boot-01/guest.log"
        task4_order_lines = task4_order_log.read_text(
            encoding="utf-8"
        ).splitlines()
        task4_order_lines = rewrite_task4_receipt(
            task4_order_lines,
            challenge,
            lambda values: values.__setitem__(9, values[10]),
        )
        task4_order_log.write_text(
            "\n".join(task4_order_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(task4_order_plan, task4_order_root)
        expect_rejected(
            lambda: build(SUITE_PATH, task4_order_plan, task4_order_root),
            "Task4 attribute/content lifecycle receipt is inconsistent",
        )

        task4_semantic_root = root / "forged-task4-query-semantic"
        task4_semantic_root.mkdir()
        task4_semantic_plan = write_campaign(task4_semantic_root, suite)
        task4_semantic_log = task4_semantic_root / "boot-01/guest.log"
        task4_semantic_lines = task4_semantic_log.read_text(
            encoding="utf-8"
        ).splitlines()
        task4_semantic_lines = rewrite_task4_receipt(
            task4_semantic_lines,
            challenge,
            lambda values: values.__setitem__(22, values[22] ^ 1),
        )
        task4_semantic_log.write_text(
            "\n".join(task4_semantic_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(task4_semantic_plan, task4_semantic_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, task4_semantic_plan, task4_semantic_root
            ),
            "Task4 attribute/content lifecycle receipt is inconsistent",
        )

        failed_receipt_root = root / "failed-functional-receipt"
        failed_receipt_root.mkdir()
        failed_receipt_plan = write_campaign(failed_receipt_root, suite)
        failed_receipt_log = failed_receipt_root / "boot-01/guest.log"
        failed_receipt_log.write_text(
            failed_receipt_log.read_text(encoding="utf-8").replace(
                "status=passed\nagenteval_ucore: functional schema=1 task=task3 ",
                "status=failed\nagenteval_ucore: functional schema=1 task=task3 ",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(failed_receipt_plan, failed_receipt_root)
        expect_rejected(
            lambda: build(
                SUITE_PATH, failed_receipt_plan, failed_receipt_root
            ),
            "functional receipt status differs",
        )

        partial_boot_root = root / "partial-functional-boots"
        partial_boot_root.mkdir()
        partial_boot_plan = write_campaign(
            partial_boot_root, suite, count=8, unavailable_last=True
        )
        partial_summary, _ = build(
            SUITE_PATH, partial_boot_plan, partial_boot_root
        )
        assert partial_summary["run"]["status"] == "unavailable"
        assert all(
            item["functional_status"] == "unavailable"
            for item in partial_summary["scenarios"]
            if item["task"] != "task6"
        )

        scenario_samples = []
        required_modules = [
            "context", "structured_tool", "metadata_query", "observation"
        ]
        for index in range(1, 8):
            boot_id = f"boot-{index:02d}"
            challenge = f"ch-{index:012d}"
            outcome = {"status": "completed", "challenge": challenge}
            outcome_fingerprint = _binding_sha256(
                outcome, "research-platform-outcome-v2"
            )
            plain_raw = {
                "functional_acceptance": {
                    "required": False,
                    "status": "not_applicable",
                },
                "resource_stability": {
                    "required": False,
                    "status": "not_applicable",
                },
            }
            plain_raw["sha256"] = _binding_sha256(
                plain_raw, "scenario-raw-source-receipt-v1"
            )
            module_receipt_sha = hashlib.sha256(
                f"agentos-modules-{index}".encode("ascii")
            ).hexdigest()
            challenge_source_sha = hashlib.sha256(
                f"challenge-source-{index}".encode("ascii")
            ).hexdigest()
            run_summary_sha = hashlib.sha256(
                f"run-summary-{index}".encode("ascii")
            ).hexdigest()
            state_inventory_sha = hashlib.sha256(
                f"state-inventory-{index}".encode("ascii")
            ).hexdigest()
            acceptance_binding = {
                "schema": "agentos-task6-functional-binding-v1",
                "challenge": challenge,
                "challenge_source_sha256": challenge_source_sha,
                "run_summary_sha256": run_summary_sha,
                "state_inventory_sha256": state_inventory_sha,
                "module_receipt_sha256": module_receipt_sha,
                "required_modules": required_modules,
            }
            acceptance_binding["sha256"] = _binding_sha256(
                acceptance_binding, "agentos-task6-functional-binding-v1"
            )
            acceptance_receipt = {
                "required": True,
                "status": "verified",
                "path": "state-extracted/rp_agentos_acceptance",
                "bytes": 128,
                "sha256": module_receipt_sha,
                "acceptance": _scenario_agentos_acceptance(challenge, index),
                "binding": acceptance_binding,
            }
            resource_receipt_sha = hashlib.sha256(
                f"agentos-resource-stability-{index}".encode("ascii")
            ).hexdigest()
            resource_binding = {
                "schema": "agentos-task6-resource-stability-binding-v1",
                "challenge": challenge,
                "challenge_source_sha256": challenge_source_sha,
                "run_summary_sha256": run_summary_sha,
                "state_inventory_sha256": state_inventory_sha,
                "resource_receipt_sha256": resource_receipt_sha,
                "measurement_scope": RESOURCE_STABILITY_MEASUREMENT_SCOPE,
            }
            resource_binding["sha256"] = _binding_sha256(
                resource_binding,
                "agentos-task6-resource-stability-binding-v1",
            )
            resource_receipt = {
                "required": True,
                "status": "verified",
                "path": "state-extracted/rp_resource_stability",
                "bytes": 256,
                "sha256": resource_receipt_sha,
                "acceptance": _scenario_resource_stability(challenge, index),
                "binding": resource_binding,
            }
            agentos_raw = {
                "challenge_source": {"sha256": challenge_source_sha},
                "run_summary": {"sha256": run_summary_sha},
                "state_inventory": {
                    "sha256": state_inventory_sha,
                    "files": [
                        {
                            "path": "rp_agentos_acceptance",
                            "bytes": 128,
                            "sha256": module_receipt_sha,
                        },
                        {
                            "path": "rp_resource_stability",
                            "bytes": 256,
                            "sha256": resource_receipt_sha,
                        },
                    ],
                },
                "functional_acceptance": acceptance_receipt,
                "resource_stability": resource_receipt,
            }
            agentos_raw["sha256"] = _binding_sha256(
                agentos_raw, "scenario-raw-source-receipt-v1"
            )
            binding = {
                "source_commit": COMMIT,
                "run_id": "contract-test",
                "boot_id": boot_id,
                "boot_order": index,
                "target_order": "AB" if index % 2 else "BA",
                "challenge": challenge,
                "program_order": ["rp_runner"],
                "outcome_fingerprint": outcome_fingerprint,
                "source_receipts": {
                    "plain": plain_raw["sha256"],
                    "agentos": agentos_raw["sha256"],
                },
            }
            binding["sha256"] = _binding_sha256(binding, "scenario-sample-v1")
            scenario_samples.append({
                "sample_id": f"contract-test:{boot_id}",
                "binding": binding,
                "outcome": outcome,
                "outcome_fingerprint": outcome_fingerprint,
                "targets": {
                    "plain": {
                        "makespan_ms": 100 + index,
                        "programs": [{"program": "rp_runner", "elapsed_ms": 100 + index}],
                        "raw_source_receipt": plain_raw,
                    },
                    "agentos": {
                        "makespan_ms": 80 + index,
                        "programs": [{"program": "rp_runner", "elapsed_ms": 80 + index}],
                        "raw_source_receipt": agentos_raw,
                    },
                },
            })
        scenario = {
            "schema_version": 2,
            "scenario_id": "research-platform-seeded",
            "source_commit": COMMIT,
            "run_id": "contract-test",
            "status": "supported",
            "samples": scenario_samples,
            "summary": summarize_scenario(scenario_samples),
        }
        scenario["report_sha256"] = _binding_sha256(scenario, "scenario-report-v2")
        scenario_dir = root / "scenario"
        scenario_dir.mkdir()
        scenario_path = scenario_dir / "report.json"
        scenario_path.write_text(json.dumps(scenario) + "\n", encoding="utf-8")
        expect_rejected(
            lambda: build(SUITE_PATH, plan_path, root, scenario_path),
            "supplied together",
        )
        scenario_plan_value = {
            "phase": "collected",
            "report": {
                "status": "recorded",
                "sha256": __import__("hashlib").sha256(scenario_path.read_bytes()).hexdigest(),
            },
            "run": {
                "id": "contract-test",
                "commit": COMMIT,
                "environment_sha256": ENVIRONMENT,
            },
            "boots": [
                {
                    "boot_id": f"boot-{index:02d}",
                    "status": "passed",
                    "challenge": f"ch-{index:012d}",
                    "target_order": "plain-agentos" if index % 2 else "agentos-plain",
                }
                for index in range(1, 8)
            ],
        }
        scenario_plan_path = scenario_dir / "scenario-plan.json"
        scenario_plan_path.write_text(json.dumps(scenario_plan_value), encoding="utf-8")
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            scenario_summary, _ = build(
                SUITE_PATH, plan_path, root, scenario_path, scenario_plan_path
            )
        validate_summary(scenario_summary)
        task6 = next(item for item in scenario_summary["scenarios"] if item["task"] == "task6")
        assert set(task6) == {
            "id", "label", "task", "functional_status", "performance_status",
            "performance", "evidence_ids",
        }
        assert task6["functional_status"] == "pass"
        assert task6["performance_status"] == "supported"
        assert task6["performance"] == scenario["summary"]["paired_improvement"]
        assert task6["evidence_ids"] == [
            "research-scenario-plan", "research-scenario-report"
        ]
        scenario_evidence_paths = {
            item["id"]: item["path"] for item in scenario_summary["evidence"]
        }
        assert scenario_evidence_paths["research-scenario-report"] == "scenario/report.json"
        assert scenario_evidence_paths["research-scenario-plan"] == "scenario/scenario-plan.json"

        broadened_resource_claim = copy.deepcopy(scenario)
        broadened_resource_claim["summary"]["resource_stability"][
            "global_observation"
        ]["account_counters"] = "measured"
        broadened_resource_claim.pop("report_sha256")
        broadened_resource_claim["report_sha256"] = _binding_sha256(
            broadened_resource_claim, "scenario-report-v2"
        )
        broadened_resource_path = root / "broadened-resource-claim.json"
        broadened_resource_path.write_text(
            json.dumps(broadened_resource_claim) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(
                broadened_resource_path, load_run_plan(plan_path)[0]
            ),
            "summary differs from bound samples",
        )

        forged_terminal_growth = copy.deepcopy(scenario)
        fs_block_observation = next(
            resource
            for resource in forged_terminal_growth["summary"][
                "resource_stability"
            ]["global_observation"]["resources"]
            if resource["kind"] == "fs_block"
        )
        fs_block_observation["terminal_observed_growth"] = (
            fs_block_observation["terminal_growth_bound"] + 1
        )
        forged_terminal_growth.pop("report_sha256")
        forged_terminal_growth["report_sha256"] = _binding_sha256(
            forged_terminal_growth, "scenario-report-v2"
        )
        forged_terminal_path = root / "forged-terminal-growth.json"
        forged_terminal_path.write_text(
            json.dumps(forged_terminal_growth) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(
                forged_terminal_path, load_run_plan(plan_path)[0]
            ),
            "summary differs from bound samples",
        )

        failed_boot_plan = copy.deepcopy(scenario_plan_value)
        failed_boot_plan["boots"][0]["status"] = "failed"
        failed_boot_plan_path = root / "failed-boot-scenario-plan.json"
        failed_boot_plan_path.write_text(
            json.dumps(failed_boot_plan), encoding="utf-8"
        )
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            expect_rejected(
                lambda: build(
                    SUITE_PATH,
                    plan_path,
                    root,
                    scenario_path,
                    failed_boot_plan_path,
                ),
                "differs from its sealed boot plan",
            )

        inconclusive_scenario = copy.deepcopy(scenario)
        for sample in inconclusive_scenario["samples"]:
            plain_ms = sample["targets"]["plain"]["makespan_ms"]
            sample["targets"]["agentos"]["makespan_ms"] = plain_ms
            sample["targets"]["agentos"]["programs"][0]["elapsed_ms"] = plain_ms
        inconclusive_scenario["summary"] = summarize_scenario(
            inconclusive_scenario["samples"]
        )
        inconclusive_scenario["status"] = "inconclusive"
        inconclusive_scenario.pop("report_sha256")
        inconclusive_scenario["report_sha256"] = _binding_sha256(
            inconclusive_scenario, "scenario-report-v2"
        )
        inconclusive_path = root / "inconclusive-scenario.json"
        inconclusive_path.write_text(
            json.dumps(inconclusive_scenario) + "\n", encoding="utf-8"
        )
        inconclusive_plan = copy.deepcopy(scenario_plan_value)
        inconclusive_plan["report"]["sha256"] = hashlib.sha256(
            inconclusive_path.read_bytes()
        ).hexdigest()
        inconclusive_plan_path = root / "inconclusive-scenario-plan.json"
        inconclusive_plan_path.write_text(
            json.dumps(inconclusive_plan), encoding="utf-8"
        )
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            inconclusive_summary, _ = build(
                SUITE_PATH,
                plan_path,
                root,
                inconclusive_path,
                inconclusive_plan_path,
            )
        validate_summary(inconclusive_summary)
        inconclusive_task6 = next(
            item for item in inconclusive_summary["scenarios"]
            if item["task"] == "task6"
        )
        assert inconclusive_task6["functional_status"] == "pass"
        assert inconclusive_task6["performance_status"] == "inconclusive"
        assert inconclusive_task6["performance"] == inconclusive_scenario[
            "summary"
        ]["paired_improvement"]

        regressed_scenario = copy.deepcopy(scenario)
        for sample in regressed_scenario["samples"]:
            plain_ms = sample["targets"]["plain"]["makespan_ms"]
            sample["targets"]["agentos"]["makespan_ms"] = plain_ms + 20
            sample["targets"]["agentos"]["programs"][0]["elapsed_ms"] = plain_ms + 20
        regressed_scenario["summary"] = summarize_scenario(
            regressed_scenario["samples"]
        )
        regressed_scenario["status"] = "regressed"
        regressed_scenario.pop("report_sha256")
        regressed_scenario["report_sha256"] = _binding_sha256(
            regressed_scenario, "scenario-report-v2"
        )
        regressed_path = root / "regressed-scenario.json"
        regressed_path.write_text(
            json.dumps(regressed_scenario) + "\n", encoding="utf-8"
        )
        regressed_plan = copy.deepcopy(scenario_plan_value)
        regressed_plan["report"]["sha256"] = hashlib.sha256(
            regressed_path.read_bytes()
        ).hexdigest()
        regressed_plan_path = root / "regressed-scenario-plan.json"
        regressed_plan_path.write_text(
            json.dumps(regressed_plan), encoding="utf-8"
        )
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            regressed_summary, _ = build(
                SUITE_PATH,
                plan_path,
                root,
                regressed_path,
                regressed_plan_path,
            )
        regressed_task6 = next(
            item for item in regressed_summary["scenarios"]
            if item["task"] == "task6"
        )
        assert regressed_task6["functional_status"] == "pass"
        assert regressed_task6["performance_status"] == "regressed"
        assert regressed_task6["performance"]["sign_test"]["losses"] == 7
        assert regressed_task6["performance"]["regression_mcid_sign_test"][
            "losses"
        ] == 7
        assert regressed_summary["acceptance"]["scientific_evidence"][
            "status"
        ] == "publishable"
        assert not regressed_summary["acceptance"]["competition_ready"]
        assert regressed_summary["acceptance"]["tasks"]["task6"] == "not_ready"

        forged_regression_status = copy.deepcopy(regressed_scenario)
        forged_regression_status["status"] = "inconclusive"
        forged_regression_status.pop("report_sha256")
        forged_regression_status["report_sha256"] = _binding_sha256(
            forged_regression_status, "scenario-report-v2"
        )
        forged_regression_path = root / "forged-regression-status.json"
        forged_regression_path.write_text(
            json.dumps(forged_regression_status) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(
                forged_regression_path, load_run_plan(plan_path)[0]
            ),
            "status differs from paired performance gate",
        )

        forged_reverse_count = copy.deepcopy(regressed_scenario)
        forged_reverse_count["summary"]["paired_improvement"][
            "regression_mcid_sign_test"
        ]["losses"] = 6
        forged_reverse_count.pop("report_sha256")
        forged_reverse_count["report_sha256"] = _binding_sha256(
            forged_reverse_count, "scenario-report-v2"
        )
        forged_reverse_path = root / "forged-reverse-count.json"
        forged_reverse_path.write_text(
            json.dumps(forged_reverse_count) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(
                forged_reverse_path, load_run_plan(plan_path)[0]
            ),
            "summary differs from bound samples",
        )

        scenario_summary_path = root / "scenario-summary.json"
        scenario_rows_path = root / "scenario-metrics.jsonl"
        write_json(scenario_summary_path, inconclusive_summary)
        write_jsonl(scenario_rows_path, rows)
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            assert verify(
                SUITE_PATH,
                plan_path,
                root,
                scenario_summary_path,
                scenario_rows_path,
                inconclusive_path,
                inconclusive_plan_path,
            ) == inconclusive_summary
        forged_contract_summary = copy.deepcopy(inconclusive_summary)
        forged_task6 = next(
            item for item in forged_contract_summary["scenarios"]
            if item["task"] == "task6"
        )
        forged_task6["functional_status"] = "partial"
        forged_task6["performance_status"] = "supported"
        write_json(scenario_summary_path, forged_contract_summary)
        with mock.patch("evaluation_campaign.validate_scenario_campaign"):
            expect_rejected(
                lambda: verify(
                    SUITE_PATH,
                    plan_path,
                    root,
                    scenario_summary_path,
                    scenario_rows_path,
                    inconclusive_path,
                    inconclusive_plan_path,
                ),
                "summary differs",
            )

        insufficient_scenario = copy.deepcopy(inconclusive_scenario)
        insufficient_scenario["samples"] = insufficient_scenario["samples"][:6]
        insufficient_scenario["summary"] = summarize_scenario(
            insufficient_scenario["samples"]
        )
        insufficient_scenario.pop("report_sha256")
        insufficient_scenario["report_sha256"] = _binding_sha256(
            insufficient_scenario, "scenario-report-v2"
        )
        insufficient_path = root / "insufficient-scenario.json"
        insufficient_path.write_text(
            json.dumps(insufficient_scenario) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(insufficient_path, load_run_plan(plan_path)[0]),
            "functional acceptance is incomplete",
        )

        reordered_scenario = copy.deepcopy(inconclusive_scenario)
        reordered_scenario["samples"][0], reordered_scenario["samples"][1] = (
            reordered_scenario["samples"][1], reordered_scenario["samples"][0]
        )
        reordered_scenario["summary"] = summarize_scenario(
            reordered_scenario["samples"]
        )
        reordered_scenario.pop("report_sha256")
        reordered_scenario["report_sha256"] = _binding_sha256(
            reordered_scenario, "scenario-report-v2"
        )
        reordered_path = root / "reordered-scenario.json"
        reordered_path.write_text(
            json.dumps(reordered_scenario) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(reordered_path, load_run_plan(plan_path)[0]),
            "not in sealed boot order",
        )

        forged_output = copy.deepcopy(scenario)
        forged_output["samples"][0]["outcome"]["status"] = "forged"
        forged_output.pop("report_sha256")
        forged_output["report_sha256"] = _binding_sha256(
            forged_output, "scenario-report-v2"
        )
        forged_output_path = root / "forged-output-scenario.json"
        forged_output_path.write_text(
            json.dumps(forged_output) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(forged_output_path, load_run_plan(plan_path)[0]),
            "normalized output differs from its fingerprint",
        )

        forged_receipt = copy.deepcopy(scenario)
        forged_receipt["samples"][0]["targets"]["plain"][
            "raw_source_receipt"
        ]["sha256"] = "3" * 64
        forged_receipt.pop("report_sha256")
        forged_receipt["report_sha256"] = _binding_sha256(
            forged_receipt, "scenario-report-v2"
        )
        forged_receipt_path = root / "forged-receipt-scenario.json"
        forged_receipt_path.write_text(
            json.dumps(forged_receipt) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: load_scenario_report(forged_receipt_path, load_run_plan(plan_path)[0]),
            "differs from its bound source receipt",
        )

        forged_scenario = copy.deepcopy(scenario)
        for sample in forged_scenario["samples"]:
            plain_ms = sample["targets"]["plain"]["makespan_ms"]
            sample["targets"]["agentos"]["makespan_ms"] = plain_ms + 10
            sample["targets"]["agentos"]["programs"][0]["elapsed_ms"] = plain_ms + 10
        forged_scenario["summary"] = summarize_scenario(forged_scenario["samples"])
        forged_scenario["status"] = "supported"
        forged_scenario.pop("report_sha256")
        forged_scenario["report_sha256"] = _binding_sha256(
            forged_scenario, "scenario-report-v2"
        )
        forged_scenario_path = root / "forged-scenario.json"
        forged_scenario_path.write_text(
            json.dumps(forged_scenario) + "\n", encoding="utf-8"
        )
        micro_plan, _ = load_run_plan(plan_path)
        expect_rejected(
            lambda: load_scenario_report(forged_scenario_path, micro_plan),
            "status differs from paired performance gate",
        )

        summary_path = root / "summary.json"
        rows_path = root / "metrics.jsonl"
        write_json(summary_path, summary)

        rebound_rows = copy.deepcopy(rows)
        rebound_rows[0]["environment_sha256"] = "f" * 64
        write_jsonl(rows_path, rebound_rows)
        expect_rejected(
            lambda: verify(SUITE_PATH, plan_path, root, summary_path, rows_path),
            "metrics JSONL differs",
        )
        write_jsonl(rows_path, rows)
        assert verify(SUITE_PATH, plan_path, root, summary_path, rows_path) == summary

        rebound = copy.deepcopy(summary)
        rebound["run"]["id"] = "rebound"
        write_json(summary_path, rebound)
        expect_rejected(
            lambda: verify(SUITE_PATH, plan_path, root, summary_path, rows_path),
            "summary differs",
        )
        write_json(summary_path, summary)

        extra_suite = copy.deepcopy(suite)
        extra_suite["unexpected"] = True
        extra_suite_path = root / "extra-suite.json"
        extra_suite_path.write_text(json.dumps(extra_suite), encoding="utf-8")
        expect_rejected(lambda: load_suite(extra_suite_path), "extra=['unexpected']")

        for name, mutate, message in (
            (
                "missing-scheduled-workload",
                lambda value: value["execution_schedule"].pop(),
                "cover every configured workload exactly once",
            ),
            (
                "duplicate-scheduled-workload",
                lambda value: value["execution_schedule"].__setitem__(
                    -1, copy.deepcopy(value["execution_schedule"][0])
                ),
                "cover every configured workload exactly once",
            ),
            (
                "unknown-scheduled-load",
                lambda value: value["execution_schedule"][0].__setitem__(
                    "load", 7
                ),
                "references an unknown workload",
            ),
            (
                "reordered-guest-dispatch",
                lambda value: value["execution_schedule"].__setitem__(
                    slice(0, 2),
                    list(reversed(value["execution_schedule"][:2])),
                ),
                "differs from the registered Guest dispatcher",
            ),
        ):
            forged = copy.deepcopy(suite)
            mutate(forged)
            forged_path = root / f"{name}.json"
            forged_path.write_text(json.dumps(forged), encoding="utf-8")
            expect_rejected(lambda path=forged_path: load_suite(path), message)

        weakened_family = copy.deepcopy(suite)
        weakened_family["claim_family"]["method"] = "none"
        weakened_family_path = root / "weakened-family-suite.json"
        weakened_family_path.write_text(
            json.dumps(weakened_family), encoding="utf-8"
        )
        expect_rejected(
            lambda: load_suite(weakened_family_path),
            "headline claim family is invalid",
        )

        for name, experiment_index, mutate in (
            (
                "wrong-contest-loads",
                0,
                lambda experiment: experiment.__setitem__(
                    "loads", [8, 24, 48, 95]
                ),
            ),
            (
                "wrong-contest-operations",
                0,
                lambda experiment: experiment.__setitem__(
                    "operation_counts", [8, 6, 4, 3]
                ),
            ),
            (
                "relabelled-contest-baseline",
                0,
                lambda experiment: experiment["baseline"].__setitem__(
                    "id", "scan"
                ),
            ),
            (
                "weakened-ablation-cache",
                1,
                lambda experiment: experiment["baseline"].__setitem__(
                    "cache", "warm"
                ),
            ),
        ):
            forged = copy.deepcopy(suite)
            mutate(forged["experiments"][experiment_index])
            forged_path = root / f"{name}.json"
            forged_path.write_text(json.dumps(forged), encoding="utf-8")
            expect_rejected(
                lambda path=forged_path: load_suite(path),
                "differs from its registered workload contract",
            )

        for name, mutate, message in (
            (
                "missing-task4-claim",
                lambda value: value["competition_claims"].clear(),
                "competition claims",
            ),
            (
                "dangling-task4-claim",
                lambda value: value["competition_claims"]["task4"].__setitem__(
                    "benchmark_id", "missing_benchmark"
                ),
                "Task 4 competition claim is invalid",
            ),
            (
                "wrong-task4-claim",
                lambda value: value["competition_claims"]["task4"].__setitem__(
                    "benchmark_id", "tool_batch"
                ),
                "Task 4 competition claim is invalid",
            ),
            (
                "weakened-task4-status",
                lambda value: value["competition_claims"]["task4"].__setitem__(
                    "required_status", "not_supported"
                ),
                "Task 4 competition claim is invalid",
            ),
        ):
            forged = copy.deepcopy(suite)
            mutate(forged)
            forged_path = root / f"{name}.json"
            forged_path.write_text(json.dumps(forged), encoding="utf-8")
            expect_rejected(lambda path=forged_path: load_suite(path), message)

        rebound_suite = copy.deepcopy(suite)
        rebound_suite["experiments"][0]["direction"] = "higher_is_better"
        rebound_suite_path = root / "rebound-suite.json"
        rebound_suite_path.write_text(json.dumps(rebound_suite), encoding="utf-8")
        expect_rejected(
            lambda: build(rebound_suite_path, plan_path, root),
            "differs from its registered workload contract",
        )

        duplicate_image_plan = root / "duplicate-image-plan.json"
        duplicate_image = json.loads(plan_path.read_text(encoding="utf-8"))
        duplicate_image["logs"][1]["image_input_sha256"] = (
            duplicate_image["logs"][0]["image_input_sha256"]
        )
        duplicate_image_plan.write_text(json.dumps(duplicate_image), encoding="utf-8")
        expect_rejected(
            lambda: build(SUITE_PATH, duplicate_image_plan, root),
            "challenge-specialized pristine image",
        )

        divergent_command_plan = root / "divergent-command-plan.json"
        divergent_command = json.loads(plan_path.read_text(encoding="utf-8"))
        divergent_command["logs"][1]["command_argv"].insert(-2, "EXTRA_BUILD_MODE=1")
        command_raw = json.dumps(
            divergent_command["logs"][1]["command_argv"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        divergent_command["logs"][1]["command_sha256"] = hashlib.sha256(command_raw).hexdigest()
        divergent_command_plan.write_text(json.dumps(divergent_command), encoding="utf-8")
        expect_rejected(
            lambda: build(SUITE_PATH, divergent_command_plan, root),
            "differ beyond planned challenge/log paths",
        )

        nan_plan = root / "nan-plan.json"
        nan_plan.write_text(plan_path.read_text(encoding="utf-8").replace('"schema_version": 2', '"schema_version": NaN'), encoding="utf-8")
        expect_rejected(lambda: build(SUITE_PATH, nan_plan, root), "non-finite")

        one_boot = root / "one-boot"
        one_boot.mkdir()
        one_plan = write_campaign(one_boot, suite, count=1)
        expect_rejected(lambda: build(SUITE_PATH, one_plan, one_boot), "fewer than seven")

        missing = root / "missing"
        missing.mkdir()
        missing_plan = write_campaign(missing, suite)
        first_log = missing / "boot-01/guest.log"
        lines = first_log.read_text(encoding="utf-8").splitlines()
        lines.pop(next(
            i for i, line in enumerate(lines)
            if "experiment=file_query_path_index load=24 pair=1 variant=index" in line
        ))
        first_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        refresh_plan_hash(missing_plan, missing)
        expect_rejected(
            lambda: build(SUITE_PATH, missing_plan, missing),
            "physical order differs from preregistration",
        )

        extra_marker = root / "extra-marker"
        extra_marker.mkdir()
        extra_plan = write_campaign(extra_marker, suite)
        extra_log = extra_marker / "boot-01/guest.log"
        extra_log.write_text(
            extra_log.read_text(encoding="utf-8").replace(
                "agenteval_ucore: sample schema=2",
                "agenteval_ucore: sample schema=2 extra=1",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(extra_plan, extra_marker)
        expect_rejected(lambda: build(SUITE_PATH, extra_plan, extra_marker), "schema/order mismatch")

        fingerprint = root / "fingerprint"
        fingerprint.mkdir()
        fingerprint_plan = write_campaign(fingerprint, suite)
        fingerprint_log = fingerprint / "boot-01/guest.log"
        fingerprint_log.write_text(
            re.sub(
                r"result_fingerprint=[0-9a-f]{16}",
                "result_fingerprint=ffffffffffffffff",
                fingerprint_log.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(fingerprint_plan, fingerprint)
        expect_rejected(lambda: build(SUITE_PATH, fingerprint_plan, fingerprint), "Host semantic oracle")

        workload = root / "workload-fingerprint"
        workload.mkdir()
        workload_plan = write_campaign(workload, suite)
        workload_log = workload / "boot-01/guest.log"
        workload_log.write_text(
            re.sub(
                r"workload_fingerprint=[0-9a-f]{16}",
                "workload_fingerprint=ffffffffffffffff",
                workload_log.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(workload_plan, workload)
        expect_rejected(lambda: build(SUITE_PATH, workload_plan, workload), "challenge-bound")

        reused = root / "reused-result"
        reused.mkdir()
        reused_plan = write_campaign(reused, suite)
        first_results = {}
        for line in (reused / "boot-01/guest.log").read_text(encoding="utf-8").splitlines():
            if line.startswith("agenteval_ucore: sample "):
                key = re.search(r"experiment=([^ ]+) load=([0-9]+) pair=([0-9]+)", line).groups()
                first_results[key] = re.search(r"result_fingerprint=([0-9a-f]{16})", line).group(1)
        second_log = reused / "boot-02/guest.log"
        rewritten = []
        for line in second_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("agenteval_ucore: sample "):
                key = re.search(r"experiment=([^ ]+) load=([0-9]+) pair=([0-9]+)", line).groups()
                line = re.sub(
                    r"result_fingerprint=[0-9a-f]{16}",
                    f"result_fingerprint={first_results[key]}",
                    line,
                )
            rewritten.append(line)
        second_log.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        refresh_plan_hash(reused_plan, reused)
        expect_rejected(lambda: build(SUITE_PATH, reused_plan, reused), "Host semantic oracle")

        challenge_receipt = root / "challenge-as-result"
        challenge_receipt.mkdir()
        challenge_receipt_plan = write_campaign(challenge_receipt, suite)
        for boot in range(1, 8):
            log_path = challenge_receipt / f"boot-{boot:02d}/guest.log"
            challenge = f"{boot:016x}"
            log_path.write_text(
                re.sub(
                    r"result_fingerprint=[0-9a-f]{16}",
                    f"result_fingerprint={challenge}",
                    log_path.read_text(encoding="utf-8"),
                ),
                encoding="utf-8",
            )
        refresh_plan_hash(challenge_receipt_plan, challenge_receipt)
        expect_rejected(
            lambda: build(SUITE_PATH, challenge_receipt_plan, challenge_receipt),
            "Host semantic oracle",
        )

        workload_as_result = root / "workload-as-result"
        workload_as_result.mkdir()
        workload_as_result_plan = write_campaign(workload_as_result, suite)
        result_log = workload_as_result / "boot-01/guest.log"
        result_lines = []
        for line in result_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("agenteval_ucore: sample "):
                workload_value = re.search(
                    r"workload_fingerprint=([0-9a-f]{16})", line
                ).group(1)
                line = re.sub(
                    r"result_fingerprint=[0-9a-f]{16}",
                    f"result_fingerprint={workload_value}",
                    line,
                )
            result_lines.append(line)
        result_log.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(workload_as_result_plan, workload_as_result)
        expect_rejected(
            lambda: build(SUITE_PATH, workload_as_result_plan, workload_as_result),
            "Host semantic oracle",
        )

        failed_marker = root / "guest-check-failed"
        failed_marker.mkdir()
        failed_plan = write_campaign(failed_marker, suite)
        failed_log = failed_marker / "boot-01/guest.log"
        failed_log.write_text(
            failed_log.read_text(encoding="utf-8").replace(
                "agenteval_ucore: worker passed",
                "agenteval_ucore: check failed: forged-success\nagenteval_ucore: worker passed",
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(failed_plan, failed_marker)
        expect_rejected(lambda: build(SUITE_PATH, failed_plan, failed_marker), "check failure")

        early_completion = root / "early-worker-completion"
        early_completion.mkdir()
        early_completion_plan = write_campaign(early_completion, suite)
        early_completion_log = early_completion / "boot-01/guest.log"
        completion_lines = early_completion_log.read_text(
            encoding="utf-8"
        ).splitlines()
        completion_lines.remove("agenteval_ucore: worker passed")
        challenge_position = next(
            index for index, line in enumerate(completion_lines)
            if line.startswith("agenteval_ucore: challenge=")
        )
        completion_lines.insert(
            challenge_position + 1, "agenteval_ucore: worker passed"
        )
        early_completion_log.write_text(
            "\n".join(completion_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(early_completion_plan, early_completion)
        expect_rejected(
            lambda: build(
                SUITE_PATH, early_completion_plan, early_completion
            ),
            "challenge/completion does not enclose samples",
        )

        late_business_root = root / "business-marker-after-worker"
        late_business_root.mkdir()
        late_business_plan = write_campaign(late_business_root, suite)
        late_business_log = late_business_root / "boot-01/guest.log"
        late_business_lines = late_business_log.read_text(
            encoding="utf-8"
        ).splitlines()
        late_business_lines.remove("agenteval_ucore: worker passed")
        task5_position = next(
            index for index, line in enumerate(late_business_lines)
            if line.startswith("agenteval_ucore: functional schema=1 task=task5 ")
        )
        late_business_lines.insert(task5_position, "agenteval_ucore: worker passed")
        late_business_log.write_text(
            "\n".join(late_business_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(late_business_plan, late_business_root)
        expect_rejected(
            lambda: build(SUITE_PATH, late_business_plan, late_business_root),
            "business marker order differs",
        )

        diagnostic = root / "diagnostic"
        diagnostic.mkdir()
        diagnostic_plan = write_campaign(diagnostic, suite)
        diagnostic_log = diagnostic / "boot-01/guest.log"
        diagnostic_log.write_text(
            diagnostic_log.read_text(encoding="utf-8").replace(
                "index_rebuild_records=0 result_cache_hits=0",
                "index_rebuild_records=1 result_cache_hits=0",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(diagnostic_plan, diagnostic)
        expect_rejected(lambda: build(SUITE_PATH, diagnostic_plan, diagnostic), "readiness conflicts")

        diagnostic_cache_hit = root / "diagnostic-cache-hit"
        diagnostic_cache_hit.mkdir()
        diagnostic_cache_hit_plan = write_campaign(diagnostic_cache_hit, suite)
        diagnostic_cache_hit_log = diagnostic_cache_hit / "boot-01/guest.log"
        diagnostic_cache_hit_log.write_text(
            diagnostic_cache_hit_log.read_text(encoding="utf-8").replace(
                "result_cache_hits=0 workload_fingerprint=",
                "result_cache_hits=1 workload_fingerprint=",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(diagnostic_cache_hit_plan, diagnostic_cache_hit)
        expect_rejected(
            lambda: build(
                SUITE_PATH, diagnostic_cache_hit_plan, diagnostic_cache_hit
            ),
            "diagnostic work contract",
        )

        diagnostic_shape = root / "diagnostic-shape"
        diagnostic_shape.mkdir()
        diagnostic_shape_plan = write_campaign(diagnostic_shape, suite)
        diagnostic_shape_log = diagnostic_shape / "boot-01/guest.log"
        diagnostic_shape_log.write_text(
            diagnostic_shape_log.read_text(encoding="utf-8").replace(
                "cache=ready operations=1 dataset_size=24 work_units=1 "
                "result_items=1",
                "cache=ready operations=1 dataset_size=23 work_units=1 "
                "result_items=1",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(diagnostic_shape_plan, diagnostic_shape)
        expect_rejected(
            lambda: build(SUITE_PATH, diagnostic_shape_plan, diagnostic_shape),
            "diagnostic work contract",
        )

        ready_work = root / "diagnostic-ready-real-work"
        ready_work.mkdir()
        write_campaign(ready_work, suite)
        ready_work_log = ready_work / "boot-01/guest.log"
        ready_work_text, replacements = re.subn(
            r"(agenteval_ucore: diagnostic [^\n]*cache=ready [^\n]*"
            r"work_units=)1 ",
            r"\g<1>3 ",
            ready_work_log.read_text(encoding="utf-8"),
            count=1,
        )
        assert replacements == 1
        ready_work_log.write_text(ready_work_text, encoding="utf-8")
        assert validate_guest_log(
            ready_work_log, suite, "0000000000000001"
        )["status"] == "supported"

        cold_work = root / "diagnostic-cold-work"
        cold_work.mkdir()
        cold_work_plan = write_campaign(cold_work, suite)
        cold_work_log = cold_work / "boot-01/guest.log"
        cold_work_log.write_text(
            cold_work_log.read_text(encoding="utf-8").replace(
                "cache=ready operations=1 dataset_size=24 work_units=1 "
                "result_items=1 duration_us=1 index_rebuild_records=0",
                "cache=cold-rebuild operations=1 dataset_size=24 work_units=1 "
                "result_items=1 duration_us=1 index_rebuild_records=2",
                1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(cold_work_plan, cold_work)
        expect_rejected(
            lambda: build(SUITE_PATH, cold_work_plan, cold_work),
            "readiness conflicts",
        )

        diagnostic_workload = root / "diagnostic-workload"
        diagnostic_workload.mkdir()
        diagnostic_workload_plan = write_campaign(diagnostic_workload, suite)
        diagnostic_workload_log = diagnostic_workload / "boot-01/guest.log"
        diagnostic_workload_log.write_text(
            re.sub(
                r"(agenteval_ucore: diagnostic [^\n]*workload_fingerprint=)[0-9a-f]{16}",
                r"\1ffffffffffffffff",
                diagnostic_workload_log.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(diagnostic_workload_plan, diagnostic_workload)
        expect_rejected(
            lambda: build(
                SUITE_PATH, diagnostic_workload_plan, diagnostic_workload
            ),
            "diagnostic workload fingerprint",
        )

        diagnostic_result = root / "diagnostic-result"
        diagnostic_result.mkdir()
        diagnostic_result_plan = write_campaign(diagnostic_result, suite)
        diagnostic_result_log = diagnostic_result / "boot-01/guest.log"
        diagnostic_result_log.write_text(
            re.sub(
                r"(agenteval_ucore: diagnostic [^\n]*result_fingerprint=)[0-9a-f]{16}",
                r"\1ffffffffffffffff",
                diagnostic_result_log.read_text(encoding="utf-8"),
                count=1,
            ),
            encoding="utf-8",
        )
        refresh_plan_hash(diagnostic_result_plan, diagnostic_result)
        expect_rejected(
            lambda: build(SUITE_PATH, diagnostic_result_plan, diagnostic_result),
            "diagnostic result fingerprint",
        )

        timed_rebuild = root / "timed-index-rebuild"
        timed_rebuild.mkdir()
        timed_rebuild_plan = write_campaign(timed_rebuild, suite)
        timed_rebuild_log = timed_rebuild / "boot-01/guest.log"
        timed_rebuild_lines = timed_rebuild_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(timed_rebuild_lines):
            if "experiment=file_query_path_index load=24 pair=1 variant=index " in line:
                timed_rebuild_lines[index] = line.replace(
                    "index_rebuild_records=0", "index_rebuild_records=1"
                )
                break
        timed_rebuild_log.write_text(
            "\n".join(timed_rebuild_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(timed_rebuild_plan, timed_rebuild)
        expect_rejected(
            lambda: build(SUITE_PATH, timed_rebuild_plan, timed_rebuild),
            "timed sample includes index rebuild",
        )

        timed_cache_hit = root / "timed-result-cache-hit"
        timed_cache_hit.mkdir()
        timed_cache_hit_plan = write_campaign(timed_cache_hit, suite)
        timed_cache_hit_log = timed_cache_hit / "boot-01/guest.log"
        timed_cache_hit_lines = timed_cache_hit_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(timed_cache_hit_lines):
            if "experiment=tool_batch load=24 pair=1 variant=batch " in line:
                timed_cache_hit_lines[index] = line.replace(
                    "result_cache_hits=0", "result_cache_hits=1"
                )
                break
        timed_cache_hit_log.write_text(
            "\n".join(timed_cache_hit_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(timed_cache_hit_plan, timed_cache_hit)
        expect_rejected(
            lambda: build(SUITE_PATH, timed_cache_hit_plan, timed_cache_hit),
            "timed sample includes index rebuild or result cache hit",
        )

        order = root / "same-order"
        order.mkdir()
        order_plan = write_campaign(order, suite, same_order=True)
        expect_rejected(
            lambda: build(SUITE_PATH, order_plan, order),
            "physical order differs from preregistration",
        )

        pair_blocks = root / "swapped-pair-blocks"
        pair_blocks.mkdir()
        pair_blocks_plan = write_campaign(pair_blocks, suite)
        pair_blocks_log = pair_blocks / "boot-01/guest.log"
        pair_lines = pair_blocks_log.read_text(encoding="utf-8").splitlines()
        pair1 = [
            index for index, line in enumerate(pair_lines)
            if "experiment=file_query_path_index load=24 pair=1 " in line
        ]
        pair2 = [
            index for index, line in enumerate(pair_lines)
            if "experiment=file_query_path_index load=24 pair=2 " in line
        ]
        assert pair1 == list(range(pair1[0], pair1[0] + 2))
        assert pair2 == list(range(pair2[0], pair2[0] + 2))
        first_block = pair_lines[pair1[0]:pair1[0] + 2]
        second_block = pair_lines[pair2[0]:pair2[0] + 2]
        pair_lines[pair1[0]:pair1[0] + 2] = second_block
        pair_lines[pair2[0]:pair2[0] + 2] = first_block
        pair_blocks_log.write_text("\n".join(pair_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(pair_blocks_plan, pair_blocks)
        expect_rejected(
            lambda: build(SUITE_PATH, pair_blocks_plan, pair_blocks),
            "physical order differs from preregistration",
        )

        incomplete_path_walk = root / "incomplete-path-walk"
        incomplete_path_walk.mkdir()
        incomplete_path_plan = write_campaign(incomplete_path_walk, suite)
        incomplete_path_log = incomplete_path_walk / "boot-01/guest.log"
        incomplete_path_lines = incomplete_path_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(incomplete_path_lines):
            if (
                "experiment=file_query_path_index load=24 pair=1 "
                "variant=path_walk " in line
            ):
                incomplete_path_lines[index] = line.replace(
                    "work_units=144 records_examined=144",
                    "work_units=138 records_examined=138",
                )
                break
        incomplete_path_log.write_text(
            "\n".join(incomplete_path_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(incomplete_path_plan, incomplete_path_walk)
        expect_rejected(
            lambda: build(
                SUITE_PATH, incomplete_path_plan, incomplete_path_walk
            ),
            "did not examine all N paths",
        )

        relabelled_table_scan = root / "table-scan-as-path-walk"
        relabelled_table_scan.mkdir()
        relabelled_table_plan = write_campaign(relabelled_table_scan, suite)
        relabelled_table_log = relabelled_table_scan / "boot-01/guest.log"
        relabelled_lines = relabelled_table_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(relabelled_lines):
            if (
                "experiment=file_query_path_index load=24 pair=1 "
                "variant=path_walk " in line
            ):
                relabelled_lines[index] = line.replace(
                    "work_units=144 records_examined=144",
                    "work_units=3072 records_examined=144",
                )
                break
        relabelled_table_log.write_text(
            "\n".join(relabelled_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(relabelled_table_plan, relabelled_table_scan)
        expect_rejected(
            lambda: build(
                SUITE_PATH, relabelled_table_plan, relabelled_table_scan
            ),
            "did not examine all N paths",
        )

        shallow_scan = root / "shallow-file-scan"
        shallow_scan.mkdir()
        shallow_scan_plan = write_campaign(shallow_scan, suite)
        shallow_scan_log = shallow_scan / "boot-01/guest.log"
        shallow_lines = shallow_scan_log.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(shallow_lines):
            if "experiment=file_query_table_ablation load=96 pair=1 variant=scan " in line:
                shallow_lines[index] = line.replace(
                    "work_units=8192 records_examined=1568",
                    "work_units=1568 records_examined=1568",
                )
                break
        shallow_scan_log.write_text("\n".join(shallow_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(shallow_scan_plan, shallow_scan)
        expect_rejected(
            lambda: build(SUITE_PATH, shallow_scan_plan, shallow_scan),
            "not a full-table scan",
        )

        drifting_census = root / "drifting-metadata-census"
        drifting_census.mkdir()
        drifting_census_plan = write_campaign(drifting_census, suite)
        drifting_census_log = drifting_census / "boot-01/guest.log"
        drifting_lines = drifting_census_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(drifting_lines):
            if (
                "experiment=file_query_table_ablation load=24 pair=1 "
                "variant=scan " in line
            ):
                drifting_lines[index] = line.replace(
                    "records_examined=416", "records_examined=432"
                )
                break
        drifting_census_log.write_text(
            "\n".join(drifting_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(drifting_census_plan, drifting_census)
        expect_rejected(
            lambda: build(SUITE_PATH, drifting_census_plan, drifting_census),
            "ambient census is inconsistent",
        )

        fractional_census = root / "fractional-metadata-census"
        fractional_census.mkdir()
        fractional_census_plan = write_campaign(fractional_census, suite)
        fractional_census_log = fractional_census / "boot-01/guest.log"
        fractional_lines = fractional_census_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(fractional_lines):
            if (
                "experiment=file_query_table_ablation load=24 pair=1 "
                "variant=scan " in line
            ):
                fractional_lines[index] = line.replace(
                    "records_examined=416", "records_examined=417"
                )
                break
        fractional_census_log.write_text(
            "\n".join(fractional_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(fractional_census_plan, fractional_census)
        expect_rejected(
            lambda: build(SUITE_PATH, fractional_census_plan, fractional_census),
            "ambient census is not integral",
        )

        shallow_index = root / "shallow-file-index"
        shallow_index.mkdir()
        shallow_index_plan = write_campaign(shallow_index, suite)
        shallow_index_log = shallow_index / "boot-01/guest.log"
        shallow_lines = shallow_index_log.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(shallow_lines):
            if "experiment=file_query_table_ablation load=96 pair=1 variant=index " in line:
                shallow_lines[index] = line.replace(
                    "work_units=16 records_examined=16",
                    "work_units=1 records_examined=1",
                )
                break
        shallow_index_log.write_text("\n".join(shallow_lines) + "\n", encoding="utf-8")
        refresh_plan_hash(shallow_index_plan, shallow_index)
        expect_rejected(
            lambda: build(SUITE_PATH, shallow_index_plan, shallow_index),
            "no bounded measured work",
        )

        overcounted_index = root / "overcounted-file-index"
        overcounted_index.mkdir()
        overcounted_plan = write_campaign(overcounted_index, suite)
        overcounted_log = overcounted_index / "boot-01/guest.log"
        overcounted_lines = overcounted_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(overcounted_lines):
            if "experiment=file_query_table_ablation load=24 pair=1 variant=index " in line:
                overcounted_lines[index] = line.replace(
                    "work_units=16 records_examined=16",
                    "work_units=16 records_examined=17",
                )
                break
        overcounted_log.write_text(
            "\n".join(overcounted_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(overcounted_plan, overcounted_index)
        expect_rejected(
            lambda: build(SUITE_PATH, overcounted_plan, overcounted_index),
            "work receipt is inconsistent",
        )

        colliding_index = root / "colliding-file-index"
        colliding_index.mkdir()
        colliding_index_plan = write_campaign(colliding_index, suite)
        colliding_index_log = colliding_index / "boot-01/guest.log"
        colliding_lines = colliding_index_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(colliding_lines):
            if "experiment=file_query_table_ablation load=24 pair=1 variant=index " in line:
                colliding_lines[index] = line.replace(
                    "work_units=16 records_examined=16",
                    "work_units=32 records_examined=16",
                )
                break
        colliding_index_log.write_text(
            "\n".join(colliding_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(colliding_index_plan, colliding_index)
        colliding_targeted = validate_guest_log(
            colliding_index_log, suite, "0000000000000001"
        )
        assert colliding_targeted["status"] == "supported"
        colliding_summary, colliding_rows = build(
            SUITE_PATH, colliding_index_plan, colliding_index
        )
        validate_summary(colliding_summary)
        assert any(
            row.get("kind") == "agentos-evaluation-metric-row"
            and row["experiment"] == "file_query_table_ablation"
            and row["boot_id"] == "boot-01"
            and row["load"] == 24
            and row["inner_pair"] == 1
            and row["role"] == "treatment"
            and row["work_units"] == 32
            and row["records_examined"] == 16
            for row in colliding_rows
        )

        unbounded_index = root / "unbounded-file-index"
        unbounded_index.mkdir()
        unbounded_index_plan = write_campaign(unbounded_index, suite)
        unbounded_index_log = unbounded_index / "boot-01/guest.log"
        unbounded_lines = unbounded_index_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(unbounded_lines):
            if "experiment=file_query_table_ablation load=24 pair=1 variant=index " in line:
                unbounded_lines[index] = line.replace(
                    "work_units=16 records_examined=16",
                    "work_units=8208 records_examined=16",
                )
                break
        unbounded_index_log.write_text(
            "\n".join(unbounded_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(unbounded_index_plan, unbounded_index)
        expect_rejected(
            lambda: build(SUITE_PATH, unbounded_index_plan, unbounded_index),
            "no bounded measured work",
        )

        ineffective_index = root / "ineffective-file-index"
        ineffective_index.mkdir()
        ineffective_index_plan = write_campaign(ineffective_index, suite)
        ineffective_index_log = ineffective_index / "boot-01/guest.log"
        ineffective_lines = ineffective_index_log.read_text(
            encoding="utf-8"
        ).splitlines()
        for index, line in enumerate(ineffective_lines):
            if "experiment=file_query_table_ablation load=24 pair=1 variant=index " in line:
                ineffective_lines[index] = line.replace(
                    "work_units=16 records_examined=16",
                    "work_units=8192 records_examined=384",
                )
                break
        ineffective_index_log.write_text(
            "\n".join(ineffective_lines) + "\n", encoding="utf-8"
        )
        refresh_plan_hash(ineffective_index_plan, ineffective_index)
        ineffective_summary, ineffective_rows = build(
            SUITE_PATH, ineffective_index_plan, ineffective_index
        )
        assert any(
            row.get("kind") == "agentos-evaluation-metric-row"
            and row["experiment"] == "file_query_table_ablation"
            and row["boot_id"] == "boot-01"
            and row["load"] == 24
            and row["inner_pair"] == 1
            and row["role"] == "treatment"
            and row["work_units"] == 8192
            and row["records_examined"] == 384
            for row in ineffective_rows
        )
        assert next(
            claim for claim in ineffective_summary["claims"]
            if claim["benchmark_id"] == "file_query_table_ablation"
        )["status"] == "supported"

        work = root / "wrong-work"
        work.mkdir()
        work_plan = write_campaign(work, suite)
        work_log = work / "boot-01/guest.log"
        work_log.write_text(work_log.read_text(encoding="utf-8").replace("operations=24 dataset_size=0 work_units=24", "operations=1 dataset_size=0 work_units=24", 2), encoding="utf-8")
        refresh_plan_hash(work_plan, work)
        expect_rejected(lambda: build(SUITE_PATH, work_plan, work), "configured load")

        crossing = root / "crossing"
        crossing.mkdir()
        crossing_plan = write_campaign(crossing, suite, improvement_by_boot=[100, -100, 100, -100, 100, -100, 100])
        crossing_summary, _ = build(SUITE_PATH, crossing_plan, crossing)
        assert all(claim["status"] == "not_supported" for claim in crossing_summary["claims"])
        assert crossing_summary["acceptance"]["tasks"]["task4"] == "not_ready"
        assert not crossing_summary["acceptance"]["competition_ready"]
        assert any(pair["ci_low"] <= 0 for pair in crossing_summary["benchmarks"][0]["paired"])
        assert any(pair["relative_ci_low"] <= 0 for pair in crossing_summary["benchmarks"][0]["paired"])

        contest_negative = root / "contest-negative-ablation-positive"
        contest_negative.mkdir()
        contest_negative_plan = write_campaign(contest_negative, suite)
        make_experiment_slower(
            contest_negative, "file_query_path_index", "index"
        )
        refresh_plan_hash(contest_negative_plan, contest_negative)
        contest_negative_summary, _ = build(
            SUITE_PATH, contest_negative_plan, contest_negative
        )
        statuses = {
            claim["benchmark_id"]: claim["status"]
            for claim in contest_negative_summary["claims"]
        }
        assert statuses["file_query_path_index"] == "not_supported"
        assert statuses["file_query_table_ablation"] == "supported"
        assert contest_negative_summary["acceptance"]["tasks"]["task4"] == (
            "not_ready"
        )
        assert contest_negative_summary["acceptance"]["task4_gate"] == {
            "benchmark_id": "file_query_path_index",
            "functional_status": "pass",
            "claim_status": "not_supported",
            "required_status": "supported",
        }

        ablation_negative = root / "contest-positive-ablation-negative"
        ablation_negative.mkdir()
        ablation_negative_plan = write_campaign(ablation_negative, suite)
        make_experiment_slower(
            ablation_negative, "file_query_table_ablation", "index"
        )
        refresh_plan_hash(ablation_negative_plan, ablation_negative)
        ablation_negative_summary, _ = build(
            SUITE_PATH, ablation_negative_plan, ablation_negative
        )
        statuses = {
            claim["benchmark_id"]: claim["status"]
            for claim in ablation_negative_summary["claims"]
        }
        assert statuses["file_query_path_index"] == "supported"
        assert statuses["file_query_table_ablation"] == "not_supported"
        assert ablation_negative_summary["acceptance"]["tasks"]["task4"] == (
            "pass"
        )

        descriptive_only = root / "descriptive-bootstrap-only"
        descriptive_only.mkdir()
        descriptive_only_plan = write_campaign(
            descriptive_only,
            suite,
            improvement_by_boot=[5, 100, 100, 100, 100, 100, 100],
        )
        descriptive_only_summary, _ = build(
            SUITE_PATH, descriptive_only_plan, descriptive_only
        )
        assert all(
            claim["status"] == "not_supported"
            for claim in descriptive_only_summary["claims"]
        )
        for item in descriptive_only_summary["benchmarks"]:
            gate = item["claim_gate"]
            for pair in item["paired"]:
                assert pair["ci_low"] >= gate["minimum_absolute_improvement_us"]
                assert pair["relative_ci_low"] >= gate[
                    "minimum_relative_improvement_percent"
                ]
                assert pair["sign_test"]["p_value"] <= headline_alpha
                assert pair["mcid_sign_test"]["wins"] == 6
                assert pair["mcid_sign_test"]["non_wins"] == 1
                assert pair["mcid_sign_test"]["p_value"] == 1 / 16

        quantized = root / "timer-quantized"
        quantized.mkdir()
        quantized_plan = write_campaign(quantized, suite)
        baseline_variants = {
            experiment["baseline"]["id"] for experiment in suite["experiments"]
        }
        for boot in range(1, 8):
            log_path = quantized / f"boot-{boot:02d}/guest.log"
            rewritten = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("agenteval_ucore: sample "):
                    variant = re.search(r" variant=([^ ]+) ", line).group(1)
                    duration = 1 if variant in baseline_variants else 0
                    line = re.sub(r"duration_us=[0-9]+", f"duration_us={duration}", line)
                rewritten.append(line)
            log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        refresh_plan_hash(quantized_plan, quantized)
        quantized_summary, _ = build(SUITE_PATH, quantized_plan, quantized)
        validate_summary(quantized_summary)
        assert all(
            claim["status"] == "not_supported"
            for claim in quantized_summary["claims"]
        )

        heterogeneous = root / "heterogeneous-pairs"
        heterogeneous.mkdir()
        heterogeneous_plan = write_campaign(heterogeneous, suite)
        baseline_durations = [100, 2, 50, 3, 60, 4, 70]
        treatment_durations = [101, 3, 0, 4, 1, 5, 2]
        for boot in range(1, 8):
            log_path = heterogeneous / f"boot-{boot:02d}/guest.log"
            rewritten = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("agenteval_ucore: sample ") and "experiment=file_query_table_ablation load=24 " in line:
                    pair = int(re.search(r" pair=([1-7]) ", line).group(1))
                    values = baseline_durations if " variant=scan " in line else treatment_durations
                    line = re.sub(
                        r"duration_us=[0-9]+",
                        f"duration_us={values[pair - 1] * 16}",
                        line,
                    )
                rewritten.append(line)
            log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        refresh_plan_hash(heterogeneous_plan, heterogeneous)
        heterogeneous_summary, _ = build(SUITE_PATH, heterogeneous_plan, heterogeneous)
        validate_summary(heterogeneous_summary)
        heterogeneous_file = next(
            item for item in heterogeneous_summary["benchmarks"]
            if item["id"] == "file_query_table_ablation"
        )
        pair24 = next(item for item in heterogeneous_file["paired"] if item["load"] == 24)
        estimates24 = {
            item["target_id"]: item["value"]
            for item in heterogeneous_file["estimates"] if item["load"] == 24
        }
        assert pair24["median"] == -1
        assert (
            estimates24["file_query_table_ablation:scan"]
            - estimates24["file_query_table_ablation:index"]
        ) == 47
        assert all(sample["value"] == -1 for sample in pair24["samples"])
        assert all(sample["baseline_value"] == 50 for sample in pair24["samples"])
        assert all(sample["treatment_value"] == 3 for sample in pair24["samples"])
        assert all(len(sample["inner_pairs"]) == 7 for sample in pair24["samples"])
        assert pair24["samples"][0]["inner_pairs"][0] == {
            "pair": 1,
            "baseline_value": 100,
            "treatment_value": 101,
            "value": -1,
            "relative_percent": -1.0,
        }

        unavailable = root / "unavailable"
        unavailable.mkdir()
        unavailable_plan = write_campaign(unavailable, suite, unavailable_last=True)
        unavailable_summary, unavailable_rows = build(SUITE_PATH, unavailable_plan, unavailable)
        validate_summary(unavailable_summary)
        assert unavailable_summary["run"]["status"] == "unavailable"
        assert all(item["status"] == "unavailable" for item in unavailable_summary["benchmarks"])
        assert all(not item["estimates"] and not item["samples"] for item in unavailable_summary["benchmarks"])
        assert all(row["value"] > 0 for row in unavailable_rows)

        for capture_status in ("unavailable", "failed"):
            status_root = root / f"all-{capture_status}"
            status_root.mkdir()
            status_plan = write_campaign(status_root, suite, all_status=capture_status)
            status_summary, status_rows = build(SUITE_PATH, status_plan, status_root)
            validate_summary(status_summary)
            assert status_summary["run"]["status"] == capture_status
            assert status_rows == []
            status_summary_path = status_root / "summary.json"
            status_rows_path = status_root / "metrics.jsonl"
            write_json(status_summary_path, status_summary)
            write_jsonl(status_rows_path, status_rows)
            assert status_rows_path.read_bytes() == b""
            assert verify(
                SUITE_PATH,
                status_plan,
                status_root,
                status_summary_path,
                status_rows_path,
            ) == status_summary

    print("test_evaluation_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
