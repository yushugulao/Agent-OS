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
        write_required_runtime_files(plain)
        write_summary(plain, ["rp_backend", "rp_agentcmp", "rp_host_run_result"])

        write_state_file(agentos, "rp_backend", "runner_report=file_scan;status=ready;kernel=observed\n")
        write_state_file(agentos, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_state_file(agentos, "rp_agentos_mainflow", "stage=context;status=ready\n")
        write_required_runtime_files(agentos)
        write_summary(
            agentos,
            ["rp_backend", "rp_agentcmp", "rp_agentos_mainflow", "rp_host_run_result"],
        )

        summary = compare.compare_state(plain, agentos, min_common_files=3)
        assert summary["plain_files"] == 3, summary
        assert summary["agentos_files"] == 4, summary
        assert summary["common_files"] == 3, summary
        assert summary["agentos_extra_files"] == 1, summary
        assert summary["checked_success_records"] == 3, summary
        assert summary["preserved_plain_costs"] == 0, summary
        assert summary["embedded_action_records"] == 1, summary
        assert summary["run_result_match"] == 1, summary

        (agentos / "rp_agentcmp").unlink()
        write_summary(agentos, ["rp_backend", "rp_agentos_mainflow", "rp_host_run_result"])
        expect_failure(plain, agentos, "missing plain files")

        write_state_file(agentos, "rp_agentcmp", "plain_kernel=failed;programs=69;status=failed\n")
        write_summary(
            agentos,
            ["rp_backend", "rp_agentcmp", "rp_agentos_mainflow", "rp_host_run_result"],
        )
        expect_failure(plain, agentos, "missing plain success records")

        write_state_file(agentos, "rp_agentcmp", "plain_kernel=passed;programs=69;status=ready\n")
        write_required_runtime_files(agentos, action_records=2)
        expect_failure(plain, agentos, "embedded action record count differs")

        write_required_runtime_files(agentos, passed=0)
        expect_failure(plain, agentos, "host run did not pass")

    print("test_compare_dual_platform_state: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
