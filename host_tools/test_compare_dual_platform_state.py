#!/usr/bin/env python3
"""双平台状态比较的单元测试。"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import compare_dual_platform_state as compare
import reference_catalog_contract as reference_catalog
from dual_state_evidence_contract import (
    agentos_program_launch_contract,
    evidence_check_count,
)


RUNNER_SOURCE_FILES = ("rp_runner_context_src", "rp_runner_fsmeta_src")


def write_state_file(root: Path, name: str, text: str) -> None:
    (root / name).write_bytes(text.encode("utf-8"))


def append_state_file(root: Path, name: str, text: str) -> None:
    with (root / name).open("a", encoding="utf-8", newline="") as handle:
        handle.write(text)


BACKEND_COSTS = {
    "plain-ucore": ("file_scan_manifest", "batch_tool_context"),
    "retry-recovery": ("retry_file_stage_file", "event_context"),
    "user-context": ("rebuild_steps_6", "kernel_context_path"),
    "user-fsmeta": ("scan_records_128", "metadata_index"),
    "user-recovery": ("manual_retry_contract", "capability_checked_action"),
    "user-event": ("file_polling", "kernel_event_queue"),
    "user-audit": ("append_only_logs", "kernel_ledger_provenance"),
    "agentos-context": ("rebuild_steps_6", "kernel_context_path"),
    "agentos-fsmeta": ("scan_records_128", "metadata_index"),
    "agentos-recovery": ("manual_retry_contract", "capability_checked_action"),
    "agentos-event": ("file_polling", "kernel_event_queue"),
    "agentos-audit": ("append_only_logs", "kernel_ledger_provenance"),
    "agentos-edit": ("userland_lock_file", "kernel_edit_lease"),
}


def backend_query_receipt_line(
    target: str, overrides: dict[str, object] | None = None
) -> str:
    values: dict[str, object] = {
        **compare.BACKEND_QUERY_EXPECTED_COMMON,
        "records_examined": 49152 if target == "plain" else 4096,
        "backend": compare.BACKEND_QUERY_EXPECTED_BACKENDS[target],
    }
    if overrides:
        values.update(overrides)
    return ";".join(
        f"{field}={values[field]}" for field in compare.BACKEND_QUERY_RECEIPT_FIELDS
    )


def write_backend_catalog(root: Path, target: str) -> None:
    if target == "plain":
        lines = [
            "evidence_file_role=demo_reference",
            "evidence_file_generation=demo_expected",
            "runtime_cases=0",
        ]
        for case in compare.BACKEND_REPORT_CASES["plain"]:
            cost, replacement = BACKEND_COSTS[case]
            lines.append(
                f"runner_report={case};plain_cost={cost};agentos_replace={replacement};"
                "risk=fixture;status=reference_ready"
            )
        lines.append("evidence_file_status=reference_ready")
        write_state_file(root, "rp_backend_exec", "\n".join(lines) + "\n")
        return

    lines = []
    for identity in sorted(reference_catalog.allowed_record_identities("agentos")):
        if identity.destination != "rp_backend_exec":
            continue
        fields = [
            "evidence_role=demo_reference",
            "catalog_generation=demo_expected",
            identity.anchor,
        ]
        if identity.anchor.startswith("runner_report="):
            case = identity.anchor.split("=", 1)[1]
            cost, replacement = BACKEND_COSTS[case]
            fields.extend(
                [f"plain_cost={cost}", f"agentos_replace={replacement}", "risk=fixture"]
            )
        fields.append("status=reference_ready")
        lines.append(";".join(fields))
    write_state_file(root, "rp_backend_exec", "\n".join(lines) + "\n")


def seed_reference_inventory(root: Path, target: str) -> None:
    envelope = (
        "evidence_file_role=demo_reference\n"
        "evidence_file_generation=demo_expected\n"
        "evidence_file_status=reference_ready\n"
    )
    for file_name in reference_catalog.allowed_file_identities(target):
        path = root / file_name
        if file_name == "rp_backend_exec":
            continue
        if path.exists():
            path.write_text(envelope + path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            write_state_file(root, file_name, envelope)
    for identity in sorted(reference_catalog.allowed_record_identities(target)):
        if identity.destination == "rp_backend_exec":
            continue
        append_state_file(
            root,
            identity.destination,
            "evidence_role=demo_reference;catalog_generation=demo_expected;"
            f"{identity.anchor};status=reference_ready\n",
        )


def reference_state_files(target: str) -> set[str]:
    return set(reference_catalog.allowed_file_identities(target)) | {
        identity.destination
        for identity in reference_catalog.allowed_record_identities(target)
    }


def snapshot_state(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    }


def restore_state(root: Path, snapshot: dict[str, bytes]) -> None:
    for path in root.iterdir():
        if path.is_file() and path.name not in snapshot:
            path.unlink()
    for name, data in snapshot.items():
        (root / name).write_bytes(data)


def expect_value_error(callable_obj, expected: str) -> None:
    try:
        callable_obj()
    except ValueError as error:
        assert expected in str(error), str(error)
        return
    raise AssertionError("operation unexpectedly passed")


def write_summary(root: Path, files: list[str]) -> None:
    (root / "extract-summary.json").write_text(
        json.dumps(
            {
                "extracted_state_files": len(files),
                "files": files,
                "status": "ready",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_host_run_result(
    path: Path,
    *,
    target: str,
    state_dir: Path,
    extracted_state_files: int,
    action_records: int = 1,
    passed: int = 1,
    controlled_exit: bool = False,
) -> None:
    chapter, init_proc = compare.RUN_RESULT_IDENTITIES[target]
    guest_state_files, guest_state_sha256 = compare.guest_state_inventory_sha256(
        state_dir
    )
    path.write_bytes((
        "host_runner=plain_ucore_action_runner\n"
        f"target={target}\n"
        f"chapter={chapter}\n"
        f"init_proc={init_proc}\n"
        "status=ready\n"
        f"passed={passed}\n"
        f"embedded_action_records={action_records}\n"
        f"extracted_state_files={extracted_state_files}\n"
        f"guest_state_receipt_schema={compare.GUEST_STATE_RECEIPT_SCHEMA}\n"
        f"guest_state_files={guest_state_files}\n"
        f"guest_state_sha256={guest_state_sha256}\n"
        "build_returncode=0\n"
        "guest_returncode=0\n"
        f"guest_raw_returncode={'-15' if controlled_exit else '0'}\n"
        "failure_phase=\n"
        "failure_reason=\n"
        "qemu_timed_out=0\n"
        f"qemu_runner_terminated={1 if controlled_exit else 0}\n"
        "qemu_output_eof=1\n"
        f"qemu_runner_signals={'15' if controlled_exit else ''}\n"
        "qemu_orch_passed=1\n"
    ).encode("utf-8"))


def write_action_inputs(root: Path, action_records: int = 1) -> tuple[Path, Path, Path]:
    plain_log = root / "plain-qemu.log"
    agentos_log = root / "agentos-qemu.log"
    payload = (
        f"rp_web_export: host_reader_actions={action_records}\n"
        f"rp_compare_plain: host_actions={action_records} verified\n"
    ).encode("utf-8")
    plain_log.write_bytes(payload)
    agentos_log.write_bytes(payload)
    seeded_summary = root / "seeded-action-state.json"
    seeded_summary.write_bytes((json.dumps({
        "status": "ready",
        "action": "/actions/research/rerun",
        "action_count": action_records,
        "action_kinds": [f"fixture_{index}" for index in range(action_records)],
    }) + "\n").encode("utf-8"))
    return plain_log, agentos_log, seeded_summary


def write_agentos_evidence_files(root: Path, omit_token: str = "") -> None:
    source_statuses = {
        source: source_status
        for source, _claim_key, _claim_value, source_status in compare.MAINFLOW_RUNTIME_SPECS.values()
    }
    for file_name, tokens in compare.AGENTOS_EVIDENCE_REQUIREMENTS.items():
        kept = [token for token in tokens if token != omit_token]
        source_status = source_statuses.get(file_name, "ready")
        write_state_file(
            root, file_name, "\n".join(kept) + f"\nstatus={source_status}\n"
        )


def write_agentos_mainflow_stages(
    root: Path,
    omit_token: str = "",
) -> None:
    text = "".join(
        ";".join(
            [f"stage={spec.stage}"]
            + [
                f"{key}={value}"
                for key, value in spec.telemetry_fields
                if f"{key}={value}" != omit_token
            ]
            + ["status=ready"]
        )
        + "\n"
        for spec in compare.MAIN_FLOW_SOURCE_SPECS
    )
    write_state_file(root, "rp_agentos_mainflow", text)


def timing_programs() -> list[str]:
    programs, roles, errors = compare.read_expected_programs(
        Path(__file__).resolve().parents[1]
    )
    assert not errors and roles == compare.AGENTOS_REQUIRED_AGENT_ROLES
    return list(programs)


def write_timing(root: Path, target: str) -> None:
    programs = timing_programs()
    if target == "plain":
        lines = ["orchestrator=rp_seed_orch\n", "launcher=fork_seeded\n"]
    elif target in {"agentos", "agentos_workers"}:
        lines = ["orchestrator=rp_orch\n", "launcher=mixed_attested\n"]
    else:
        raise ValueError(f"unknown timing target: {target}")
    for i, program in enumerate(programs):
        if target == "plain":
            lines.append(
                f"program={program};launcher=fork_seeded;ok=1;code=0;elapsed_ms={i + 1}\n"
            )
            continue
        is_agent = target == "agentos" and program in compare.AGENTOS_REQUIRED_AGENT_PROGRAMS
        record_launcher, identity_source = agentos_program_launch_contract(program)
        record_role = (
            compare.AGENTOS_REQUIRED_AGENT_ROLES[program] if is_agent else "plain"
        )
        identity = (
            f";identity_source={identity_source}"
            f";is_agent={1 if is_agent else 0}"
            f";agent_role={compare.AGENTOS_ROLE_NUMBERS[record_role] if is_agent else 0}"
            ";filesystem_domain=3;filesystem_capabilities=66"
        )
        lines.append(
            f"program={program};role={record_role};launcher={record_launcher}"
            f"{identity};ok=1;code=0;elapsed_ms={i + 1}\n"
        )

    write_state_file(
        root,
        "rp_orch_timing",
        "".join(lines),
    )


def program_inventory_line(root: Path, target: str) -> str:
    data = (root / "rp_orch_timing").read_bytes()
    digest = compare.FNV_OFFSET
    for program in timing_programs():
        digest = compare.fnv1a64(program.encode("ascii") + b"\0", digest)
    measurements = (
        f"program_source=rp_orch_timing;program_source_bytes={len(data)};"
        f"program_source_hash={compare.fnv1a64(data)};program_names_digest={digest};"
        f"programs_observed={len(timing_programs())};"
    )
    if target == "plain":
        return (
            "evidence_role=demo_reference;evidence_generation=runtime;"
            "observation_source=guest_runtime;"
            + measurements
            + "status=reference_observed\n"
        )
    return (
        "evidence_role=runtime_verified;evidence_generation=runtime;"
        + measurements
        + "status=verified\n"
    )


def agentos_file_list() -> list[str]:
    return sorted(set([
        "rp_backend",
        "rp_backend_exec",
        "rp_agentcmp",
        "rp_orch_timing",
        *RUNNER_SOURCE_FILES,
        *compare.AGENTOS_EVIDENCE_REQUIREMENTS.keys(),
    ]) | reference_state_files("agentos") | set(plain_file_list()))


def plain_file_list() -> list[str]:
    return sorted(
        {
            "rp_backend",
            "rp_backend_exec",
            "rp_agentcmp",
            "rp_orch_timing",
            *RUNNER_SOURCE_FILES,
        }
        | reference_state_files("plain")
    )


def expect_failure(
    plain: Path,
    agentos: Path,
    plain_run_result: Path,
    agentos_run_result: Path,
    plain_log: Path,
    agentos_log: Path,
    seeded_summary: Path,
    expected: str,
) -> None:
    try:
        compare.compare_state(
            plain,
            agentos,
            min_common_files=1,
            plain_run_result=plain_run_result,
            agentos_run_result=agentos_run_result,
            plain_log=plain_log,
            agentos_log=agentos_log,
            seeded_summary=seeded_summary,
        )
    except ValueError as exc:
        assert expected in str(exc), str(exc)
        return
    raise AssertionError("comparison unexpectedly passed")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plain = root / "plain"
        agentos = root / "agentos"
        plain_run_result = root / "plain-host-run-result.state"
        agentos_run_result = root / "agentos-host-run-result.state"
        plain_log, agentos_log, seeded_summary = write_action_inputs(root)
        plain.mkdir()
        agentos.mkdir()

        write_state_file(
            plain,
            "rp_backend",
            "runner_report=file_scan\n"
            + backend_query_receipt_line("plain")
            + "\nruntime_cases=0\nstatus=ready\n",
        )
        write_state_file(plain, "rp_runner_context_src", "path=plain-context\n")
        write_state_file(plain, "rp_runner_fsmeta_src", "path=plain-fsmeta\n")
        write_backend_catalog(plain, "plain")
        write_timing(plain, "plain")
        write_state_file(
            plain,
            "rp_agentcmp",
            "plain_kernel=passed;programs=69;status=ready\n"
            + program_inventory_line(plain, "plain"),
        )
        seed_reference_inventory(plain, "plain")
        write_summary(plain, plain_file_list())
        write_host_run_result(
            plain_run_result,
            target="plain",
            state_dir=plain,
            extracted_state_files=len(plain_file_list()),
        )

        write_state_file(
            agentos,
            "rp_backend",
            "runner_report=file_scan;status=ready;kernel=observed\n"
            + backend_query_receipt_line("agentos")
            + "\nstatus=ready\n",
        )
        write_state_file(agentos, "rp_runner_context_src", "path=agentos-context\n")
        write_state_file(agentos, "rp_runner_fsmeta_src", "path=agentos-fsmeta\n")
        write_backend_catalog(agentos, "agentos")
        write_timing(agentos, "agentos")
        write_state_file(
            agentos,
            "rp_agentcmp",
            "plain_kernel=passed;programs=69;status=ready\n"
            + program_inventory_line(agentos, "agentos"),
        )
        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        seed_reference_inventory(agentos, "agentos")
        for file_name in plain_file_list():
            if not (agentos / file_name).exists():
                write_state_file(agentos, file_name, "fixture=agentos;status=ready\n")
        write_summary(agentos, agentos_file_list())
        write_host_run_result(
            agentos_run_result,
            target="agentos",
            state_dir=agentos,
            extracted_state_files=len(agentos_file_list()),
        )

        summary = compare.compare_state(
            plain,
            agentos,
            min_common_files=4,
            plain_run_result=plain_run_result,
            agentos_run_result=agentos_run_result,
            plain_log=plain_log,
            agentos_log=agentos_log,
            seeded_summary=seeded_summary,
        )
        assert summary["plain_files"] == len(plain_file_list()), summary
        assert summary["agentos_files"] == len(agentos_file_list()), summary
        assert summary["common_files"] == len(plain_file_list()), summary
        assert summary["agentos_extra_files"] == 12, summary
        assert summary["checked_compatibility_records"] == 1, summary
        assert summary["plain_reference_records"] == 19, summary
        assert summary["agentos_reference_records"] == 24, summary
        assert summary["plain_reference_identities"] == list(
            reference_catalog.expected_reference_identities("plain")
        ), summary
        assert summary["agentos_reference_identities"] == list(
            reference_catalog.expected_reference_identities("agentos")
        ), summary
        assert summary["guest_source_bound_runtime_records"] == 0, summary
        assert summary["preserved_plain_costs"] == 7, summary
        assert summary["cost_replacement_count"] == 8, summary
        assert any(
            row["plain_cost"] == "rebuild_steps_6" and row["preserved_from_plain"] == 1
            for row in summary["cost_replacements"]
        ), summary
        assert any(
            row["plain_cost"] == "userland_lock_file" and row["preserved_from_plain"] == 0
            for row in summary["cost_replacements"]
        ), summary
        assert summary["runner_tick_status"] == "unavailable", summary
        assert summary["runner_tick_reason"] == "plain_runtime_cases_zero", summary
        assert not {
            "runner_tick_comparison",
            "runner_tick_pairs",
            "runner_tick_expected_pairs",
        } & set(summary), summary
        assert summary["embedded_action_records"] == 1, summary
        assert summary["run_result_match"] == 1, summary
        assert summary["agentos_evidence_checks"] == evidence_check_count(), summary
        assert len(summary["scenario_evidence"]) == len(compare.SCENARIO_EVIDENCE_SPECS), summary
        assert all(row["status"] == "ready" for row in summary["scenario_evidence"]), summary
        assert any(row["scenario"] == "Context Path" and row["matched"] >= 3 for row in summary["scenario_evidence"]), summary
        assert summary["host_derived_mainflow_stages"] == len(compare.AGENTOS_MAINFLOW_STAGES), summary
        assert summary["agentos_mainflow_verification_origin"] == "host_inventory", summary
        assert summary["agentos_mainflow_facts"] == len(compare.AGENTOS_MAINFLOW_FACTS), summary
        assert summary["plain_timing_records"] == 70, summary
        assert summary["plain_agent_launches"] == 0, summary
        assert summary["plain_fork_launches"] == 70, summary
        assert summary["agentos_timing_records"] == 70, summary
        assert summary["agentos_agent_launches"] == len(compare.AGENTOS_REQUIRED_AGENT_PROGRAMS), summary
        assert summary["agentos_worker_launches"] == 70 - len(compare.AGENTOS_REQUIRED_AGENT_PROGRAMS), summary
        assert summary["backend_query_receipts"] == {
            "plain": {
                "query_workload": "research_metadata_lookup",
                "consistency": "fresh_snapshot",
                "dataset_records": 12,
                "query_operations": 4096,
                "query_matches": 4096,
                "result_digest": "13819499490441518226",
                "records_examined": 49152,
                "backend": "plain_file_scan",
                "status": "verified",
            },
            "agentos": {
                "query_workload": "research_metadata_lookup",
                "consistency": "fresh_snapshot",
                "dataset_records": 12,
                "query_operations": 4096,
                "query_matches": 4096,
                "result_digest": "13819499490441518226",
                "records_examined": 4096,
                "backend": "agent_metadata_index",
                "status": "verified",
            },
        }, summary

        write_host_run_result(
            agentos_run_result,
            target="agentos",
            state_dir=agentos,
            extracted_state_files=len(agentos_file_list()),
            controlled_exit=True,
        )
        controlled = compare.compare_state(
            plain,
            agentos,
            min_common_files=4,
            plain_run_result=plain_run_result,
            agentos_run_result=agentos_run_result,
            plain_log=plain_log,
            agentos_log=agentos_log,
            seeded_summary=seeded_summary,
        )
        assert controlled["status"] == "ready", controlled
        write_host_run_result(
            agentos_run_result,
            target="agentos",
            state_dir=agentos,
            extracted_state_files=len(agentos_file_list()),
        )

        plain_baseline = snapshot_state(plain)
        agentos_baseline = snapshot_state(agentos)
        agentos_run_baseline = agentos_run_result.read_bytes()
        plain_log_baseline = plain_log.read_bytes()
        seeded_summary_baseline = seeded_summary.read_bytes()

        def expect_current(expected: str, *, bind_state: bool = True) -> None:
            if bind_state:
                write_host_run_result(
                    plain_run_result,
                    target="plain",
                    state_dir=plain,
                    extracted_state_files=int(
                        compare.read_summary(plain)["extracted_state_files"]
                    ),
                )
                write_host_run_result(
                    agentos_run_result,
                    target="agentos",
                    state_dir=agentos,
                    extracted_state_files=int(
                        compare.read_summary(agentos)["extracted_state_files"]
                    ),
                )
            expect_failure(
                plain,
                agentos,
                plain_run_result,
                agentos_run_result,
                plain_log,
                agentos_log,
                seeded_summary,
                expected,
            )

        plain_query_receipt = backend_query_receipt_line("plain")
        agentos_query_receipt = backend_query_receipt_line("agentos")

        append_state_file(plain, "rp_backend", plain_query_receipt + "\n")
        expect_current("exactly one backend query receipt")
        restore_state(plain, plain_baseline)

        plain_backend_state = (plain / "rp_backend").read_text(encoding="utf-8")
        reordered = plain_query_receipt.replace(
            "query_workload=research_metadata_lookup;consistency=fresh_snapshot",
            "consistency=fresh_snapshot;query_workload=research_metadata_lookup",
            1,
        )
        (plain / "rp_backend").write_text(
            plain_backend_state.replace(plain_query_receipt, reordered, 1),
            encoding="utf-8",
        )
        expect_current("field order differs")
        restore_state(plain, plain_baseline)

        for label, original, replacement, expected in (
            (
                "non-canonical decimal",
                "dataset_records=12",
                "dataset_records=012",
                "non-canonical dataset_records",
            ),
            (
                "uint64 overflow",
                "result_digest=13819499490441518226",
                "result_digest=18446744073709551616",
                "exceeds uint64",
            ),
            (
                "full scan accounting",
                "records_examined=49152",
                "records_examined=49151",
                "does not account for the full scan",
            ),
        ):
            current = (plain / "rp_backend").read_text(encoding="utf-8")
            assert original in current, label
            (plain / "rp_backend").write_text(
                current.replace(original, replacement, 1), encoding="utf-8"
            )
            expect_current(expected)
            restore_state(plain, plain_baseline)

        for label, original, replacement, expected in (
            (
                "digest mismatch",
                "result_digest=13819499490441518226",
                "result_digest=13819499490441518225",
                "common fields differ",
            ),
            (
                "wrong backend",
                "backend=agent_metadata_index",
                "backend=plain_file_scan",
                "names the wrong backend",
            ),
            (
                "no index reduction",
                "records_examined=4096",
                "records_examined=49152",
                "does not prove an index reduction",
            ),
            (
                "fewer examined than matches",
                "records_examined=4096",
                "records_examined=4095",
                "does not prove an index reduction",
            ),
        ):
            current = (agentos / "rp_backend").read_text(encoding="utf-8")
            assert original in current, label
            (agentos / "rp_backend").write_text(
                current.replace(original, replacement, 1), encoding="utf-8"
            )
            expect_current(expected)
            restore_state(agentos, agentos_baseline)

        for state_dir in (plain, agentos):
            current = (state_dir / "rp_backend").read_text(encoding="utf-8")
            (state_dir / "rp_backend").write_text(
                current.replace(
                    "query_workload=research_metadata_lookup",
                    "query_workload=research_metadata_lookup_v2",
                    1,
                ),
                encoding="utf-8",
            )
        expect_current("workload differs from the release contract")
        restore_state(plain, plain_baseline)
        restore_state(agentos, agentos_baseline)

        plain_backend = plain / "rp_backend_exec"
        tagged_backend = plain_backend.read_text(encoding="utf-8")
        plain_backend.write_text(
            tagged_backend.replace(
                "runner_report=user-context;plain_cost=rebuild_steps_6;"
                "agentos_replace=kernel_context_path;risk=fixture;status=reference_ready\n",
                "runner_report=user-context;plain_cost=rebuild_steps_6;"
                "agentos_replace=kernel_context_path;risk=fixture;status=passed\n",
            ),
            encoding="utf-8",
        )
        expect_value_error(
            lambda: compare.verify_backend_costs(plain, agentos),
            "not an authorized reference",
        )
        restore_state(plain, plain_baseline)

        (agentos / "rp_agentcmp").unlink()
        write_summary(
            agentos,
            sorted(
                path.name
                for path in agentos.iterdir()
                if path.is_file() and path.name != "extract-summary.json"
            ),
        )
        expect_current("missing plain files")

        restore_state(agentos, agentos_baseline)
        agentos_run_result.write_bytes(
            agentos_run_result.read_bytes().replace(
                b"status=ready",
                b"status=failed",
                1,
            )
        )
        expect_current("AgentOS Host run result is not ready", bind_state=False)

        agentos_run_result.write_bytes(
            agentos_run_baseline.replace(b"target=agentos", b"target=plain", 1)
        )
        expect_current("invalid target identity", bind_state=False)

        agentos_run_result.write_bytes(
            agentos_run_baseline.replace(b"qemu_timed_out=0", b"qemu_timed_out=1", 1)
        )
        expect_current("contradictory success fields", bind_state=False)
        agentos_run_result.write_bytes(agentos_run_baseline)

        plain_log.write_bytes(
            plain_log_baseline.replace(b"host_reader_actions=1", b"host_reader_actions=2", 1)
        )
        expect_current("action marker differs")
        plain_log.write_bytes(plain_log_baseline)

        seeded_summary.write_bytes(
            seeded_summary_baseline.replace(b'"action_count": 1', b'"action_count": 2', 1)
        )
        expect_current("seeded action summary differs")
        seeded_summary.write_bytes(
            seeded_summary_baseline.replace(b'"action_count": 1', b'"action_count": true', 1)
        )
        expect_current("seeded action summary differs")
        seeded_summary.write_bytes(
            seeded_summary_baseline.replace(
                b'"status": "ready"',
                b'"status": "ready", "status": "ready"',
                1,
            )
        )
        expect_current("duplicate field")
        seeded_summary.write_bytes(seeded_summary_baseline)

        write_host_run_result(
            agentos_run_result,
            target="agentos",
            state_dir=agentos,
            extracted_state_files=len(agentos_file_list()),
            action_records=2,
        )
        expect_current("embedded action record count differs", bind_state=False)

        write_host_run_result(
            agentos_run_result,
            target="agentos",
            state_dir=agentos,
            extracted_state_files=len(agentos_file_list()),
            passed=0,
        )
        expect_current("Host run did not pass", bind_state=False)

        write_host_run_result(
            agentos_run_result,
            target="agentos",
            state_dir=agentos,
            extracted_state_files=len(agentos_file_list()) - 1,
        )
        expect_current("does not bind the Guest state count", bind_state=False)

        agentos_run_result.unlink()
        expect_current("AgentOS Host run result is missing or unsafe", bind_state=False)
        agentos_run_result.write_bytes(agentos_run_baseline)

        write_state_file(agentos, "rp_host_run_result", "status=ready\n")
        expect_current("files inventory differs from the state directory", bind_state=False)
        (agentos / "rp_host_run_result").unlink()
        write_state_file(agentos, "rp_host_run_result", "status=ready\n")
        write_summary(
            agentos,
            sorted(
                path.name
                for path in agentos.iterdir()
                if path.is_file() and path.name != "extract-summary.json"
            ),
        )
        expect_current(
            "Host run result must not appear in Guest state inventory",
            bind_state=False,
        )
        restore_state(agentos, agentos_baseline)

        write_agentos_evidence_files(agentos, omit_token="metadata_query=used_index")
        write_agentos_mainflow_stages(agentos, omit_token="metadata_query=used_index")
        expect_current("missing token")

        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        mainflow = agentos / "rp_agentos_mainflow"
        assert compare.verify_agentos_mainflow_stages(agentos) == len(
            compare.AGENTOS_MAINFLOW_STAGES
        )
        runtime_text = mainflow.read_text(encoding="utf-8")
        runtime_lines = runtime_text.splitlines()
        stage_lines = runtime_lines[: len(compare.AGENTOS_MAINFLOW_STAGES)]
        other_lines = runtime_lines[len(compare.AGENTOS_MAINFLOW_STAGES) :]
        all_facts = [
            f"{key}={value}"
            for spec in compare.MAIN_FLOW_SOURCE_SPECS
            for key, value in spec.telemetry_fields
        ]
        stage_mutations = (
            ("missing", stage_lines[:-1] + other_lines),
            ("duplicate", stage_lines + [stage_lines[0]] + other_lines),
            ("out of order", list(reversed(stage_lines)) + other_lines),
            (
                "missing",
                [
                    ";".join(["stage=entry", *all_facts, "status=ready"]),
                    *other_lines,
                ],
            ),
        )
        for expected, lines in stage_mutations:
            mainflow.write_bytes(("\n".join(lines) + "\n").encode())
            expect_value_error(
                lambda: compare.verify_agentos_mainflow_stages(agentos),
                expected,
            )

        write_agentos_mainflow_stages(agentos)
        runtime_text = mainflow.read_text(encoding="utf-8")
        mainflow.write_bytes(
            (runtime_text
            + "evidence_role=runtime_verified;stage=audit;source=rp_agentos_audit;"
            "generation=runtime;status=verified\n").encode(),
        )
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "Guest runtime verification is forbidden",
        )

        write_agentos_mainflow_stages(agentos)
        runtime_text = mainflow.read_text(encoding="utf-8")
        mainflow.write_bytes(
            ("stage=forged;generation=runtime;status=verified\n" + runtime_text).encode(),
        )
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "Guest runtime verification is forbidden",
        )

        write_agentos_mainflow_stages(agentos)
        runtime_text = mainflow.read_text(encoding="utf-8")
        mainflow.write_bytes(
            (runtime_text + "diagnostic=guest_failure;status=failed\n").encode()
        )
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "non-stage record",
        )

        write_agentos_mainflow_stages(agentos)
        runtime_lines = mainflow.read_text(encoding="utf-8").splitlines()
        runtime_lines[0] += ";debug=forged"
        mainflow.write_bytes(("\n".join(runtime_lines) + "\n").encode())
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "schema differs",
        )

        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        audit_path = agentos / "rp_agentos_audit"
        failed_audit = (
            audit_path.read_bytes().rstrip(b"\n") + b";status=failed\n"
        )
        audit_path.write_bytes(failed_audit)
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "Host-derived source status failed",
        )

        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        audit_source = audit_path.read_bytes()
        conflicting_audit = (
            audit_source.rstrip(b"\n") + b";audit_source=forged\n"
        )
        audit_path.write_bytes(conflicting_audit)
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "Host-derived claim failed",
        )

        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        malformed_audit = (
            audit_path.read_bytes().rstrip(b"\n") + b";unrelated=a=b\n"
        )
        audit_path.write_bytes(malformed_audit)
        expect_value_error(
            lambda: compare.verify_agentos_mainflow_stages(agentos),
            "Host-derived source is not canonical",
        )

        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        write_timing(agentos, "agentos_workers")
        expect_current("invalid AgentOS role")

        write_timing(agentos, "agentos")
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace(
            "program=rp_service_surface;role=artifact",
            "program=rp_service_surface;role=orchestrator",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_current("invalid AgentOS role")

        write_timing(agentos, "agentos")
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        support_program = next(
            program
            for program in timing_programs()
            if agentos_program_launch_contract(program)[0] == "agent_worker_batch"
        )
        support_launcher, support_identity_source = agentos_program_launch_contract(
            support_program
        )
        bad = bad.replace(
            f"program={support_program};role=plain;launcher={support_launcher}",
            f"program={support_program};role=sentinel;launcher=agent_create_role",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_current("invalid AgentOS role")

        write_timing(agentos, "agentos")
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        direct_program = next(
            program
            for program in timing_programs()
            if agentos_program_launch_contract(program)[0] == "agent_worker_create"
        )
        bad = bad.replace(
            f"program={direct_program};role=plain;launcher=agent_worker_create",
            f"program={direct_program};role=sentinel;launcher=agent_worker_create",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_current("invalid AgentOS role")

        write_timing(agentos, "agentos")
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace("is_agent=0;agent_role=0", "is_agent=1;agent_role=1", 1)
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_current("mismatched self-checked identity")

        write_timing(agentos, "agentos")
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace(
            f"program={support_program};role=plain;launcher={support_launcher};"
            f"identity_source={support_identity_source}",
            f"program={support_program};role=plain;launcher={support_launcher};"
            "identity_source=child_after_exec",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_current("invalid trusted CRT identity evidence")

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_state_file(state, "rp_source", "status=ready\n")
        write_state_file(state, "rp_evidence", "status=ready\n")
        write_summary(state, ["rp_source", "rp_evidence"])
        assert set(compare.read_summary(state)["files"]) == {
            "rp_source",
            "rp_evidence",
        }
        write_summary(state, ["../rp_source", "rp_evidence"])
        expect_value_error(
            lambda: compare.read_summary(state),
            "invalid files inventory",
        )
        write_summary(state, ["rp_source", "rp_evidence"])
        data = (state / "rp_source").read_bytes()
        valid = {
            "evidence_role": "runtime_verified",
            "generation": "runtime",
            "runtime_case": "case-a",
            "source": "rp_source",
            "source_bytes": str(len(data)),
            "source_hash": str(compare.fnv1a64(data)),
            "assertions_executed": "1",
            "assertions_passed": "1",
            "status": "verified",
        }
        assert compare.is_source_bound_runtime_record(state, valid)
        for unsafe in (
            "../rp_source",
            "nested/rp_source",
            str((state / "rp_source").resolve()),
        ):
            fields = dict(valid, source=unsafe)
            expect_value_error(
                lambda fields=fields: compare.is_source_bound_runtime_record(state, fields),
                "not a state-file name",
            )
        fields = dict(valid, source="rp_missing")
        expect_value_error(
            lambda: compare.is_source_bound_runtime_record(state, fields),
            "outside the state inventory",
        )
        write_state_file(state, "rp_unlisted", "status=ready\n")
        fields = dict(valid, source="rp_unlisted")
        expect_value_error(
            lambda: compare.is_source_bound_runtime_record(state, fields),
            "files inventory differs",
        )

        link = state / "rp_link"
        try:
            link.symlink_to(state / "rp_source")
        except OSError:
            pass
        else:
            # 某些 MSYS Python 构建会用普通副本模拟 symlink_to()。
            # 仅在平台确实创建链接时测试链接拒绝逻辑。
            if link.is_symlink():
                write_summary(state, ["rp_source", "rp_evidence", "rp_link"])
                fields = dict(valid, source="rp_link")
                expect_value_error(
                    lambda: compare.is_source_bound_runtime_record(state, fields),
                    "unsafe entry",
                )

        unauthorized = state / "rp_reference"
        write_state_file(
            state,
            "rp_reference",
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "evidence_file_status=reference_ready\n"
            "status=reference_ready\n"
            "status=ready\n",
        )
        expect_value_error(
            lambda: compare.is_reference_file(unauthorized, "plain"),
            "unauthorized plain reference product",
        )

        envelope = state / "rp_coherence"
        write_state_file(
            state,
            "rp_coherence",
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "evidence_file_status=reference_ready\n"
            "status=reference_ready\n"
            "status=ready\n",
        )
        assert compare.is_reference_file(envelope, "plain")

        write_state_file(
            state,
            "rp_coherence",
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "evidence_file_status=reference_ready\n"
            "evidence_file_status=reference_ready\n",
        )
        expect_value_error(
            lambda: compare.top_level_fields(envelope),
            "duplicate file evidence envelope key",
        )

        write_state_file(
            state,
            "rp_coherence",
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "status=ready\n",
        )
        expect_value_error(
            lambda: compare.is_reference_file(envelope, "plain"),
            "incomplete file envelope",
        )
        expect_value_error(
            lambda: compare.is_reference_record(
                {"evidence_role": "demo_reference", "status": "ready"},
                "plain",
                file_name="rp_package",
            ),
            "unknown evidence envelope",
        )
        assert not compare.is_reference_record(
            {"status": "reference_ready"}, "plain", file_name="rp_package"
        )
        assert compare.is_reference_record(
            {
                "evidence_role": "demo_reference",
                "catalog_generation": "demo_expected",
                "decision_support": "rp_decsupport",
                "status": "reference_ready",
            },
            "plain",
            file_name="rp_package",
        )

        inventory = state / "inventory"
        inventory.mkdir()
        write_state_file(inventory, "rp_source", "status=ready\n")
        write_state_file(inventory, "rp_evidence", "status=ready\n")
        write_backend_catalog(inventory, "agentos")
        seed_reference_inventory(inventory, "agentos")
        inventory_files = sorted(path.name for path in inventory.iterdir())
        write_summary(inventory, inventory_files)
        runtime_data = (inventory / "rp_source").read_bytes()
        duplicate_fields = dict(
            valid,
            source_bytes=str(len(runtime_data)),
            source_hash=str(compare.fnv1a64(runtime_data)),
        )
        duplicate_line = ";".join(
            f"{key}={value}" for key, value in duplicate_fields.items()
        )
        write_state_file(
            inventory,
            "rp_evidence",
            duplicate_line + "\n" + duplicate_line + "\n",
        )
        expect_value_error(
            lambda: compare.collect_evidence_counts(
                inventory, set(inventory_files), "agentos"
            ),
            "duplicate source-bound runtime evidence",
        )

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_timing(state, "plain")
        observation = program_inventory_line(state, "plain").rstrip("\n")
        write_state_file(state, "rp_agentcmp", "status=ready\n" + observation + "\n")
        write_backend_catalog(state, "plain")
        seed_reference_inventory(state, "plain")
        state_files = sorted(path.name for path in state.iterdir())
        write_summary(state, state_files)
        agentcmp = state / "rp_agentcmp"
        catalog = agentcmp.read_text(encoding="utf-8")
        counts = compare.collect_evidence_counts(
            state, set(state_files), "plain"
        )
        assert counts["reference_records"] == 19, counts
        assert counts["reference_identities"] == list(
            reference_catalog.expected_reference_identities("plain")
        ), counts

        for key in (
            "evidence_generation",
            "observation_source",
            "program_source",
            "program_source_bytes",
            "program_source_hash",
            "program_names_digest",
            "programs_observed",
        ):
            fields = observation.split(";")
            mutated = ";".join(
                field for field in fields if not field.startswith(key + "=")
            )
            agentcmp.write_text(
                catalog.replace(observation, mutated, 1), encoding="utf-8"
            )
            expect_value_error(
                lambda: compare.collect_evidence_counts(
                    state, set(state_files), "plain"
                ),
                "rp_agentcmp:",
            )

        agentcmp.write_text(
            catalog.replace(
                observation,
                observation.replace(
                ";status=reference_observed",
                ";catalog_generation=demo_expected;status=reference_observed",
                ),
                1,
            ),
            encoding="utf-8",
        )
        expect_value_error(
            lambda: compare.collect_evidence_counts(
                state, set(state_files), "plain"
            ),
            "mixes catalog and runtime observation",
        )

        for original, replacement in (
            ("observation_source=guest_runtime", "observation_source=host_fixture"),
            ("status=reference_observed", "status=verified"),
        ):
            agentcmp.write_text(
                catalog.replace(
                    observation, observation.replace(original, replacement), 1
                ),
                encoding="utf-8",
            )
            expect_value_error(
                lambda: compare.collect_evidence_counts(
                    state, set(state_files), "plain"
                ),
                "rp_agentcmp:",
            )

        measured = compare.measure_program_inventory(state)
        agentcmp.write_text(
            catalog.replace(
                observation,
                observation.replace(
                    f"program_source_hash={measured['program_source_hash']}",
                    f"program_source_hash={measured['program_source_hash'] + 1}",
                ),
                1,
            ),
            encoding="utf-8",
        )
        expect_value_error(
            lambda: compare.collect_evidence_counts(
                state, set(state_files), "plain"
            ),
            "program_source_hash is not source-bound",
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plain = root / "plain"
        agentos = root / "agentos"
        plain.mkdir()
        agentos.mkdir()
        write_state_file(plain, "rp_backend", "runtime_cases=0\n")
        write_state_file(plain, "rp_backend_exec", "runtime_cases=0\n")
        write_summary(plain, ["rp_backend", "rp_backend_exec"])
        write_summary(agentos, [])
        evidence = compare.collect_runner_tick_evidence(plain)
        assert evidence == {
            "status": "unavailable",
            "reason": "plain_runtime_cases_zero",
        }, evidence

        write_state_file(plain, "rp_backend", "runtime_cases=1\n")
        expect_value_error(
            lambda: compare.collect_runner_tick_evidence(plain),
            "requires runtime_cases=0",
        )

    print("test_compare_dual_platform_state: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
