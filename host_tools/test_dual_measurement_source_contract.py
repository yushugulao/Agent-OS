#!/usr/bin/env python3
"""双平台测量必须由私有运行目录中的真实 Guest 日志生成。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run-dual-platforms.sh"


def validate_source(source: str) -> None:
    reject = source.find("external measured Agent log injection is forbidden")
    claim = source.find("create_private_directory")
    first_stage = source.find('stage_begin "structure-check"')
    measured = source.find('stage_begin "measured-file-query"')
    if min(reject, claim, first_stage, measured) < 0 or not reject < claim < first_stage:
        raise ValueError("外部证据没有在运行前拒绝")
    for required in (
        "secrets.token_hex(12)",
        "create_private_directory(Path(requested))",
        "umask 077",
    ):
        if required not in source:
            raise ValueError(f"私有运行目录缺少约束：{required}")
    stage = source[measured:]
    if "MEASURED_AGENT_GUEST_LOG" in stage or "MEASURED_AGENT_COMMAND_JSON" in stage:
        raise ValueError("测量阶段读取了外部证据")
    for required in (
        'measurement_guest_log="${DUAL_LOG_DIR}/dual-targeted-agentbench-guest.log"',
        "AGENT_TEST_CASE=agentbench_ucore",
        "bash scripts/run-agent-tests.sh",
        '--guest-log "${measurement_guest_log}"',
        '--generation "${DUAL_RUN_GENERATION}"',
        '--receipt-out "${measurement_receipt}"',
    ):
        if required not in stage:
            raise ValueError(f"真实测量链缺少约束：{required}")


def main() -> int:
    source = RUNNER.read_text(encoding="utf-8")
    validate_source(source)
    mutations = (
        source.replace("secrets.token_hex(12)", '"fixed"', 1),
        source.replace(
            'measurement_guest_log="${DUAL_LOG_DIR}/dual-targeted-agentbench-guest.log"',
            'measurement_guest_log="${MEASURED_AGENT_GUEST_LOG}"',
            1,
        ),
        source.replace("bash scripts/run-agent-tests.sh", "true", 1),
        source.replace('--receipt-out "${measurement_receipt}"', "", 1),
    )
    for mutation in mutations:
        try:
            validate_source(mutation)
        except ValueError:
            continue
        raise AssertionError("测量来源篡改未被拒绝")
    print("test_dual_measurement_source_contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
