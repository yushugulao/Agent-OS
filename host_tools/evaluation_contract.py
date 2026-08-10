"""Validate the functional and performance markers from one AgentOS Guest run."""

from __future__ import annotations

import argparse

import json

import math

import re

from functools import lru_cache

from pathlib import Path

from typing import Any

SCHEMA_VERSION = 1

EVALUATION_SCHEMA_VERSION = 1

EVALUATION_SUITE_ID = "agentos-guest-evaluation-v1"

MARKER_PREFIX = "agenteval_ucore: sample "

DIAGNOSTIC_PREFIX = "agenteval_ucore: diagnostic "

LAUNCHER_PREFIX = "agenteval_ucore: launcher "

FUNCTIONAL_PREFIX = "agenteval_ucore: functional "

CATALOG_PREFIX = "agenteval_ucore: catalog "

REVISIT_PREFIX = "agenteval_ucore: revisit "

REVISIT_SUMMARY_PREFIX = "agenteval_ucore: revisit_summary "

CONCURRENCY_SAMPLE_PREFIX = "agenteval_ucore: concurrency_sample "

CONCURRENCY_PREFIX = "agenteval_ucore: concurrency "

MARKER_FIELDS = (
    "schema", "experiment", "load", "pair", "variant", "order", "cache",
    "operations", "dataset_size", "work_units", "records_examined",
    "result_items", "duration_us", "index_rebuild_records",
    "result_cache_hits", "workload_fingerprint", "result_fingerprint", "status",
)

DIAGNOSTIC_FIELDS = (
    "schema", "experiment", "load", "cache", "operations", "dataset_size",
    "work_units", "result_items", "duration_us", "index_rebuild_records",
    "result_cache_hits", "workload_fingerprint", "result_fingerprint", "status",
)

LAUNCHER_FIELDS = (
    "schema", "challenge", "values", "semantic", "receipt", "status",
)

FUNCTIONAL_FIELDS = (
    "schema", "task", "challenge", "values", "semantic", "receipt", "status",
)

CATALOG_FIELDS = (
    "schema", "challenge", "index", "total", "abi", "tool_id", "flags",
    "param_count", "name", "params", "status",
)

REVISIT_FIELDS = (
    "schema", "visit", "identity", "request_id", "agent_id",
    "lifecycle_id", "lifecycle_generation", "correct", "contamination",
    "return_visit", "fallback", "result_fingerprint", "status",
)

REVISIT_SUMMARY_FIELDS = (
    "schema", "visits", "correct", "contamination", "return_visit",
    "fallback", "result_fingerprint", "status",
)

CONCURRENCY_SAMPLE_FIELDS = {
    2: (
        "schema", "concurrency", "round", "slot", "identity", "request_id",
        "submitted_us", "started_us", "completed_us", "received_us",
        "wait_us", "service_us", "turnaround_us", "correct", "contamination",
        "fallback", "isolation_ok", "result_fingerprint", "status",
    ),
}

CONCURRENCY_FIELDS = {
    2: (
        "schema", "concurrency", "rounds", "requests", "completed", "start_us",
        "end_us", "duration_us", "throughput_milli_rps", "goodput_milli_rps",
        "avg_milli_us", "p50_us", "p90_us", "p99_us", "wait_avg_milli_us",
        "wait_p50_us", "wait_p90_us", "wait_p99_us", "service_avg_milli_us",
        "service_p50_us", "service_p90_us", "service_p99_us",
        "fairness_jain_ppm", "max_min_fairness_ppm", "isolated", "correct",
        "contamination", "fallback", "workload_digest", "result_fingerprint",
        "status",
    ),
}

CONCURRENCY_PUBLIC_FIELDS = {
    2: (
        "concurrency", "rounds", "requests", "completed", "duration_us",
        "throughput_milli_rps", "goodput_milli_rps", "avg_milli_us", "p50_us",
        "p90_us", "p99_us", "wait_avg_milli_us", "wait_p50_us",
        "wait_p90_us", "wait_p99_us", "service_avg_milli_us",
        "service_p50_us", "service_p90_us", "service_p99_us",
        "fairness_jain_ppm", "max_min_fairness_ppm", "isolated", "correct",
        "contamination", "fallback", "workload_digest", "result_fingerprint",
    ),
}

QOS_REGISTRATION_FIELDS = (
    "qos_schema_version", "latency_metrics", "turnaround_definition",
    "goodput_unit", "fairness_scale", "fairness_basis", "isolation_definition",
    "digest",
)

FUNCTIONAL_TASKS = tuple(f"task{number}" for number in range(1, 6))

HEX16 = re.compile(r"^[0-9a-f]{16}$")

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

FILE_META_CAPACITY = 512

TASK1_CONTEXT_CONTRACT = {
    "base": 0x3FFFFE7000,
    "size": 7 * 4096,
    "magic": 0x4147435458543031,
    "version": 9,
    "capacity": 128,
    "user_cache_offset": 6 * 4096,
    "user_cache_size": 4096,
}

FILE_QUERY_PATH_INDEX = "file_query_path_index"

FILE_QUERY_TABLE_ABLATION = "file_query_table_ablation"

FILE_QUERY_EXPERIMENTS = frozenset({
    FILE_QUERY_PATH_INDEX,
    FILE_QUERY_TABLE_ABLATION,
})

REGISTERED_FILE_EXPERIMENTS = {
    FILE_QUERY_PATH_INDEX: {
        "loads": [8, 24, 48, 96],
        "operation_counts": [8, 6, 4, 1],
        "baseline": {"id": "path_walk", "cache": "warm-paths"},
        "treatment": {"id": "index", "cache": "ready-index"},
    },
    FILE_QUERY_TABLE_ABLATION: {
        "loads": [24, 64, 96],
        "operation_counts": [16, 16, 16],
        "baseline": {"id": "scan", "cache": "forced-scan"},
        "treatment": {"id": "index", "cache": "ready-index"},
    },
}

REGISTERED_EXECUTION_SCHEDULE = (
    (FILE_QUERY_PATH_INDEX, 8),
    (FILE_QUERY_PATH_INDEX, 24),
    (FILE_QUERY_TABLE_ABLATION, 24),
    (FILE_QUERY_PATH_INDEX, 48),
    (FILE_QUERY_TABLE_ABLATION, 64),
    (FILE_QUERY_PATH_INDEX, 96),
    (FILE_QUERY_TABLE_ABLATION, 96),
    ("tool_batch", 24),
    ("tool_batch", 64),
    ("tool_batch", 96),
    ("context_access", 24),
    ("context_access", 64),
    ("context_access", 96),
)

TASK5_DELAY_TICKS = 8

TASK5_MAX_WAIT_LOOPS = 3

class EvaluationError(RuntimeError):
    pass

def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result

def _reject_nonfinite(value: str) -> Any:
    raise EvaluationError(f"non-finite JSON number {value!r}")

def strict_json_loads(raw: str | bytes) -> Any:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvaluationError(f"invalid strict JSON: {error}") from error

def _read_json(path: Path) -> Any:
    try:
        safe_path = _safe_regular_file(path, "JSON file")
        return strict_json_loads(safe_path.read_bytes())
    except (OSError, ValueError) as error:
        raise EvaluationError(f"cannot read {path}: {error}") from error

def _safe_regular_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise EvaluationError(f"{label} is missing")
    return path

def _exact(value: object, fields: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = set(value) if isinstance(value, dict) else set()
        raise EvaluationError(
            f"{where} fields differ: missing={sorted(fields - actual)} "
            f"extra={sorted(actual - fields)}"
        )
    return value

def _text(value: object, where: str, pattern: re.Pattern[str] = IDENTIFIER) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise EvaluationError(f"{where} is invalid")
    return value

def _label(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise EvaluationError(f"{where} is invalid")
    return value

def _int(value: object, where: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > (1 << 53) - 1:
        raise EvaluationError(f"{where} is invalid")
    return value

def load_suite(path: Path) -> dict[str, Any]:
    raw_suite = _read_json(path)
    if not isinstance(raw_suite, dict):
        raise EvaluationError("suite must be an object")
    schema_version = raw_suite.get("schema_version")
    if type(schema_version) is not int or schema_version != EVALUATION_SCHEMA_VERSION:
        raise EvaluationError("suite schema version is invalid")
    if raw_suite.get("suite_id") != EVALUATION_SUITE_ID:
        raise EvaluationError("suite id is invalid")
    suite = _exact(
        raw_suite,
        {
            "schema_version", "kind", "suite_id", "pairing", "experiments",
            "execution_schedule", "supplementary_scenarios",
        },
        "suite",
    )
    if suite["kind"] != "agentos-guest-evaluation-suite":
        raise EvaluationError("suite header is invalid")
    supplementary = suite["supplementary_scenarios"]
    if not isinstance(supplementary, list) or len(supplementary) != 1:
        raise EvaluationError("supplementary scenario registration is invalid")
    revisit = _exact(
        supplementary[0],
        {
            "id", "label", "task", "identity_order", "visit_sequence",
            "concurrency_levels", "rounds_per_level", "latency_unit",
            "throughput_unit", "percentile_method", "performance_gate",
            *QOS_REGISTRATION_FIELDS,
        },
        "supplementary revisit scenario",
    )
    if (
        revisit["id"] != "multi_identity_revisit_isolation"
        or revisit["task"] != "task1"
        or revisit["identity_order"] != ["A", "B", "C", "D"]
        or revisit["visit_sequence"] != ["A", "B", "C", "D", "A"]
        or revisit["concurrency_levels"] != [1, 2, 4]
        or revisit["rounds_per_level"] != 16
        or revisit["latency_unit"] != "us"
        or revisit["throughput_unit"] != "milli_requests_per_second"
        or revisit["percentile_method"] != "nearest_rank"
        or revisit["performance_gate"] is not None
        or revisit["qos_schema_version"] != 2
        or revisit["latency_metrics"] != ["wait", "service", "turnaround"]
        or revisit["turnaround_definition"]
        != "worker_completed_minus_parent_submitted"
        or revisit["goodput_unit"] != "milli_requests_per_second"
        or revisit["fairness_scale"] != "parts_per_million"
        or revisit["fairness_basis"] != "per_identity_isolated_completions"
        or revisit["isolation_definition"]
        != "correct_and_zero_contamination_and_no_fallback"
        or revisit["digest"] != "fnv1a64_challenge_bound"
    ):
        raise EvaluationError(
            "supplementary revisit scenario differs from its contract"
        )
    _label(revisit["label"], "supplementary revisit scenario label")
    pairing = _exact(
        suite["pairing"],
        {"minimum_inner_pairs", "orders"},
        "pairing",
    )
    if (
        _int(pairing["minimum_inner_pairs"], "minimum_inner_pairs", 7) != 7
        or pairing["orders"] != ["AB", "BA"]
    ):
        raise EvaluationError("pairing contract is invalid")
    if not isinstance(suite["experiments"], list) or not suite["experiments"]:
        raise EvaluationError("suite experiments are invalid")
    seen: set[str] = set()
    for item in suite["experiments"]:
        experiment = _exact(
            item,
            {
                "id", "task", "loads", "unit", "operation_counts",
                "selector", "baseline", "treatment",
            },
            "experiment",
        )
        experiment_id = _text(experiment["id"], "experiment id", TOKEN)
        if experiment_id in seen:
            raise EvaluationError("duplicate experiment id")
        seen.add(experiment_id)
        _label(experiment["task"], "experiment task")
        if (
            not isinstance(experiment["loads"], list)
            or not experiment["loads"]
            or any(type(load) is not int or load <= 0 for load in experiment["loads"])
            or len(set(experiment["loads"])) != len(experiment["loads"])
        ):
            raise EvaluationError(f"{experiment_id} loads are invalid")
        if (
            not isinstance(experiment["operation_counts"], list)
            or len(experiment["operation_counts"]) != len(experiment["loads"])
            or any(
                type(count) is not int or count <= 0
                for count in experiment["operation_counts"]
            )
        ):
            raise EvaluationError(f"{experiment_id} operation counts are invalid")
        if experiment["unit"] not in {"us", "us/query"}:
            raise EvaluationError(f"{experiment_id} metric is invalid")
        if type(experiment["selector"]) is not int or not 0 <= experiment["selector"] <= (1 << 64) - 1:
            raise EvaluationError(f"{experiment_id} selector is invalid")
        variants = []
        for role in ("baseline", "treatment"):
            variant = _exact(experiment[role], {"id", "cache"}, role)
            variants.append(_text(variant["id"], f"{role} variant", TOKEN))
            _text(variant["cache"], f"{role} cache", TOKEN)
        if variants[0] == variants[1]:
            raise EvaluationError(f"{experiment_id} variants are identical")
        if (
            experiment_id in FILE_QUERY_EXPERIMENTS
            and (
                min(experiment["loads"]) < pairing["minimum_inner_pairs"]
                or any(
                    operations > load
                    for load, operations in zip(
                        experiment["loads"], experiment["operation_counts"]
                    )
                )
            )
        ):
            raise EvaluationError(
                f"{experiment_id} cannot derive a unique target for every inner pair"
            )
    if seen != {
        FILE_QUERY_PATH_INDEX,
        FILE_QUERY_TABLE_ABLATION,
        "tool_batch",
        "context_access",
    }:
        raise EvaluationError("evaluation experiments differ from the registered four")
    experiments = _experiment_map(suite)
    for experiment_id, registered in REGISTERED_FILE_EXPERIMENTS.items():
        experiment = experiments[experiment_id]
        if (
            experiment["loads"] != registered["loads"]
            or experiment["operation_counts"] != registered["operation_counts"]
            or experiment["task"] != "task4"
            or experiment["unit"] != "us/query"
            or experiment["selector"] != 17
            or any(
                experiment[role]["id"] != registered[role]["id"]
                or experiment[role]["cache"] != registered[role]["cache"]
                for role in ("baseline", "treatment")
            )
        ):
            raise EvaluationError(
                f"{experiment_id} differs from its registered workload contract"
            )
    schedule = suite["execution_schedule"]
    if not isinstance(schedule, list) or not schedule:
        raise EvaluationError("execution schedule is invalid")
    scheduled: list[tuple[str, int]] = []
    for index, raw_item in enumerate(schedule):
        item = _exact(
            raw_item, {"experiment", "load"},
            f"execution schedule item {index}",
        )
        experiment_id = _text(
            item["experiment"], "scheduled experiment", TOKEN
        )
        load = _int(item["load"], "scheduled load", 1)
        if (
            experiment_id not in experiments
            or load not in experiments[experiment_id]["loads"]
        ):
            raise EvaluationError("execution schedule references an unknown workload")
        scheduled.append((experiment_id, load))
    configured = [
        (experiment["id"], load)
        for experiment in suite["experiments"]
        for load in experiment["loads"]
    ]
    if len(scheduled) != len(set(scheduled)) or set(scheduled) != set(configured):
        raise EvaluationError(
            "execution schedule must cover every configured workload exactly once"
        )
    if tuple(scheduled) != REGISTERED_EXECUTION_SCHEDULE:
        raise EvaluationError(
            "execution schedule differs from the registered Guest dispatcher"
        )
    return suite

def _parse_marker(line: str, line_number: int) -> dict[str, Any]:
    if not line.startswith(MARKER_PREFIX):
        raise EvaluationError(f"line {line_number} is not an evaluation marker")
    fields: dict[str, str] = {}
    tokens = line[len(MARKER_PREFIX):].split(" ")
    if not tokens or any(not token or token.count("=") != 1 for token in tokens):
        raise EvaluationError(f"malformed marker at line {line_number}")
    for token in tokens:
        key, value = token.split("=", 1)
        if key in fields or not key or not value:
            raise EvaluationError(f"duplicate or empty marker field at line {line_number}")
        fields[key] = value
    if tuple(fields) != MARKER_FIELDS:
        raise EvaluationError(f"marker schema/order mismatch at line {line_number}")
    if fields["schema"] != "2" or fields["status"] != "measured":
        raise EvaluationError(f"unsupported marker schema/status at line {line_number}")
    for key in ("experiment", "variant", "cache"):
        _text(fields[key], f"marker {key}", TOKEN)
    for key in (
        "load", "pair", "operations", "dataset_size", "work_units",
        "records_examined", "result_items", "duration_us",
        "index_rebuild_records", "result_cache_hits",
    ):
        try:
            number = int(fields[key], 10)
        except ValueError as error:
            raise EvaluationError(f"marker {key} is invalid at line {line_number}") from error
        minimum = 1 if key in {"load", "pair", "operations"} else 0
        if number < minimum or number > (1 << 53) - 1 or str(number) != fields[key]:
            raise EvaluationError(f"marker {key} is invalid at line {line_number}")
        fields[key] = number
    if fields["order"] not in {"AB", "BA"}:
        raise EvaluationError(f"marker order is invalid at line {line_number}")
    for key in ("workload_fingerprint", "result_fingerprint"):
        _text(fields[key], f"marker {key}", HEX16)
    return fields

def _parse_diagnostic(line: str, line_number: int) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    tokens = line[len(DIAGNOSTIC_PREFIX):].split(" ")
    if not tokens or any(not token or token.count("=") != 1 for token in tokens):
        raise EvaluationError(f"malformed diagnostic at line {line_number}")
    for token in tokens:
        key, value = token.split("=", 1)
        if key in fields or not key or not value:
            raise EvaluationError(f"duplicate or empty diagnostic field at line {line_number}")
        fields[key] = value
    if tuple(fields) != DIAGNOSTIC_FIELDS:
        raise EvaluationError(f"diagnostic schema/order mismatch at line {line_number}")
    if (
        fields["schema"] != "2"
        or fields["experiment"] not in FILE_QUERY_EXPERIMENTS
        or fields["status"] != "measured"
    ):
        raise EvaluationError(f"diagnostic header is invalid at line {line_number}")
    if fields["cache"] not in {"cold-rebuild", "ready"}:
        raise EvaluationError(f"diagnostic cache is invalid at line {line_number}")
    for key in (
        "load", "operations", "dataset_size", "work_units", "result_items",
        "duration_us", "index_rebuild_records", "result_cache_hits",
    ):
        try:
            number = int(fields[key], 10)
        except ValueError as error:
            raise EvaluationError(f"diagnostic {key} is invalid at line {line_number}") from error
        if number < 0 or number > (1 << 53) - 1 or str(number) != fields[key]:
            raise EvaluationError(f"diagnostic {key} is invalid at line {line_number}")
        fields[key] = number
    if (
        fields["load"] <= 0
        or fields["operations"] != 1
        or fields["dataset_size"] != fields["load"]
        or fields["work_units"] <= 0
        or fields["result_items"] != 1
        or fields["result_cache_hits"] != 0
    ):
        raise EvaluationError(f"diagnostic work contract is invalid at line {line_number}")
    rebuild = fields["index_rebuild_records"]
    if (
        fields["cache"] == "cold-rebuild"
        and (rebuild == 0 or fields["work_units"] != rebuild)
    ) or (
        fields["cache"] == "ready"
        and rebuild != 0
    ):
        raise EvaluationError(f"diagnostic readiness conflicts with rebuild at line {line_number}")
    for key in ("workload_fingerprint", "result_fingerprint"):
        _text(fields[key], f"diagnostic {key}", HEX16)
    fields["line"] = line_number
    return fields

def _experiment_map(suite: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in suite["experiments"]}

def _fnv_bytes(value: int, raw: bytes) -> int:
    for byte in raw:
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return value

def _fnv_u64(value: int, item: int) -> int:
    return _fnv_bytes(value, (item & ((1 << 64) - 1)).to_bytes(8, "little"))

def _parse_receipt_fields(
    line: str,
    line_number: int,
    prefix: str,
    expected_fields: tuple[str, ...],
) -> dict[str, str]:
    fields: dict[str, str] = {}
    tokens = line[len(prefix):].split(" ")
    if not tokens or any(not token or token.count("=") != 1 for token in tokens):
        raise EvaluationError(f"malformed functional receipt at line {line_number}")
    for token in tokens:
        key, value = token.split("=", 1)
        if key in fields or not key or not value:
            raise EvaluationError(
                f"duplicate or empty functional receipt field at line {line_number}"
            )
        fields[key] = value
    if tuple(fields) != expected_fields:
        raise EvaluationError(
            f"functional receipt schema/order mismatch at line {line_number}"
        )
    return fields

def _parse_receipt_values(value: str, line_number: int) -> list[int]:
    tokens = value.split(",")
    if not tokens or any(not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", token) for token in tokens):
        raise EvaluationError(f"functional receipt values are invalid at line {line_number}")
    values = [int(token, 10) for token in tokens]
    if (
        any(str(number) != token for number, token in zip(values, tokens))
        or any(number < -(1 << 63) or number > (1 << 64) - 1 for number in values)
    ):
        raise EvaluationError(f"functional receipt value is out of range at line {line_number}")
    return values

def _functional_receipt_hash(
    task: str,
    challenge: str,
    values: list[int],
    semantic: str,
) -> str:
    value = _fnv_bytes(1469598103934665603, b"agentos-functional-receipt-v1")
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_bytes(value, task.encode("ascii"))
    for item in values:
        value = _fnv_u64(value, item)
    value = _fnv_u64(value, int(semantic, 16))
    return f"{value:016x}"

def _format_functional_receipt(
    task: str,
    challenge: str,
    values: list[int],
    semantic: str,
    *,
    launcher: bool = False,
) -> str:
    """为 Host 回归夹具构建规范合成回执。"""
    value_text = ",".join(str(value) for value in values)
    receipt = _functional_receipt_hash(task, challenge, values, semantic)
    if launcher:
        return (
            f"{LAUNCHER_PREFIX}schema=1 challenge={challenge} values={value_text} "
            f"semantic={semantic} receipt={receipt} status=ready"
        )
    return (
        f"{FUNCTIONAL_PREFIX}schema=1 task={task} challenge={challenge} "
        f"values={value_text} semantic={semantic} receipt={receipt} status=passed"
    )

def _operations_for(experiment: dict[str, Any], load: int) -> int:
    try:
        return experiment["operation_counts"][experiment["loads"].index(load)]
    except (ValueError, IndexError) as error:
        raise EvaluationError(f"load {load} has no operation count") from error

def _semantic_token(domain: str, load: int, pair: int, item: int, challenge: str) -> int:
    value = _fnv_bytes(1469598103934665603, domain.encode("ascii"))
    for part in (int(challenge, 16), load, pair, item):
        value = _fnv_u64(value, part)
    return value | (1 << 63)

def _parse_supplementary_fields(
    line: str,
    line_number: int,
    prefix: str,
    expected_fields: tuple[str, ...],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    tokens = line[len(prefix):].split(" ")
    if not tokens or any(not token or token.count("=") != 1 for token in tokens):
        raise EvaluationError(
            f"malformed supplementary marker at line {line_number}"
        )
    for token in tokens:
        key, value = token.split("=", 1)
        if key in fields or not key or not value:
            raise EvaluationError(
                f"duplicate or empty supplementary field at line {line_number}"
            )
        fields[key] = value
    if tuple(fields) != expected_fields:
        raise EvaluationError(
            f"supplementary marker schema/order mismatch at line {line_number}"
        )
    return fields

def _supplementary_uint(
    fields: dict[str, Any], key: str, line_number: int, *, minimum: int = 0
) -> int:
    value = fields[key]
    try:
        number = int(value, 10)
    except ValueError as error:
        raise EvaluationError(
            f"supplementary {key} is invalid at line {line_number}"
        ) from error
    if (
        number < minimum
        or number > (1 << 53) - 1
        or str(number) != value
    ):
        raise EvaluationError(
            f"supplementary {key} is invalid at line {line_number}"
        )
    fields[key] = number
    return number

def _nearest_rank_int(values: list[int], percentile: int) -> int:
    if not values or percentile <= 0 or percentile > 100:
        raise EvaluationError("supplementary percentile input is invalid")
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[rank - 1]

def _parse_revisit_evaluation(
    lines: list[str], challenge: str, config: dict[str, Any]
) -> dict[str, Any]:
    identities = config["identity_order"]
    identity_indexes = {identity: index for index, identity in enumerate(identities)}
    qos_schema = config["qos_schema_version"]
    sample_field_names = CONCURRENCY_SAMPLE_FIELDS[qos_schema]
    summary_field_names = CONCURRENCY_FIELDS[qos_schema]
    visit_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(REVISIT_PREFIX)
    ]
    summary_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(REVISIT_SUMMARY_PREFIX)
    ]
    sample_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(CONCURRENCY_SAMPLE_PREFIX)
    ]
    concurrency_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(CONCURRENCY_PREFIX)
    ]
    expected_visits = config["visit_sequence"]
    if len(visit_lines) != len(expected_visits) or len(summary_lines) != 1:
        raise EvaluationError("revisit observation coverage is incomplete")

    visits: list[dict[str, Any]] = []
    identity_receipts: dict[str, tuple[int, int, int]] = {}
    for index, (line_number, line) in enumerate(visit_lines, 1):
        fields = _parse_supplementary_fields(
            line, line_number, REVISIT_PREFIX, REVISIT_FIELDS
        )
        if fields["schema"] != "1" or fields["status"] != "observed":
            raise EvaluationError(f"revisit marker header differs at line {line_number}")
        for key in (
            "visit", "agent_id", "lifecycle_id", "lifecycle_generation",
            "correct", "contamination", "return_visit", "fallback",
        ):
            _supplementary_uint(
                fields, key, line_number,
                minimum=1 if key in {
                    "visit", "agent_id", "lifecycle_id", "lifecycle_generation"
                } else 0,
            )
        identity = fields["identity"]
        if identity not in identity_indexes or identity != expected_visits[index - 1]:
            raise EvaluationError(f"revisit identity/order differs at line {line_number}")
        if fields["visit"] != index:
            raise EvaluationError(f"revisit index differs at line {line_number}")
        for key in ("correct", "return_visit", "fallback"):
            if fields[key] not in {0, 1}:
                raise EvaluationError(f"revisit {key} is not binary at line {line_number}")
        expected_return_position = index == len(expected_visits)
        if (
            fields["fallback"] != 1 - fields["correct"]
            or fields["return_visit"] > fields["correct"]
            or (not expected_return_position and fields["return_visit"] != 0)
            or (fields["contamination"] > 0 and fields["correct"] != 0)
        ):
            raise EvaluationError(
                f"revisit outcome fields conflict at line {line_number}"
            )
        request_id = _text(
            fields["request_id"], "revisit request id", HEX16
        )
        ordinal = 2 if expected_return_position else 1
        expected_request_id = _semantic_token(
            "aios-revisit-visit-v1", index, identity_indexes[identity], ordinal,
            challenge,
        )
        if int(request_id, 16) != expected_request_id:
            raise EvaluationError(
                f"revisit request is not challenge-bound at line {line_number}"
            )
        receipt = (
            fields["agent_id"], fields["lifecycle_id"],
            fields["lifecycle_generation"],
        )
        previous = identity_receipts.setdefault(identity, receipt)
        if previous != receipt:
            raise EvaluationError(
                f"return visit changed workflow identity at line {line_number}"
            )
        result_fingerprint = _text(
            fields["result_fingerprint"], "revisit result fingerprint", HEX16
        )
        expected_fingerprint = _functional_semantic(
            "aios-revisit-observation-v1",
            challenge,
            [
                index, identity_indexes[identity], expected_request_id,
                fields["agent_id"], fields["lifecycle_id"],
                fields["lifecycle_generation"], fields["correct"],
                fields["contamination"], fields["return_visit"],
                fields["fallback"],
            ],
        )
        if result_fingerprint != expected_fingerprint:
            raise EvaluationError(
                f"revisit result fingerprint differs at line {line_number}"
            )
        fields["line"] = line_number
        visits.append(fields)

    if set(identity_receipts) != set(identities):
        raise EvaluationError("revisit identities are incomplete")
    agents = [receipt[0] for receipt in identity_receipts.values()]
    lifecycles = [receipt[1:] for receipt in identity_receipts.values()]
    unique_identity = (
        len(agents) == len(set(agents))
        and len(lifecycles) == len(set(lifecycles))
    )
    if not unique_identity and any(item["correct"] for item in visits):
        raise EvaluationError("duplicate workflow identity was reported correct")

    summary_line_number, summary_line = summary_lines[0]
    summary = _parse_supplementary_fields(
        summary_line, summary_line_number, REVISIT_SUMMARY_PREFIX,
        REVISIT_SUMMARY_FIELDS,
    )
    if summary["schema"] != "1" or summary["status"] != "measured":
        raise EvaluationError("revisit summary header differs")
    for key in ("visits", "correct", "contamination", "return_visit", "fallback"):
        _supplementary_uint(summary, key, summary_line_number)
    expected_totals = {
        "visits": len(visits),
        "correct": sum(item["correct"] for item in visits),
        "contamination": sum(item["contamination"] for item in visits),
        "return_visit": sum(item["return_visit"] for item in visits),
        "fallback": sum(item["fallback"] for item in visits),
    }
    if any(summary[key] != value for key, value in expected_totals.items()):
        raise EvaluationError("revisit summary differs from raw observations")
    summary_fingerprint = _text(
        summary["result_fingerprint"], "revisit summary fingerprint", HEX16
    )
    expected_summary_fingerprint = _functional_semantic(
        "aios-revisit-summary-v1",
        challenge,
        [
            summary["visits"], summary["correct"], summary["contamination"],
            summary["return_visit"], summary["fallback"],
            *(int(item["result_fingerprint"], 16) for item in visits),
        ],
    )
    if summary_fingerprint != expected_summary_fingerprint:
        raise EvaluationError("revisit summary fingerprint differs")
    summary["line"] = summary_line_number

    expected_sample_count = config["rounds_per_level"] * sum(
        config["concurrency_levels"]
    )
    if (
        len(sample_lines) != expected_sample_count
        or len(concurrency_lines) != len(config["concurrency_levels"])
    ):
        raise EvaluationError("revisit concurrency coverage is incomplete")
    samples_by_level: dict[int, list[dict[str, Any]]] = {
        level: [] for level in config["concurrency_levels"]
    }
    for line_number, line in sample_lines:
        fields = _parse_supplementary_fields(
            line, line_number, CONCURRENCY_SAMPLE_PREFIX,
            sample_field_names,
        )
        if fields["schema"] != str(qos_schema) or fields["status"] != "measured":
            raise EvaluationError(
                f"revisit concurrency sample header differs at line {line_number}"
            )
        numeric_fields = [
            "concurrency", "round", "slot", "correct", "contamination",
            "fallback", "submitted_us", "started_us", "completed_us",
            "received_us", "wait_us", "service_us", "turnaround_us",
            "isolation_ok",
        ]
        for key in numeric_fields:
            _supplementary_uint(fields, key, line_number)
        level = fields["concurrency"]
        if level not in samples_by_level:
            raise EvaluationError(
                f"unregistered revisit concurrency at line {line_number}"
            )
        expected_index = len(samples_by_level[level])
        expected_round, expected_slot = divmod(expected_index, level)
        expected_identity_index = expected_index % len(identities)
        identity = fields["identity"]
        if (
            fields["round"] != expected_round
            or fields["slot"] != expected_slot
            or expected_round >= config["rounds_per_level"]
            or identity not in identity_indexes
            or identity_indexes[identity] != expected_identity_index
        ):
            raise EvaluationError(
                f"revisit concurrency schedule differs at line {line_number}"
            )
        for key in ("correct", "fallback", "isolation_ok"):
            if fields[key] not in {0, 1}:
                raise EvaluationError(
                    f"revisit concurrency {key} is not binary at line {line_number}"
                )
        if (
            fields["fallback"] != 1 - fields["correct"]
            or (fields["contamination"] > 0 and fields["correct"] != 0)
            or fields["isolation_ok"]
            != int(
                fields["correct"] == 1
                and fields["contamination"] == 0
                and fields["fallback"] == 0
            )
        ):
            raise EvaluationError(
                f"revisit concurrency outcome conflicts at line {line_number}"
            )
        request_id = _text(
            fields["request_id"], "revisit concurrency request id", HEX16
        )
        expected_request_id = _semantic_token(
            "agentos-qos-request-v2",
            level, expected_round,
            expected_slot * len(identities) + expected_identity_index,
            challenge,
        )
        if int(request_id, 16) != expected_request_id:
            raise EvaluationError(
                f"revisit concurrency request is not challenge-bound at line {line_number}"
            )
        result_fingerprint = _text(
            fields["result_fingerprint"],
            "revisit concurrency result fingerprint", HEX16,
        )
        if not (
            fields["submitted_us"] <= fields["started_us"]
            <= fields["completed_us"] <= fields["received_us"]
            and fields["wait_us"]
            == fields["started_us"] - fields["submitted_us"]
            and fields["service_us"]
            == fields["completed_us"] - fields["started_us"]
            and fields["turnaround_us"]
            == fields["completed_us"] - fields["submitted_us"]
            == fields["wait_us"] + fields["service_us"]
        ):
            raise EvaluationError(
                f"revisit QoS timestamps differ at line {line_number}"
            )
        fingerprint_values = [
            level, expected_round, expected_slot, expected_identity_index,
            expected_request_id, fields["correct"], fields["contamination"],
            fields["fallback"], fields["isolation_ok"],
            fields["submitted_us"], fields["started_us"],
            fields["completed_us"], fields["received_us"], fields["wait_us"],
            fields["service_us"], fields["turnaround_us"],
        ]
        expected_fingerprint = _functional_semantic(
            "agentos-qos-sample-v2", challenge, fingerprint_values
        )
        if result_fingerprint != expected_fingerprint:
            raise EvaluationError(
                f"revisit concurrency sample fingerprint differs at line {line_number}"
            )
        fields["line"] = line_number
        samples_by_level[level].append(fields)

    summaries: list[dict[str, Any]] = []
    for expected_level, (line_number, line) in zip(
        config["concurrency_levels"], concurrency_lines
    ):
        fields = _parse_supplementary_fields(
            line, line_number, CONCURRENCY_PREFIX, summary_field_names
        )
        if fields["schema"] != str(qos_schema) or fields["status"] != "measured":
            raise EvaluationError(
                f"revisit concurrency header differs at line {line_number}"
            )
        for key in summary_field_names[1:]:
            if key in {"workload_digest", "result_fingerprint", "status"}:
                continue
            _supplementary_uint(fields, key, line_number)
        if fields["concurrency"] != expected_level:
            raise EvaluationError("revisit concurrency summary order differs")
        samples = samples_by_level[expected_level]
        durations = [item["turnaround_us"] for item in samples]
        requests = config["rounds_per_level"] * expected_level
        expected_values = {
            "rounds": config["rounds_per_level"],
            "requests": requests,
            "completed": len(samples),
            "duration_us": fields["end_us"] - fields["start_us"],
            "throughput_milli_rps": (
                requests * 1_000_000_000 // fields["duration_us"]
                if fields["duration_us"] > 0 else -1
            ),
            "avg_milli_us": sum(durations) * 1000 // requests,
            "p50_us": _nearest_rank_int(durations, 50),
            "p90_us": _nearest_rank_int(durations, 90),
            "p99_us": _nearest_rank_int(durations, 99),
            "correct": sum(item["correct"] for item in samples),
            "contamination": sum(item["contamination"] for item in samples),
            "fallback": sum(item["fallback"] for item in samples),
        }
        wait_values = [item["wait_us"] for item in samples]
        service_values = [item["service_us"] for item in samples]
        isolated = sum(item["isolation_ok"] for item in samples)
        identity_good = [
            sum(
                item["isolation_ok"] for item in samples
                if item["identity"] == identity
            )
            for identity in identities
        ]
        squares = sum(value * value for value in identity_good)
        maximum = max(identity_good)
        expected_values.update({
            "goodput_milli_rps": (
                isolated * 1_000_000_000 // fields["duration_us"]
                if fields["duration_us"] > 0 else -1
            ),
            "wait_avg_milli_us": sum(wait_values) * 1000 // requests,
            "wait_p50_us": _nearest_rank_int(wait_values, 50),
            "wait_p90_us": _nearest_rank_int(wait_values, 90),
            "wait_p99_us": _nearest_rank_int(wait_values, 99),
            "service_avg_milli_us": sum(service_values) * 1000 // requests,
            "service_p50_us": _nearest_rank_int(service_values, 50),
            "service_p90_us": _nearest_rank_int(service_values, 90),
            "service_p99_us": _nearest_rank_int(service_values, 99),
            "fairness_jain_ppm": (
                isolated * isolated * 1_000_000
                // (len(identities) * squares) if squares else 0
            ),
            "max_min_fairness_ppm": (
                min(identity_good) * 1_000_000 // maximum if maximum else 0
            ),
            "isolated": isolated,
        })
        if (
            fields["start_us"] > min(item["submitted_us"] for item in samples)
            or fields["end_us"] < max(item["received_us"] for item in samples)
        ):
            raise EvaluationError(
                f"revisit QoS interval differs at line {line_number}"
            )
        if (
            fields["end_us"] < fields["start_us"]
            or fields["duration_us"] <= 0
            or any(fields[key] != value for key, value in expected_values.items())
        ):
            raise EvaluationError(
                f"revisit concurrency summary differs from samples at line {line_number}"
            )
        result_fingerprint = _text(
            fields["result_fingerprint"],
            "revisit concurrency summary fingerprint", HEX16,
        )
        workload_digest = _text(
            fields["workload_digest"], "revisit workload digest", HEX16
        )
        expected_workload_digest = _functional_semantic(
            "agentos-qos-workload-v2", challenge,
            [
                fields["concurrency"],
                *(
                    value
                    for item in samples
                    for value in (
                        item["round"], item["slot"],
                        identity_indexes[item["identity"]],
                        int(item["request_id"], 16),
                    )
                ),
            ],
        )
        if workload_digest != expected_workload_digest:
            raise EvaluationError(
                f"revisit workload digest differs at line {line_number}"
            )
        summary_values = [
            fields[key] for key in summary_field_names[1:]
            if key not in {"workload_digest", "result_fingerprint", "status"}
        ]
        summary_values.append(int(workload_digest, 16))
        expected_fingerprint = _functional_semantic(
            "agentos-qos-summary-v2",
            challenge,
            [
                *summary_values,
                *(int(item["result_fingerprint"], 16) for item in samples),
            ],
        )
        if result_fingerprint != expected_fingerprint:
            raise EvaluationError(
                f"revisit concurrency summary fingerprint differs at line {line_number}"
            )
        fields["line"] = line_number
        fields["samples"] = samples
        summaries.append(fields)

    physical = [
        *(item["line"] for item in visits), summary["line"],
        *(
            line
            for level in config["concurrency_levels"]
            for line in (
                *(item["line"] for item in samples_by_level[level]),
                next(item["line"] for item in summaries
                     if item["concurrency"] == level),
            )
        ),
    ]
    if physical != sorted(physical) or len(physical) != len(set(physical)):
        raise EvaluationError("revisit marker physical order differs")
    return {
        "id": config["id"],
        "task": config["task"],
        "qos_schema_version": qos_schema,
        "performance_gate": None,
        "identity_unique": unique_identity,
        "visits": visits,
        "summary": summary,
        "concurrency": summaries,
        "line_numbers": physical,
    }

def _file_target_step(load: int, challenge: str) -> int:
    if load <= 0:
        raise EvaluationError("file-query load must be positive")
    challenge_value = int(challenge, 16)
    mixed = challenge_value ^ (challenge_value >> 32)
    step = ((mixed >> 8) | 1) % load
    if step == 0:
        step = 1
    while math.gcd(step, load) != 1:
        step += 2
        if step >= load:
            step = 1
    return step

def _file_target_meta(load: int, pair: int, challenge: str) -> int:
    if load <= 0 or pair < 0:
        raise EvaluationError("file-query pair must be non-negative")
    challenge_value = int(challenge, 16)
    mixed = challenge_value ^ (challenge_value >> 32)
    start = mixed % load
    if pair == 0:
        return start
    return (start + (pair - 1) * _file_target_step(load, challenge)) % load

def _file_target_sequence(load: int, pairs: int, challenge: str) -> list[int]:
    if pairs <= 0 or pairs > load:
        raise EvaluationError("file-query target sequence cannot be unique")
    targets = [_file_target_meta(load, pair, challenge) for pair in range(1, pairs + 1)]
    if len(set(targets)) != pairs:
        raise EvaluationError("file-query target sequence repeats a target")
    return targets

def _file_operation_targets(
    load: int, pair: int, operations: int, challenge: str
) -> list[int]:
    if operations <= 0 or operations > load:
        raise EvaluationError("file-query operations cannot form a unique target sequence")
    first = _file_target_meta(load, pair, challenge)
    step = _file_target_step(load, challenge)
    targets = [(first + item * step) % load for item in range(operations)]
    if len(set(targets)) != operations:
        raise EvaluationError("file-query operation target sequence repeats a target")
    return targets

def _file_manifest_selector(
    load: int, pair: int, operations: int, challenge: str
) -> int:
    value = _fnv_bytes(1469598103934665603, b"agentos-file-manifest-v1")
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_u64(value, load)
    for item in range(load):
        value = _fnv_bytes(value, f"e{item:03d}".encode("ascii"))
    for target in _file_operation_targets(load, pair, operations, challenge):
        value = _fnv_u64(value, target)
    return value

def _expected_workload_cached(
    experiment_id: str,
    selector_value: int,
    load: int,
    pair: int,
    challenge: str,
    operations: int,
) -> str:
    value = 1469598103934665603
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_bytes(value, experiment_id.encode("ascii"))
    selector = (
        _file_manifest_selector(load, pair, operations, challenge)
        if experiment_id in FILE_QUERY_EXPERIMENTS
        else selector_value
    )
    for item in (load, pair, operations, selector):
        value = _fnv_u64(value, item)
    return f"{value:016x}"

def _expected_workload(
    experiment: dict[str, Any],
    load: int,
    pair: int,
    challenge: str,
    *,
    operations_override: int | None = None,
) -> str:
    experiment_id = experiment["id"]
    operations = (
        _operations_for(experiment, load)
        if operations_override is None
        else operations_override
    )
    return _expected_workload_cached(
        experiment_id,
        experiment["selector"],
        load,
        pair,
        challenge,
        operations,
    )

def _expected_result_cached(
    experiment_id: str,
    load: int,
    pair: int,
    challenge: str,
    operations: int,
) -> str:
    value = _fnv_bytes(1469598103934665603, b"agentos-result-v1")
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_bytes(value, experiment_id.encode("ascii"))
    value = _fnv_u64(value, load)
    value = _fnv_u64(value, pair)
    if experiment_id in FILE_QUERY_EXPERIMENTS:
        dependency = _fnv_bytes(1469598103934665603, b"ready")
        dependency = 1 << (dependency % 60)
        for target in _file_operation_targets(
            load, pair, operations, challenge
        ):
            code = f"{target:03d}"
            for item in (1, 1, 1, 0, 2000 + target):
                value = _fnv_u64(value, item)
            for text in (
                f"e{code}", f"e{code}", "query", "artifact", f"q{code}",
                "measured evaluation fixture",
            ):
                value = _fnv_bytes(value, text.encode("ascii"))
            value = _fnv_u64(value, dependency)
    elif experiment_id == "tool_batch":
        challenge_value = int(challenge, 16)
        for item in range(operations):
            request_id = _semantic_token(
                "tool-request-v1", load, pair, item, challenge
            )
            arg0 = challenge_value ^ (pair << 32) ^ item
            arg1 = (load << 32) | pair
            for field in (request_id, arg0, arg1, 9, arg0, arg1):
                value = _fnv_u64(value, field)
            value = _fnv_bytes(value, b"agenteval")
    elif experiment_id == "context_access":
        request_id = _semantic_token(
            "context-request-v1", load, pair, 0, challenge
        )
        arg0 = int(challenge, 16) ^ (load << 32) ^ pair
        arg1 = (pair << 32) | load
        for _ in range(operations):
            for field in (request_id, arg0, 12, arg0, arg1):
                value = _fnv_u64(value, field)
            value = _fnv_bytes(value, b"context-eval")
            value = _fnv_bytes(value, b"context-eval")
    else:
        raise EvaluationError(f"no result oracle for experiment {experiment_id}")
    return f"{value:016x}"

def _expected_result(
    experiment: dict[str, Any],
    load: int,
    pair: int,
    challenge: str,
    *,
    operations_override: int | None = None,
) -> str:
    operations = (
        _operations_for(experiment, load)
        if operations_override is None
        else operations_override
    )
    return _expected_result_cached(
        experiment["id"], load, pair, challenge, operations
    )

def _functional_semantic(
    domain: str,
    challenge: str,
    values: list[int],
) -> str:
    value = _fnv_bytes(1469598103934665603, domain.encode("ascii"))
    value = _fnv_u64(value, int(challenge, 16))
    for item in values:
        value = _fnv_u64(value, item)
    return f"{value:016x}"

def _as_u64(value: int) -> int:
    return value & ((1 << 64) - 1)

def _task2_schema_fingerprint() -> int:
    value = _fnv_bytes(1469598103934665603, b"task2-tool-schema-v1")
    selected = (
        (1, 3, 1, "echo", "payload:string,arg0:uint64,arg1:uint64"),
        (4, 1, 1, "query_process", "type?:uint64"),
        (13, 2, 1, "capability_check", "role:uint64,action:string"),
    )
    for tool_id, param_count, flags, name, params in selected:
        value = _fnv_u64(value, tool_id)
        value = _fnv_u64(value, param_count)
        value = _fnv_u64(value, flags)
        value = _fnv_bytes(value, name.encode("ascii"))
        value = _fnv_bytes(value, params.encode("ascii"))
    return value

TASK2_REQUIRED_TOOLS = (
    (1, 3, 1, "echo", "payload:string,arg0:uint64,arg1:uint64"),
    (4, 1, 1, "query_process", "type?:uint64"),
    (13, 2, 1, "capability_check", "role:uint64,action:string"),
)

def _parse_catalog_schema(params: str, count: int, line_number: int) -> None:
    if params == "none":
        if count != 0:
            raise EvaluationError(f"catalog schema count differs at line {line_number}")
        return
    entries = params.split(",")
    if len(entries) != count or count <= 0 or count > 8:
        raise EvaluationError(f"catalog schema count differs at line {line_number}")
    seen: set[str] = set()
    for entry in entries:
        match = re.fullmatch(r"([a-z][a-z0-9_]{0,14})(\?)?:(uint64|string)", entry)
        if match is None:
            raise EvaluationError(f"catalog parameter schema is invalid at line {line_number}")
        key = match.group(1)
        if key in seen:
            raise EvaluationError(f"duplicate catalog parameter at line {line_number}")
        seen.add(key)

def _parse_tool_catalog(
    lines: list[str], challenge: str,
) -> dict[str, Any]:
    catalog_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(CATALOG_PREFIX)
    ]
    if not catalog_lines:
        raise EvaluationError("Task2 versioned tool catalog is missing")
    descriptors: list[dict[str, Any]] = []
    for line_number, line in catalog_lines:
        fields = _parse_receipt_fields(
            line, line_number, CATALOG_PREFIX, CATALOG_FIELDS
        )
        if (
            fields["schema"] != "1"
            or fields["challenge"] != challenge
            or fields["status"] != "listed"
        ):
            raise EvaluationError(f"catalog challenge/schema differs at line {line_number}")
        for key in ("index", "total", "abi", "tool_id", "flags", "param_count"):
            try:
                number = int(fields[key], 10)
            except ValueError as error:
                raise EvaluationError(f"catalog {key} is invalid at line {line_number}") from error
            if number < 0 or number > (1 << 53) - 1 or str(number) != fields[key]:
                raise EvaluationError(f"catalog {key} is invalid at line {line_number}")
            fields[key] = number
        name = fields["name"]
        params = fields["params"]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", name):
            raise EvaluationError(f"catalog tool name is invalid at line {line_number}")
        if (
            fields["abi"] != 2
            or fields["tool_id"] <= 0
            or fields["flags"] not in {1, 2}
        ):
            raise EvaluationError(f"catalog descriptor envelope is invalid at line {line_number}")
        _parse_catalog_schema(params, fields["param_count"], line_number)
        fields["line"] = line_number
        descriptors.append(fields)
    total = descriptors[0]["total"]
    if total < len(TASK2_REQUIRED_TOOLS) or total > 4096 or total != len(descriptors):
        raise EvaluationError("Task2 catalog total is inconsistent")
    if any(
        item["index"] != index or item["total"] != total
        for index, item in enumerate(descriptors)
    ):
        raise EvaluationError("Task2 catalog indexes/totals are inconsistent")
    ids = [item["tool_id"] for item in descriptors]
    names = [item["name"] for item in descriptors]
    if len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise EvaluationError("Task2 catalog contains duplicate tool identity")
    by_id = {item["tool_id"]: item for item in descriptors}
    required_mask = 0
    for index, (tool_id, param_count, flags, name, params) in enumerate(
        TASK2_REQUIRED_TOOLS
    ):
        item = by_id.get(tool_id)
        if item is None or (
            item["param_count"], item["flags"], item["name"], item["params"]
        ) != (param_count, flags, name, params):
            raise EvaluationError("Task2 required core tool schema differs")
        required_mask |= 1 << index
    catalog_hash = _fnv_bytes(1469598103934665603, b"task2-tool-catalog-v1")
    for item in (int(challenge, 16), 1, 2, total):
        catalog_hash = _fnv_u64(catalog_hash, item)
    for item in descriptors:
        for field in (
            item["tool_id"], item["abi"], 216, item["param_count"], item["flags"]
        ):
            catalog_hash = _fnv_u64(catalog_hash, field)
        catalog_hash = _fnv_bytes(catalog_hash, item["name"].encode("ascii"))
        catalog_hash = _fnv_bytes(catalog_hash, item["params"].encode("ascii"))
    return {
        "total": total,
        "callable_count": sum(item["flags"] == 1 for item in descriptors),
        "required_count": len(TASK2_REQUIRED_TOOLS),
        "required_mask": required_mask,
        "catalog_hash": catalog_hash,
        "core_hash": _task2_schema_fingerprint(),
        "line_numbers": [item["line"] for item in descriptors],
    }

def _task3_tool_semantic(challenge: str) -> int:
    rounds = 6
    value = _fnv_bytes(1469598103934665603, b"task3-tool-path-v1")
    value = _fnv_u64(value, int(challenge, 16))
    challenge_value = int(challenge, 16)
    for item in range(rounds + 1):
        sequence = item + 1
        parent_sequence = item if item < rounds else 3
        request_id = _semantic_token("task3-tool-v1", rounds, 0, item, challenge)
        arg0 = challenge_value ^ item
        arg1 = (rounds << 32) | item
        for field in (
            sequence,
            request_id,
            parent_sequence,
            arg0,
            len("ctx-tool"),
            arg0,
            arg1,
            1,
            1,
            0,
        ):
            value = _fnv_u64(value, field)
        value = _fnv_bytes(value, b"ctx-tool")
        value = _fnv_bytes(value, b"ctx-tool")
    return value

def _task3_semantic(challenge: str, values: list[int]) -> str:
    """把完整任务 3 回执绑定到新的 Host challenge。"""
    return _functional_semantic("task3-semantic-v2", challenge, values)

def _task4_fixture(challenge: str) -> dict[str, Any]:
    """返回由 challenge 派生的任务 4 属性和文件内容。"""
    challenge_value = int(challenge, 16)
    code = (challenge_value ^ (challenge_value >> 32)) % 1000
    suffix = f"{code:03d}"
    fid_base = 10000 + code * 4
    names = [f"t4{label}{suffix}" for label in ("a", "b", "c")]
    summaries = [
        f"memory needle {challenge}",
        f"memory peer {challenge}",
        f"memory decoy {challenge}",
    ]
    bodies = [f"task4-content-{label}-{challenge}" for label in ("a", "b", "c")]
    return {
        "code": code,
        "fids": [fid_base + offset for offset in range(3)],
        "names": names,
        "summaries": summaries,
        "bodies": bodies,
        "dependency_mask": 1 << (
            _fnv_bytes(1469598103934665603, b"ready") % 60
        ),
    }

def _task4_query_semantic(
    domain: str,
    challenge: str,
    query: dict[str, Any],
    result: dict[str, Any],
    hits: list[dict[str, Any]],
) -> int:
    """从语义字段重算一次任务 4 结构化查询结果。"""
    value = _fnv_bytes(1469598103934665603, domain.encode("ascii"))
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_u64(value, query["flags"])
    value = _fnv_u64(value, query["max_hits"])
    for field in (
        "physical_name", "logical_path", "project", "workflow", "run_id",
        "stage", "kind", "status", "summary_contains",
    ):
        value = _fnv_bytes(value, query[field].encode("ascii"))
    for field in (
        "total_hits", "returned", "truncated", "used_index", "plan",
        "fs_generation",
    ):
        value = _fnv_u64(value, result[field])
    if len(hits) != result["returned"]:
        raise EvaluationError("Task4 query oracle hit count is inconsistent")
    for hit in hits:
        value = _fnv_u64(value, hit["fid"])
        for field in (
            "physical_name", "logical_path", "stage", "kind", "status",
            "summary",
        ):
            value = _fnv_bytes(value, hit[field].encode("ascii"))
        for field in (
            "dependency_mask", "dev", "inum", "incarnation", "size",
            "fs_generation",
        ):
            value = _fnv_u64(value, hit[field])
    return value

def _parse_one_functional_receipt(
    line: str,
    line_number: int,
    challenge: str,
    *,
    launcher: bool,
) -> dict[str, Any]:
    prefix = LAUNCHER_PREFIX if launcher else FUNCTIONAL_PREFIX
    expected_fields = LAUNCHER_FIELDS if launcher else FUNCTIONAL_FIELDS
    fields = _parse_receipt_fields(line, line_number, prefix, expected_fields)
    if fields["schema"] != "1" or fields["challenge"] != challenge:
        raise EvaluationError(
            f"functional receipt challenge/schema differs at line {line_number}"
        )
    expected_status = "ready" if launcher else "passed"
    if fields["status"] != expected_status:
        raise EvaluationError(f"functional receipt status differs at line {line_number}")
    task = "launcher" if launcher else fields["task"]
    if not launcher and task not in FUNCTIONAL_TASKS:
        raise EvaluationError(f"functional receipt task is invalid at line {line_number}")
    values = _parse_receipt_values(fields["values"], line_number)
    semantic = _text(fields["semantic"], "functional semantic", HEX16)
    receipt = _text(fields["receipt"], "functional receipt", HEX16)
    if receipt != _functional_receipt_hash(task, challenge, values, semantic):
        raise EvaluationError(f"functional receipt hash differs at line {line_number}")
    return {
        "task": task,
        "values": values,
        "semantic": semantic,
        "receipt": receipt,
        "line": line_number,
    }

def _validate_functional_task1(
    launcher: dict[str, Any],
    receipt: dict[str, Any],
    challenge: str,
) -> None:
    launch = launcher["values"]
    values = receipt["values"]
    if len(launch) != 5 or len(values) != 19:
        raise EvaluationError("Task1 functional receipt value count differs")
    if (
        launch[0] <= 0
        or launch[1:] != [0, 0, 0, 0]
        or launcher["semantic"]
        != _functional_semantic("task1-launcher-semantic-v1", challenge, launch)
    ):
        raise EvaluationError("Task1 launcher is not a plain process receipt")
    (
        agent_pid, parent_pid, is_agent, role, agent_id, context_base,
        context_size, magic, version, capacity, resource_quota, loop_state,
        user_cache_offset, user_cache_size, direct_token, sentinel_pid,
        sentinel_status, sentinel_role, compatibility_api,
    ) = values
    if (
        agent_pid <= 0
        or agent_pid == parent_pid
        or parent_pid != launch[0]
        or is_agent != 1
        or role != 4
        or agent_id <= 0
        or context_base != TASK1_CONTEXT_CONTRACT["base"]
        or context_size != TASK1_CONTEXT_CONTRACT["size"]
        or magic != TASK1_CONTEXT_CONTRACT["magic"]
        or version != TASK1_CONTEXT_CONTRACT["version"]
        or capacity != TASK1_CONTEXT_CONTRACT["capacity"]
        or resource_quota != capacity
        or loop_state not in {1, 2, 3}
        or user_cache_offset != TASK1_CONTEXT_CONTRACT["user_cache_offset"]
        or user_cache_size != TASK1_CONTEXT_CONTRACT["user_cache_size"]
        or _as_u64(direct_token)
        != (int(challenge, 16) ^ agent_pid ^ context_base)
        or sentinel_pid <= 0
        or sentinel_pid in {agent_pid, parent_pid}
        or sentinel_status != 0
        or sentinel_role != 1
        or compatibility_api != 1
        or receipt["semantic"]
        != _functional_semantic("task1-semantic-v1", challenge, values)
    ):
        raise EvaluationError("Task1 Agent PCB/context receipt is inconsistent")

def _validate_functional_task2(
    receipt: dict[str, Any],
    challenge: str,
    task1: dict[str, Any],
    catalog: dict[str, Any],
) -> None:
    values = receipt["values"]
    if len(values) != 33:
        raise EvaluationError("Task2 functional receipt value count differs")
    (
        catalog_schema, catalog_abi, tool_count, callable_count,
        required_count, required_mask, catalog_hash, schema_hash,
        echo_sequence, echo_length,
        echo_arg0, echo_arg1, echo_payload_hash, query_sequence, query_used,
        query_agents, query_runnable, cap_sequence, cap_allowed, cap_role,
        cap_mask, unknown_sequence, unknown_status, unknown_error_hash,
        mismatch_sequence, mismatch_status, mismatch_error_hash,
        duplicate_sequence, duplicate_status, duplicate_error_hash,
        bad_type_sequence, bad_type_status, bad_type_error_hash,
    ) = values
    agent_pid = task1["values"][0]
    expected_payload_hash = _fnv_bytes(1469598103934665603, b"eval-v2")
    if (
        catalog_schema != 1
        or catalog_abi != 2
        or tool_count != catalog["total"]
        or callable_count != catalog["callable_count"]
        or required_count != catalog["required_count"]
        or required_mask != catalog["required_mask"]
        or _as_u64(catalog_hash) != catalog["catalog_hash"]
        or _as_u64(schema_hash) != catalog["core_hash"]
        or not (0 < echo_sequence < query_sequence < cap_sequence)
        or echo_length != len("eval-v2")
        or _as_u64(echo_arg0) != (int(challenge, 16) ^ agent_pid)
        or _as_u64(echo_arg1)
        != (int(challenge, 16) ^ 0xA5A5A5A5A5A5A5A5)
        or _as_u64(echo_payload_hash) != expected_payload_hash
        or query_used < 1
        or query_agents != query_used
        or query_runnable < 1
        or cap_allowed != 1
        or cap_role != 4
        or (_as_u64(cap_mask) & (1 << 9)) == 0
        or unknown_sequence != 0
        or unknown_status != -2
        or _as_u64(unknown_error_hash)
        != _fnv_bytes(1469598103934665603, b"unknown_tool")
        or mismatch_sequence != 0
        or mismatch_status != -1
        or _as_u64(mismatch_error_hash)
        != _fnv_bytes(1469598103934665603, b"tool_mismatch")
        or duplicate_sequence != 0
        or duplicate_status != -9
        or _as_u64(duplicate_error_hash)
        != _fnv_bytes(1469598103934665603, b"duplicate_param")
        or bad_type_sequence != 0
        or bad_type_status != -15
        or _as_u64(bad_type_error_hash)
        != _fnv_bytes(1469598103934665603, b"bad_param_type")
        or receipt["semantic"]
        != _functional_semantic("task2-semantic-v1", challenge, values)
    ):
        raise EvaluationError("Task2 structured-tool receipt is inconsistent")

def _validate_functional_task3(
    receipt: dict[str, Any],
    challenge: str,
) -> None:
    values = receipt["values"]
    if len(values) != 22:
        raise EvaluationError("Task3 functional receipt value count differs")
    (
        rounds, query_count, direct_count, first_sequence, last_sequence,
        tool_semantic, rollback_sequence, active_after_rollback, old_branch,
        new_branch, branch_sequence, branch_parent, post_query_count,
        post_direct_count, clear_count, capacity, fifo_count, fifo_dropped,
        fifo_oldest, fifo_latest, eviction_policy, active_after_branch,
    ) = values
    if (
        rounds != 6
        or query_count != rounds
        or direct_count != rounds
        or first_sequence != 1
        or last_sequence != rounds
        or _as_u64(tool_semantic) != _task3_tool_semantic(challenge)
        or rollback_sequence != first_sequence + 2
        or active_after_rollback != 3
        or old_branch <= 0
        or new_branch <= 0
        or new_branch == old_branch
        or branch_sequence != last_sequence + 1
        or branch_parent != rollback_sequence
        or active_after_branch != active_after_rollback + 1
        or post_query_count != active_after_branch
        or post_direct_count != active_after_branch
        or clear_count != 0
        or capacity != 128
        or fifo_count != capacity
        or fifo_dropped != 5
        or fifo_oldest != 6
        or fifo_latest != capacity + fifo_dropped
        or fifo_latest - fifo_oldest + 1 != fifo_count
        or eviction_policy != 1
        or receipt["semantic"] != _task3_semantic(challenge, values)
    ):
        raise EvaluationError("Task3 Context Path receipt is inconsistent")

def _validate_functional_task4(
    receipt: dict[str, Any],
    challenge: str,
) -> None:
    values = receipt["values"]
    if len(values) != 56:
        raise EvaluationError("Task4 functional receipt value count differs")
    (
        fixture_code, fid_a, fid_b, fid_c,
        and_total, and_returned, and_truncated, and_used_index, and_plan,
        and_fid0, and_fid1,
        and_dev0, and_inum0, and_incarnation0, and_size0, and_hit_generation0,
        and_dev1, and_inum1, and_incarnation1, and_size1, and_hit_generation1,
        and_generation, and_semantic,
        summary_total, summary_returned, summary_truncated, summary_used_index,
        summary_plan, summary_fid, summary_dev, summary_inum,
        summary_incarnation, summary_size, summary_hit_generation,
        summary_generation, summary_semantic,
        digest_request, digest_response_request, digest_sequence, digest_status,
        digest_tool_id, digest_size, digest_bytes, digest_hash,
        digest_preview_hash,
        delete_a_status, after_a_total, after_a_returned, after_a_fid,
        after_a_generation, after_a_semantic,
        delete_b_status, after_b_total, after_b_returned, after_b_generation,
        after_b_semantic,
    ) = values
    fixture = _task4_fixture(challenge)
    names = fixture["names"]
    fids = fixture["fids"]
    summaries = fixture["summaries"]
    bodies = fixture["bodies"]
    dependency = fixture["dependency_mask"]
    common_query = {
        "flags": 2,
        "max_hits": 8,
        "physical_name": "",
        "logical_path": "",
        "project": "eval4",
        "workflow": "query-proof",
        "run_id": challenge[1:],
        "stage": "memory",
        "kind": "artifact",
        "status": "ready",
        "summary_contains": "",
    }
    summary_query = dict(common_query)
    summary_query["summary_contains"] = f"needle {challenge}"

    def expected_hit(index: int, dev: int, inum: int, incarnation: int,
                     size: int, generation: int) -> dict[str, Any]:
        return {
            "fid": fids[index],
            "physical_name": names[index],
            "logical_path": names[index],
            "stage": "memory",
            "kind": "artifact" if index < 2 else "report",
            "status": "ready",
            "summary": summaries[index],
            "dependency_mask": dependency,
            "dev": dev,
            "inum": inum,
            "incarnation": incarnation,
            "size": size,
            "fs_generation": generation,
        }

    hit_a = expected_hit(
        0, and_dev0, and_inum0, and_incarnation0, and_size0,
        and_hit_generation0,
    )
    hit_b = expected_hit(
        1, and_dev1, and_inum1, and_incarnation1, and_size1,
        and_hit_generation1,
    )
    and_result = {
        "total_hits": and_total,
        "returned": and_returned,
        "truncated": and_truncated,
        "used_index": and_used_index,
        "plan": and_plan,
        "fs_generation": and_generation,
    }
    summary_result = {
        "total_hits": summary_total,
        "returned": summary_returned,
        "truncated": summary_truncated,
        "used_index": summary_used_index,
        "plan": summary_plan,
        "fs_generation": summary_generation,
    }
    summary_hit = expected_hit(
        0, summary_dev, summary_inum, summary_incarnation, summary_size,
        summary_hit_generation,
    )
    after_a_result = {
        "total_hits": after_a_total,
        "returned": after_a_returned,
        "truncated": 0,
        "used_index": 0,
        "plan": 0,
        "fs_generation": after_a_generation,
    }
    after_b_result = {
        "total_hits": after_b_total,
        "returned": after_b_returned,
        "truncated": 0,
        "used_index": 0,
        "plan": 0,
        "fs_generation": after_b_generation,
    }
    expected_digest_request = _semantic_token(
        "task4-digest-v2", fixture_code, 0, 0, challenge
    )
    expected_digest = _fnv_bytes(1469598103934665603, bodies[0].encode("ascii"))
    expected_and_semantic = _task4_query_semantic(
        "task4-attributes-v2", challenge, common_query, and_result,
        [hit_a, hit_b],
    )
    expected_summary_semantic = _task4_query_semantic(
        "task4-summary-v2", challenge, summary_query, summary_result,
        [summary_hit],
    )
    expected_after_a_semantic = _task4_query_semantic(
        "task4-delete-one-v2", challenge, common_query, after_a_result,
        [hit_b],
    )
    expected_after_b_semantic = _task4_query_semantic(
        "task4-delete-all-v2", challenge, common_query, after_b_result, [],
    )
    if (
        fixture_code != fixture["code"]
        or [fid_a, fid_b, fid_c] != fids
        or [and_total, and_returned, and_truncated, and_used_index, and_plan]
        != [2, 2, 0, 0, 0]
        or [and_fid0, and_fid1] != fids[:2]
        or min(
            and_dev0, and_inum0, and_incarnation0, and_hit_generation0,
            and_dev1, and_inum1, and_incarnation1, and_hit_generation1,
            and_generation,
        ) <= 0
        or (and_dev0, and_inum0, and_incarnation0)
        == (and_dev1, and_inum1, and_incarnation1)
        or and_size0 != len(bodies[0])
        or and_size1 != len(bodies[1])
        or _as_u64(and_semantic) != expected_and_semantic
        or [summary_total, summary_returned, summary_truncated,
            summary_used_index, summary_plan, summary_fid]
        != [1, 1, 0, 0, 0, fid_a]
        or (summary_dev, summary_inum, summary_incarnation, summary_size,
            summary_hit_generation)
        != (and_dev0, and_inum0, and_incarnation0, and_size0,
            and_hit_generation0)
        or summary_generation != and_generation
        or _as_u64(summary_semantic) != expected_summary_semantic
        or _as_u64(digest_request) != expected_digest_request
        or _as_u64(digest_response_request) != expected_digest_request
        or digest_sequence <= 0
        or digest_status != 0
        or digest_tool_id != 20
        or digest_size != len(bodies[0])
        or digest_bytes != len(bodies[0])
        or _as_u64(digest_hash) != expected_digest
        or _as_u64(digest_preview_hash) != expected_digest
        or delete_a_status != 0
        or [after_a_total, after_a_returned, after_a_fid] != [1, 1, fid_b]
        or after_a_generation <= summary_generation
        or _as_u64(after_a_semantic) != expected_after_a_semantic
        or delete_b_status != 0
        or [after_b_total, after_b_returned] != [0, 0]
        or after_b_generation <= after_a_generation
        or _as_u64(after_b_semantic) != expected_after_b_semantic
        or receipt["semantic"]
        != _functional_semantic("task4-semantic-v2", challenge, values)
    ):
        raise EvaluationError(
            "Task4 attribute/content lifecycle receipt is inconsistent"
        )

def _validate_functional_task5(
    receipt: dict[str, Any],
    challenge: str,
    task1: dict[str, Any],
) -> None:
    values = receipt["values"]
    if len(values) != 28:
        raise EvaluationError("Task5 functional receipt value count differs")
    (
        primary_pid, helper_pid, event_source, event_target, corr_id, event_id,
        event_tick, sleep_before, sleep_after, wake_before, wake_after,
        heartbeat_first_tick, heartbeat_second_tick, heartbeat_sleep_delta,
        heartbeat_wake_delta, stopped_timeout_status, helper_exit_status,
        stable_agents, helper_role, wait_tick_before, wait_tick_after,
        wait_dispatch_before, wait_dispatch_after, wait_vruntime_before,
        wait_vruntime_after, wait_loop_before, wait_loop_after, sched_weight,
    ) = values
    expected_corr = _semantic_token("task5-event-v1", 2, 0, 0, challenge)
    wait_ticks = wait_tick_after - wait_tick_before
    wait_dispatches = wait_dispatch_after - wait_dispatch_before
    wait_vruntime = wait_vruntime_after - wait_vruntime_before
    dispatch_cost = max(1, 1000 // sched_weight) if sched_weight > 0 else 0
    wait_loops = wait_loop_after - wait_loop_before
    if (
        primary_pid != task1["values"][0]
        or helper_pid <= 0
        or helper_pid == primary_pid
        or event_source != helper_pid
        or event_target != primary_pid
        or _as_u64(corr_id) != expected_corr
        or event_id <= 0
        or event_tick <= 0
        or sleep_after <= sleep_before
        or wake_after <= wake_before
        or heartbeat_first_tick <= 0
        or heartbeat_second_tick <= heartbeat_first_tick
        or heartbeat_sleep_delta < 1
        or heartbeat_wake_delta < 1
        or stopped_timeout_status != -7
        or helper_exit_status != 0
        or stable_agents != 2
        or helper_role != 1
        or wait_tick_after < wait_tick_before
        or wait_ticks < TASK5_DELAY_TICKS
        or not wait_tick_before <= event_tick <= wait_tick_after
        or wait_dispatch_after < wait_dispatch_before
        or wait_dispatches < 1
        or wait_vruntime_after < wait_vruntime_before
        or wait_vruntime != wait_dispatches * dispatch_cost
        or wait_loop_after < wait_loop_before
        or not 1 <= wait_loops <= TASK5_MAX_WAIT_LOOPS
        or receipt["semantic"]
        != _functional_semantic("task5-semantic-v2", challenge, values)
    ):
        raise EvaluationError(
            "Task5 blocking wait and scheduler-accounting receipt is inconsistent"
        )

def validate_functional_log(
    lines: list[str],
    challenge: str,
    sample_rows: list[dict[str, Any]],
    suite: dict[str, Any],
) -> dict[str, Any]:
    """校验任务 1-5 回执并返回其原始标记绑定。"""
    launcher_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(LAUNCHER_PREFIX)
    ]
    functional_lines = [
        (number, line) for number, line in enumerate(lines, 1)
        if line.startswith(FUNCTIONAL_PREFIX)
    ]
    if len(launcher_lines) != 1:
        raise EvaluationError("Guest must emit exactly one Task1 launcher receipt")
    if len(functional_lines) != len(FUNCTIONAL_TASKS):
        raise EvaluationError("Guest must emit exactly one receipt for every Task1-5")
    launcher = _parse_one_functional_receipt(
        launcher_lines[0][1], launcher_lines[0][0], challenge, launcher=True
    )
    receipts: dict[str, dict[str, Any]] = {}
    for line_number, line in functional_lines:
        receipt = _parse_one_functional_receipt(
            line, line_number, challenge, launcher=False
        )
        task = receipt["task"]
        if task in receipts:
            raise EvaluationError(f"duplicate {task} functional receipt")
        receipts[task] = receipt
    if list(receipts) != list(FUNCTIONAL_TASKS):
        raise EvaluationError("Task1-5 functional receipts are missing or out of order")
    catalog = _parse_tool_catalog(lines, challenge)
    revisit = _parse_revisit_evaluation(
        lines, challenge, suite["supplementary_scenarios"][0]
    )
    measurement_lines = [
        row.get("source_line", row.get("line")) for row in sample_rows
    ]
    worker_lines = [
        number for number, line in enumerate(lines, 1)
        if line == "agenteval_ucore: worker passed"
    ]
    parent_lines = [
        number for number, line in enumerate(lines, 1)
        if line == "agenteval_ucore: parent passed"
    ]
    if len(worker_lines) != 1 or len(parent_lines) != 1:
        raise EvaluationError("functional log lacks unique completion markers")
    if (
        not measurement_lines
        or launcher["line"] >= min(measurement_lines)
        or receipts["task1"]["line"] <= max(measurement_lines)
        or launcher["line"] >= receipts["task1"]["line"]
        or receipts["task1"]["line"] >= catalog["line_numbers"][0]
        or catalog["line_numbers"][-1] >= receipts["task2"]["line"]
    ):
        raise EvaluationError("functional probes are not outside the measured interval")
    if receipts["task5"]["line"] >= worker_lines[0]:
        raise EvaluationError("business marker order differs from Guest lifecycle")
    if (
        worker_lines[0] >= revisit["line_numbers"][0]
        or revisit["line_numbers"][-1] >= parent_lines[0]
    ):
        raise EvaluationError(
            "supplementary revisit probes overlap the headline measured interval"
        )
    _validate_functional_task1(launcher, receipts["task1"], challenge)
    _validate_functional_task2(
        receipts["task2"], challenge, receipts["task1"], catalog
    )
    _validate_functional_task3(receipts["task3"], challenge)
    _validate_functional_task4(receipts["task4"], challenge)
    _validate_functional_task5(receipts["task5"], challenge, receipts["task1"])
    ordered_lines = sorted({
        launcher["line"],
        *(receipts[task]["line"] for task in FUNCTIONAL_TASKS),
        *catalog["line_numbers"],
        *revisit["line_numbers"],
    })
    return {
        "launcher": launcher,
        "tasks": receipts,
        "catalog": catalog,
        "supplementary": revisit,
        "line_numbers": ordered_lines,
    }

def extract_log(
    path: Path,
    suite: dict[str, Any],
    challenge: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_ref = path.name
    path = _safe_regular_file(path, f"source log {source_ref}")
    raw = path.read_bytes()
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise EvaluationError(f"source log is not UTF-8: {source_ref}") from error
    experiments = _experiment_map(suite)
    if any(line.startswith("agenteval_ucore: check failed:") for line in lines):
        raise EvaluationError(f"Guest reported an evaluation check failure: {source_ref}")
    challenge_lines = [
        (line_number, line) for line_number, line in enumerate(lines, 1)
        if line.startswith("agenteval_ucore: challenge=")
    ]
    expected_challenge_line = f"agenteval_ucore: challenge={challenge}"
    if len(challenge_lines) != 1 or challenge_lines[0][1] != expected_challenge_line:
        raise EvaluationError(f"source log challenge differs from run plan: {source_ref}")
    pass_lines = [
        line_number for line_number, line in enumerate(lines, 1)
        if line == "agenteval_ucore: parent passed"
    ]
    worker_lines = [
        line_number for line_number, line in enumerate(lines, 1)
        if line == "agenteval_ucore: worker passed"
    ]
    if len(pass_lines) != 1 or len(worker_lines) != 1 or worker_lines[0] >= pass_lines[0]:
        raise EvaluationError(f"source log lacks ordered worker/parent completion: {source_ref}")
    diagnostics: dict[tuple[str, int], dict[str, Any]] = {}
    for line_number, line in enumerate(lines, 1):
        if not line.startswith(DIAGNOSTIC_PREFIX):
            continue
        diagnostic = _parse_diagnostic(line, line_number)
        experiment = experiments.get(diagnostic["experiment"])
        if experiment is None or diagnostic["load"] not in experiment["loads"]:
            raise EvaluationError(f"unconfigured diagnostic at line {line_number}")
        expected_workload = _expected_workload(
            experiment,
            diagnostic["load"],
            0,
            challenge,
            operations_override=1,
        )
        if diagnostic["workload_fingerprint"] != expected_workload:
            raise EvaluationError(
                f"diagnostic workload fingerprint is not challenge-bound at line {line_number}"
            )
        expected_result = _expected_result(
            experiment,
            diagnostic["load"],
            0,
            challenge,
            operations_override=1,
        )
        if diagnostic["result_fingerprint"] != expected_result:
            raise EvaluationError(
                f"diagnostic result fingerprint differs from Host semantic oracle at line {line_number}"
            )
        diagnostic_key = (diagnostic["experiment"], diagnostic["load"])
        if diagnostic_key in diagnostics:
            raise EvaluationError(f"duplicate readiness diagnostic in {source_ref}")
        diagnostics[diagnostic_key] = diagnostic
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for line_number, line in enumerate(lines, 1):
        if not line.startswith(MARKER_PREFIX):
            continue
        marker = _parse_marker(line, line_number)
        experiment = experiments.get(marker["experiment"])
        if experiment is None or marker["load"] not in experiment["loads"]:
            raise EvaluationError(f"unconfigured sample at line {line_number}")
        role = None
        for candidate in ("baseline", "treatment"):
            variant = experiment[candidate]
            if marker["variant"] == variant["id"]:
                role = candidate
                if marker["cache"] != variant["cache"]:
                    raise EvaluationError(f"cache policy mismatch at line {line_number}")
                break
        if role is None:
            raise EvaluationError(f"unconfigured variant at line {line_number}")
        if marker["workload_fingerprint"] != _expected_workload(
            experiment, marker["load"], marker["pair"], challenge
        ):
            raise EvaluationError(f"workload fingerprint is not challenge-bound at line {line_number}")
        if marker["result_fingerprint"] != _expected_result(
            experiment, marker["load"], marker["pair"], challenge
        ):
            raise EvaluationError(
                f"result fingerprint differs from Host semantic oracle at line {line_number}"
            )
        expected_position = (
            (role == "baseline" and marker["order"] == "AB")
            or (role == "treatment" and marker["order"] == "BA")
        )
        position = 1 if expected_position else 2
        key = (marker["experiment"], marker["load"], marker["pair"], role)
        if key in seen:
            raise EvaluationError(f"duplicate sample at line {line_number}")
        seen.add(key)
        rows.append({
            "kind": "agentos-evaluation-metric-row",
            "experiment": marker["experiment"],
            "load": marker["load"],
            "inner_pair": marker["pair"],
            "role": role,
            "order": marker["order"],
            "operations": marker["operations"],
            "dataset_size": marker["dataset_size"],
            "work_units": marker["work_units"],
            "records_examined": marker["records_examined"],
            "result_items": marker["result_items"],
            "index_rebuild_records": marker["index_rebuild_records"],
            "result_cache_hits": marker["result_cache_hits"],
            "workload_fingerprint": marker["workload_fingerprint"],
            "result_fingerprint": marker["result_fingerprint"],
            "source_line": line_number,
        })
        # 下方还会按物理标记顺序复核位置。
        rows[-1]["_position"] = position
    actual_physical_order: list[tuple[Any, ...]] = []
    for row in rows:
        actual_physical_order.append((
            row["source_line"], "sample", row["experiment"], row["load"],
            row["inner_pair"], row["role"],
        ))
    for (experiment_id, load), diagnostic in diagnostics.items():
        actual_physical_order.append((
            diagnostic["line"], "diagnostic", experiment_id, load, 0, "readiness",
        ))
    actual_sequence = [item[1:] for item in sorted(actual_physical_order)]
    expected_sequence: list[tuple[Any, ...]] = []
    experiments = _experiment_map(suite)
    for scheduled in suite["execution_schedule"]:
        experiment = experiments[scheduled["experiment"]]
        load = scheduled["load"]
        if experiment["id"] in FILE_QUERY_EXPERIMENTS:
            expected_sequence.append(
                ("diagnostic", experiment["id"], load, 0, "readiness")
            )
        for pair in range(1, suite["pairing"]["minimum_inner_pairs"] + 1):
            order = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
            roles = (
                ("baseline", "treatment")
                if order == "AB"
                else ("treatment", "baseline")
            )
            expected_sequence.extend(
                ("sample", experiment["id"], load, pair, role)
                for role in roles
            )
    if actual_sequence != expected_sequence:
        raise EvaluationError(
            f"evaluation marker physical order differs from preregistration in {source_ref}"
        )
    _validate_complete_boot(rows, suite, source_ref, challenge)
    first_measurement = min(
        [row["source_line"] for row in rows] + [item["line"] for item in diagnostics.values()]
    )
    last_measurement = max(
        [row["source_line"] for row in rows] + [item["line"] for item in diagnostics.values()]
    )
    if (
        challenge_lines[0][0] >= first_measurement
        or worker_lines[0] <= last_measurement
        or pass_lines[0] <= worker_lines[0]
    ):
        raise EvaluationError(f"challenge/completion does not enclose samples in {source_ref}")
    expected_diagnostics = {
        (experiment["id"], load)
        for experiment in suite["experiments"]
        if experiment["id"] in FILE_QUERY_EXPERIMENTS
        for load in experiment["loads"]
    }
    if set(diagnostics) != expected_diagnostics:
        raise EvaluationError(f"missing readiness diagnostic in {source_ref}")
    for (experiment_id, load), diagnostic in diagnostics.items():
        first_sample = min(
            row["source_line"] for row in rows
            if row["experiment"] == experiment_id and row["load"] == load
        )
        if diagnostic["line"] >= first_sample:
            raise EvaluationError(f"readiness diagnostic follows samples in {source_ref}")
    for row in rows:
        del row["_position"]
    functional = validate_functional_log(
        lines, challenge, rows, suite
    )
    supplementary_lines = set(functional["supplementary"]["line_numbers"])
    headline_business_lines = {
        *(row["source_line"] for row in rows),
        *(item["line"] for item in diagnostics.values()),
        *(line for line in functional["line_numbers"]
          if line not in supplementary_lines),
    }
    if (
        challenge_lines[0][0] >= functional["launcher"]["line"]
        or any(
            line <= challenge_lines[0][0] or line >= worker_lines[0]
            for line in headline_business_lines
        )
        or any(
            line <= worker_lines[0] or line >= pass_lines[0]
            for line in supplementary_lines
        )
    ):
        raise EvaluationError(
            f"business marker order differs from Guest lifecycle in {source_ref}"
        )
    for (experiment_id, load), diagnostic in sorted(diagnostics.items()):
        rows.append({
            "kind": "agentos-evaluation-diagnostic-row",
        })
    return rows, functional

def validate_guest_log(
    path: Path,
    suite: dict[str, Any],
    challenge: str,
) -> dict[str, Any]:
    """用规范抽取核心校验一次指定 Guest 启动。"""
    if not isinstance(challenge, str) or not HEX16.fullmatch(challenge):
        raise EvaluationError("targeted Guest challenge is invalid")
    if int(challenge, 16) == 0:
        raise EvaluationError("targeted Guest challenge must be nonzero")
    rows, functional = extract_log(path, suite, challenge)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "agentos-evaluation-guest-validation",
        "challenge": challenge,
        "samples": sum(
            row["kind"] == "agentos-evaluation-metric-row" for row in rows
        ),
        "diagnostics": sum(
            row["kind"] == "agentos-evaluation-diagnostic-row" for row in rows
        ),
        "functional_receipts": 1 + len(functional["tasks"]),
        "catalog_descriptors": functional["catalog"]["total"],
        "revisit_isolation": {
            "id": functional["supplementary"]["id"],
            "qos_schema_version": functional["supplementary"]
            ["qos_schema_version"],
            "performance_gate": None,
            "correct": functional["supplementary"]["summary"]["correct"],
            "contamination": functional["supplementary"]["summary"]
            ["contamination"],
            "return_visit": functional["supplementary"]["summary"]
            ["return_visit"],
            "fallback": functional["supplementary"]["summary"]["fallback"],
            "result_fingerprint": functional["supplementary"]["summary"]
            ["result_fingerprint"],
            "concurrency": [
                {
                    key: item[key]
                    for key in CONCURRENCY_PUBLIC_FIELDS[
                        functional["supplementary"]["qos_schema_version"]
                    ]
                }
                for item in functional["supplementary"]["concurrency"]
            ],
        },
        "status": "supported",
    }

def _validate_complete_boot(
    rows: list[dict[str, Any]],
    suite: dict[str, Any],
    source_ref: str,
    challenge: str,
) -> None:
    minimum = suite["pairing"]["minimum_inner_pairs"]
    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["experiment"], row["load"], row["inner_pair"]), []).append(row)
    expected_combinations = {
        (experiment["id"], load)
        for experiment in suite["experiments"]
        for load in experiment["loads"]
    }
    actual_combinations = {(key[0], key[1]) for key in grouped}
    if actual_combinations != expected_combinations:
        raise EvaluationError(f"missing configured samples in {source_ref}")
    experiments = _experiment_map(suite)
    table_ambient_records: int | None = None
    for combination in expected_combinations:
        experiment = experiments[combination[0]]
        expected_operations = _operations_for(experiment, combination[1])
        pairs = sorted(key[2] for key in grouped if key[:2] == combination)
        if len(pairs) < minimum or pairs != list(range(1, len(pairs) + 1)):
            raise EvaluationError(f"missing inner pair in {source_ref}: {combination}")
        if combination[0] in FILE_QUERY_EXPERIMENTS:
            _file_target_sequence(combination[1], len(pairs), challenge)
        orders: list[str] = []
        for pair in pairs:
            samples = grouped[(combination[0], combination[1], pair)]
            if len(samples) != 2 or {row["role"] for row in samples} != {"baseline", "treatment"}:
                raise EvaluationError(f"missing baseline/treatment sample in {source_ref}")
            if len({row["order"] for row in samples}) != 1:
                raise EvaluationError(f"pair order mismatch in {source_ref}")
            order = samples[0]["order"]
            expected_order = "AB" if (pair & 1) == (int(challenge, 16) & 1) else "BA"
            if order != expected_order:
                raise EvaluationError(f"pair order differs from preregistration in {source_ref}")
            orders.append(order)
            expected_roles = ["baseline", "treatment"] if order == "AB" else ["treatment", "baseline"]
            physical = [row["role"] for row in sorted(samples, key=lambda row: row["source_line"])]
            if physical != expected_roles:
                raise EvaluationError(f"marker order does not match AB/BA in {source_ref}")
            if any(
                row["index_rebuild_records"] != 0
                or row["result_cache_hits"] != 0
                for row in samples
            ):
                raise EvaluationError(
                    f"timed sample includes index rebuild or result cache hit in {source_ref}"
                )
            for field in (
                "workload_fingerprint", "result_fingerprint", "operations",
                "dataset_size", "result_items", "index_rebuild_records",
                "result_cache_hits",
            ):
                if len({row[field] for row in samples}) != 1:
                    raise EvaluationError(f"paired {field} mismatch in {source_ref}")
            if (
                samples[0]["operations"] != expected_operations
                or samples[0]["result_items"] != expected_operations
                or any(row["work_units"] <= 0 for row in samples)
            ):
                raise EvaluationError(f"sample work does not match configured load in {source_ref}")
            if combination[0] in FILE_QUERY_EXPERIMENTS:
                by_role = {row["role"]: row for row in samples}
                if samples[0]["dataset_size"] != combination[1]:
                    raise EvaluationError(f"file query dataset size differs from load in {source_ref}")
                if any(
                    row["records_examined"] <= 0
                    or row["records_examined"] > row["work_units"]
                    for row in samples
                ):
                    raise EvaluationError(f"file query work receipt is inconsistent in {source_ref}")
                if combination[0] == FILE_QUERY_PATH_INDEX:
                    expected_path_work = combination[1] * expected_operations
                    if (
                        by_role["baseline"]["work_units"] != expected_path_work
                        or by_role["baseline"]["records_examined"]
                        != expected_path_work
                    ):
                        raise EvaluationError(
                            f"Task 4 baseline did not examine all N paths in {source_ref}"
                        )
                else:
                    baseline = by_role["baseline"]
                    if baseline["work_units"] != baseline["records_examined"]:
                        raise EvaluationError(
                            f"metadata ablation baseline did not scan every visible record in {source_ref}"
                        )
                    if baseline["work_units"] % expected_operations != 0:
                        raise EvaluationError(
                            f"metadata ablation ambient census is not integral in {source_ref}"
                        )
                    visible_records = (
                        baseline["work_units"] // expected_operations
                    )
                    ambient_records = visible_records - combination[1]
                    if ambient_records < 0:
                        raise EvaluationError(
                            f"metadata ablation ambient census is below fixture load in {source_ref}"
                        )
                    if table_ambient_records is None:
                        table_ambient_records = ambient_records
                    elif table_ambient_records != ambient_records:
                        raise EvaluationError(
                            f"metadata ablation ambient census is inconsistent in {source_ref}"
                        )
                treatment = by_role["treatment"]
                if (
                    treatment["work_units"] < expected_operations
                    or treatment["work_units"]
                    > FILE_META_CAPACITY * expected_operations
                    or treatment["records_examined"] < expected_operations
                    or treatment["records_examined"] > treatment["work_units"]
                ):
                    raise EvaluationError(
                        f"file query index has no bounded measured work in {source_ref}"
                    )
            elif (
                samples[0]["dataset_size"] != 0
                or any(row["records_examined"] != 0 for row in samples)
                or len({row["work_units"] for row in samples}) != 1
                or samples[0]["work_units"] != expected_operations
            ):
                raise EvaluationError(f"completed work differs from load in {source_ref}")
            if int(samples[0]["workload_fingerprint"], 16) == 0 or int(samples[0]["result_fingerprint"], 16) == 0:
                raise EvaluationError(f"zero fingerprint in {source_ref}")
        counts = [orders.count("AB"), orders.count("BA")]
        if min(counts) == 0 or abs(counts[0] - counts[1]) > 1:
            raise EvaluationError(f"same-order bias in {source_ref}: {combination}")

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparser = parser.add_subparsers(dest="command", required=True)
    targeted = subparser.add_parser("validate-guest")
    targeted.add_argument("--suite", type=Path, required=True)
    targeted.add_argument("--log", type=Path, required=True)
    targeted.add_argument("--challenge", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_guest_log(
            args.log, load_suite(args.suite), args.challenge
        )
    except EvaluationError as error:
        raise SystemExit(f"Guest evaluation failed: {error}") from error
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
