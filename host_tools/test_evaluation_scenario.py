#!/usr/bin/env python3
"""研究平台场景收集器的回归测试。"""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from . import evaluation_scenario as scenario
    from . import check_seeded_action_state as seeded
except ImportError:  # 支持从 host_tools/ 目录直接执行。
    import evaluation_scenario as scenario
    import check_seeded_action_state as seeded


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


def _ledger(
    target: str,
    programs: tuple[str, ...],
    roles: dict[str, str],
    boot_number: int,
    agentos_advantage_ms: int = 5,
) -> tuple[str, int]:
    if target == "plain":
        lines = ["orchestrator=rp_seed_orch", "launcher=fork_seeded"]
    else:
        lines = ["orchestrator=rp_orch", "launcher=mixed_attested"]
    program_elapsed_ms_total = 0
    for index, program in enumerate(programs):
        elapsed = (
            12
            if target == "plain"
            else 12 - agentos_advantage_ms
        ) + boot_number + index
        program_elapsed_ms_total += elapsed
        if target == "plain":
            lines.append(
                f"program={program};launcher=fork_seeded;ok=1;code=0;elapsed_ms={elapsed}"
            )
            continue
        role = roles.get(program, "plain")
        is_agent = 1 if program in roles else 0
        role_number = scenario.ROLE_NUMBERS.get(role, 0)
        launcher = "agent_create_role" if is_agent else "agent_worker_create"
        lines.append(
            f"program={program};role={role};launcher={launcher};"
            f"identity_source=child_after_exec;is_agent={is_agent};"
            f"agent_role={role_number};filesystem_domain=3;"
            f"filesystem_capabilities=66;ok=1;code=0;elapsed_ms={elapsed}"
        )
    return "\n".join(lines) + "\n", program_elapsed_ms_total


def _challenge(number: int) -> str:
    return f"ch-{number:012d}"


def _outcome_files(challenge: str) -> dict[str, str | bytes]:
    values = seeded.derive_challenge(challenge)
    input_data, output_data = seeded.task6_artifact_payloads(challenge)
    return {
        "rp_runner": (
            f"host_action_rerun=usable-run:{values.rerun_id};"
            f"parent={values.run_id};status=completed\n"
        ),
        "rp_stage_state": (
            f"host_workflow_run_id={values.run_id}\n"
            "host_workflow_stage_action=align;attempt=2;status=failed;"
            "command=align_reads;duration_ms=1200\n"
        ),
        "rp_stage_dag": f"host_workflow_id={values.workflow_id}\n",
        "rp_artifact": (
            "host_artifact_input=raw-counts.csv;kind=counts_csv;"
            f"sha256={values.input_sha256};bytes={values.input_bytes};source=host_challenge\n"
            "host_artifact_derive=raw-counts.csv;output=normalized-counts.csv;"
            f"operation=normalize_ppm;stage=analyze;sha256={values.derived_sha256}\n"
            f"task6_artifact_receipt={seeded.TASK6_ARTIFACT_RECEIPT_SCHEMA};"
            f"challenge={challenge};input_storage={seeded.TASK6_ARTIFACT_INPUT_STORAGE};"
            f"input_bytes={values.input_bytes};input_fnv64={values.input_fnv64};"
            f"input_sha256={values.input_sha256};"
            f"output_storage={seeded.TASK6_ARTIFACT_OUTPUT_STORAGE};"
            f"output_bytes={values.derived_bytes};output_fnv64={values.derived_fnv64};"
            f"output_sha256={values.derived_sha256};operation=normalize_ppm\n"
        ),
        seeded.TASK6_ARTIFACT_INPUT_STORAGE: input_data,
        seeded.TASK6_ARTIFACT_OUTPUT_STORAGE: output_data,
        "rp_llm_resp": (
            "host_llm_response_id=host-r1\n"
            "host_llm_response_request=host-q1\n"
            "host_llm_response_provider=template\n"
            "host_llm_response_mode=template\n"
            "host_llm_response_summary=host_response_ready\n"
            "host_llm_response_citations=5\n"
        ),
    }


def _workflow_timing(target: str, program_elapsed_ms_total: int, boot_number: int) -> str:
    steady_elapsed_ms = program_elapsed_ms_total + 20
    start_ms = 10_000 + boot_number * 1_000 + (500 if target == "agentos" else 0)
    if target == "plain":
        entry = "rp_seed_orch"
        handoff = "direct"
        phase_mask = 0
        completion = "local_final_validation"
        completion_phase_mask = scenario.PLAIN_COMPLETION_PHASE_MASK
        setup_elapsed_ms = 17
        exec_elapsed_ms = 0
    else:
        entry = "rp_agentos_orch"
        handoff = "delegated_pipe_v1"
        phase_mask = scenario.AGENTOS_INIT_PHASE_MASK
        completion = "parent_wait_final_validation"
        completion_phase_mask = scenario.AGENTOS_COMPLETION_PHASE_MASK
        setup_elapsed_ms = 15
        exec_elapsed_ms = 2
    ready_ms = start_ms + setup_elapsed_ms
    steady_start_ms = ready_ms + exec_elapsed_ms
    end_ms = steady_start_ms + steady_elapsed_ms
    workflow_elapsed_ms = end_ms - start_ms
    return (
        "schema=guest_workflow_timing_v3;clock=monotonic_mtime_ms;"
        f"entry={entry};handoff={handoff};init_phase_mask={phase_mask};"
        f"completion={completion};completion_phase_mask={completion_phase_mask};"
        f"start_ms={start_ms};ready_ms={ready_ms};"
        f"steady_start_ms={steady_start_ms};end_ms={end_ms};"
        f"setup_elapsed_ms={setup_elapsed_ms};exec_elapsed_ms={exec_elapsed_ms};"
        f"steady_elapsed_ms={steady_elapsed_ms};"
        f"workflow_elapsed_ms={workflow_elapsed_ms}\n"
    )


def _agentos_acceptance(challenge: str) -> str:
    values = seeded.derive_challenge(challenge)
    suffix = str(int(challenge[3:]))
    request_id = 1_000_000_000_000 + int(suffix) * 4
    followup_request_id = request_id + 1
    payload = f"wf:{suffix}"
    kernel_run_id = f"r{suffix}"
    target_physical = f"a{suffix}"
    return (
        "schema=agentos_task6_acceptance_v3;module_count=4;"
        f"workflow_id={values.workflow_id};workflow_run_id={values.run_id};"
        f"input_sha256={values.input_sha256};derived_sha256={values.derived_sha256};"
        "workflow_outputs=verified\n"
        "module=context;operation=context_snapshot;status=verified;"
        f"records=4;latest_sequence=10;request_id={request_id};tool_id=1;"
        f"record_sequence=9;record_hash=101;payload={payload};"
        f"result={payload};followup_sequence=10;followup_record_hash=102\n"
        "module=structured_tool;operation=agent_run_echo;status=verified;"
        f"request_id={request_id};tool_id=1;request_payload={payload};"
        f"arg0={request_id};arg1={followup_request_id};"
        "result_version=1;result_status=0;result_tool_id=1;"
        f"result_request_id={request_id};result_payload={payload};"
        f"result_value0={len(payload)};result_value1={request_id};"
        f"result_value2={followup_request_id};result_sequence=9\n"
        "module=metadata_query;operation=file_query_stage_index;status=verified;"
        f"project=lab-gene-x;workflow_id={values.workflow_id};"
        f"workflow_run_id={values.run_id};kernel_run_id={kernel_run_id};"
        f"input_sha256={values.input_sha256};derived_sha256={values.derived_sha256};"
        "stage=align;returned=1;used_index=1;plan=2;target_fid=101;"
        f"target_physical={target_physical};target_stage=align;"
        f"target_kind=artifact;target_status=ok;target_summary=input-{values.input_sha256}\n"
        "module=observation;operation=timeline_provenance_ledger;status=verified;"
        "timeline_records=4;provenance_edges=1;ledger_records=9;ledger_hash=999;"
        "edge_kind=1;edge_tool_id=1;edge_status=0;source_sequence=9;"
        "target_sequence=10;source_record_hash=101;target_record_hash=102;"
        f"request_id={request_id};workflow_id={values.workflow_id}\n"
    )


def _resource_mix(hash_value: int, value: int) -> int:
    for _ in range(8):
        hash_value ^= value & 0xFF
        hash_value = (
            hash_value * scenario.RESOURCE_STABILITY_FNV_PRIME
        ) & scenario.UINT64_MAX
        value >>= 8
    return hash_value


def _resource_nonce(challenge: str, workflow_index: int, mode: str) -> int:
    suffix = str(int(challenge[3:]))
    request_id = 1_000_000_000_000 + int(suffix) * 4
    mode_value = {"load": 1, "terminal": 2}[mode]
    hash_value = scenario.RESOURCE_STABILITY_FNV_OFFSET
    for value in (
        scenario.RESOURCE_STABILITY_REPORT_MAGIC,
        scenario.RESOURCE_STABILITY_REPORT_VERSION,
        request_id,
        workflow_index,
        mode_value,
    ):
        hash_value = _resource_mix(hash_value, value)
    return hash_value or 1


def _resource_report_guard(values: dict[str, object]) -> int:
    mode_value = {"load": 1, "terminal": 2}[str(values["mode"])]
    hash_value = scenario.RESOURCE_STABILITY_FNV_OFFSET
    fields = (
        scenario.RESOURCE_STABILITY_REPORT_MAGIC,
        scenario.RESOURCE_STABILITY_REPORT_VERSION,
        scenario.RESOURCE_STABILITY_REPORT_SIZE,
        int(values["workflow_index"]),
        mode_value,
        *(
            int(values[key])
            for key in scenario.RESOURCE_STABILITY_RECORD_PREFIX_KEYS[2:-1]
        ),
    )
    for value in fields:
        hash_value = _resource_mix(hash_value, value)
    return hash_value


def _resource_stability(challenge: str) -> str:
    suffix = str(int(challenge[3:]))
    lines = [
        "schema=agentos_resource_stability_v5;"
        "measurement_scope=post_cache_warmup_workflow_acceptance;"
        "timed_makespan_included=0;"
        "claim_scope=configured_global_counter_reclamation;"
        "configured_kind_coverage=measured_mask_only;"
        "account_coverage=self_identity_only;"
        "rate_budget_coverage=not_measured;"
        "global_leak_freedom=not_claimed;"
        f"challenge_suffix={suffix};"
        f"load_workflows={scenario.RESOURCE_STABILITY_LOAD_WORKFLOWS};"
        f"terminal_workflows={scenario.RESOURCE_STABILITY_TERMINAL_WORKFLOWS};"
        f"child_rounds_per_workflow={scenario.RESOURCE_STABILITY_CHILD_ROUNDS};"
        f"memory_pages_per_round={scenario.RESOURCE_STABILITY_MEMORY_PAGES};"
        f"file_objects_per_round={scenario.RESOURCE_STABILITY_FILE_OBJECTS};"
        f"metadata_ops_per_round={scenario.RESOURCE_STABILITY_METADATA_OPS};"
        "context_records_per_round="
        f"{scenario.RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND};"
        "sequence_bound_status=verified;"
        "status=verified"
    ]
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
    policy = [
        "record=global_policy",
        "measured_mask=255",
        "measured_mask_semantics=configured_global_resource_kind_counters_only",
        "snapshot_consistency=single_core_irq_coherent",
        "coverage=configured_global_kind_counters",
        "account_counter_coverage=not_measured",
        "rate_budget_coverage=not_measured",
        "growth_bound_semantics=per_class_positive_delta_sum",
        "decrease_semantics=reclamation_allowed",
        "free_pages_status=measured",
        "terminal_workflow_pair_bound=0",
    ]
    for kind in scenario.RESOURCE_STABILITY_RESOURCE_KINDS:
        policy.extend(
            (
                f"{kind}_status=measured",
                f"{kind}_capacity={capacities[kind]}",
                f"{kind}_per_workflow_growth_bound={scenario.RESOURCE_STABILITY_GROWTH_BOUNDS[kind]}",
                f"{kind}_terminal_growth_bound={scenario.RESOURCE_STABILITY_GROWTH_BOUNDS[kind]}",
            )
        )
    lines.append(";".join(policy))
    for index in range(scenario.RESOURCE_STABILITY_WORKFLOWS):
        load = index < scenario.RESOURCE_STABILITY_LOAD_WORKFLOWS
        rounds = scenario.RESOURCE_STABILITY_CHILD_ROUNDS if load else 0
        scope_id = 20 + index
        values = {
            "workflow_index": index,
            "mode": "load" if load else "terminal",
            "challenge_nonce": _resource_nonce(
                challenge, index, "load" if load else "terminal"
            ),
            "lifecycle_id": 100 + index,
            "lifecycle_generation": int(suffix) * 10 + index + 1,
            "scope_id": scope_id,
            "io_owner": 0x80000000 | scope_id,
            "resource_account_slot": 40 + index,
            "resource_account_reserved": 0,
            "resource_account_generation": int(suffix) * 20 + index + 1,
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
                rounds * scenario.RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND
            ),
            "final_context_records": (
                rounds * scenario.RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND
            ),
            "initial_completion_sequence": 1000 + index * 100,
            "final_completion_sequence": 1000 + index * 100 + rounds,
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
        values["report_guard"] = _resource_report_guard(values)
        for resource_index, kind in enumerate(
            scenario.RESOURCE_STABILITY_RESOURCE_KINDS
        ):
            ordinary_before = 10 + resource_index * 10
            reserved_before = 1 + resource_index
            growth = 0
            if load and kind in {"fs_block", "buffer_cache"}:
                growth = 2
            values[f"{kind}_ordinary_used_before"] = ordinary_before
            values[f"{kind}_ordinary_used_after"] = ordinary_before + growth
            values[f"{kind}_ordinary_pending_before"] = 0
            values[f"{kind}_ordinary_pending_after"] = 0
            values[f"{kind}_reserved_used_before"] = reserved_before
            values[f"{kind}_reserved_used_after"] = reserved_before
            values[f"{kind}_reserved_pending_before"] = 0
            values[f"{kind}_reserved_pending_after"] = 0
        lines.append(
            ";".join(f"{key}={values[key]}" for key in scenario.RESOURCE_STABILITY_RECORD_KEYS)
        )
    return "\n".join(lines) + "\n"


def _rewrite_workflow_timing(path: Path, **updates: object) -> None:
    fields = {}
    for field in path.read_text(encoding="ascii").strip().split(";"):
        key, value = field.split("=", 1)
        fields[key] = value
    fields.update({key: str(value) for key, value in updates.items()})
    path.write_text(
        ";".join(f"{key}={fields[key]}" for key in scenario.WORKFLOW_TIMING_KEYS)
        + "\n",
        encoding="ascii",
        newline="\n",
    )


def _rewrite_resource_workflow(
    path: Path, workflow_index: int, **updates: object
) -> None:
    lines = path.read_text(encoding="ascii").splitlines()
    line_index = workflow_index + 2
    fields = {}
    for field in lines[line_index].split(";"):
        key, value = field.split("=", 1)
        fields[key] = value
    fields.update({key: str(value) for key, value in updates.items()})
    if "report_guard" not in updates:
        fields["report_guard"] = str(_resource_report_guard(fields))
    lines[line_index] = ";".join(
        f"{key}={fields[key]}" for key in scenario.RESOURCE_STABILITY_RECORD_KEYS
    )
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def _resource_workflow_fields(path: Path, workflow_index: int) -> dict[str, str]:
    line = path.read_text(encoding="ascii").splitlines()[workflow_index + 2]
    return dict(field.split("=", 1) for field in line.split(";"))


def _write_target(
    boot: Path,
    target: str,
    programs: tuple[str, ...],
    roles: dict[str, str],
    boot_number: int,
    target_order: str | None,
    challenge: str,
    agentos_advantage_ms: int = 5,
) -> None:
    run_dir = boot / target
    state_dir = run_dir / "state-extracted"
    state_dir.mkdir(parents=True)
    files = _outcome_files(challenge)
    ledger, program_elapsed_ms_total = _ledger(
        target, programs, roles, boot_number, agentos_advantage_ms
    )
    workflow_elapsed_ms = program_elapsed_ms_total + 37
    files["rp_orch_timing"] = ledger
    files["rp_workflow_timing"] = _workflow_timing(
        target, program_elapsed_ms_total, boot_number
    )
    if target == "agentos":
        files[scenario.AGENTOS_ACCEPTANCE_FILE] = _agentos_acceptance(challenge)
        files[scenario.RESOURCE_STABILITY_FILE] = _resource_stability(challenge)
    for name, data in files.items():
        if isinstance(data, bytes):
            (state_dir / name).write_bytes(data)
        else:
            (state_dir / name).write_text(data, encoding="ascii", newline="\n")
    names = sorted(files)
    extract_summary = {
        "image": f"diagnostic/{target}/fs-copy.img",
        "scanned_rp_entries": len(names),
        "extracted_state_files": len(names),
        "skipped_binary_entries": 0,
        "available_scope_ids": [] if target == "plain" else [3],
        "selected_scope_id": None if target == "plain" else 3,
        "scope_layout": "legacy" if target == "plain" else "selected",
        "files": names,
        "status": "ready",
    }
    (state_dir / "extract-summary.json").write_text(
        json.dumps(extract_summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    marker = "rp_orch: passed" if target == "plain" else "rp_agentos_orch: passed"
    (run_dir / "ucore-run.log").write_text(
        f"boot {boot_number} target {target}\n{marker}\n",
        encoding="utf-8",
        newline="\n",
    )
    actions = seeded.seeded_actions(challenge)
    actions_text = json.dumps(actions, indent=2, ensure_ascii=False) + "\n"
    (run_dir / "actions.json").write_text(
        actions_text, encoding="utf-8", newline="\n"
    )
    challenge_receipt = seeded.challenge_input_receipt(
        challenge,
        actions,
        hashlib.sha256(actions_text.encode("utf-8")).hexdigest(),
    )
    receipt_text = json.dumps(
        challenge_receipt, indent=2, ensure_ascii=False, allow_nan=False
    ) + "\n"
    (run_dir / seeded.CHALLENGE_RECEIPT_NAME).write_text(
        receipt_text, encoding="utf-8", newline="\n"
    )
    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir()
    runtime_artifacts = {}
    for name in ("kernel", "image_input", "image_final"):
        data = f"{target}:{boot_number}:{challenge}:{name}\n".encode("ascii")
        (artifact_dir / name).write_bytes(data)
        runtime_artifacts[name] = {
            "path": f"artifacts/{name}",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    run_summary = {
        "commands": ["fixture command is diagnostic only"],
        "returncode": 0,
        "build_returncode": 0,
        "guest_returncode": 0,
        "guest_raw_returncode": 0,
        "marker_seen": True,
        "failure_seen": False,
        "failure_line": "",
        "failure_reason": "",
        "failure_phase": "",
        "timed_out": False,
        "runner_terminated": False,
        "runner_signals": [],
        "output_eof": True,
        "idle_notices": 0,
        "elapsed_seconds": (workflow_elapsed_ms + 250) / 1000.0,
        "embedded_action_records": 44,
        "extracted_state_files": len(names),
        "extract_status": "ready",
        "runtime_artifacts": runtime_artifacts,
        "source_commit": SOURCE_COMMIT,
        "source_tree_clean": True,
        "target_identity": target,
        "chapter": "platform_seeded" if target == "plain" else "platform_agentos",
        "init_proc": "rp_seed_orch" if target == "plain" else "rp_agentos_orch",
        "passed": True,
        "status": "ready",
        "challenge": challenge,
        "challenge_receipt": seeded.CHALLENGE_RECEIPT_NAME,
        "challenge_receipt_sha256": hashlib.sha256(
            receipt_text.encode("utf-8")
        ).hexdigest(),
        "challenge_binding_sha256": challenge_receipt["receipt_sha256"],
    }
    if target_order is not None:
        run_summary["target_order"] = target_order
    (run_dir / "ucore-run-summary.json").write_text(
        json.dumps(run_summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


class ScenarioFixture:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="evaluation-scenario-")
        self.root = Path(self._temporary.name)
        self.programs, self.roles = scenario.read_expected_programs()

    def close(self) -> None:
        self._temporary.cleanup()

    def boot(
        self,
        number: int,
        target_order: str | None = None,
        challenge: str | None = None,
        agentos_advantage_ms: int = 5,
    ) -> Path:
        boot = self.root / f"boot-{number:04d}"
        resolved_challenge = challenge or _challenge(number)
        _write_target(
            boot,
            "plain",
            self.programs,
            self.roles,
            number,
            target_order,
            resolved_challenge,
            agentos_advantage_ms,
        )
        _write_target(
            boot,
            "agentos",
            self.programs,
            self.roles,
            number,
            target_order,
            resolved_challenge,
            agentos_advantage_ms,
        )
        return boot


class EvaluationScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ScenarioFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def collect(
        self, boots: list[Path], target_orders: list[str] | None = None
    ) -> dict[str, object]:
        return scenario.collect_scenario(
            boots,
            source_commit=SOURCE_COMMIT,
            run_id="evaluation-run-1",
            target_orders=target_orders,
        )

    def test_single_boot_is_real_but_inconclusive(self) -> None:
        report = self.collect([self.fixture.boot(1, "AB")])

        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["summary"]["independent_boots"], 1)
        self.assertEqual(report["summary"]["paired_success_rate"], 1.0)
        self.assertEqual(report["summary"]["target_order_counts"], {"AB": 1, "BA": 0})
        source_comparability = report["summary"]["source_comparability"]
        self.assertEqual(source_comparability["expected_programs"], 70)
        self.assertEqual(source_comparability["same_source_programs"], 28)
        self.assertEqual(source_comparability["platform_specific_programs"], 42)
        sample = report["samples"][0]
        self.assertEqual(sample["binding"]["source_commit"], SOURCE_COMMIT)
        self.assertEqual(sample["binding"]["boot_order"], 1)
        self.assertEqual(sample["binding"]["target_order"], "AB")
        for target in ("plain", "agentos"):
            receipt = sample["targets"][target]["raw_source_receipt"]
            self.assertEqual(receipt["schema"], "scenario-raw-source-receipt-v1")
            self.assertEqual(
                receipt["state_inventory"]["file_count"],
                9 if target == "plain" else 11,
            )
            self.assertEqual(
                receipt["workflow_timing"]["path"],
                "state-extracted/rp_workflow_timing",
            )
            self.assertEqual(
                receipt["challenge_source"]["challenge"], _challenge(1)
            )
            self.assertEqual(len(receipt["qemu_log"]["sha256"]), 64)
            self.assertEqual(len(receipt["run_summary"]["sha256"]), 64)
            source_receipt = receipt["program_source_comparability"]
            self.assertEqual(
                source_receipt["sha256"],
                source_comparability["receipt_sha256"],
            )
            self.assertEqual(len(source_receipt["programs"]), 70)
            provenance = receipt["artifact_provenance"]
            self.assertEqual(provenance["schema"], "task6-artifact-provenance-v1")
            self.assertEqual(provenance["challenge"], _challenge(1))
            self.assertEqual(
                provenance["input"]["sha256"],
                seeded.derive_challenge(_challenge(1)).input_sha256,
            )
        plain_acceptance = sample["targets"]["plain"]["raw_source_receipt"][
            "functional_acceptance"
        ]
        self.assertEqual(
            plain_acceptance, {"required": False, "status": "not_applicable"}
        )
        agentos_receipt = sample["targets"]["agentos"]["raw_source_receipt"]
        acceptance = agentos_receipt["functional_acceptance"]
        self.assertEqual(acceptance["status"], "verified")
        self.assertEqual(
            acceptance["acceptance"]["required_modules"],
            list(scenario.REQUIRED_AGENTOS_MODULES),
        )
        self.assertEqual(
            acceptance["binding"]["challenge"], sample["binding"]["challenge"]
        )
        self.assertEqual(
            acceptance["binding"]["run_summary_sha256"],
            agentos_receipt["run_summary"]["sha256"],
        )
        stability = agentos_receipt["resource_stability"]
        self.assertEqual(stability["status"], "verified")
        self.assertFalse(stability["acceptance"]["timed_makespan_included"])
        self.assertEqual(
            stability["acceptance"]["measurement_scope"],
            scenario.RESOURCE_STABILITY_MEASUREMENT_SCOPE,
        )
        self.assertEqual(
            len(stability["acceptance"]["workflows"]),
            scenario.RESOURCE_STABILITY_WORKFLOWS,
        )

    def test_unknown_target_file_cannot_be_promoted_to_evidence(self) -> None:
        boot = self.fixture.boot(1, "AB")
        (boot / "plain" / "temporary-output.log").write_text(
            "unplanned\n", encoding="ascii"
        )
        report = self.collect([boot])
        self.assertEqual(report["status"], "failed")
        self.assertIn("unknown or temporary file", report["errors"][0])

    def test_artifact_provenance_rejects_changed_input_bytes(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = (
            boot
            / "plain"
            / "state-extracted"
            / seeded.TASK6_ARTIFACT_INPUT_STORAGE
        )
        rows = path.read_bytes().splitlines(keepends=True)
        name, count = rows[1].rstrip(b"\n").rsplit(b",", 1)
        rows[1] = name + b"," + str(int(count) + 1).encode("ascii") + b"\n"
        path.write_bytes(b"".join(rows))

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("input artifact bytes", report["errors"][0])

    def test_artifact_provenance_rejects_self_consistent_forged_labels(self) -> None:
        boot = self.fixture.boot(1, "AB")
        state = boot / "plain" / "state-extracted"
        output_path = state / seeded.TASK6_ARTIFACT_OUTPUT_STORAGE
        original = output_path.read_bytes()
        rows = original.splitlines(keepends=True)
        name, value = rows[1].rstrip(b"\n").rsplit(b",", 1)
        rows[1] = name + b"," + str(int(value) + 1).encode("ascii") + b"\n"
        forged = b"".join(rows)
        self.assertNotEqual(forged, original)
        output_path.write_bytes(forged)
        artifact_path = state / "rp_artifact"
        artifact = artifact_path.read_text(encoding="ascii")
        expected = seeded.derive_challenge(_challenge(1))
        forged_sha = hashlib.sha256(forged).hexdigest()
        forged_fnv = seeded.task6_fnv64(forged)
        artifact = artifact.replace(expected.derived_sha256, forged_sha).replace(
            f"output_fnv64={expected.derived_fnv64}",
            f"output_fnv64={forged_fnv}",
        )
        artifact_path.write_text(artifact, encoding="ascii", newline="\n")

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("registered transformation", report["errors"][0])

    def test_artifact_provenance_rejects_output_fixed_to_another_challenge(self) -> None:
        challenge = _challenge(2)
        boot = self.fixture.boot(2, "BA")
        state = boot / "plain" / "state-extracted"
        output_path = state / seeded.TASK6_ARTIFACT_OUTPUT_STORAGE
        fixed = seeded.task6_artifact_payloads(_challenge(1))[1]
        output_path.write_bytes(fixed)

        expected = seeded.derive_challenge(challenge)
        fixed_sha = hashlib.sha256(fixed).hexdigest()
        fixed_fnv = seeded.task6_fnv64(fixed)
        artifact_path = state / "rp_artifact"
        artifact = artifact_path.read_text(encoding="ascii")
        artifact = artifact.replace(expected.derived_sha256, fixed_sha)
        artifact = artifact.replace(
            f"output_bytes={expected.derived_bytes}",
            f"output_bytes={len(fixed)}",
        ).replace(
            f"output_fnv64={expected.derived_fnv64}",
            f"output_fnv64={fixed_fnv}",
        )
        artifact_path.write_text(artifact, encoding="ascii", newline="\n")

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("registered transformation", report["errors"][0])

    def test_makespan_uses_guest_workflow_record_not_program_sum(self) -> None:
        report = self.collect([self.fixture.boot(1, "AB")])

        for target in ("plain", "agentos"):
            measurement = report["samples"][0]["targets"][target]
            program_total = sum(row["elapsed_ms"] for row in measurement["programs"])
            self.assertEqual(measurement["makespan_ms"], program_total + 37)
            self.assertGreater(measurement["makespan_ms"], program_total)
            timing = measurement["raw_source_receipt"]["workflow_timing"][
                "measurement"
            ]
            self.assertEqual(timing["workflow_elapsed_ms"], program_total + 37)
            self.assertEqual(timing["steady_elapsed_ms"], program_total + 20)
        agentos_timing = report["samples"][0]["targets"]["agentos"][
            "raw_source_receipt"
        ]["workflow_timing"]["measurement"]
        self.assertEqual(agentos_timing["entry"], "rp_agentos_orch")
        self.assertEqual(agentos_timing["handoff"], "delegated_pipe_v1")
        self.assertGreater(agentos_timing["setup_elapsed_ms"], 0)

    def test_guest_sources_keep_cleanup_and_parent_validation_outside_blind_spots(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        plain = (root / "baseline_ucore/user/src/rp_seed_orch.c").read_text(
            encoding="utf-8"
        )
        plain_main = plain[plain.index("int main(void)") :]
        self.assertLess(
            plain_main.index("release_self_image();"),
            plain_main.index("workflow_start = get_mtime();"),
        )
        self.assertLess(
            plain_main.index("record_workflow_timing(workflow_start, steady_start)"),
            plain_main.index("release_program_images();"),
        )

        agentos = (root / "user/src/rp_agentos_orch.c").read_text(
            encoding="utf-8"
        )
        kernel_state_writes = re.findall(
            r'rp_(?:write|append)_file\(\s*"rp_agentos_kernel"', agentos
        )
        self.assertEqual(kernel_state_writes, ['rp_write_file("rp_agentos_kernel"'])
        agentos_main = agentos[agentos.index("int main(void)") :]
        wait_at = agentos_main.index("waitpid(pid, &code)")
        completion_at = agentos_main.index("read_workflow_completion(")
        validation_at = agentos_main.index('rp_file_contains("rp_agentos_kernel"')
        end_at = agentos_main.index("uint64 workflow_end = get_mtime();")
        timing_at = agentos_main.index("record_workflow_timing(&completion")
        stability_at = agentos_main.index("run_resource_stability_acceptance()")
        self.assertLess(wait_at, completion_at)
        self.assertLess(completion_at, validation_at)
        self.assertLess(validation_at, end_at)
        self.assertLess(end_at, timing_at)
        self.assertLess(timing_at, stability_at)

        agentos_runner = agentos[
            agentos.index("static int run_research_orchestrator") :
            agentos.index("int main(void)")
        ]
        workflow_wait_at = agentos_runner.index(
            "waitpid(workflow_pid, &workflow_code)"
        )
        output_check_at = agentos_runner.index(
            "load_challenge_workflow_outputs("
        )
        metadata_seed_at = agentos_runner.index(
            "seed_challenge_research_metadata(&orch_workflow)"
        )
        tool_at = agentos_runner.index("make_echo(&orch_echo_op")
        context_at = agentos_runner.index("context_snapshot(&orch_header")
        observation_at = agentos_runner.index("agent_timeline_snapshot(")
        metadata_query_at = agentos_runner.index(
            "verify_kernel_dependency_path(&orch_workflow)"
        )
        receipt_at = agentos_runner.index("record_functional_acceptance(")
        self.assertLess(workflow_wait_at, output_check_at)
        self.assertLess(output_check_at, metadata_seed_at)
        self.assertLess(metadata_seed_at, tool_at)
        self.assertLess(tool_at, context_at)
        self.assertLess(context_at, observation_at)
        self.assertLess(observation_at, metadata_query_at)
        self.assertLess(metadata_query_at, receipt_at)

        output_parser = agentos[
            agentos.index("static int output_record_value") :
            agentos.index("static void make_echo")
        ]
        self.assertIn("record_count == 1 && field_count == 1", output_parser)
        for path, anchor in (
            ("rp_input", "host_action_rerun_id="),
            ("rp_stage_dag", "host_workflow_id="),
            ("rp_stage_state", "host_workflow_run_id="),
            ("rp_artifact", "host_artifact_input="),
            ("rp_artifact", "host_artifact_derive="),
            ("rp_runner", "host_action_workflow="),
            ("rp_runner", "host_action_rerun="),
        ):
            self.assertIn(f'"{path}", "{anchor}"', output_parser)

    def test_task6_protocol_keeps_legacy_artifact_actions_independent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "user/src/rp_artifact_ops.c",
            "baseline_ucore/user/src/rp_artifact_ops.c",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            main = source[source.index("int main(void)") :]
            self.assertIn(
                "task6 && (!has_derive || !apply_task6_artifact_actions())",
                main,
            )
            self.assertIn(
                "!task6 && has_input && !append_legacy_artifact_input_action()",
                main,
            )
            self.assertIn(
                "!task6 && has_derive && !append_legacy_artifact_derive_action()",
                main,
            )
            self.assertNotIn(
                'rp_host_seed_has("kind=artifact_input") !=',
                main,
            )

    def test_resource_stability_rejects_nonfresh_replacement_workflow(self) -> None:
        boot = self.fixture.boot(80, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(path, 0, initial_leased=1)

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("not challenge-bound, fresh, and quiescent", report["errors"][0])

    def test_resource_stability_rejects_replayed_lifecycle_key(self) -> None:
        boot = self.fixture.boot(81, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        lines = path.read_text(encoding="ascii").splitlines()
        first_id = re.search(r"lifecycle_id=([0-9]+)", lines[2]).group(1)
        first_generation = re.search(
            r"lifecycle_generation=([0-9]+)", lines[2]
        ).group(1)
        _rewrite_resource_workflow(
            path,
            1,
            lifecycle_id=first_id,
            lifecycle_generation=first_generation,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("not challenge-bound, fresh, and quiescent", report["errors"][0])

    def test_resource_stability_terminal_probe_cannot_claim_work(self) -> None:
        boot = self.fixture.boot(82, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(
            path, scenario.RESOURCE_STABILITY_WORKFLOWS - 1, process_rounds=1
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("not challenge-bound, fresh, and quiescent", report["errors"][0])

    def test_resource_stability_allows_terminal_settlement_io(self) -> None:
        boot = self.fixture.boot(87, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        terminal = scenario.RESOURCE_STABILITY_WORKFLOWS - 1
        fields = _resource_workflow_fields(path, terminal)
        _rewrite_resource_workflow(
            path,
            terminal,
            final_completion_sequence=(
                int(fields["initial_completion_sequence"]) + 314
            ),
            final_cache_resident=int(fields["initial_cache_resident"]) + 1,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "inconclusive")
        receipt = report["samples"][0]["targets"]["agentos"][
            "raw_source_receipt"
        ]["resource_stability"]
        self.assertEqual(receipt["status"], "verified")
        terminal_record = receipt["acceptance"]["workflows"][-1]
        self.assertEqual(
            terminal_record["final_completion_sequence"]
            - terminal_record["initial_completion_sequence"],
            314,
        )
        self.assertEqual(
            terminal_record["final_cache_resident"]
            - terminal_record["initial_cache_resident"],
            1,
        )

    def test_resource_stability_rejects_observation_delta_drift(self) -> None:
        expected = (
            scenario.RESOURCE_STABILITY_CHILD_ROUNDS
            * scenario.RESOURCE_STABILITY_CONTEXT_RECORDS_PER_ROUND
        )
        cases = (
            (83, "final_agent_calls", expected - 1),
            (84, "final_agent_calls", expected + 1),
            (85, "final_context_records", expected - 1),
            (86, "final_context_records", expected + 1),
        )
        for boot_number, field, observed in cases:
            with self.subTest(field=field, observed=observed):
                boot = self.fixture.boot(boot_number, "AB")
                path = (
                    boot
                    / "agentos"
                    / "state-extracted"
                    / scenario.RESOURCE_STABILITY_FILE
                )
                _rewrite_resource_workflow(path, 0, **{field: observed})

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn(
                    "not challenge-bound, fresh, and quiescent",
                    report["errors"][0],
                )

    def test_resource_stability_rejects_global_growth_above_bound(self) -> None:
        boot = self.fixture.boot(85, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(
            path,
            0,
            fs_block_ordinary_used_before=40,
            fs_block_ordinary_used_after=(
                40 + scenario.RESOURCE_STABILITY_GROWTH_BOUNDS["fs_block"] + 1
            ),
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("global resource delta exceeds", report["errors"][0])

    def test_resource_stability_rejects_pending_global_resource(self) -> None:
        boot = self.fixture.boot(86, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(
            path, 0, file_object_ordinary_pending_after=1
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("global resource delta exceeds", report["errors"][0])

    def test_resource_stability_rejects_unrecovered_free_pages(self) -> None:
        boot = self.fixture.boot(87, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(path, 0, ordinary_free_pages_after=19999)

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("not challenge-bound, fresh, and quiescent", report["errors"][0])

    def test_resource_stability_unmeasured_kind_cannot_pass(self) -> None:
        boot = self.fixture.boot(88, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        lines = path.read_text(encoding="ascii").splitlines()
        lines[0] = lines[0].replace(";status=verified", ";status=partial", 1)
        lines[1] = lines[1].replace("measured_mask=255", "measured_mask=254")
        lines[1] = lines[1].replace(
            "process_status=measured;process_capacity=128",
            "process_status=not_measured;process_capacity=0",
        )
        path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
        for workflow_index in range(scenario.RESOURCE_STABILITY_WORKFLOWS):
            _rewrite_resource_workflow(
                path,
                workflow_index,
                process_ordinary_used_before=0,
                process_ordinary_used_after=0,
                process_ordinary_pending_before=0,
                process_ordinary_pending_after=0,
                process_reserved_used_before=0,
                process_reserved_used_after=0,
                process_reserved_pending_before=0,
                process_reserved_pending_after=0,
                per_workflow_bound_status="not_measured",
            )

        report = self.collect([boot])

        self.assertNotEqual(
            report["summary"]["resource_stability"]["status"], "passed"
        )
        self.assertEqual(
            report["summary"]["resource_stability"]["status"], "partial"
        )
        self.assertEqual(
            report["summary"]["resource_stability"]["global_observation"]
            ["resources"][0]["status"],
            "not_measured",
        )

    def test_resource_stability_is_challenge_bound_across_boots(self) -> None:
        first = self.fixture.boot(83, "AB")
        second = self.fixture.boot(84, "BA")
        first_path = (
            first
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        second_path = (
            second
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        second_path.write_bytes(first_path.read_bytes())

        report = self.collect([first, second])

        self.assertEqual(report["status"], "failed")
        self.assertIn("registered workload", report["errors"][0])

    def test_resource_stability_rejects_nonce_transplant_between_boots(self) -> None:
        first = self.fixture.boot(89, "AB")
        second = self.fixture.boot(90, "BA")
        first_path = (
            first
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        second_path = (
            second
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        transplanted_nonce = _resource_workflow_fields(first_path, 0)[
            "challenge_nonce"
        ]
        _rewrite_resource_workflow(
            second_path, 0, challenge_nonce=transplanted_nonce
        )

        report = self.collect([second])

        self.assertEqual(report["status"], "failed")
        self.assertIn("not challenge-bound", report["errors"][0])

    def test_resource_stability_rejects_duplicate_scope_and_io_identity(self) -> None:
        boot = self.fixture.boot(91, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        first = _resource_workflow_fields(path, 0)
        _rewrite_resource_workflow(
            path,
            1,
            scope_id=first["scope_id"],
            io_owner=first["io_owner"],
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("fresh, and quiescent", report["errors"][0])

    def test_resource_stability_rejects_duplicate_account_handle(self) -> None:
        boot = self.fixture.boot(92, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        first = _resource_workflow_fields(path, 0)
        _rewrite_resource_workflow(
            path,
            1,
            resource_account_slot=first["resource_account_slot"],
            resource_account_generation=first["resource_account_generation"],
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("fresh, and quiescent", report["errors"][0])

    def test_resource_stability_rejects_category_redistribution(self) -> None:
        boot = self.fixture.boot(93, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(
            path,
            0,
            fs_block_ordinary_used_before=40,
            fs_block_ordinary_used_after=73,
            fs_block_reserved_used_before=40,
            fs_block_reserved_used_after=7,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("configured global resource delta exceeds", report["errors"][0])

    def test_resource_stability_allows_terminal_reclamation(self) -> None:
        boot = self.fixture.boot(98, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        _rewrite_resource_workflow(
            path,
            scenario.RESOURCE_STABILITY_WORKFLOWS - 1,
            buffer_cache_reserved_used_before=9,
            buffer_cache_reserved_used_after=6,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "inconclusive")
        receipt = report["samples"][0]["targets"]["agentos"][
            "raw_source_receipt"
        ]["resource_stability"]
        self.assertEqual(receipt["status"], "verified")
        terminal_record = receipt["acceptance"]["workflows"][-1]
        terminal_growth = max(
            terminal_record["buffer_cache_ordinary_used_after"]
            - terminal_record["buffer_cache_ordinary_used_before"],
            0,
        ) + max(
            terminal_record["buffer_cache_reserved_used_after"]
            - terminal_record["buffer_cache_reserved_used_before"],
            0,
        )
        exact_terminal_recovery = (
            terminal_record["buffer_cache_ordinary_used_before"]
            == terminal_record["buffer_cache_ordinary_used_after"]
            and terminal_record["buffer_cache_reserved_used_before"]
            == terminal_record["buffer_cache_reserved_used_after"]
        )
        self.assertEqual(terminal_growth, 0)
        self.assertFalse(exact_terminal_recovery)

    def test_resource_stability_rejects_cumulative_terminal_growth(self) -> None:
        boot = self.fixture.boot(94, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        pairs = ((40, 48), (48, 56), (56, 64), (64, 72), (80, 80))
        for workflow_index, (before, after) in enumerate(pairs):
            _rewrite_resource_workflow(
                path,
                workflow_index,
                fs_block_ordinary_used_before=before,
                fs_block_ordinary_used_after=after,
            )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("terminal growth exceeds", report["errors"][0])

    def test_resource_stability_terminal_equality_is_not_a_plateau(self) -> None:
        boot = self.fixture.boot(97, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        pairs = ((40, 42), (42, 44), (44, 46), (46, 48), (48, 48))
        for workflow_index, (before, after) in enumerate(pairs):
            _rewrite_resource_workflow(
                path,
                workflow_index,
                fs_block_ordinary_used_before=before,
                fs_block_ordinary_used_after=after,
            )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("lacks plateau or reclamation", report["errors"][0])

    def test_resource_stability_rejects_broadened_counter_coverage(self) -> None:
        boot = self.fixture.boot(95, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        path.write_text(
            path.read_text(encoding="ascii").replace(
                "measured_mask_semantics=configured_global_resource_kind_counters_only",
                "measured_mask_semantics=global_account_and_rate_counters",
                1,
            ),
            encoding="ascii",
            newline="\n",
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("global policy is invalid", report["errors"][0])

    def test_resource_stability_rejects_report_guard_tampering(self) -> None:
        boot = self.fixture.boot(96, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.RESOURCE_STABILITY_FILE
        )
        fields = _resource_workflow_fields(path, 0)
        _rewrite_resource_workflow(
            path, 0, report_guard=int(fields["report_guard"]) + 1
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("not challenge-bound", report["errors"][0])

    def test_seven_balanced_boots_are_supported(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [self.fixture.boot(index, order) for index, order in enumerate(orders, 1)]
        report = self.collect(boots)

        self.assertEqual(report["status"], "supported")
        self.assertEqual(report["summary"]["target_order_counts"], {"AB": 4, "BA": 3})
        self.assertTrue(report["summary"]["target_order_balanced"])
        self.assertEqual(report["summary"]["unique_challenges"], 7)
        functional = report["summary"]["functional_acceptance"]
        self.assertEqual(functional["status"], "passed")
        self.assertEqual(functional["verified_boots"], 7)
        self.assertEqual(
            functional["required_modules"], list(scenario.REQUIRED_AGENTOS_MODULES)
        )
        self.assertEqual(len(functional["boot_receipts"]), 7)
        stability = report["summary"]["resource_stability"]
        self.assertEqual(stability["status"], "passed")
        self.assertEqual(stability["verified_boots"], 7)
        self.assertEqual(
            stability["interpretation"]["timing_relationship"],
            "excluded_from_task6_makespan",
        )
        self.assertEqual(
            stability["interpretation"]["global_leak_freedom"],
            "not_claimed",
        )
        self.assertEqual(len(stability["boot_receipts"]), 7)
        paired = report["summary"]["paired_improvement"]
        expected_improvement = 5 * len(self.fixture.programs)
        self.assertEqual(paired["direction"], "plain_minus_agentos_positive_is_better")
        self.assertTrue(paired["lower_is_better"])
        self.assertEqual(paired["n"], 7)
        self.assertEqual(paired["median"], expected_improvement)
        self.assertEqual(paired["ci_low"], expected_improvement)
        self.assertEqual(paired["ci_high"], expected_improvement)
        self.assertGreater(paired["relative_ci_low"], 0)
        self.assertGreater(paired["relative_ci_high"], 0)
        self.assertEqual(paired["sign_test"]["wins"], 7)
        self.assertEqual(paired["sign_test"]["losses"], 0)
        self.assertEqual(paired["sign_test"]["ties"], 0)
        self.assertEqual(paired["sign_test"]["numerator"], 1)
        self.assertEqual(paired["sign_test"]["denominator"], 128)
        self.assertEqual(paired["mcid_sign_test"]["wins"], 7)
        self.assertEqual(paired["mcid_sign_test"]["non_wins"], 0)
        self.assertEqual(paired["mcid_sign_test"]["n"], 7)
        self.assertEqual(paired["mcid_sign_test"]["numerator"], 1)
        self.assertEqual(paired["mcid_sign_test"]["denominator"], 128)
        self.assertEqual(paired["regression_mcid_sign_test"]["losses"], 0)
        self.assertEqual(paired["regression_mcid_sign_test"]["non_losses"], 7)
        self.assertEqual(paired["inference"]["alpha"], 0.05)
        self.assertEqual(
            paired["inference"]["multiplicity"],
            "two_directions_within_task6_scenario",
        )
        self.assertEqual(paired["inference"]["directional_hypothesis_count"], 2)
        self.assertEqual(paired["inference"]["correction"], "Bonferroni")
        self.assertEqual(paired["inference"]["per_direction_alpha"], 0.025)
        self.assertEqual(paired["interpretation"]["design"], "full-stack")
        self.assertEqual(
            paired["interpretation"]["causal_attribution"],
            "non-single-mechanism",
        )
        self.assertEqual(
            paired["interpretation"]["host_page_cache"], "uncontrolled"
        )
        self.assertEqual(paired["bootstrap"]["repetitions"], 2_000)
        self.assertEqual(paired["bootstrap"]["role"], "descriptive_only")
        self.assertEqual(len(paired["bootstrap"]["seed_sha256"]), 64)
        self.assertEqual(
            paired["claim_gate"]["minimum_relative_improvement_percent"], 5.0
        )
        self.assertEqual(len(paired["samples"]), 7)
        for sample in paired["samples"]:
            self.assertEqual(sample["improvement_ms"], expected_improvement)
            self.assertGreater(sample["relative_improvement_percent"], 0)
        for target in ("plain", "agentos"):
            metrics = report["summary"]["targets"][target]
            self.assertIn("p50", metrics["makespan_ms"])
            self.assertIn("p95", metrics["makespan_ms"])
            self.assertEqual(
                set(metrics["programs"]), set(self.fixture.programs)
            )

    def test_balanced_boots_without_positive_effect_are_inconclusive(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(index, order, agentos_advantage_ms=0)
            for index, order in enumerate(orders, 1)
        ]

        report = self.collect(boots)

        self.assertEqual(report["status"], "inconclusive")
        paired = report["summary"]["paired_improvement"]
        self.assertEqual(paired["median"], 0)
        self.assertEqual(paired["ci_low"], 0)
        self.assertEqual(paired["relative_ci_low"], 0.0)
        self.assertEqual(paired["sign_test"]["ties"], 7)
        self.assertEqual(paired["sign_test"]["p_value"], 1.0)
        self.assertEqual(paired["mcid_sign_test"]["wins"], 0)
        self.assertEqual(paired["mcid_sign_test"]["non_wins"], 7)
        self.assertEqual(paired["mcid_sign_test"]["n"], 7)
        self.assertEqual(paired["mcid_sign_test"]["p_value"], 1.0)
        self.assertEqual(paired["regression_mcid_sign_test"]["losses"], 0)
        self.assertEqual(paired["regression_mcid_sign_test"]["non_losses"], 7)

    def test_balanced_material_regression_is_not_softened_to_inconclusive(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(index, order, agentos_advantage_ms=-5)
            for index, order in enumerate(orders, 1)
        ]

        report = self.collect(boots)

        self.assertEqual(report["status"], "regressed")
        paired = report["summary"]["paired_improvement"]
        self.assertLess(paired["median"], 0)
        self.assertLess(paired["relative_median_percent"], 0)
        self.assertEqual(paired["sign_test"]["wins"], 0)
        self.assertEqual(paired["sign_test"]["losses"], 7)
        self.assertEqual(paired["sign_test"]["ties"], 0)
        self.assertEqual(paired["mcid_sign_test"]["wins"], 0)
        reverse = paired["regression_mcid_sign_test"]
        self.assertEqual(reverse["losses"], 7)
        self.assertEqual(reverse["non_losses"], 0)
        self.assertEqual(reverse["numerator"], 1)
        self.assertEqual(reverse["denominator"], 128)
        self.assertLessEqual(
            reverse["p_value"], paired["inference"]["per_direction_alpha"]
        )

    def test_negative_but_sub_mcid_effect_remains_inconclusive(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(index, order, agentos_advantage_ms=-1)
            for index, order in enumerate(orders, 1)
        ]

        report = self.collect(boots)

        self.assertEqual(report["status"], "inconclusive")
        paired = report["summary"]["paired_improvement"]
        self.assertEqual(paired["sign_test"]["losses"], 7)
        self.assertEqual(paired["regression_mcid_sign_test"]["losses"], 0)
        self.assertEqual(paired["regression_mcid_sign_test"]["p_value"], 1.0)

    def test_tampered_reverse_mcid_count_cannot_forge_regressed(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(index, order, agentos_advantage_ms=-5)
            for index, order in enumerate(orders, 1)
        ]
        report = self.collect(boots)
        summary = report["summary"]
        summary["paired_improvement"]["regression_mcid_sign_test"]["losses"] = 6
        summary["paired_improvement"]["regression_mcid_sign_test"]["non_losses"] = 1

        with self.assertRaisesRegex(
            scenario.ScenarioEvidenceError, "directional MCID statistics"
        ):
            scenario._classify_claim(summary)

    def test_tampered_paired_sample_count_fails_instead_of_becoming_inconclusive(
        self,
    ) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        summary = self.collect(
            [self.fixture.boot(index, order) for index, order in enumerate(orders, 1)]
        )["summary"]
        summary["paired_improvement"]["n"] = 6

        with self.assertRaisesRegex(
            scenario.ScenarioEvidenceError, "sample count differs from samples"
        ):
            scenario._classify_claim(summary)

    def test_statistically_positive_but_sub_mcid_effect_is_inconclusive(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(index, order, agentos_advantage_ms=1)
            for index, order in enumerate(orders, 1)
        ]

        report = self.collect(boots)

        paired = report["summary"]["paired_improvement"]
        self.assertEqual(report["status"], "inconclusive")
        self.assertGreater(paired["ci_low"], 0)
        self.assertLess(
            paired["relative_ci_low"],
            paired["claim_gate"]["minimum_relative_improvement_percent"],
        )
        self.assertEqual(paired["sign_test"]["wins"], 7)
        self.assertEqual(paired["mcid_sign_test"]["wins"], 0)
        self.assertEqual(paired["mcid_sign_test"]["non_wins"], 7)

    def test_exact_sign_test_gate_rejects_six_wins_and_one_loss(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(
                index,
                order,
                agentos_advantage_ms=5 if index < 7 else -5,
            )
            for index, order in enumerate(orders, 1)
        ]

        report = self.collect(boots)

        self.assertEqual(report["status"], "inconclusive")
        paired = report["summary"]["paired_improvement"]
        self.assertGreater(paired["ci_low"], 0)
        self.assertGreater(paired["relative_ci_low"], 0)
        self.assertEqual(paired["sign_test"]["wins"], 6)
        self.assertEqual(paired["sign_test"]["losses"], 1)
        self.assertEqual(paired["sign_test"]["numerator"], 1)
        self.assertEqual(paired["sign_test"]["denominator"], 16)
        self.assertGreater(paired["sign_test"]["p_value"], 0.05)

    def test_joint_mcid_gate_rejects_six_of_seven_when_old_gate_passes(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [
            self.fixture.boot(
                index,
                order,
                agentos_advantage_ms=5 if index < 7 else 2,
            )
            for index, order in enumerate(orders, 1)
        ]

        report = self.collect(boots)

        self.assertEqual(report["status"], "inconclusive")
        paired = report["summary"]["paired_improvement"]
        self.assertGreaterEqual(paired["ci_low"], scenario.MIN_ABSOLUTE_IMPROVEMENT_MS)
        self.assertGreaterEqual(
            paired["relative_ci_low"], scenario.MIN_RELATIVE_IMPROVEMENT_PERCENT
        )
        self.assertEqual(paired["sign_test"]["wins"], 7)
        self.assertEqual(paired["sign_test"]["p_value"], 1 / 128)
        self.assertEqual(paired["mcid_sign_test"]["wins"], 6)
        self.assertEqual(paired["mcid_sign_test"]["non_wins"], 1)
        self.assertEqual(paired["mcid_sign_test"]["n"], 7)
        self.assertEqual(paired["mcid_sign_test"]["numerator"], 1)
        self.assertEqual(paired["mcid_sign_test"]["denominator"], 16)
        self.assertEqual(paired["mcid_sign_test"]["p_value"], 1 / 16)

    def test_joint_mcid_test_counts_every_non_exceedance_in_full_n(self) -> None:
        test = scenario._joint_mcid_sign_test(
            [11, 10, 11, 11, 0],
            [6.0, 6.0, 5.0, None, 0.0],
        )

        self.assertEqual(test["wins"], 1)
        self.assertEqual(test["non_wins"], 4)
        self.assertEqual(test["n"], 5)
        self.assertEqual(test["numerator"], 31)
        self.assertEqual(test["denominator"], 32)
        self.assertEqual(test["p_value"], 31 / 32)

    def test_support_gate_recomputes_joint_mcid_and_ignores_descriptive_gates(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        supported = self.collect(
            [self.fixture.boot(index, order) for index, order in enumerate(orders, 1)]
        )["summary"]
        supported["paired_improvement"]["ci_low"] = -1
        supported["paired_improvement"]["relative_ci_low"] = -1
        supported["paired_improvement"]["sign_test"]["p_value"] = 1.0
        self.assertEqual(scenario._classify_claim(supported), "supported")

        boundary = self.collect(
            [
                self.fixture.boot(
                    index + 10,
                    order,
                    agentos_advantage_ms=5 if index < 7 else 2,
                )
                for index, order in enumerate(orders, 1)
            ]
        )["summary"]
        boundary["paired_improvement"]["mcid_sign_test"]["p_value"] = 0.01
        with self.assertRaisesRegex(
            scenario.ScenarioEvidenceError, "directional MCID statistics"
        ):
            scenario._classify_claim(boundary)

    def test_paired_bootstrap_is_deterministic(self) -> None:
        orders = ["AB", "BA", "AB", "BA", "AB", "BA", "AB"]
        boots = [self.fixture.boot(index, order) for index, order in enumerate(orders, 1)]

        first = self.collect(boots)
        second = self.collect(boots)

        self.assertEqual(
            first["summary"]["paired_improvement"],
            second["summary"]["paired_improvement"],
        )
        self.assertEqual(first["report_sha256"], second["report_sha256"])

    def test_unbalanced_seven_boots_remain_inconclusive(self) -> None:
        boots = [self.fixture.boot(index, "AB") for index in range(1, 8)]
        report = self.collect(boots)
        self.assertEqual(report["status"], "inconclusive")
        self.assertFalse(report["summary"]["target_order_balanced"])

    def test_outcome_tamper_is_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "agentos" / "state-extracted" / "rp_runner"
        path.write_text(
            "host_action_rerun=usable-run:RUN-999-rerun;"
            "parent=RUN-TAMPERED;status=completed\n",
            encoding="ascii",
            newline="\n",
        )

        report = self.collect([boot])
        self.assertEqual(report["status"], "failed")
        self.assertIn("does not match the Host challenge", report["errors"][0])

    def test_runtime_artifact_tamper_is_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        (boot / "agentos" / "artifacts" / "kernel").write_bytes(b"tampered\n")

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("artifact", report["errors"][0])

    def test_equal_but_precomputed_outputs_are_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        for target in ("plain", "agentos"):
            path = boot / target / "state-extracted" / "rp_runner"
            path.write_text(
                "host_action_rerun=usable-run:RUN-999-rerun;"
                "parent=RUN-999;status=completed\n",
                encoding="ascii",
                newline="\n",
            )

        report = self.collect([boot])
        self.assertEqual(report["status"], "failed")
        self.assertIn("does not match the Host challenge", report["errors"][0])

    def test_duplicate_challenge_across_boots_is_rejected(self) -> None:
        duplicate = _challenge(77)
        first = self.fixture.boot(1, "AB", duplicate)
        second = self.fixture.boot(2, "BA", duplicate)

        report = self.collect([first, second])

        self.assertEqual(report["status"], "failed")
        self.assertIn("unique across independent boots", report["errors"][0])

    def test_summary_must_bind_raw_challenge_receipt(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "plain" / "ucore-run-summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["challenge_receipt_sha256"] = "0" * 64
        path.write_text(
            json.dumps(summary, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("does not bind the challenge bytes", report["errors"][0])

    def test_run_summary_must_bind_clean_planned_source(self) -> None:
        mutations = {
            "wrong_commit": ("source_commit", "b" * 40, "planned source commit"),
            "dirty_tree": ("source_tree_clean", False, "clean tracked source tree"),
        }
        for boot_number, (case, mutation) in enumerate(mutations.items(), 60):
            with self.subTest(case=case):
                key, value, expected_error = mutation
                boot = self.fixture.boot(boot_number, "AB")
                path = boot / "agentos" / "ucore-run-summary.json"
                summary = json.loads(path.read_text(encoding="utf-8"))
                summary[key] = value
                path.write_text(
                    json.dumps(summary, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn(expected_error, report["errors"][0])

    def test_program_order_deviation_is_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "agentos" / "state-extracted" / "rp_orch_timing"
        lines = path.read_text(encoding="ascii").splitlines()
        lines[2], lines[3] = lines[3], lines[2]
        path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")

        report = self.collect([boot])
        self.assertEqual(report["status"], "failed")
        self.assertIn("ledger expected", report["errors"][0])

    def test_first_boot_cannot_replace_the_manifest_program_set(self) -> None:
        boot = self.fixture.boot(1, "AB")
        for target in ("plain", "agentos"):
            path = boot / target / "state-extracted" / "rp_orch_timing"
            lines = path.read_text(encoding="ascii").splitlines()
            path.write_text(
                "\n".join(lines[:-1]) + "\n", encoding="ascii", newline="\n"
            )

        report = self.collect([boot])
        self.assertEqual(report["status"], "failed")
        self.assertIn("program count differs from the manifest", report["errors"][0])

    def test_missing_workflow_timing_record_is_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        state_dir = boot / "plain" / "state-extracted"
        (state_dir / "rp_workflow_timing").unlink()
        summary_path = state_dir / "extract-summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["files"].remove("rp_workflow_timing")
        summary["extracted_state_files"] -= 1
        summary_path.write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("rp_workflow_timing", report["errors"][0])

    def test_workflow_timing_smaller_than_program_total_is_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "agentos" / "state-extracted" / "rp_workflow_timing"
        fields = dict(
            field.split("=", 1)
            for field in path.read_text(encoding="ascii").strip().split(";")
        )
        start_ms = int(fields["start_ms"])
        steady_start_ms = int(fields["steady_start_ms"])
        _rewrite_workflow_timing(
            path,
            end_ms=steady_start_ms + 1,
            steady_elapsed_ms=1,
            workflow_elapsed_ms=steady_start_ms + 1 - start_ms,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("less than the program timing total", report["errors"][0])

    def test_workflow_timing_above_scenario_timeout_is_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "plain" / "state-extracted" / "rp_workflow_timing"
        fields = dict(
            field.split("=", 1)
            for field in path.read_text(encoding="ascii").strip().split(";")
        )
        start_ms = int(fields["start_ms"])
        steady_start_ms = int(fields["steady_start_ms"])
        workflow_elapsed_ms = scenario.MAX_WORKFLOW_ELAPSED_MS + 1
        end_ms = start_ms + workflow_elapsed_ms
        _rewrite_workflow_timing(
            path,
            end_ms=end_ms,
            steady_elapsed_ms=end_ms - steady_start_ms,
            workflow_elapsed_ms=workflow_elapsed_ms,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("scenario timeout limit", report["errors"][0])

    def test_workflow_timing_record_must_be_strict_and_singular(self) -> None:
        mutations = {
            "noncanonical": lambda line: re.sub(
                r"workflow_elapsed_ms=([0-9]+)$",
                lambda match: "workflow_elapsed_ms=0" + match.group(1),
                line,
            ),
            "extra_field": lambda line: line + ";source=host",
            "trailing_record": lambda line: line + "\ntrailing=1",
        }
        for boot_number, (case, mutate) in enumerate(mutations.items(), 30):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = boot / "plain" / "state-extracted" / "rp_workflow_timing"
                timing_record = mutate(path.read_text(encoding="ascii").strip())
                path.write_text(
                    timing_record + "\n", encoding="ascii", newline="\n"
                )

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")

    def test_agentos_cold_start_cannot_omit_or_relabel_initialization(self) -> None:
        mutations = {
            "wrong_entry": (
                {"entry": "rp_orch"},
                "trusted entry and completion",
            ),
            "wrong_handoff": (
                {"handoff": "direct"},
                "trusted entry and completion",
            ),
            "child_only_completion": (
                {"completion": "child_exit"},
                "trusted entry and completion",
            ),
            "missing_parent_wait": (
                {"completion_phase_mask": 1},
                "omits required completion phases",
            ),
            "missing_phases": (
                {"init_phase_mask": 0},
                "omits required initialization phases",
            ),
        }
        for boot_number, (case, (updates, expected)) in enumerate(
            mutations.items(), 70
        ):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = boot / "agentos" / "state-extracted" / "rp_workflow_timing"
                _rewrite_workflow_timing(path, **updates)

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn(expected, report["errors"][0])

    def test_agentos_cold_start_rejects_a_late_or_tampered_start(self) -> None:
        boot = self.fixture.boot(80, "AB")
        path = boot / "agentos" / "state-extracted" / "rp_workflow_timing"
        fields = dict(
            field.split("=", 1)
            for field in path.read_text(encoding="ascii").strip().split(";")
        )
        ready_ms = int(fields["ready_ms"])
        end_ms = int(fields["end_ms"])
        _rewrite_workflow_timing(
            path,
            start_ms=ready_ms,
            setup_elapsed_ms=0,
            workflow_elapsed_ms=end_ms - ready_ms,
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("does not include orchestrator initialization", report["errors"][0])

    def test_agentos_functional_receipts_are_required_per_boot(self) -> None:
        mutations = {
            "missing_module": (
                lambda text: "\n".join(text.splitlines()[:-1]) + "\n",
                "functional acceptance",
            ),
            "unverified": (
                lambda text: text.replace(
                    "module=context;operation=context_snapshot;status=verified",
                    "module=context;operation=context_snapshot;status=claimed",
                    1,
                ),
                "functional receipt",
            ),
            "empty_observation": (
                lambda text: text.replace(
                    "timeline_records=4", "timeline_records=0", 1
                ),
                "observation receipt",
            ),
        }
        for boot_number, (case, (mutate, expected)) in enumerate(
            mutations.items(), 90
        ):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = (
                    boot
                    / "agentos"
                    / "state-extracted"
                    / scenario.AGENTOS_ACCEPTANCE_FILE
                )
                path.write_text(
                    mutate(path.read_text(encoding="ascii")),
                    encoding="ascii",
                    newline="\n",
                )

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn(expected, report["errors"][0])

    def test_agentos_acceptance_fields_are_recomputed_from_the_challenge(self) -> None:
        boot = self.fixture.boot(108, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.AGENTOS_ACCEPTANCE_FILE
        )
        path.write_text(
            path.read_text(encoding="ascii").replace(
                "workflow_id=wf-host-108", "workflow_id=wf-host-42", 1
            ),
            encoding="ascii",
            newline="\n",
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("challenge oracle", report["errors"][0])

    def test_cross_boot_fixed_agentos_acceptance_is_rejected(self) -> None:
        first = self.fixture.boot(108, "AB")
        second = self.fixture.boot(109, "BA")
        first_path = (
            first
            / "agentos"
            / "state-extracted"
            / scenario.AGENTOS_ACCEPTANCE_FILE
        )
        second_path = (
            second
            / "agentos"
            / "state-extracted"
            / scenario.AGENTOS_ACCEPTANCE_FILE
        )
        second_path.write_bytes(first_path.read_bytes())

        report = self.collect([first, second])

        self.assertEqual(report["status"], "failed")
        self.assertIn("challenge oracle", report["errors"][0])

    def test_functional_summary_rejects_a_replayed_receipt_hash(self) -> None:
        report = self.collect(
            [self.fixture.boot(106, "AB"), self.fixture.boot(107, "BA")]
        )
        samples = json.loads(json.dumps(report["samples"]))
        first_receipt = samples[0]["targets"]["agentos"]["raw_source_receipt"][
            "functional_acceptance"
        ]
        second_sample = samples[1]
        second_raw = second_sample["targets"]["agentos"]["raw_source_receipt"]
        second_receipt = second_raw["functional_acceptance"]
        replayed_sha = first_receipt["sha256"]
        second_receipt["sha256"] = replayed_sha
        second_receipt["binding"]["module_receipt_sha256"] = replayed_sha
        unsigned_binding = dict(second_receipt["binding"])
        unsigned_binding.pop("sha256")
        second_receipt["binding"]["sha256"] = scenario._binding_sha256(
            unsigned_binding, "agentos-task6-functional-binding-v1"
        )
        inventory_entry = next(
            entry
            for entry in second_raw["state_inventory"]["files"]
            if entry["path"] == scenario.AGENTOS_ACCEPTANCE_FILE
        )
        inventory_entry["sha256"] = replayed_sha
        unsigned_raw = dict(second_raw)
        unsigned_raw.pop("sha256")
        second_raw["sha256"] = scenario._binding_sha256(
            unsigned_raw, "scenario-raw-source-receipt-v1"
        )
        second_sample["binding"]["source_receipts"]["agentos"] = second_raw[
            "sha256"
        ]

        with self.assertRaisesRegex(
            scenario.ScenarioEvidenceError, "replays a module receipt"
        ):
            scenario._functional_acceptance_summary(samples)

    def test_agentos_tool_receipt_must_prove_echo_request_and_response(self) -> None:
        mutations = {
            "request_payload": lambda text: re.sub(
                r"request_payload=[^;]+", "request_payload=claimed", text, count=1
            ),
            "result_payload": lambda text: re.sub(
                r"result_payload=[^;]+", "result_payload=claimed", text, count=1
            ),
            "result_value": lambda text: re.sub(
                r"result_value2=[0-9]+", "result_value2=7", text, count=1
            ),
        }
        for boot_number, (case, mutate) in enumerate(mutations.items(), 110):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = (
                    boot
                    / "agentos"
                    / "state-extracted"
                    / scenario.AGENTOS_ACCEPTANCE_FILE
                )
                path.write_text(
                    mutate(path.read_text(encoding="ascii")),
                    encoding="ascii",
                    newline="\n",
                )

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn("echo semantics", report["errors"][0])

    def test_agentos_context_receipt_must_match_echo_response(self) -> None:
        boot = self.fixture.boot(120, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.AGENTOS_ACCEPTANCE_FILE
        )
        path.write_text(
            path.read_text(encoding="ascii").replace(
                "result_sequence=9", "result_sequence=8", 1
            ),
            encoding="ascii",
            newline="\n",
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("does not match the tool response", report["errors"][0])

    def test_agentos_metadata_receipt_must_bind_the_returned_hit(self) -> None:
        boot = self.fixture.boot(121, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.AGENTOS_ACCEPTANCE_FILE
        )
        path.write_text(
            path.read_text(encoding="ascii").replace(
                "target_physical=a121", "target_physical=p121", 1
            ),
            encoding="ascii",
            newline="\n",
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("target hit", report["errors"][0])

    def test_agentos_provenance_must_be_nonzero_and_bound_to_context(self) -> None:
        mutations = {
            "zero_edges": ("provenance_edges=1", "provenance_edges=0"),
            "wrong_source": ("source_sequence=9", "source_sequence=8"),
            "wrong_hash": ("target_record_hash=102", "target_record_hash=103"),
        }
        for boot_number, (case, (old, new)) in enumerate(mutations.items(), 130):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = (
                    boot
                    / "agentos"
                    / "state-extracted"
                    / scenario.AGENTOS_ACCEPTANCE_FILE
                )
                path.write_text(
                    path.read_text(encoding="ascii").replace(old, new, 1),
                    encoding="ascii",
                    newline="\n",
                )

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn("bound provenance", report["errors"][0])

    def test_agentos_receipt_counts_cannot_exceed_guest_capacities(self) -> None:
        mutations = {
            "context": ("records=4", "records=5", "context receipt"),
            "timeline": ("timeline_records=4", "timeline_records=5", "bound provenance"),
            "provenance": ("provenance_edges=1", "provenance_edges=5", "bound provenance"),
            "metadata": ("returned=1", "returned=2", "target hit"),
        }
        for boot_number, (case, (old, new, message)) in enumerate(
            mutations.items(), 140
        ):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = (
                    boot
                    / "agentos"
                    / "state-extracted"
                    / scenario.AGENTOS_ACCEPTANCE_FILE
                )
                path.write_text(
                    path.read_text(encoding="ascii").replace(old, new, 1),
                    encoding="ascii",
                    newline="\n",
                )

                report = self.collect([boot])

                self.assertEqual(report["status"], "failed")
                self.assertIn(message, report["errors"][0])

    def test_agentos_receipt_uints_are_bounded_to_the_guest_abi(self) -> None:
        boot = self.fixture.boot(145, "AB")
        path = (
            boot
            / "agentos"
            / "state-extracted"
            / scenario.AGENTOS_ACCEPTANCE_FILE
        )
        text = path.read_text(encoding="ascii")
        text = re.sub(
            r"request_id=[0-9]+",
            f"request_id={1 << 64}",
            text,
            count=1,
        )
        path.write_text(text, encoding="ascii", newline="\n")

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("uint64 range", report["errors"][0])

    def test_functional_summary_rechecks_normalized_module_oracle(self) -> None:
        report = self.collect([self.fixture.boot(146, "AB")])
        sample = report["samples"][0]
        raw = sample["targets"]["agentos"]["raw_source_receipt"]
        raw["functional_acceptance"]["acceptance"]["modules"][0][
            "request_id"
        ] = 42
        unsigned_raw = dict(raw)
        unsigned_raw.pop("sha256")
        raw["sha256"] = scenario._binding_sha256(
            unsigned_raw, "scenario-raw-source-receipt-v1"
        )
        sample["binding"]["source_receipts"]["agentos"] = raw["sha256"]

        with self.assertRaisesRegex(
            scenario.ScenarioEvidenceError, "challenge-derived echo record"
        ):
            scenario._functional_acceptance_summary([sample])

    def test_functional_summary_rejects_tampered_challenge_binding(self) -> None:
        report = self.collect([self.fixture.boot(100, "AB")])
        sample = report["samples"][0]
        receipt = sample["targets"]["agentos"]["raw_source_receipt"]
        receipt["functional_acceptance"]["binding"]["challenge"] = _challenge(999)

        with self.assertRaisesRegex(
            scenario.ScenarioEvidenceError, "raw source receipt binding differs"
        ):
            scenario._summarize(report["samples"])

    def test_workflow_timing_cannot_exceed_host_observation(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "plain" / "ucore-run-summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["elapsed_seconds"] = 0.001
        path.write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

        report = self.collect([boot])

        self.assertEqual(report["status"], "failed")
        self.assertIn("Host-observed run duration", report["errors"][0])

    def test_timing_records_reject_failure_noninteger_and_extra_fields(self) -> None:
        mutations = {
            "failure": lambda line: line.replace(";ok=1;code=0;", ";ok=0;code=7;"),
            "noninteger": lambda line: re.sub(
                r";elapsed_ms=[0-9]+$", ";elapsed_ms=1.2", line
            ),
            "extra": lambda line: line + ";fixed_reference=1",
        }
        for boot_number, (case, mutate) in enumerate(mutations.items(), 10):
            with self.subTest(case=case):
                boot = self.fixture.boot(boot_number, "AB")
                path = boot / "plain" / "state-extracted" / "rp_orch_timing"
                lines = path.read_text(encoding="ascii").splitlines()
                lines[2] = mutate(lines[2])
                path.write_text(
                    "\n".join(lines) + "\n", encoding="ascii", newline="\n"
                )
                report = self.collect([boot])
                self.assertEqual(report["status"], "failed")

    def test_failed_summary_and_duplicate_json_field_are_rejected(self) -> None:
        boot = self.fixture.boot(1, "AB")
        path = boot / "plain" / "ucore-run-summary.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace('"passed": true,', '"passed": true,\n  "passed": false,'),
            encoding="utf-8",
            newline="\n",
        )
        report = self.collect([boot])
        self.assertEqual(report["status"], "failed")
        self.assertIn("strict JSON", report["errors"][0])

    def test_cli_order_can_supply_missing_summary_order(self) -> None:
        boot = self.fixture.boot(1, None)
        report = self.collect([boot], ["BA"])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["samples"][0]["binding"]["target_order"], "BA")


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EvaluationScenarioTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
