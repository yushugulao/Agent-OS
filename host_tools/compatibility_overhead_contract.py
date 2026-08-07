#!/usr/bin/env python3
"""对称 baseline/AgentOS 兼容性基准的合同。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
import signal
import statistics
from pathlib import Path
from typing import Any


SCHEMA = "agentos-compatibility-overhead-v2"
PLAN_SCHEMA = "agentos-compatibility-overhead-plan-v2"
BUILD_STAMP_SCHEMA = "agentos-compatibility-build-stamp-v1"
SOURCE_SCHEMA = "agentos-compatibility-source-v2"
FORMAL_CONTEXT_SCHEMA = "agentos-compatibility-formal-context-v2"
GUEST_SCHEMA = 2
GUEST_ROUNDS = 3
FORMAL_BOOT_COUNT = 7
GUEST_CACHE_STATE = "warm_guest_paths"
GUEST_SCHEDULE = "challenge_rotated_v1"
MEASUREMENT_STATE = {
    "cache_state": GUEST_CACHE_STATE,
    "schedule": GUEST_SCHEDULE,
    "untimed_warmup": True,
    "fresh_boot_per_target": True,
}
CANONICAL_SOURCE = "evaluation_guest/compatbench.c"
TARGETS = ("plain", "agentos")
ORDERS = (("plain", "agentos"), ("agentos", "plain"))
COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
HEX16_RE = re.compile(r"[0-9a-f]{16}\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_GUEST_LOG_BYTES = 2 * 1024 * 1024
MAX_ELAPSED_MS = 30 * 60 * 1000
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
EVIDENCE_TIER = "formal_traditional_compatibility_tax"
LIMITATIONS = (
    "Results describe traditional uCore compatibility and Guest application-path overhead only.",
    "Metrics remain separate; no composite AgentOS score is defined.",
    "AgentOS-only capabilities are intentionally outside this comparison.",
    "Both targets compile one canonical C source separately; binary identity is not claimed.",
    "research_artifact_pipeline is an application-shaped full path, not a pure-kernel cost.",
    "Timed samples follow one identical untimed warmup and a challenge-derived rotating metric order.",
    "Guest timings trust the evaluated kernel clock; Host process time is only an outer sanity bound.",
    "Commit provenance is supplied by the formal bundle's measurement-source receipt.",
)

PIPELINE_WORKLOAD = {
    "kind": "bounded_research_artifact_pipeline",
    "input_shards": 8,
    "records_per_shard": 64,
    "source_records": 512,
    "record_bytes": 16,
    "source_bytes": 8192,
    "aggregation_groups": 8,
    "artifact_bytes": 64,
    "stages": [
        "generate_input_shards",
        "read_validate_transform",
        "aggregate_groups",
        "write_read_verify_artifact",
    ],
    "samples_per_target_boot": GUEST_ROUNDS,
    "formal_paired_boots": FORMAL_BOOT_COUNT,
    "formal_samples_per_target": GUEST_ROUNDS * FORMAL_BOOT_COUNT,
    "cache_state": GUEST_CACHE_STATE,
    "schedule": GUEST_SCHEDULE,
}

METRICS: tuple[dict[str, object], ...] = (
    {
        "id": "fork_wait",
        "operations": 32,
        "operation_unit": "process",
        "window": "before_first_fork_to_after_last_waitpid",
    },
    {
        "id": "fork_exec_wait",
        "operations": 12,
        "operation_unit": "process_replacement",
        "window": "before_first_fork_to_after_last_exec_child_waitpid",
    },
    {
        "id": "pipe_roundtrip",
        "operations": 1024,
        "operation_unit": "round_trip",
        "window": "after_child_ready_to_after_last_response",
    },
    {
        "id": "seq_file_io",
        "operations": 262144,
        "operation_unit": "byte_read_or_written",
        "window": "after_create_to_after_read_close",
    },
    {
        "id": "research_artifact_pipeline",
        "operations": 512,
        "operation_unit": "source_record_transformed",
        "window": "before_first_input_create_to_after_verified_artifact_read_close",
        "workload": PIPELINE_WORKLOAD,
        "attribution": "guest_application_full_path_not_pure_kernel",
    },
)
METRIC_BY_ID = {str(item["id"]): item for item in METRICS}
GUEST_SAMPLE_COUNT = GUEST_ROUNDS * len(METRICS)

BEGIN_RE = re.compile(
    rf"compatbench: begin schema={GUEST_SCHEMA} challenge=([0-9a-f]{{16}}) "
    rf"clock=gettimeofday_ms rounds={GUEST_ROUNDS} source=canonical-v2 "
    rf"cache={GUEST_CACHE_STATE} schedule={GUEST_SCHEDULE}\Z"
)
SAMPLE_RE = re.compile(
    rf"compatbench: sample schema={GUEST_SCHEMA} challenge=([0-9a-f]{{16}}) "
    r"metric=([a-z_]+) round=([1-9][0-9]*) ops=([1-9][0-9]*) "
    r"elapsed_ms=([1-9][0-9]*) checksum=([0-9a-f]{8})\Z"
)
DONE_RE = re.compile(
    rf"compatbench: done schema={GUEST_SCHEMA} challenge=([0-9a-f]{{16}}) "
    rf"samples={GUEST_SAMPLE_COUNT} receipt=([0-9a-f]{{8}})\Z"
)
PASS_LINE = "compatbench: passed"


class CompatibilityContractError(ValueError):
    """兼容性证据不满足协议时抛出。"""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fnv_byte(value: int, byte: int) -> int:
    return ((value ^ (byte & 0xFF)) * 16777619) & 0xFFFFFFFF


def _fnv_u32(value: int, word: int) -> int:
    for shift in range(0, 32, 8):
        value = _fnv_byte(value, word >> shift)
    return value


def expected_sample_sequence(challenge: str) -> list[tuple[int, str]]:
    """返回两个目标共同使用的预提交单次启动指标顺序。"""

    if HEX16_RE.fullmatch(challenge) is None or int(challenge, 16) == 0:
        raise CompatibilityContractError("schedule challenge must be nonzero 64-bit hex")
    challenge_value = int(challenge, 16)
    sequence: list[tuple[int, str]] = []
    metric_count = len(METRICS)
    for round_number in range(1, GUEST_ROUNDS + 1):
        start = ((challenge_value & 0xFFFFFFFF) + (round_number - 1) * 2) % metric_count
        forward = ((challenge_value >> 8) ^ round_number) & 1
        for position in range(metric_count):
            offset = position if forward else (-position) % metric_count
            metric = str(METRICS[(start + offset) % metric_count]["id"])
            sequence.append((round_number, metric))
    return sequence


def research_artifact_pipeline_checksum(challenge: str) -> str:
    """根据 challenge 重建确定性的应用工件结果。"""

    if HEX16_RE.fullmatch(challenge) is None or int(challenge, 16) == 0:
        raise CompatibilityContractError("pipeline challenge must be nonzero 64-bit hex")
    challenge_value = int(challenge, 16)
    source_checksum = 2166136261
    group_count = int(PIPELINE_WORKLOAD["aggregation_groups"])
    if group_count <= 0 or group_count & (group_count - 1):
        raise CompatibilityContractError(
            "pipeline aggregation group count must be a power of two"
        )
    group_totals = [0] * group_count
    group_mask = group_count - 1
    records_per_shard = int(PIPELINE_WORKLOAD["records_per_shard"])
    for index in range(int(PIPELINE_WORKLOAD["source_records"])):
        lane = (challenge_value >> ((index & 7) * 8)) & 0xFF
        group = (index + (challenge_value & 0xFFFFFFFF)) & group_mask
        measurement = (
            lane * 257 + index * 17 + (index // records_per_shard) * 31
        ) & 0xFFFF
        proof = 2166136261
        for word in (
            challenge_value & 0xFFFFFFFF,
            challenge_value >> 32,
            index,
            group,
            measurement,
        ):
            proof = _fnv_u32(proof, word)
        for word in (index, group, measurement, proof):
            source_checksum = _fnv_u32(source_checksum, word)
        group_totals[group] = (
            group_totals[group] + ((measurement ^ proof) & 0xFFFF)
        ) & 0xFFFFFFFF

    artifact_checksum = 2166136261
    for word in (
        0x52504150,
        1,
        int(PIPELINE_WORKLOAD["source_records"]),
        int(PIPELINE_WORKLOAD["source_bytes"]),
        int(PIPELINE_WORKLOAD["source_records"]),
        group_count,
        *group_totals,
        source_checksum,
    ):
        artifact_checksum = _fnv_u32(artifact_checksum, word)
    return f"{artifact_checksum:08x}"


def metric_checksum(challenge: str, metric: str) -> str:
    """不信任 Guest 输出，独立重建指标的确定性结果。"""

    if HEX16_RE.fullmatch(challenge) is None or int(challenge, 16) == 0:
        raise CompatibilityContractError("metric challenge must be nonzero 64-bit hex")
    challenge_value = int(challenge, 16)
    value = 2166136261
    if metric == "fork_wait":
        for index in range(32):
            value = _fnv_u32(value, index)
    elif metric == "fork_exec_wait":
        for index in range(12):
            value = _fnv_u32(value, index + 0x100)
    elif metric == "pipe_roundtrip":
        for index in range(1024):
            word = ((challenge_value & 0xFFFFFFFF) ^ index) ^ 0xA5A55A5A
            value = _fnv_u32(value, word)
    elif metric == "seq_file_io":
        def file_byte(offset: int) -> int:
            lane = offset & 7
            return (
                ((challenge_value >> (lane * 8)) & 0xFF)
                ^ (offset * 131)
                ^ (offset >> 7)
            ) & 0xFF

        for chunk in range(256):
            value = _fnv_u32(value, file_byte(chunk))
        for _chunk in range(256):
            value = _fnv_u32(value, file_byte(511))
    elif metric == "research_artifact_pipeline":
        return research_artifact_pipeline_checksum(challenge)
    else:
        raise CompatibilityContractError(f"unknown metric checksum oracle: {metric}")
    return f"{value:08x}"


def guest_receipt(challenge: str, samples: Sequence[Mapping[str, object]]) -> str:
    if HEX16_RE.fullmatch(challenge) is None or int(challenge, 16) == 0:
        raise CompatibilityContractError("guest challenge must be nonzero 64-bit hex")
    challenge_value = int(challenge, 16)
    value = 2166136261
    value = _fnv_u32(value, challenge_value & 0xFFFFFFFF)
    value = _fnv_u32(value, challenge_value >> 32)
    for sample in samples:
        metric = str(sample.get("metric", ""))
        try:
            metric_number = [str(item["id"]) for item in METRICS].index(metric)
        except ValueError as error:
            raise CompatibilityContractError(f"unknown metric in receipt: {metric}") from error
        checksum = str(sample.get("checksum", ""))
        if re.fullmatch(r"[0-9a-f]{8}", checksum) is None:
            raise CompatibilityContractError("sample checksum is malformed")
        for word in (
            metric_number,
            int(sample["round"]),
            int(sample["operations"]),
            int(sample["elapsed_ms"]),
            int(checksum, 16),
        ):
            value = _fnv_u32(value, word)
    return f"{value:08x}"


def workload_outcome_sha256(
    challenge: str, samples: Sequence[Mapping[str, object]]
) -> str:
    """绑定确定性工作结果，并明确排除计时值。"""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "challenge": challenge,
                "samples": [
                    {
                        "metric": sample.get("metric"),
                        "round": sample.get("round"),
                        "operations": sample.get("operations"),
                        "checksum": sample.get("checksum"),
                    }
                    for sample in samples
                ],
            }
        )
    )


def parse_guest_log(text: str, expected_challenge: str) -> dict[str, object]:
    """仅从 QEMU 日志解析完整且绑定 challenge 的 Guest 记录。"""

    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_GUEST_LOG_BYTES:
        raise CompatibilityContractError("guest log is absent or exceeds its byte limit")
    if HEX16_RE.fullmatch(expected_challenge) is None or int(expected_challenge, 16) == 0:
        raise CompatibilityContractError("expected challenge must be nonzero 64-bit hex")

    protocol_lines: list[tuple[int, str]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = ANSI_RE.sub("", raw).rstrip("\r")
        if line.startswith("compatbench:"):
            protocol_lines.append((line_number, line))
    if not protocol_lines:
        raise CompatibilityContractError("guest log has no compatibility records")

    begins = [(number, line, BEGIN_RE.fullmatch(line)) for number, line in protocol_lines]
    begins = [(number, match) for number, _line, match in begins if match is not None]
    done = [(number, DONE_RE.fullmatch(line)) for number, line in protocol_lines]
    done = [(number, match) for number, match in done if match is not None]
    passes = [number for number, line in protocol_lines if line == PASS_LINE]
    samples_with_lines: list[tuple[int, re.Match[str]]] = []
    recognized_lines: set[int] = set(passes)
    recognized_lines.update(number for number, _match in begins)
    recognized_lines.update(number for number, _match in done)
    for number, line in protocol_lines:
        match = SAMPLE_RE.fullmatch(line)
        if match is not None:
            samples_with_lines.append((number, match))
            recognized_lines.add(number)
    unknown = [(number, line) for number, line in protocol_lines if number not in recognized_lines]
    if unknown:
        raise CompatibilityContractError(
            f"guest log has unknown compatibility record at line {unknown[0][0]}"
        )
    if len(begins) != 1 or len(done) != 1 or len(passes) != 1:
        raise CompatibilityContractError("guest begin/done/pass records must each occur once")
    begin_line, begin_match = begins[0]
    done_line, done_match = done[0]
    pass_line = passes[0]
    if not begin_line < done_line < pass_line:
        raise CompatibilityContractError("guest protocol records are out of order")
    if begin_match.group(1) != expected_challenge or done_match.group(1) != expected_challenge:
        raise CompatibilityContractError("guest envelope challenge differs from the Host plan")

    samples: list[dict[str, object]] = []
    expected_sequence = expected_sample_sequence(expected_challenge)
    if len(samples_with_lines) != len(expected_sequence):
        raise CompatibilityContractError("guest sample count differs from the fixed protocol")
    for (line_number, match), expected in zip(samples_with_lines, expected_sequence):
        challenge, metric, round_text, operations_text, elapsed_text, checksum = match.groups()
        round_number = int(round_text)
        operations = int(operations_text)
        elapsed_ms = int(elapsed_text)
        if line_number <= begin_line or line_number >= done_line:
            raise CompatibilityContractError("guest sample lies outside its protocol envelope")
        if challenge != expected_challenge:
            raise CompatibilityContractError("guest sample challenge differs from the Host plan")
        if (round_number, metric) != expected:
            raise CompatibilityContractError("guest samples are missing, duplicated, or reordered")
        spec = METRIC_BY_ID[metric]
        if operations != int(spec["operations"]):
            raise CompatibilityContractError(f"{metric} operation count drifted")
        if elapsed_ms <= 0 or elapsed_ms > MAX_ELAPSED_MS:
            raise CompatibilityContractError(f"{metric} elapsed window is invalid")
        normalized_us = elapsed_ms * 1000.0 / operations
        if not math.isfinite(normalized_us) or normalized_us <= 0:
            raise CompatibilityContractError(f"{metric} normalized timing is invalid")
        sample: dict[str, object] = {
            "line": line_number,
            "challenge": challenge,
            "metric": metric,
            "round": round_number,
            "operations": operations,
            "elapsed_ms": elapsed_ms,
            "checksum": checksum,
            "microseconds_per_operation": normalized_us,
        }
        if checksum != metric_checksum(expected_challenge, metric):
            raise CompatibilityContractError(
                f"{metric} output differs from its challenge oracle"
            )
        if metric == "seq_file_io":
            sample["mib_per_second"] = operations * 1000.0 / elapsed_ms / (1024 * 1024)
        elif metric == "research_artifact_pipeline":
            sample["workload"] = dict(PIPELINE_WORKLOAD)
        samples.append(sample)

    expected_receipt = guest_receipt(expected_challenge, samples)
    if done_match.group(2) != expected_receipt:
        raise CompatibilityContractError("guest receipt does not bind the complete sample stream")
    return {
        "schema": GUEST_SCHEMA,
        "challenge": expected_challenge,
        "clock": "gettimeofday_ms",
        "cache_state": GUEST_CACHE_STATE,
        "schedule": GUEST_SCHEDULE,
        "rounds": GUEST_ROUNDS,
        "sample_count": len(samples),
        "receipt": expected_receipt,
        "workload_outcome_sha256": workload_outcome_sha256(
            expected_challenge, samples
        ),
        "samples": samples,
        "status": "ready",
    }


def validate_guest_receipt(value: object, expected_challenge: str) -> dict[str, object]:
    """消费评测 JSON 时再次校验其解析形式。"""

    if (
        not isinstance(value, dict)
        or value.get("schema") != GUEST_SCHEMA
        or value.get("status") != "ready"
        or value.get("challenge") != expected_challenge
        or value.get("clock") != "gettimeofday_ms"
        or value.get("cache_state") != GUEST_CACHE_STATE
        or value.get("schedule") != GUEST_SCHEDULE
        or value.get("rounds") != GUEST_ROUNDS
    ):
        raise CompatibilityContractError("parsed Guest receipt envelope is invalid")
    samples = value.get("samples")
    if not isinstance(samples, list) or value.get("sample_count") != len(samples):
        raise CompatibilityContractError("parsed Guest sample inventory is invalid")
    expected_sequence = expected_sample_sequence(expected_challenge)
    if len(samples) != len(expected_sequence):
        raise CompatibilityContractError("parsed Guest sample count is invalid")
    previous_line = 0
    for sample, (round_number, metric) in zip(samples, expected_sequence):
        if not isinstance(sample, dict):
            raise CompatibilityContractError("parsed Guest sample is invalid")
        expected_fields = {
            "line", "challenge", "metric", "round", "operations",
            "elapsed_ms", "checksum", "microseconds_per_operation",
        }
        if metric == "seq_file_io":
            expected_fields.add("mib_per_second")
        elif metric == "research_artifact_pipeline":
            expected_fields.add("workload")
        if set(sample) != expected_fields:
            raise CompatibilityContractError("parsed Guest sample fields differ")
        line = sample.get("line")
        elapsed = sample.get("elapsed_ms")
        operations = sample.get("operations")
        checksum = sample.get("checksum")
        if (
            type(line) is not int
            or line <= previous_line
            or sample.get("challenge") != expected_challenge
            or sample.get("metric") != metric
            or sample.get("round") != round_number
            or operations != METRIC_BY_ID[metric]["operations"]
            or type(elapsed) is not int
            or elapsed <= 0
            or elapsed > MAX_ELAPSED_MS
            or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{8}", checksum) is None
        ):
            raise CompatibilityContractError("parsed Guest sample fields are invalid")
        previous_line = line
        normalized = elapsed * 1000.0 / int(operations)
        if sample.get("microseconds_per_operation") != normalized:
            raise CompatibilityContractError("parsed Guest normalized timing was rewritten")
        if checksum != metric_checksum(expected_challenge, metric):
            raise CompatibilityContractError(
                f"parsed {metric} output differs from its challenge oracle"
            )
        if metric == "seq_file_io":
            throughput = int(operations) * 1000.0 / elapsed / (1024 * 1024)
            if sample.get("mib_per_second") != throughput:
                raise CompatibilityContractError("parsed file throughput was rewritten")
        elif metric == "research_artifact_pipeline":
            if sample.get("workload") != PIPELINE_WORKLOAD:
                raise CompatibilityContractError(
                    "parsed research artifact pipeline workload was rewritten"
                )
        elif "mib_per_second" in sample:
            raise CompatibilityContractError("non-file sample has a file throughput field")
    expected_receipt = guest_receipt(expected_challenge, samples)
    if value.get("receipt") != expected_receipt:
        raise CompatibilityContractError("parsed Guest receipt is not derived from its samples")
    if value.get("workload_outcome_sha256") != workload_outcome_sha256(
        expected_challenge, samples
    ):
        raise CompatibilityContractError("parsed workload outcome is not derived from its samples")
    return value


def _require_commit(commit: object) -> str:
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise CompatibilityContractError("source commit must be a full lowercase object id")
    return commit


def create_plan(
    source_commit: str,
) -> dict[str, object]:
    source_commit = _require_commit(source_commit)
    boots = FORMAL_BOOT_COUNT
    start_order = int.from_bytes(
        hashlib.sha256(
            f"{PLAN_SCHEMA}\0target-order\0{source_commit}".encode("ascii")
        ).digest()[:8],
        "big",
    ) & 1
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for index in range(boots):
        material = (
            f"{PLAN_SCHEMA}\0challenge\0{source_commit}\0{index + 1}"
        ).encode("ascii")
        challenge = hashlib.sha256(material).hexdigest()[:16]
        if int(challenge, 16) == 0 or challenge in seen:
            raise CompatibilityContractError("derived challenge is zero or duplicated")
        seen.add(challenge)
        order = ORDERS[(start_order + index) % len(ORDERS)]
        entries.append(
            {
                "boot_id": f"compat-{index + 1:02d}",
                "challenge": challenge,
                "target_order": "-".join(order),
            }
        )
    body: dict[str, object] = {
        "schema": PLAN_SCHEMA,
        "source_commit": source_commit,
        "boots": entries,
        "fixed_boot_count": FORMAL_BOOT_COUNT,
        "fresh_boot_per_target": True,
        "stopping_rule": "exactly-seven-precommitted-paired-repetitions",
        "optional_stopping_forbidden": True,
        "challenge_policy": "sha256-source-commit-and-boot-id",
        "order_policy": "source-commit-derived-start-then-alternating-ab-ba",
        "target_order_balanced": False,
        "target_order_max_count_difference": 1,
        "target_order_counts": {
            "plain-agentos": sum(
                boot["target_order"] == "plain-agentos" for boot in entries
            ),
            "agentos-plain": sum(
                boot["target_order"] == "agentos-plain" for boot in entries
            ),
        },
        "guest_rounds_per_boot": GUEST_ROUNDS,
        "measurement_state": dict(MEASUREMENT_STATE),
        "metrics": [dict(metric) for metric in METRICS],
        "aggregate_score_forbidden": True,
    }
    return {**body, "plan_sha256": sha256_bytes(canonical_json_bytes(body))}


def validate_plan(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("schema") != PLAN_SCHEMA:
        raise CompatibilityContractError("campaign plan schema is invalid")
    source_commit = _require_commit(value.get("source_commit"))
    boots = value.get("boots")
    if not isinstance(boots, list) or len(boots) != FORMAL_BOOT_COUNT:
        raise CompatibilityContractError("campaign plan boots are invalid")
    expected = create_plan(source_commit)
    if value != expected:
        raise CompatibilityContractError("campaign plan differs from its deterministic contract")
    return value


def _extract_make_variable(
    text: str, name: str, resolving: tuple[str, ...] = ()
) -> str:
    """解析由简单 ``:=`` 路径绑定构成的闭合链。"""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in resolving:
        raise CompatibilityContractError(f"Makefile has an unsafe {name} binding")
    matches = re.findall(
        rf"^{re.escape(name)}\s*:=\s*(\S+)\s*$", text, re.MULTILINE
    )
    if len(matches) != 1:
        raise CompatibilityContractError(f"Makefile does not uniquely bind {name}")
    value = matches[0]

    def expand(match: re.Match[str]) -> str:
        return _extract_make_variable(text, match.group(1), resolving + (name,))

    value = re.sub(r"\$\(([A-Z][A-Z0-9_]*)\)", expand, value)
    if "$" in value or len(value) > 4096:
        raise CompatibilityContractError(f"Makefile has an unsafe {name} binding")
    return value


def source_receipt(repo: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    canonical = repo / CANONICAL_SOURCE
    if not canonical.is_file() or canonical.is_symlink():
        raise CompatibilityContractError("canonical compatibility source is unavailable")
    source_data = canonical.read_bytes()
    if b"\r\n" in source_data or b"\0" in source_data:
        raise CompatibilityContractError("canonical compatibility source is not normalized text")

    bindings: list[dict[str, str]] = []
    for target, makefile_path in (
        ("plain", repo / "baseline_ucore/user/Makefile"),
        ("agentos", repo / "user/Makefile"),
    ):
        if not makefile_path.is_file() or makefile_path.is_symlink():
            raise CompatibilityContractError(f"{target} user Makefile is unavailable")
        text = makefile_path.read_text(encoding="utf-8")
        value = _extract_make_variable(text, "COMPAT_BENCH_SOURCE")
        resolved = (makefile_path.parent / value).resolve(strict=True)
        try:
            same = resolved.samefile(canonical)
        except OSError:
            same = False
        if not same:
            raise CompatibilityContractError(f"{target} does not compile the canonical source")
        required_tokens = (
            "CHAPTER), compat_eval",
            "$(COMPAT_BENCH_HEADER)",
            "$(COMPAT_BENCH_SOURCE) -o $@",
            "$(OBJCOPY_CMD) -O binary $@ $(bin_dir)/compatbench",
        )
        if any(token not in text for token in required_tokens):
            raise CompatibilityContractError(f"{target} compatibility build rule drifted")
        root_makefile = (
            repo / "baseline_ucore/Makefile" if target == "plain" else repo / "Makefile"
        )
        root_text = root_makefile.read_text(encoding="utf-8")
        if (
            "COMPAT_BENCH_CHALLENGE_HEX ?= 0000000000000001" not in root_text
            or "COMPAT_BENCH_CHALLENGE_HEX=$(call shell_quote,$(COMPAT_BENCH_CHALLENGE_HEX))"
            not in root_text
            or "run-prebuilt:" not in root_text
            or "-kernel build/kernel" not in root_text
            or "-drive file=$(F)/fs-copy.img" not in root_text
        ):
            raise CompatibilityContractError(
                f"{target} root build or fixed runtime artifact path drifted"
            )
        bindings.append(
            {
                "target": target,
                "makefile": makefile_path.relative_to(repo).as_posix(),
                "makefile_sha256": sha256_file(makefile_path),
                "root_makefile": root_makefile.relative_to(repo).as_posix(),
                "root_makefile_sha256": sha256_file(root_makefile),
                "resolved_source": CANONICAL_SOURCE,
            }
        )

    source_text = source_data.decode("utf-8")
    source_requirements = (
        "#define BENCH_SCHEMA 2",
        "#define BENCH_ROUNDS 3",
        "#define METRIC_COUNT 5",
        '#define BENCH_CACHE_STATE "warm_guest_paths"',
        '#define BENCH_SCHEDULE "challenge_rotated_v1"',
        "#define FORK_WAIT_OPS 32",
        "#define FORK_EXEC_WAIT_OPS 12",
        "#define PIPE_ROUNDTRIP_OPS 1024",
        "#define FILE_CHUNKS_PER_DIRECTION 256",
        "#define PIPELINE_INPUT_SHARDS 8",
        "#define PIPELINE_RECORDS_PER_SHARD 64",
        "#define PIPELINE_GROUPS 8",
        "#define PIPELINE_RECORD_BYTES 16",
        "#define PIPELINE_ARTIFACT_BYTES 64",
        "get_mtime()",
        'exec("compatbench", worker_argv)',
        "bench_pipe_roundtrip",
        "bench_seq_file_io",
        '"research_artifact_pipeline"',
        "bench_research_artifact_pipeline",
        "pipeline_artifact_checksum",
        "scheduled_metric",
    )
    if any(token not in source_text for token in source_requirements):
        raise CompatibilityContractError("canonical workload or timing contract drifted")
    target_branch_tokens = (
        "#ifdef AGENTOS",
        "#if defined(AGENTOS",
        "#ifdef BASELINE_UCORE",
        "#if defined(BASELINE_UCORE",
        "#ifdef TARGET",
    )
    if any(token in source_text for token in target_branch_tokens):
        raise CompatibilityContractError("canonical workload contains a target-specific branch")

    return {
        "schema": SOURCE_SCHEMA,
        "canonical_path": CANONICAL_SOURCE,
        "canonical_sha256": sha256_bytes(source_data),
        "canonical_bytes": len(source_data),
        "target_bindings": bindings,
        "clock": "gettimeofday_ms",
        "guest_rounds": GUEST_ROUNDS,
        "measurement_state": dict(MEASUREMENT_STATE),
        "metrics": [dict(metric) for metric in METRICS],
        "single_canonical_source": True,
        "target_specific_guest_branches": False,
        "target_build_relation": "same_c_source_separately_compiled",
        "same_binary_claimed": False,
        "pure_kernel_cost_claimed": False,
    }


def validate_source_receipt(value: object) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema", "canonical_path", "canonical_sha256", "canonical_bytes",
            "target_bindings", "clock", "guest_rounds", "measurement_state", "metrics",
            "single_canonical_source", "target_specific_guest_branches",
            "target_build_relation", "same_binary_claimed",
            "pure_kernel_cost_claimed",
        }
        or value.get("schema") != SOURCE_SCHEMA
        or value.get("canonical_path") != CANONICAL_SOURCE
        or not isinstance(value.get("canonical_sha256"), str)
        or HEX64_RE.fullmatch(str(value["canonical_sha256"])) is None
        or type(value.get("canonical_bytes")) is not int
        or not 0 < int(value["canonical_bytes"]) <= MAX_ARTIFACT_BYTES
        or value.get("clock") != "gettimeofday_ms"
        or value.get("guest_rounds") != GUEST_ROUNDS
        or value.get("measurement_state") != MEASUREMENT_STATE
        or value.get("metrics") != [dict(metric) for metric in METRICS]
        or value.get("single_canonical_source") is not True
        or value.get("target_specific_guest_branches") is not False
        or value.get("target_build_relation")
        != "same_c_source_separately_compiled"
        or value.get("same_binary_claimed") is not False
        or value.get("pure_kernel_cost_claimed") is not False
    ):
        raise CompatibilityContractError("campaign source receipt is invalid")
    bindings = value.get("target_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(TARGETS):
        raise CompatibilityContractError("campaign source target bindings are invalid")
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise CompatibilityContractError("campaign source target binding is invalid")
        target = binding.get("target")
        if (
            target not in TARGETS
            or target in seen
            or binding.get("resolved_source") != CANONICAL_SOURCE
            or not isinstance(binding.get("makefile"), str)
            or not isinstance(binding.get("root_makefile"), str)
            or not isinstance(binding.get("makefile_sha256"), str)
            or HEX64_RE.fullmatch(str(binding["makefile_sha256"])) is None
            or not isinstance(binding.get("root_makefile_sha256"), str)
            or HEX64_RE.fullmatch(str(binding["root_makefile_sha256"])) is None
        ):
            raise CompatibilityContractError("campaign source target binding is invalid")
        seen.add(str(target))
    if seen != set(TARGETS):
        raise CompatibilityContractError("campaign source target bindings are incomplete")
    return value


def validate_build_stamp(
    value: object,
    *,
    target: str,
    challenge: str,
    source_commit: str,
    canonical_sha256: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("schema") != BUILD_STAMP_SCHEMA:
        raise CompatibilityContractError("build stamp schema is invalid")
    if value.get("target") != target or target not in TARGETS:
        raise CompatibilityContractError("build stamp target is invalid")
    if value.get("challenge") != challenge or HEX16_RE.fullmatch(challenge) is None:
        raise CompatibilityContractError("build stamp challenge is invalid")
    if value.get("source_commit") != source_commit:
        raise CompatibilityContractError("build stamp source commit is invalid")
    if value.get("canonical_source_sha256") != canonical_sha256:
        raise CompatibilityContractError("build stamp canonical source hash is invalid")
    if (
        not isinstance(value.get("source_tracked_sha256"), str)
        or HEX64_RE.fullmatch(str(value["source_tracked_sha256"])) is None
        or not isinstance(value.get("build_log_sha256"), str)
        or HEX64_RE.fullmatch(str(value["build_log_sha256"])) is None
        or value.get("build_log") != "build.log"
        or not isinstance(value.get("build_command"), str)
        or not str(value["build_command"])
    ):
        raise CompatibilityContractError("build stamp source or log binding is invalid")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "compatbench_binary",
        "compatbench_elf",
        "filesystem_image",
        "kernel",
    }:
        raise CompatibilityContractError("build stamp artifact inventory is invalid")
    for name, artifact in artifacts.items():
        if (
            not isinstance(artifact, dict)
            or not isinstance(artifact.get("bytes"), int)
            or int(artifact["bytes"]) <= 0
            or int(artifact["bytes"]) > MAX_ARTIFACT_BYTES
            or not isinstance(artifact.get("sha256"), str)
            or HEX64_RE.fullmatch(str(artifact["sha256"])) is None
        ):
            raise CompatibilityContractError(f"build artifact is invalid: {name}")
        archive = artifact.get("archive")
        expected_archive = {
            "compatbench_binary": ("compatbench.bin", "raw"),
            "compatbench_elf": ("compatbench.elf", "raw"),
            "filesystem_image": ("fs-input.img.gz", "gzip-mtime0"),
            "kernel": ("kernel.gz", "gzip-mtime0"),
        }[name]
        if not isinstance(archive, dict) or archive.get("path") != expected_archive[0]:
            raise CompatibilityContractError(f"build artifact archive is invalid: {name}")
        if expected_archive[1] == "raw":
            if (
                archive.get("bytes") != artifact["bytes"]
                or archive.get("sha256") != artifact["sha256"]
                or "encoding" in archive
            ):
                raise CompatibilityContractError(f"raw build archive is invalid: {name}")
        elif (
            archive.get("encoding") != "gzip-mtime0"
            or type(archive.get("bytes")) is not int
            or not 0 < int(archive["bytes"]) <= MAX_ARTIFACT_BYTES
            or not isinstance(archive.get("sha256"), str)
            or HEX64_RE.fullmatch(str(archive["sha256"])) is None
            or archive.get("uncompressed_bytes") != artifact["bytes"]
            or archive.get("uncompressed_sha256") != artifact["sha256"]
        ):
            raise CompatibilityContractError(f"compressed build archive is invalid: {name}")
    if value.get("chapter") != "compat_eval" or value.get("init_proc") != "compatbench":
        raise CompatibilityContractError("build stamp launch contract is invalid")
    return value


def validate_formal_context(
    value: object, *, source_commit: str
) -> dict[str, object]:
    expected_fields = {
        "schema",
        "micro_campaign_path",
        "micro_campaign_sha256",
        "micro_run_id",
        "source_commit",
        "clean_worktree",
        "phase",
        "formal_boot_count",
        "platform_sha256",
        "environment_sha256",
        "tool_identities_sha256",
        "shell_environment_sha256",
        "execution_domain",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise CompatibilityContractError("formal evaluation context is incomplete")
    digests = (
        value.get("micro_campaign_sha256"),
        value.get("platform_sha256"),
        value.get("environment_sha256"),
        value.get("tool_identities_sha256"),
        value.get("shell_environment_sha256"),
    )
    if (
        value.get("schema") != FORMAL_CONTEXT_SCHEMA
        or value.get("micro_campaign_path") != "campaign.json"
        or value.get("source_commit") != source_commit
        or value.get("clean_worktree") is not True
        or value.get("phase") != "collected"
        or value.get("formal_boot_count") != FORMAL_BOOT_COUNT
        or not isinstance(value.get("micro_run_id"), str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", str(value["micro_run_id"])
        )
        is None
        or not isinstance(value.get("execution_domain"), str)
        or not str(value["execution_domain"])
        or any(
            not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None
            for digest in digests
        )
    ):
        raise CompatibilityContractError("formal evaluation context is invalid")
    return value


def summarize_boots(boots: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """返回各指标的描述性配对结果，不生成综合分数。"""

    outcome_pairs: list[dict[str, object]] = []
    for boot in boots:
        targets = boot.get("targets")
        if not isinstance(targets, Mapping):
            raise CompatibilityContractError("boot target results are unavailable")
        outcomes: dict[str, str] = {}
        for target in TARGETS:
            result = targets.get(target)
            guest = result.get("guest") if isinstance(result, Mapping) else None
            outcome = guest.get("workload_outcome_sha256") if isinstance(guest, Mapping) else None
            if not isinstance(outcome, str) or HEX64_RE.fullmatch(outcome) is None:
                raise CompatibilityContractError("boot workload outcome is unavailable")
            outcomes[target] = outcome
        if outcomes["plain"] != outcomes["agentos"]:
            raise CompatibilityContractError(
                "plain and AgentOS workload outcomes are not equivalent"
            )
        outcome_pairs.append(
            {
                "boot_id": boot.get("boot_id"),
                "challenge": boot.get("challenge"),
                "outcome_sha256": outcomes["plain"],
                "equivalent": True,
            }
        )

    metric_results: dict[str, object] = {}
    for spec in METRICS:
        metric = str(spec["id"])
        pairs: list[dict[str, object]] = []
        for boot in boots:
            targets = boot.get("targets")
            if not isinstance(targets, Mapping):
                raise CompatibilityContractError("boot target results are unavailable")
            boot_values: dict[str, float] = {}
            for target in TARGETS:
                result = targets.get(target)
                if not isinstance(result, Mapping):
                    raise CompatibilityContractError(f"boot lacks {target} result")
                guest = result.get("guest")
                if not isinstance(guest, Mapping) or not isinstance(guest.get("samples"), list):
                    raise CompatibilityContractError(f"boot lacks {target} Guest samples")
                values = [
                    float(sample["microseconds_per_operation"])
                    for sample in guest["samples"]
                    if isinstance(sample, Mapping) and sample.get("metric") == metric
                ]
                if len(values) != GUEST_ROUNDS or any(not math.isfinite(item) or item <= 0 for item in values):
                    raise CompatibilityContractError(f"boot has invalid {target} {metric} rounds")
                boot_values[target] = statistics.median(values)
            ratio = boot_values["agentos"] / boot_values["plain"]
            pairs.append(
                {
                    "boot_id": boot.get("boot_id"),
                    "challenge": boot.get("challenge"),
                    "plain_microseconds_per_operation": boot_values["plain"],
                    "agentos_microseconds_per_operation": boot_values["agentos"],
                    "agentos_over_plain_ratio": ratio,
                }
            )
        metric_results[metric] = {
            "operation_unit": spec["operation_unit"],
            "window": spec["window"],
            "paired_boots": len(pairs),
            "samples_per_target_boot": GUEST_ROUNDS,
            "samples_per_target": GUEST_ROUNDS * len(pairs),
            "plain_median_microseconds_per_operation": statistics.median(
                float(pair["plain_microseconds_per_operation"]) for pair in pairs
            ),
            "agentos_median_microseconds_per_operation": statistics.median(
                float(pair["agentos_microseconds_per_operation"]) for pair in pairs
            ),
            "median_agentos_over_plain_ratio": statistics.median(
                float(pair["agentos_over_plain_ratio"]) for pair in pairs
            ),
            "pairs": pairs,
            "inference": "descriptive_paired_boot_medians_only",
        }
        if "workload" in spec:
            metric_results[metric]["workload"] = dict(spec["workload"])
            metric_results[metric]["attribution"] = spec["attribution"]
    return {
        "metrics": metric_results,
        "workload_equivalence": {
            "all_paired_outcomes_equal": True,
            "paired_boots": len(outcome_pairs),
            "pairs": outcome_pairs,
        },
        "aggregate_score": None,
        "aggregate_score_forbidden": True,
        "claim_scope": "traditional_ucore_compatibility_overhead_only",
    }


def validate_campaign(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise CompatibilityContractError("campaign schema is invalid")
    if value.get("status") != "ready":
        raise CompatibilityContractError("campaign is not ready")
    plan = validate_plan(value.get("plan"))
    source = validate_source_receipt(value.get("source"))
    formal_context = validate_formal_context(
        value.get("formal_context"), source_commit=str(plan["source_commit"])
    )
    identity = value.get("source_identity")
    if (
        not isinstance(identity, dict)
        or identity.get("source_commit") != plan["source_commit"]
        or identity.get("source_tree_clean") is not True
        or not isinstance(identity.get("source_tracked_sha256"), str)
        or HEX64_RE.fullmatch(str(identity["source_tracked_sha256"])) is None
    ):
        raise CompatibilityContractError("campaign source identity is invalid")
    boots = value.get("boots")
    if not isinstance(boots, list) or len(boots) != len(plan["boots"]):
        raise CompatibilityContractError("campaign boot inventory is invalid")
    for boot, planned in zip(boots, plan["boots"]):
        if not isinstance(boot, dict) or any(
            boot.get(key) != planned[key]
            for key in ("boot_id", "challenge", "target_order")
        ):
            raise CompatibilityContractError("campaign boot differs from its plan")
        targets = boot.get("targets")
        if not isinstance(targets, dict) or set(targets) != set(TARGETS):
            raise CompatibilityContractError("campaign target inventory is invalid")
        for target in TARGETS:
            result = targets[target]
            if (
                not isinstance(result, dict)
                or result.get("status") != "ready"
                or result.get("target") != target
                or result.get("challenge") != planned["challenge"]
                or result.get("fresh_boot") is not True
            ):
                raise CompatibilityContractError(f"campaign {target} result is invalid")
            observer = result.get("observer")
            if (
                not isinstance(observer, dict)
                or observer.get("marker_seen") is not True
                or observer.get("failure_seen") is not False
                or observer.get("timed_out") is not False
                or observer.get("returncode") != 0
                or type(observer.get("runner_terminated")) is not bool
                or observer.get("host_process_quiesced") is not True
                or observer.get("output_eof") is not True
                or observer.get("output_error") != ""
                or type(observer.get("wsl_cleanup_applicable")) is not bool
                or observer.get("wsl_cleanup_verified") is not True
                or observer.get("wsl_cleanup_initial_survivors") != 0
                or observer.get("wsl_cleanup_remaining_survivors") != 0
                or observer.get("wsl_cleanup_error") != ""
                or not isinstance(observer.get("elapsed_seconds"), (int, float))
                or isinstance(observer.get("elapsed_seconds"), bool)
                or not math.isfinite(float(observer["elapsed_seconds"]))
                or float(observer["elapsed_seconds"]) <= 0
            ):
                raise CompatibilityContractError("campaign Guest observer receipt is invalid")
            termination_mode = observer.get("termination_mode")
            raw_returncode = observer.get("raw_returncode")
            runner_signals = observer.get("runner_signals")
            natural_exit = (
                termination_mode == "natural_exit"
                and observer["runner_terminated"] is False
                and type(raw_returncode) is int
                and raw_returncode == 0
                and runner_signals == []
            )
            observer_sigterm = (
                termination_mode == "observer_sigterm"
                and observer["runner_terminated"] is True
                and runner_signals == [int(signal.SIGTERM)]
                and (raw_returncode is None or type(raw_returncode) is int)
            )
            if not (natural_exit or observer_sigterm):
                raise CompatibilityContractError(
                    "campaign Guest observer termination receipt is invalid"
                )
            guest = validate_guest_receipt(
                result.get("guest"), str(planned["challenge"])
            )
            guest_elapsed_ms = sum(
                int(sample["elapsed_ms"]) for sample in guest["samples"]
            )
            if guest_elapsed_ms > float(observer["elapsed_seconds"]) * 1000.0 + 1.0:
                raise CompatibilityContractError(
                    "Guest timing windows exceed their Host process observation"
                )
            stamp = validate_build_stamp(
                result.get("build_stamp"),
                target=target,
                challenge=str(planned["challenge"]),
                source_commit=str(plan["source_commit"]),
                canonical_sha256=str(source["canonical_sha256"]),
            )
            if stamp.get("source_tracked_sha256") != identity["source_tracked_sha256"]:
                raise CompatibilityContractError("build stamp tracked source identity differs")
            if (
                result.get("build_log") != "build.log"
                or result.get("guest_log") != "guest.log"
                or result.get("build_stamp_path") != "build-stamp.json"
                or result.get("build_log_sha256") != stamp["build_log_sha256"]
                or not isinstance(result.get("guest_log_sha256"), str)
                or HEX64_RE.fullmatch(str(result["guest_log_sha256"])) is None
            ):
                raise CompatibilityContractError("campaign raw artifact binding is invalid")
            runtime = result.get("runtime_artifact_attestation")
            expected_pre = {
                name: artifact["sha256"]
                for name, artifact in stamp["artifacts"].items()
            }
            if (
                not isinstance(runtime, dict)
                or runtime.get("launch_contract")
                != "make-run-prebuilt-fixed-kernel-and-fs-paths"
                or runtime.get("pre_run_sha256") != expected_pre
                or runtime.get("immutable_runtime_artifacts_unchanged") is not True
                or runtime.get("filesystem_expected_mutable") is not True
            ):
                raise CompatibilityContractError("runtime artifact attestation is invalid")
            post = runtime.get("post_run_sha256")
            if not isinstance(post, dict) or set(post) != set(expected_pre):
                raise CompatibilityContractError("post-run artifact inventory is invalid")
            if any(
                not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None
                for digest in post.values()
            ):
                raise CompatibilityContractError("post-run artifact hash is invalid")
            for immutable in ("compatbench_binary", "compatbench_elf", "kernel"):
                if post[immutable] != expected_pre[immutable]:
                    raise CompatibilityContractError("immutable runtime artifact changed")
        plain_outcome = targets["plain"]["guest"]["workload_outcome_sha256"]
        agentos_outcome = targets["agentos"]["guest"]["workload_outcome_sha256"]
        if plain_outcome != agentos_outcome:
            raise CompatibilityContractError("paired workload outcomes differ")
    expected_summary = summarize_boots(boots)
    if value.get("summary") != expected_summary:
        raise CompatibilityContractError("campaign summary is not derived from raw paired boots")
    if identity.get("source_commit") != formal_context["source_commit"]:
        raise CompatibilityContractError("formal and clean source identities differ")
    if value.get("formal_bundle_eligible") is not True:
        raise CompatibilityContractError("formal compatibility evidence is not bundle eligible")
    if value.get("evidence_tier") != EVIDENCE_TIER or value.get("limitations") != list(LIMITATIONS):
        raise CompatibilityContractError("independent producer evidence scope is invalid")
    return value


__all__ = [
    "BUILD_STAMP_SCHEMA",
    "CANONICAL_SOURCE",
    "CompatibilityContractError",
    "EVIDENCE_TIER",
    "FORMAL_BOOT_COUNT",
    "FORMAL_CONTEXT_SCHEMA",
    "GUEST_CACHE_STATE",
    "GUEST_ROUNDS",
    "GUEST_SAMPLE_COUNT",
    "GUEST_SCHEDULE",
    "GUEST_SCHEMA",
    "LIMITATIONS",
    "METRICS",
    "MEASUREMENT_STATE",
    "PIPELINE_WORKLOAD",
    "PLAN_SCHEMA",
    "SCHEMA",
    "SOURCE_SCHEMA",
    "TARGETS",
    "canonical_json_bytes",
    "create_plan",
    "expected_sample_sequence",
    "guest_receipt",
    "metric_checksum",
    "parse_guest_log",
    "research_artifact_pipeline_checksum",
    "sha256_file",
    "source_receipt",
    "summarize_boots",
    "validate_build_stamp",
    "validate_campaign",
    "validate_guest_receipt",
    "validate_formal_context",
    "validate_plan",
    "validate_source_receipt",
    "workload_outcome_sha256",
]
