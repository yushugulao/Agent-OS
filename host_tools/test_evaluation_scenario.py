#!/usr/bin/env python3
"""Regression tests for the research-platform scenario collector."""

from __future__ import annotations

import json
import hashlib
import re
import tempfile
import unittest
from pathlib import Path

try:
    from . import evaluation_scenario as scenario
    from . import check_seeded_action_state as seeded
except ImportError:  # Direct execution from host_tools/.
    import evaluation_scenario as scenario
    import check_seeded_action_state as seeded


SOURCE_COMMIT = "a" * 40


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


def _outcome_files(challenge: str) -> dict[str, str]:
    values = seeded.derive_challenge(challenge)
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
            "host_artifact_input=reads_R1.fastq;kind=fastq;"
            f"sha256={values.input_sha256};bytes=2048;source=upload\n"
            "host_artifact_derive=raw-counts.csv;output=normalized-counts.csv;"
            f"operation=normalize;stage=analyze;sha256={values.derived_sha256}\n"
        ),
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


def _agentos_acceptance() -> str:
    return (
        "schema=agentos_task6_acceptance_v2;module_count=4\n"
        "module=context;operation=context_snapshot;status=verified;"
        "records=4;latest_sequence=10;request_id=9001;tool_id=1;"
        "record_sequence=9;record_hash=101;payload=rp-agentos-orch;"
        "result=rp-agentos-orch;followup_sequence=10;followup_record_hash=102\n"
        "module=structured_tool;operation=agent_run_echo;status=verified;"
        "request_id=9001;tool_id=1;request_payload=rp-agentos-orch;"
        "arg0=9001;arg1=9002;result_version=1;result_status=0;"
        "result_tool_id=1;result_request_id=9001;"
        "result_payload=rp-agentos-orch;result_value0=15;"
        "result_value1=9001;result_value2=9002;result_sequence=9\n"
        "module=metadata_query;operation=file_query_stage_index;status=verified;"
        "project=lab-gene-x;run_id=RUN-042;stage=align;"
        "returned=1;used_index=1;plan=2;target_fid=1;"
        "target_physical=r42align;target_stage=align;"
        "target_kind=artifact;target_status=ok\n"
        "module=observation;operation=timeline_provenance_ledger;status=verified;"
        "timeline_records=4;provenance_edges=1;ledger_records=9;ledger_hash=999;"
        "edge_kind=1;edge_tool_id=1;edge_status=0;source_sequence=9;"
        "target_sequence=10;source_record_hash=101;target_record_hash=102\n"
    )


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
        files[scenario.AGENTOS_ACCEPTANCE_FILE] = _agentos_acceptance()
    for name, text in files.items():
        (state_dir / name).write_text(text, encoding="ascii", newline="\n")
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
        self._temporary = tempfile.TemporaryDirectory(
            prefix="evaluation-scenario-", dir=Path(__file__).resolve().parent
        )
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
        sample = report["samples"][0]
        self.assertEqual(sample["binding"]["source_commit"], SOURCE_COMMIT)
        self.assertEqual(sample["binding"]["boot_order"], 1)
        self.assertEqual(sample["binding"]["target_order"], "AB")
        for target in ("plain", "agentos"):
            receipt = sample["targets"][target]["raw_source_receipt"]
            self.assertEqual(receipt["schema"], "scenario-raw-source-receipt-v1")
            self.assertEqual(
                receipt["state_inventory"]["file_count"],
                7 if target == "plain" else 8,
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
        agentos_main = agentos[agentos.index("int main(void)") :]
        wait_at = agentos_main.index("waitpid(pid, &code)")
        completion_at = agentos_main.index("read_workflow_completion(")
        validation_at = agentos_main.index('rp_file_contains("rp_agentos_kernel"')
        end_at = agentos_main.index("uint64 workflow_end = get_mtime();")
        timing_at = agentos_main.index("record_workflow_timing(&completion")
        self.assertLess(wait_at, completion_at)
        self.assertLess(completion_at, validation_at)
        self.assertLess(validation_at, end_at)
        self.assertLess(end_at, timing_at)

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
        self.assertEqual(paired["bootstrap"]["repetitions"], 2_000)
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

    def test_agentos_tool_receipt_must_prove_echo_request_and_response(self) -> None:
        mutations = {
            "request_payload": (
                "request_payload=rp-agentos-orch",
                "request_payload=claimed",
            ),
            "result_payload": (
                "result_payload=rp-agentos-orch",
                "result_payload=claimed",
            ),
            "result_value": ("result_value2=9002", "result_value2=7"),
        }
        for boot_number, (case, (old, new)) in enumerate(mutations.items(), 110):
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
                "target_physical=r42align", "target_physical=r42report", 1
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
