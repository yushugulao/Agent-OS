#!/usr/bin/env python3
"""Regression checks for runner-owned measured Guest evidence."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-dual-platforms.sh"


def validate_source(source: str) -> None:
    guard = "external measured Agent log injection is forbidden"
    guard_at = source.find(guard)
    first_work_at = source.find('mkdir -p "${DUAL_LOG_DIR}"')
    stage_at = source.find('stage_begin "measured-file-query"')
    if guard_at < 0 or first_work_at < 0 or guard_at > first_work_at:
        raise ValueError("external evidence is not rejected before runner work")
    if stage_at < 0:
        raise ValueError("targeted measurement stage is missing")
    stage = source[stage_at:]
    if "MEASURED_AGENT_GUEST_LOG" in stage or "MEASURED_AGENT_COMMAND_JSON" in stage:
        raise ValueError("targeted measurement stage consumes external evidence")
    for required in (
        'measurement_guest_log="${DUAL_LOG_DIR}/dual-targeted-agentbench-guest.log"',
        "AGENT_TEST_CASE=agentbench_ucore",
        "bash scripts/run-agent-tests.sh",
        '--guest-log "${measurement_guest_log}"',
        '--command-json "${measurement_command_json}"',
    ):
        if required not in stage:
            raise ValueError(f"targeted measurement stage is incomplete: {required}")


def main() -> int:
    source = RUNNER.read_text(encoding="utf-8")
    validate_source(source)
    mutations = (
        source.replace("external measured Agent log injection is forbidden", "external evidence accepted", 1),
        source.replace(
            'measurement_guest_log="${DUAL_LOG_DIR}/dual-targeted-agentbench-guest.log"',
            'measurement_guest_log="${MEASURED_AGENT_GUEST_LOG}"',
            1,
        ),
        source.replace(
            'stage_begin "measured-file-query"',
            'stage_begin "measured-file-query"\nMEASURED_AGENT_COMMAND_JSON="${MEASURED_AGENT_COMMAND_JSON:-[]}"',
            1,
        ),
    )
    for mutated in mutations:
        try:
            validate_source(mutated)
        except ValueError:
            pass
        else:
            raise AssertionError("external evidence acceptance mutation was not rejected")
    print("test_dual_measurement_source_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
