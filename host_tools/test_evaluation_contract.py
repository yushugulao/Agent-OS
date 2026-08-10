#!/usr/bin/env python3
"""Focused tests for real AgentOS Guest functional and performance validation."""

from __future__ import annotations

import copy
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host_tools"))

from evaluation_contract import (  # noqa: E402
    EvaluationError,
    _expected_result,
    _expected_workload,
    _file_operation_targets,
    _file_target_sequence,
    _fnv_bytes,
    _fnv_u64,
    _format_functional_receipt,
    _functional_semantic,
    _operations_for,
    _semantic_token,
    _task2_schema_fingerprint,
    _task3_semantic,
    _task3_tool_semantic,
    _task4_fixture,
    _task4_query_semantic,
    load_suite,
    main as evaluation_contract_main,
    validate_guest_log,
)

SUITE_PATH = ROOT / "ci" / "evaluation-suite.json"
def expect_rejected(action, message: str) -> None:
    try:
        action()
    except EvaluationError as error:
        assert message in str(error), error
    else:
        raise AssertionError(f"accepted invalid Guest output: {message}")

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

    context_base = 0x3FFFFE7000
    task1_values = [
        agent_pid, launcher_pid, 1, 4, 1000 + boot_number, context_base,
        7 * 4096, 0x4147435458543031, 9, 128, 128, 2,
        6 * 4096, 4096,
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

def revisit_lines(
    challenge: str, boot_number: int, suite: dict
) -> list[str]:
    config = suite["supplementary_scenarios"][0]
    identities = config["identity_order"]
    visit_markers: list[str] = []
    visit_fingerprints: list[int] = []
    for visit, identity in enumerate(config["visit_sequence"], 1):
        identity_index = identities.index(identity)
        ordinal = 2 if visit == len(config["visit_sequence"]) else 1
        request_id = _semantic_token(
            "aios-revisit-visit-v1", visit, identity_index, ordinal, challenge
        )
        agent_id = 10_000 + boot_number * 10 + identity_index
        lifecycle_id = identity_index + 1
        lifecycle_generation = 1000 + boot_number * 10 + identity_index
        values = [
            visit, identity_index, request_id, agent_id, lifecycle_id,
            lifecycle_generation, 1, 0, 1 if ordinal == 2 else 0, 0,
        ]
        fingerprint = _functional_semantic(
            "aios-revisit-observation-v1", challenge, values
        )
        visit_fingerprints.append(int(fingerprint, 16))
        visit_markers.append(
            "agenteval_ucore: revisit schema=1 "
            f"visit={visit} identity={identity} request_id={request_id:016x} "
            f"agent_id={agent_id} lifecycle_id={lifecycle_id} "
            f"lifecycle_generation={lifecycle_generation} correct=1 "
            f"contamination=0 return_visit={1 if ordinal == 2 else 0} "
            f"fallback=0 result_fingerprint={fingerprint} status=observed"
        )
    summary_values = [len(visit_markers), len(visit_markers), 0, 1, 0]
    summary_fingerprint = _functional_semantic(
        "aios-revisit-summary-v1",
        challenge,
        [*summary_values, *visit_fingerprints],
    )
    lines = [
        *visit_markers,
        (
            "agenteval_ucore: revisit_summary schema=1 "
            f"visits={len(visit_markers)} correct={len(visit_markers)} "
            "contamination=0 return_visit=1 fallback=0 "
            f"result_fingerprint={summary_fingerprint} status=measured"
        ),
    ]
    for level in config["concurrency_levels"]:
        samples: list[dict[str, int | str]] = []
        base_us = 1_000_000 + boot_number * 100_000 + level * 10_000
        for round_number in range(config["rounds_per_level"]):
            for slot in range(level):
                index = round_number * level + slot
                identity_index = index % len(identities)
                identity = identities[identity_index]
                request_id = _semantic_token(
                    "agentos-qos-request-v2", level, round_number,
                    slot * len(identities) + identity_index, challenge,
                )
                submitted_us = base_us + index * 100
                wait_us = 1 + (level + round_number + slot) % 4
                service_us = 5 + boot_number + identity_index
                started_us = submitted_us + wait_us
                completed_us = started_us + service_us
                received_us = completed_us + 2
                turnaround_us = wait_us + service_us
                fingerprint = _functional_semantic(
                    "agentos-qos-sample-v2",
                    challenge,
                    [
                        level, round_number, slot, identity_index, request_id,
                        1, 0, 0, 1, submitted_us, started_us, completed_us,
                        received_us, wait_us, service_us, turnaround_us,
                    ],
                )
                samples.append({
                    "round": round_number,
                    "slot": slot,
                    "identity": identity,
                    "request_id": request_id,
                    "submitted_us": submitted_us,
                    "started_us": started_us,
                    "completed_us": completed_us,
                    "received_us": received_us,
                    "wait_us": wait_us,
                    "service_us": service_us,
                    "turnaround_us": turnaround_us,
                    "fingerprint": fingerprint,
                })
                lines.append(
                    "agenteval_ucore: concurrency_sample schema=2 "
                    f"concurrency={level} round={round_number} slot={slot} "
                    f"identity={identity} request_id={request_id:016x} "
                    f"submitted_us={submitted_us} started_us={started_us} "
                    f"completed_us={completed_us} received_us={received_us} "
                    f"wait_us={wait_us} service_us={service_us} "
                    f"turnaround_us={turnaround_us} correct=1 contamination=0 "
                    "fallback=0 isolation_ok=1 "
                    f"result_fingerprint={fingerprint} status=measured"
                )
        waits = sorted(int(item["wait_us"]) for item in samples)
        services = sorted(int(item["service_us"]) for item in samples)
        durations = sorted(int(item["turnaround_us"]) for item in samples)
        requests = len(samples)
        start_us = base_us - 1
        end_us = int(samples[-1]["received_us"]) + 1
        duration_us = end_us - start_us
        throughput = requests * 1_000_000_000 // duration_us
        goodput = throughput
        average = sum(durations) * 1000 // requests

        def nearest(values: list[int], percentile: int) -> int:
            rank = (percentile * requests + 99) // 100
            return values[rank - 1]

        identity_good = [
            sum(item["identity"] == identity for item in samples)
            for identity in identities
        ]
        squares = sum(value * value for value in identity_good)
        fairness = requests * requests * 1_000_000 // (len(identities) * squares)
        max_min = min(identity_good) * 1_000_000 // max(identity_good)
        workload_values = [
            level,
            *(
                value
                for item in samples
                for value in (
                    int(item["round"]), int(item["slot"]),
                    identities.index(str(item["identity"])),
                    int(item["request_id"]),
                )
            ),
        ]
        workload_digest = _functional_semantic(
            "agentos-qos-workload-v2", challenge, workload_values
        )

        summary_values = [
            level, config["rounds_per_level"], requests, requests,
            start_us, end_us, duration_us, throughput, goodput, average,
            nearest(durations, 50), nearest(durations, 90),
            nearest(durations, 99), sum(waits) * 1000 // requests,
            nearest(waits, 50), nearest(waits, 90), nearest(waits, 99),
            sum(services) * 1000 // requests, nearest(services, 50),
            nearest(services, 90), nearest(services, 99), fairness, max_min,
            requests, requests, 0, 0, int(workload_digest, 16),
            *(int(item["fingerprint"], 16) for item in samples),
        ]
        fingerprint = _functional_semantic(
            "agentos-qos-summary-v2", challenge, summary_values
        )
        lines.append(
            "agenteval_ucore: concurrency schema=2 "
            f"concurrency={level} rounds={config['rounds_per_level']} "
            f"requests={requests} completed={requests} start_us={start_us} "
            f"end_us={end_us} duration_us={duration_us} "
            f"throughput_milli_rps={throughput} goodput_milli_rps={goodput} "
            f"avg_milli_us={average} p50_us={nearest(durations, 50)} "
            f"p90_us={nearest(durations, 90)} p99_us={nearest(durations, 99)} "
            f"wait_avg_milli_us={sum(waits) * 1000 // requests} "
            f"wait_p50_us={nearest(waits, 50)} wait_p90_us={nearest(waits, 90)} "
            f"wait_p99_us={nearest(waits, 99)} "
            f"service_avg_milli_us={sum(services) * 1000 // requests} "
            f"service_p50_us={nearest(services, 50)} "
            f"service_p90_us={nearest(services, 90)} "
            f"service_p99_us={nearest(services, 99)} "
            f"fairness_jain_ppm={fairness} max_min_fairness_ppm={max_min} "
            f"isolated={requests} correct={requests} contamination=0 fallback=0 "
            f"workload_digest={workload_digest} "
            f"result_fingerprint={fingerprint} status=measured"
        )
    return lines

def make_log(suite: dict, boot_number: int, improvement: int = 100, same_order: bool = False) -> tuple[str, str]:
    challenge = f"{boot_number + 1:016x}"
    launcher, receipts = functional_lines(challenge, boot_number)
    lines = [
        "boot", f"agenteval_ucore: challenge={challenge}", launcher,
    ]
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
    lines.extend((*receipts, "agenteval_ucore: worker passed"))
    lines.extend(revisit_lines(challenge, boot_number, suite))
    lines.append("agenteval_ucore: parent passed")
    return challenge, "\n".join(lines) + "\n"

def main() -> int:
    suite = load_suite(SUITE_PATH)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        challenge, content = make_log(suite, 0)
        log_path = root / "guest.log"
        log_path.write_text(content, encoding="utf-8")

        result = validate_guest_log(log_path, suite, challenge)
        assert {
            key: value for key, value in result.items()
            if key != "revisit_isolation"
        } == {
            "schema_version": 1,
            "kind": "agentos-evaluation-guest-validation",
            "challenge": challenge,
            "samples": 182,
            "diagnostics": 7,
            "functional_receipts": 6,
            "catalog_descriptors": 4,
            "status": "supported",
        }
        revisit = result["revisit_isolation"]
        assert revisit["correct"] == 5
        assert revisit["contamination"] == 0
        assert revisit["return_visit"] == 1
        assert revisit["fallback"] == 0
        assert [item["concurrency"] for item in revisit["concurrency"]] == [1, 2, 4]
        assert all(
            item["completed"] == item["requests"]
            and item["goodput_milli_rps"] == item["throughput_milli_rps"]
            and item["p50_us"] <= item["p90_us"] <= item["p99_us"]
            and item["wait_p50_us"] <= item["wait_p90_us"] <= item["wait_p99_us"]
            and item["service_p50_us"] <= item["service_p90_us"] <= item["service_p99_us"]
            and item["fairness_jain_ppm"] == 1_000_000
            and item["max_min_fairness_ppm"] == 1_000_000
            and item["isolated"] == item["requests"]
            for item in revisit["concurrency"]
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert evaluation_contract_main([
                "validate-guest",
                "--suite", str(SUITE_PATH),
                "--log", str(log_path),
                "--challenge", challenge,
            ]) == 0
        assert json.loads(stdout.getvalue()) == result

        expect_rejected(
            lambda: validate_guest_log(log_path, suite, "0000000000000002"),
            "challenge differs",
        )

        lines = content.splitlines()
        sample_index = next(
            index for index, line in enumerate(lines)
            if line.startswith("agenteval_ucore: sample ")
        )
        bad_fingerprint = list(lines)
        bad_fingerprint[sample_index] = re.sub(
            r"result_fingerprint=[0-9a-f]{16}",
            "result_fingerprint=0000000000000000",
            bad_fingerprint[sample_index],
        )
        bad_fingerprint_path = root / "bad-fingerprint.log"
        bad_fingerprint_path.write_text(
            "\n".join(bad_fingerprint) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: validate_guest_log(bad_fingerprint_path, suite, challenge),
            "Host semantic oracle",
        )

        missing_task = [
            line for line in lines
            if not line.startswith(
                "agenteval_ucore: functional schema=1 task=task3 "
            )
        ]
        missing_task_path = root / "missing-task.log"
        missing_task_path.write_text(
            "\n".join(missing_task) + "\n", encoding="utf-8"
        )
        expect_rejected(
            lambda: validate_guest_log(missing_task_path, suite, challenge),
            "exactly one receipt for every Task1-5",
        )

        qos_index = next(
            index for index, line in enumerate(lines)
            if line.startswith("agenteval_ucore: concurrency_sample ")
        )
        bad_qos = list(lines)
        bad_qos[qos_index] = bad_qos[qos_index].replace(
            "correct=1 contamination=0", "correct=0 contamination=1", 1
        )
        bad_qos_path = root / "bad-qos.log"
        bad_qos_path.write_text("\n".join(bad_qos) + "\n", encoding="utf-8")
        expect_rejected(
            lambda: validate_guest_log(bad_qos_path, suite, challenge),
            "revisit",
        )

        malformed_suite = copy.deepcopy(suite)
        malformed_suite.pop("execution_schedule")
        malformed_path = root / "malformed-suite.json"
        malformed_path.write_text(json.dumps(malformed_suite), encoding="utf-8")
        expect_rejected(lambda: load_suite(malformed_path), "suite fields differ")

    file_experiment = next(
        item for item in suite["experiments"]
        if item["id"] == "file_query_path_index"
    )
    targets = _file_target_sequence(24, 7, "0000000000000001")
    assert len(targets) == len(set(targets)) == 7
    assert len(_file_operation_targets(24, 1, 6, "0000000000000001")) == 6
    assert _expected_workload(
        file_experiment, 24, 1, "0000000000000001"
    ) == "0e245d6ca9258c40"
    assert _expected_result(
        file_experiment, 24, 1, "0000000000000001"
    ) != _expected_result(
        file_experiment, 24, 1, "0000000000000002"
    )

    print("test_evaluation_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
