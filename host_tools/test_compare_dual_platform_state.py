#!/usr/bin/env python3
"""Unit checks for dual-platform state comparison."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import compare_dual_platform_state as compare


def write_state_file(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


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
        record_launcher = "agent_create_role" if is_agent else launcher
        record_role = "orchestrator" if is_agent else "plain"
        lines.append(
            f"program={program};role={record_role};launcher={record_launcher};ok=1;code=0;elapsed_ms={i + 1}\n"
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
        write_state_file(
            plain,
            "rp_backend_exec",
            "runner_report=user-context;plain_cost=rebuild_steps_6;agentos_replace=none;risk=untrusted_context;status=passed\n",
        )
        write_state_file(plain, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_required_runtime_files(plain)
        write_timing(plain, "fork_seeded")
        write_summary(plain, ["rp_backend", "rp_backend_exec", "rp_agentcmp", "rp_host_run_result", "rp_orch_timing"])

        write_state_file(agentos, "rp_backend", "runner_report=file_scan;status=ready;kernel=observed\n")
        write_state_file(
            agentos,
            "rp_backend_exec",
            "runner_report=agentos-context;plain_cost=rebuild_steps_6;agentos_replace=kernel_context_path;risk=untrusted_context;status=passed\n"
            "runner_report=agentos-edit;plain_cost=userland_lock_file;agentos_replace=kernel_edit_lease;risk=lost_update;status=passed\n",
        )
        write_state_file(agentos, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_required_runtime_files(agentos)
        write_timing(agentos, "fork", hybrid_agentos=True)
        write_agentos_evidence_files(agentos)
        write_agentos_mainflow_stages(agentos)
        write_summary(agentos, agentos_file_list())

        summary = compare.compare_state(plain, agentos, min_common_files=4)
        assert summary["plain_files"] == 5, summary
        assert summary["agentos_files"] == 17, summary
        assert summary["common_files"] == 5, summary
        assert summary["agentos_extra_files"] == 12, summary
        assert summary["checked_success_records"] == 4, summary
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
        assert summary["agentos_fork_launches"] == 70 - len(compare.AGENTOS_REQUIRED_AGENT_PROGRAMS), summary

        (agentos / "rp_agentcmp").unlink()
        write_summary(agentos, ["rp_backend", "rp_backend_exec", "rp_host_run_result", "rp_orch_timing", *compare.AGENTOS_EVIDENCE_REQUIREMENTS.keys()])
        expect_failure(plain, agentos, "missing plain files")

        write_state_file(agentos, "rp_agentcmp", "plain_kernel=failed;programs=69;status=failed\n")
        write_summary(agentos, agentos_file_list())
        expect_failure(plain, agentos, "missing plain success records")

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
        write_timing(agentos, "fork", hybrid_agentos=False)
        expect_failure(agentos=agentos, plain=plain, expected="missing required Agent launches")

        write_timing(agentos, "fork", hybrid_agentos=True)
        bad = (agentos / "rp_orch_timing").read_text(encoding="utf-8")
        bad = bad.replace("role=plain;launcher=fork", "role=sentinel;launcher=fork", 1)
        write_state_file(agentos, "rp_orch_timing", bad)
        expect_failure(agentos=agentos, plain=plain, expected="fork support program is not recorded as plain process")

    print("test_compare_dual_platform_state: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
