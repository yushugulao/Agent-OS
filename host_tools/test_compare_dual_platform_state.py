#!/usr/bin/env python3
"""Unit checks for dual-platform state comparison."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import compare_dual_platform_state as compare


RUNNER_SOURCE_FILES = ("rp_runner_context_src", "rp_runner_fsmeta_src")


def write_state_file(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def runtime_runner_line(
    root: Path, case: str, source: str, ticks: int, reason: str
) -> str:
    data = (root / source).read_bytes()
    return (
        f"evidence_role=runtime_verified;generation=runtime;runner_case={case};"
        f"source={source};source_bytes={len(data)};source_hash={compare.fnv1a64(data)};"
        "assertions_executed=1;assertions_passed=1;"
        f"reason={reason};ticks={ticks};status=verified\n"
    )


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


def write_required_runtime_files(root: Path, action_records: int = 1, passed: int = 1) -> None:
    write_state_file(
        root,
        "rp_host_run_result",
        "host_runner=plain_ucore_action_runner\n"
        "status=ready\n"
        f"passed={passed}\n"
        f"embedded_action_records={action_records}\n"
        "qemu_orch_passed=1\n",
    )


def write_agentos_evidence_files(root: Path, omit_token: str = "") -> None:
    for file_name, tokens in compare.AGENTOS_EVIDENCE_REQUIREMENTS.items():
        kept = [token for token in tokens if token != omit_token]
        write_state_file(root, file_name, "\n".join(kept) + "\nstatus=ready\n")


def write_agentos_mainflow_stages(
    root: Path,
    stages: tuple[str, ...] = compare.AGENTOS_MAINFLOW_STAGES,
    omit_token: str = "",
) -> None:
    text = "".join(f"stage={stage};status=ready\n" for stage in stages)
    tokens = [token for token in compare.AGENTOS_EVIDENCE_REQUIREMENTS["rp_agentos_mainflow"] if token != omit_token]
    text += "\n".join(tokens) + "\n"
    write_state_file(root, "rp_agentos_mainflow", text)


def write_timing(root: Path, launcher: str = "fork", hybrid_agentos: bool = False) -> None:
    required = sorted(compare.AGENTOS_REQUIRED_AGENT_PROGRAMS)
    support = [f"rp_case_{i}" for i in range(70 - len(required))]
    programs = required + support
    lines = []
    for i, program in enumerate(programs):
        is_agent = hybrid_agentos and program in compare.AGENTOS_REQUIRED_AGENT_PROGRAMS
        agentos_record = hybrid_agentos or launcher == "agent_worker_create"
        record_launcher = (
            "agent_create_role"
            if is_agent
            else "agent_worker_create" if agentos_record else launcher
        )
        record_role = (
            compare.AGENTOS_REQUIRED_AGENT_ROLES[program] if is_agent else "plain"
        )
        identity = ""
        if agentos_record:
            identity = (
                ";identity_source=child_after_exec"
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


def agentos_file_list() -> list[str]:
    return [
        "rp_backend",
        "rp_backend_exec",
        "rp_agentcmp",
        "rp_host_run_result",
        "rp_orch_timing",
        *RUNNER_SOURCE_FILES,
        *compare.AGENTOS_EVIDENCE_REQUIREMENTS.keys(),
    ]


def expect_failure(plain: Path, agentos: Path, expected: str) -> None:
    try:
        compare.compare_state(plain, agentos, min_common_files=1)
    except ValueError as exc:
        assert expected in str(exc), str(exc)
        return
    raise AssertionError("comparison unexpectedly passed")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plain = root / "plain"
        agentos = root / "agentos"
        plain.mkdir()
        agentos.mkdir()

        write_state_file(plain, "rp_backend", "runner_report=file_scan;status=ready\n")
        write_state_file(plain, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_state_file(plain, "rp_runner_context_src", "path=plain-context\n")
        write_state_file(plain, "rp_runner_fsmeta_src", "path=plain-fsmeta\n")
        write_state_file(
            plain,
            "rp_backend_exec",
            "evidence_role=demo_reference;catalog_generation=demo_expected;runner_case=user-context;reference_result=expected_pass;ticks=999;status=reference_ready\n"
            + runtime_runner_line(plain, "user-context", "rp_runner_context_src", 6, "user_space_context_log")
            + runtime_runner_line(plain, "user-fsmeta", "rp_runner_fsmeta_src", 7, "file_manifest_scan")
            + "runner_report=user-context;plain_cost=rebuild_steps_6;agentos_replace=none;risk=untrusted_context;status=passed\n",
        )
        write_required_runtime_files(plain)
        write_timing(plain, "fork_seeded")
        write_summary(plain, ["rp_backend", "rp_backend_exec", "rp_agentcmp", "rp_host_run_result", "rp_orch_timing", *RUNNER_SOURCE_FILES])

        write_state_file(agentos, "rp_backend", "runner_report=file_scan;status=ready;kernel=observed\n")
        write_state_file(agentos, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_state_file(agentos, "rp_runner_context_src", "path=agentos-context\n")
        write_state_file(agentos, "rp_runner_fsmeta_src", "path=agentos-fsmeta\n")
        write_state_file(
            agentos,
            "rp_backend_exec",
            "evidence_role=demo_reference;catalog_generation=demo_expected;runner_case=agentos-context;reference_result=expected_pass;ticks=999;status=reference_ready\n"
            + runtime_runner_line(agentos, "agentos-context", "rp_runner_context_src", 1, "kernel_context")
            + runtime_runner_line(agentos, "agentos-fsmeta", "rp_runner_fsmeta_src", 1, "kernel_metadata")
            + "runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=passed\n"
            + "runner_report=agentos-edit;plain_cost=userland_lock_file;agentos_replace=kernel_edit_lease;risk=lost_update;status=passed\n",
        )
        write_required_runtime_files(agentos)
        write_timing(agentos, "fork", hybrid_agentos=True)
        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        write_summary(agentos, agentos_file_list())

        summary = compare.compare_state(plain, agentos, min_common_files=4)
        assert summary["plain_files"] == 7, summary
        assert summary["agentos_files"] == 19, summary
        assert summary["common_files"] == 7, summary
        assert summary["agentos_extra_files"] == 12, summary
        assert summary["checked_compatibility_records"] == 4, summary
        assert summary["plain_reference_records"] == 1, summary
        assert summary["agentos_reference_records"] == 1, summary
        assert summary["source_bound_runtime_records"] == 2, summary
        assert summary["preserved_plain_costs"] == 1, summary
        assert summary["cost_replacement_count"] == 2, summary
        assert any(
            row["plain_cost"] == "rebuild_steps_6" and row["preserved_from_plain"] == 1
            for row in summary["cost_replacements"]
        ), summary
        assert any(
            row["plain_cost"] == "userland_lock_file" and row["preserved_from_plain"] == 0
            for row in summary["cost_replacements"]
        ), summary
        assert summary["runner_tick_pairs"] == 2, summary
        assert any(
            row["label"] == "上下文路径"
            and row["plain_ticks"] == 6
            and row["agentos_ticks"] == 1
            and row["saved_ticks"] == 5
            for row in summary["runner_tick_comparison"]
        ), summary
        assert any(
            row["label"] == "文件对象查询"
            and row["plain_ticks"] == 7
            and row["agentos_ticks"] == 1
            for row in summary["runner_tick_comparison"]
        ), summary
        assert summary["embedded_action_records"] == 1, summary
        assert summary["run_result_match"] == 1, summary
        assert summary["agentos_evidence_checks"] == 32, summary
        assert len(summary["scenario_evidence"]) == len(compare.SCENARIO_EVIDENCE_SPECS), summary
        assert all(row["status"] == "ready" for row in summary["scenario_evidence"]), summary
        assert any(row["scenario"] == "Context Path" and row["matched"] >= 3 for row in summary["scenario_evidence"]), summary
        assert summary["agentos_mainflow_stages"] == len(compare.AGENTOS_MAINFLOW_STAGES), summary
        assert summary["agentos_mainflow_facts"] == len(compare.AGENTOS_MAINFLOW_FACTS), summary
        assert summary["plain_timing_records"] == 70, summary
        assert summary["plain_agent_launches"] == 0, summary
        assert summary["plain_fork_launches"] == 70, summary
        assert summary["agentos_timing_records"] == 70, summary
        assert summary["agentos_agent_launches"] == len(compare.AGENTOS_REQUIRED_AGENT_PROGRAMS), summary
        assert summary["agentos_worker_launches"] == 70 - len(compare.AGENTOS_REQUIRED_AGENT_PROGRAMS), summary

        (agentos / "rp_agentcmp").unlink()
        write_summary(agentos, ["rp_backend", "rp_backend_exec", "rp_host_run_result", "rp_orch_timing", *RUNNER_SOURCE_FILES, *compare.AGENTOS_EVIDENCE_REQUIREMENTS.keys()])
        expect_failure(plain, agentos, "missing plain files")

        write_state_file(agentos, "rp_agentcmp", "plain_kernel=failed;programs=69;status=failed\n")
        write_summary(agentos, agentos_file_list())
        expect_failure(plain, agentos, "missing plain compatibility records")

        write_state_file(agentos, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_summary(agentos, agentos_file_list())
        write_required_runtime_files(agentos, action_records=2)
        expect_failure(plain, agentos, "embedded action record count differs")

        write_required_runtime_files(agentos, passed=0)
        expect_failure(plain, agentos, "host run did not pass")

        write_required_runtime_files(agentos)
        write_agentos_evidence_files(agentos, omit_token="metadata_query=used_index")
        write_agentos_mainflow_stages(agentos, omit_token="metadata_query=used_index")
        expect_failure(agentos=agentos, plain=plain, expected="missing token")

        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos, compare.AGENTOS_MAINFLOW_STAGES[:-1])
        expect_failure(agentos=agentos, plain=plain, expected="missing kernel stage records")

        write_agentos_mainflow_stages(agentos, tuple(reversed(compare.AGENTOS_MAINFLOW_STAGES)))
        expect_failure(agentos=agentos, plain=plain, expected="out of order")

        write_agentos_mainflow_stages(agentos)
        write_timing(agentos, "agent_worker_create", hybrid_agentos=False)
        expect_failure(agentos=agentos, plain=plain, expected="used the worker launcher")

        write_timing(agentos, "fork", hybrid_agentos=True)
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace(
            "program=rp_service_surface;role=artifact",
            "program=rp_service_surface;role=orchestrator",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_failure(agentos=agentos, plain=plain, expected="wrong role")

        write_timing(agentos, "fork", hybrid_agentos=True)
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace(
            "program=rp_case_0;role=plain;launcher=agent_worker_create",
            "program=rp_case_0;role=sentinel;launcher=agent_create_role",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_failure(agentos=agentos, plain=plain, expected="unmapped Agent program")

        write_timing(agentos, "fork", hybrid_agentos=True)
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace(
            "role=plain;launcher=agent_worker_create",
            "role=sentinel;launcher=agent_worker_create",
            1,
        )
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_failure(agentos=agentos, plain=plain, expected="delegated worker is not recorded as a plain process")

        write_timing(agentos, "fork", hybrid_agentos=True)
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace("is_agent=0;agent_role=0", "is_agent=1;agent_role=1", 1)
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_failure(agentos=agentos, plain=plain, expected="worker identity is not post-exec attested")

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_state_file(state, "rp_source", "status=ready\n")
        write_state_file(state, "rp_evidence", "status=ready\n")
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
            "outside the state inventory",
        )

        link = state / "rp_link"
        try:
            link.symlink_to(state / "rp_source")
        except OSError:
            pass
        else:
            write_summary(state, ["rp_source", "rp_evidence", "rp_link"])
            fields = dict(valid, source="rp_link")
            expect_value_error(
                lambda: compare.is_source_bound_runtime_record(state, fields),
                "missing or unsafe",
            )

        envelope = state / "rp_reference"
        write_state_file(
            state,
            "rp_reference",
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "evidence_file_status=reference_ready\n"
            "status=reference_ready\n"
            "status=ready\n",
        )
        assert compare.is_reference_file(envelope)

        write_state_file(
            state,
            "rp_reference",
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
            "rp_reference",
            "evidence_file_role=demo_reference\n"
            "evidence_file_generation=demo_expected\n"
            "status=ready\n",
        )
        expect_value_error(
            lambda: compare.is_reference_file(envelope),
            "incomplete file envelope",
        )
        expect_value_error(
            lambda: compare.is_reference_record(
                {"evidence_role": "demo_reference", "status": "ready"}
            ),
            "incomplete evidence role",
        )

        write_summary(state, ["rp_source", "rp_evidence"])
        duplicate_line = ";".join(f"{key}={value}" for key, value in valid.items())
        write_state_file(
            state,
            "rp_evidence",
            duplicate_line + "\n" + duplicate_line + "\n",
        )
        expect_value_error(
            lambda: compare.collect_evidence_counts(
                state, {"rp_source", "rp_evidence"}
            ),
            "duplicate source-bound runtime evidence",
        )

    print("test_compare_dual_platform_state: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
