#!/usr/bin/env python3
"""Strict, provenance-bound evaluation contract for AgentOS Guest runs.

The statistical unit is one Guest boot (one raw log), never an inner AB/BA
pair printed by a single Guest.  Inner pairs are reduced to a per-boot median
before the exact paired tests are calculated across independent boots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import statistics
import tempfile
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
MARKER_PREFIX = "agenteval_ucore: sample "
DIAGNOSTIC_PREFIX = "agenteval_ucore: diagnostic "
LAUNCHER_PREFIX = "agenteval_ucore: launcher "
FUNCTIONAL_PREFIX = "agenteval_ucore: functional "
MARKER_FIELDS = (
    "schema", "experiment", "load", "pair", "variant", "order", "cache",
    "operations", "dataset_size", "work_units", "records_examined",
    "result_items", "duration_us", "workload_fingerprint",
    "result_fingerprint", "status",
)
DIAGNOSTIC_FIELDS = (
    "schema", "experiment", "load", "cache", "operations", "work_units",
    "duration_us", "index_rebuild_records", "status",
)
LAUNCHER_FIELDS = (
    "schema", "challenge", "values", "semantic", "receipt", "status",
)
FUNCTIONAL_FIELDS = (
    "schema", "task", "challenge", "values", "semantic", "receipt", "status",
)
FUNCTIONAL_TASKS = tuple(f"task{number}" for number in range(1, 6))
SAMPLE_ROW_FIELDS = {
    "schema_version", "kind", "status", "suite_id", "run_id",
    "environment_sha256", "experiment", "load", "inner_pair", "variant",
    "role", "target_id", "cache", "order", "operations", "work_units",
    "dataset_size", "records_examined", "result_items", "duration_us",
    "per_operation_us", "unit", "value", "workload_fingerprint", "result_fingerprint",
    "boot_id", "commit", "source_log", "source_line", "source_log_bytes",
    "source_log_sha256", "source_marker_sha256", "run_plan_sha256",
    "campaign_sha256", "kernel_sha256", "image_input_sha256",
    "image_final_sha256", "runner_log_sha256",
    "command_argv", "command_sha256",
    "suite_sha256",
}
DIAGNOSTIC_ROW_FIELDS = {
    "schema_version", "kind", "status", "suite_id", "run_id",
    "environment_sha256", "experiment", "load", "metric", "unit", "value",
    "cache", "operations", "work_units", "duration_us",
    "index_rebuild_records", "boot_id", "commit", "source_log", "source_line",
    "source_log_bytes", "source_log_sha256", "source_marker_sha256",
    "run_plan_sha256", "campaign_sha256", "suite_sha256", "kernel_sha256",
    "image_input_sha256", "image_final_sha256", "runner_log_sha256",
    "command_argv", "command_sha256",
}
HEX16 = re.compile(r"^[0-9a-f]{16}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
BOOTSTRAP_REPETITIONS = 2000


class EvaluationError(RuntimeError):
    """Raised when evaluation evidence fails closed."""


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
        return strict_json_loads(path.read_bytes())
    except OSError as error:
        raise EvaluationError(f"cannot read {path}: {error}") from error


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


def _safe_ref(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise EvaluationError(f"{where} is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise EvaluationError(f"{where} is unsafe")
    return path.as_posix()


def _is_link(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(is_junction and is_junction(path))


def _source_path(root: Path, reference: str) -> Path:
    if not root.is_dir() or _is_link(root):
        raise EvaluationError("source root is missing or link-backed")
    candidate = root.joinpath(*PurePosixPath(reference).parts)
    current = candidate
    while current != root:
        if current.exists() and _is_link(current):
            raise EvaluationError(f"source path is link-backed: {reference}")
        current = current.parent
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise EvaluationError(f"source path escapes its root: {reference}") from error
    return candidate


def _evidence_path(root: Path, path: Path, where: str) -> str:
    if not root.is_dir() or _is_link(root):
        raise EvaluationError(f"{where} root is missing or link-backed")
    resolved_root = root.resolve()
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError as error:
        raise EvaluationError(f"{where} is outside the run-plan directory") from error
    reference = _safe_ref(PurePosixPath(*relative.parts).as_posix(), where)
    current = candidate
    while current != resolved_root:
        if current.exists() and _is_link(current):
            raise EvaluationError(f"{where} is link-backed")
        current = current.parent
    return reference


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_suite(path: Path) -> dict[str, Any]:
    suite = _exact(
        _read_json(path),
        {
            "schema_version", "kind", "suite_id", "pairing", "experiments",
            "claim_family",
        },
        "suite",
    )
    if suite["schema_version"] != 1 or suite["kind"] != "agentos-evaluation-suite":
        raise EvaluationError("suite header is invalid")
    _text(suite["suite_id"], "suite_id")
    pairing = _exact(
        suite["pairing"],
        {
            "independent_unit", "minimum_boots", "minimum_inner_pairs",
            "orders", "maximum_order_imbalance",
        },
        "pairing",
    )
    if (
        pairing["independent_unit"] != "guest_boot"
        or _int(pairing["minimum_boots"], "minimum_boots", 7) < 7
        or _int(pairing["minimum_inner_pairs"], "minimum_inner_pairs", 7) < 7
        or pairing["orders"] != ["AB", "BA"]
        or _int(pairing["maximum_order_imbalance"], "order imbalance") > 1
    ):
        raise EvaluationError("pairing contract is invalid")
    if not isinstance(suite["experiments"], list) or not suite["experiments"]:
        raise EvaluationError("suite experiments are invalid")
    seen: set[str] = set()
    for item in suite["experiments"]:
        experiment = _exact(
            item,
            {
                "id", "label", "task", "loads", "unit", "direction",
                "operation_counts", "selector", "claim_gate", "baseline", "treatment",
            },
            "experiment",
        )
        experiment_id = _text(experiment["id"], "experiment id", TOKEN)
        if experiment_id in seen:
            raise EvaluationError("duplicate experiment id")
        seen.add(experiment_id)
        _label(experiment["label"], "experiment label")
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
        if experiment["unit"] not in {"us", "us/query"} or experiment["direction"] not in {"lower_is_better", "higher_is_better"}:
            raise EvaluationError(f"{experiment_id} metric is invalid")
        if type(experiment["selector"]) is not int or not 0 <= experiment["selector"] <= (1 << 64) - 1:
            raise EvaluationError(f"{experiment_id} selector is invalid")
        gate = _exact(
            experiment["claim_gate"],
            {
                "minimum_absolute_improvement_us",
                "minimum_baseline_duration_us",
                "minimum_relative_improvement_percent",
            },
            f"{experiment_id} claim gate",
        )
        _int(gate["minimum_absolute_improvement_us"], "minimum absolute improvement", 1)
        _int(gate["minimum_baseline_duration_us"], "minimum baseline duration", 1)
        relative_gate = gate["minimum_relative_improvement_percent"]
        if (
            isinstance(relative_gate, bool)
            or not isinstance(relative_gate, (int, float))
            or not math.isfinite(float(relative_gate))
            or relative_gate <= 0
            or relative_gate > 100
        ):
            raise EvaluationError(f"{experiment_id} relative improvement gate is invalid")
        variants = []
        for role in ("baseline", "treatment"):
            variant = _exact(experiment[role], {"id", "label", "cache"}, role)
            variants.append(_text(variant["id"], f"{role} variant", TOKEN))
            _label(variant["label"], f"{role} label")
            _text(variant["cache"], f"{role} cache", TOKEN)
        if variants[0] == variants[1]:
            raise EvaluationError(f"{experiment_id} variants are identical")
    claim_family = _exact(
        suite["claim_family"],
        {"id", "method", "familywise_alpha", "hypotheses", "load_gate"},
        "claim family",
    )
    _text(claim_family["id"], "claim family id")
    if (
        claim_family["method"] != "bonferroni"
        or type(claim_family["familywise_alpha"]) not in {int, float}
        or claim_family["familywise_alpha"] != 0.05
        or claim_family["load_gate"] != "intersection"
        or claim_family["hypotheses"] != [
            experiment["id"] for experiment in suite["experiments"]
        ]
        or len(claim_family["hypotheses"]) != 3
    ):
        raise EvaluationError("headline claim family is invalid")
    return suite


def load_run_plan(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    plan = _exact(
        strict_json_loads(raw),
        {
            "schema_version", "kind", "run_id", "environment_sha256",
            "campaign_sha256", "suite_sha256", "logs",
        },
        "run plan",
    )
    if plan["schema_version"] != 1 or plan["kind"] != "agentos-evaluation-run-plan":
        raise EvaluationError("run plan header is invalid")
    _text(plan["run_id"], "run id")
    _text(plan["environment_sha256"], "environment sha256", SHA256)
    _text(plan["campaign_sha256"], "campaign sha256", SHA256)
    _text(plan["suite_sha256"], "suite sha256", SHA256)
    if not isinstance(plan["logs"], list) or not plan["logs"]:
        raise EvaluationError("run plan logs are invalid")
    paths: set[str] = set()
    boots: set[str] = set()
    hashes: set[str] = set()
    challenges: set[str] = set()
    commits: set[str] = set()
    supported_inputs: set[str] = set()
    supported_kernels: set[str] = set()
    normalized_commands: set[tuple[str, ...]] = set()
    for value in plan["logs"]:
        item = _exact(
            value,
            {
                "path", "sha256", "boot_id", "commit", "challenge", "status", "detail",
                "kernel_sha256", "image_input_sha256", "image_final_sha256",
                "runner_log_sha256",
                "command_argv", "command_sha256",
            },
            "run plan log",
        )
        ref = _safe_ref(item["path"], "run plan log path")
        boot = _text(item["boot_id"], "boot id")
        digest = _text(item["sha256"], "log sha256", SHA256)
        for field in (
            "kernel_sha256", "image_input_sha256", "image_final_sha256",
            "runner_log_sha256", "command_sha256",
        ):
            _text(item[field], field.replace("_", " "), SHA256)
        command = item["command_argv"]
        if (
            not isinstance(command, list) or not command
            or any(not isinstance(value, str) or not value or len(value) > 4096 for value in command)
        ):
            raise EvaluationError("command argv is invalid")
        command_raw = json.dumps(
            command, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if item["command_sha256"] != sha256_bytes(command_raw):
            raise EvaluationError("command sha256 differs from command argv")
        commits.add(_text(item["commit"], "commit", COMMIT))
        challenge = _text(item["challenge"], "challenge", HEX16)
        if int(challenge, 16) == 0:
            raise EvaluationError("challenge must be nonzero")
        if item["status"] not in {"supported", "unavailable", "failed"}:
            raise EvaluationError("run plan status is invalid")
        if item["status"] == "supported":
            if item["detail"] is not None:
                raise EvaluationError("supported log detail must be null")
            supported_inputs.add(item["image_input_sha256"])
            supported_kernels.add(item["kernel_sha256"])
        elif not isinstance(item["detail"], str) or not item["detail"] or len(item["detail"]) > 512:
            raise EvaluationError("unavailable/failed log needs a detail")
        challenge_argument = f"AGENT_EVAL_CHALLENGE_HEX={challenge}"
        guest_log_suffix = f"/raw/{ref}"
        normalized: list[str] = []
        challenge_count = 0
        guest_log_count = 0
        for argument in command:
            if argument.startswith("AGENT_EVAL_CHALLENGE_HEX="):
                challenge_count += 1
                if argument != challenge_argument:
                    raise EvaluationError("command challenge differs from run plan")
                normalized.append("AGENT_EVAL_CHALLENGE_HEX=<challenge>")
            elif argument.startswith("AGENT_TEST_GUEST_LOG_FILE="):
                guest_log_count += 1
                guest_path = argument.removeprefix("AGENT_TEST_GUEST_LOG_FILE=").replace("\\", "/")
                if not guest_path.endswith(guest_log_suffix):
                    raise EvaluationError("command Guest log path differs from run plan")
                normalized.append("AGENT_TEST_GUEST_LOG_FILE=<guest-log>")
            else:
                normalized.append(argument)
        if challenge_count != 1 or guest_log_count != 1:
            raise EvaluationError("command must bind exactly one challenge and Guest log")
        normalized_commands.add(tuple(normalized))
        if ref in paths or boot in boots or digest in hashes or challenge in challenges:
            raise EvaluationError("logs, boot ids, challenges, and log hashes must be unique")
        paths.add(ref)
        boots.add(boot)
        hashes.add(digest)
        challenges.add(challenge)
    if len(commits) != 1:
        raise EvaluationError("all Guest boots must use one source commit")
    if len(normalized_commands) != 1:
        raise EvaluationError("Guest boot commands differ beyond planned challenge/log paths")
    if len(supported_kernels) > 1:
        raise EvaluationError("supported Guest boots do not share one kernel image")
    supported_count = sum(item["status"] == "supported" for item in plan["logs"])
    if len(supported_inputs) != supported_count:
        raise EvaluationError("each supported boot needs a challenge-specialized pristine image")
    parity = [sum(int(item["challenge"], 16) % 2 == value for item in plan["logs"]) for value in (0, 1)]
    if len(plan["logs"]) >= 7 and (min(parity) == 0 or abs(parity[0] - parity[1]) > 1):
        raise EvaluationError("challenge parity does not provide a balanced cross-boot order schedule")
    return plan, sha256_bytes(raw)


def _binding_sha256(value: object, domain: str) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256_bytes(domain.encode("ascii") + b"\0" + raw)


def load_scenario_report(path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError("scenario report is missing or unsafe")
    raw = path.read_bytes()
    report = strict_json_loads(raw)
    if not isinstance(report, dict):
        raise EvaluationError("scenario report is not an object")
    status = report.get("status")
    expected = {
        "schema_version", "scenario_id", "source_commit", "run_id", "status",
        "samples", "summary", "report_sha256",
    }
    if status == "failed":
        expected.add("errors")
    _exact(report, expected, "scenario report")
    if (
        report["schema_version"] != 1
        or report["scenario_id"] != "research-platform-seeded"
        or status not in {"supported", "inconclusive", "failed"}
        or report["run_id"] != plan["run_id"]
    ):
        raise EvaluationError("scenario report header differs from the run plan")
    commits = {item["commit"] for item in plan["logs"]}
    if len(commits) != 1 or report["source_commit"] not in commits:
        raise EvaluationError("scenario report commit differs from the run plan")
    supplied = report["report_sha256"]
    _text(supplied, "scenario report sha256", SHA256)
    unsigned = dict(report)
    del unsigned["report_sha256"]
    if supplied != _binding_sha256(unsigned, "scenario-report-v1"):
        raise EvaluationError("scenario report binding hash differs")
    if not isinstance(report["samples"], list) or not isinstance(report["summary"], dict):
        raise EvaluationError("scenario report samples/summary are invalid")
    samples = report["samples"]
    functional_status = "fail"
    performance_status = "failed"
    performance: dict[str, Any] | None = None
    if status == "failed":
        if samples or not isinstance(report.get("errors"), list) or not report["errors"]:
            raise EvaluationError("failed scenario must contain errors and no samples")
    else:
        boot_ids: set[str] = set()
        boot_orders: set[int] = set()
        challenges: set[str] = set()
        outcome_fingerprints: set[str] = set()
        expected_program_order: list[str] | None = None
        for index, raw_sample in enumerate(samples):
            sample = _exact(
                raw_sample,
                {"sample_id", "binding", "outcome", "outcome_fingerprint", "targets"},
                f"scenario sample {index}",
            )
            binding = _exact(
                sample["binding"],
                {
                    "source_commit", "run_id", "boot_id", "boot_order",
                    "target_order", "challenge", "program_order",
                    "outcome_fingerprint", "source_receipts", "sha256",
                },
                f"scenario sample {index} binding",
            )
            boot_id = _text(binding["boot_id"], "scenario boot id")
            boot_order = _int(binding["boot_order"], "scenario boot order", 1)
            if boot_order != index + 1:
                raise EvaluationError("scenario samples are not in sealed boot order")
            challenge = binding["challenge"]
            if not isinstance(challenge, str) or not re.fullmatch(r"ch-[0-9]{12}", challenge):
                raise EvaluationError("scenario challenge is invalid")
            outcome_fingerprint = _text(
                sample["outcome_fingerprint"], "scenario outcome fingerprint", SHA256
            )
            if (
                boot_id in boot_ids
                or boot_order in boot_orders
                or challenge in challenges
                or outcome_fingerprint in outcome_fingerprints
            ):
                raise EvaluationError(
                    "scenario boots/challenges/outcomes are not independent"
                )
            boot_ids.add(boot_id)
            boot_orders.add(boot_order)
            challenges.add(challenge)
            outcome_fingerprints.add(outcome_fingerprint)
            program_order = binding["program_order"]
            if (
                not isinstance(program_order, list)
                or not program_order
                or any(
                    not isinstance(program, str) or not TOKEN.fullmatch(program)
                    for program in program_order
                )
                or len(set(program_order)) != len(program_order)
            ):
                raise EvaluationError("scenario program order is invalid")
            if expected_program_order is None:
                expected_program_order = program_order
            elif program_order != expected_program_order:
                raise EvaluationError("scenario program order differs across boots")
            if (
                binding["source_commit"] != report["source_commit"]
                or binding["run_id"] != report["run_id"]
                or sample["sample_id"] != f"{report['run_id']}:{boot_id}"
                or binding["target_order"] not in {"AB", "BA"}
                or outcome_fingerprint != binding["outcome_fingerprint"]
            ):
                raise EvaluationError("scenario sample binding differs from report")
            outcome = sample["outcome"]
            if (
                not isinstance(outcome, dict)
                or not outcome
                or outcome_fingerprint
                != _binding_sha256(outcome, "research-platform-outcome-v2")
            ):
                raise EvaluationError(
                    "scenario normalized output differs from its fingerprint"
                )
            receipts = binding["source_receipts"]
            if not isinstance(receipts, dict) or set(receipts) != {"plain", "agentos"}:
                raise EvaluationError("scenario source receipts are invalid")
            for digest in receipts.values():
                _text(digest, "scenario source receipt", SHA256)
            targets = _exact(
                sample["targets"], {"plain", "agentos"},
                f"scenario sample {index} targets",
            )
            for target_name in ("plain", "agentos"):
                target = _exact(
                    targets[target_name],
                    {"makespan_ms", "programs", "raw_source_receipt"},
                    f"scenario sample {index} target {target_name}",
                )
                makespan = _int(
                    target["makespan_ms"],
                    f"scenario sample {index} {target_name} makespan",
                    1,
                )
                programs = target["programs"]
                if not isinstance(programs, list) or len(programs) != len(program_order):
                    raise EvaluationError("scenario target program evidence is incomplete")
                elapsed_total = 0
                for position, raw_program in enumerate(programs):
                    program = _exact(
                        raw_program,
                        {"program", "elapsed_ms"},
                        f"scenario sample {index} {target_name} program",
                    )
                    if program["program"] != program_order[position]:
                        raise EvaluationError("scenario target program order differs")
                    elapsed_total += _int(
                        program["elapsed_ms"],
                        f"scenario sample {index} {target_name} elapsed time",
                    )
                if makespan < elapsed_total:
                    raise EvaluationError("scenario makespan is below its program work")
                raw_receipt = target["raw_source_receipt"]
                if not isinstance(raw_receipt, dict):
                    raise EvaluationError("scenario raw source receipt is invalid")
                receipt_sha = _text(
                    raw_receipt.get("sha256"),
                    "scenario raw source receipt sha256",
                    SHA256,
                )
                if receipt_sha != receipts[target_name]:
                    raise EvaluationError(
                        "scenario target evidence differs from its bound source receipt"
                    )
            binding_hash = binding["sha256"]
            _text(binding_hash, "scenario sample binding sha256", SHA256)
            unsigned_binding = dict(binding)
            del unsigned_binding["sha256"]
            if binding_hash != _binding_sha256(unsigned_binding, "scenario-sample-v1"):
                raise EvaluationError("scenario sample binding hash differs")
        try:
            from evaluation_scenario import (
                MIN_SUPPORTED_BOOTS,
                REQUIRED_AGENTOS_MODULES,
                _summarize as summarize_scenario,
                _supports_claim as scenario_supports_claim,
            )

            rebuilt_summary = summarize_scenario(samples)
        except (ImportError, KeyError, TypeError, ValueError) as error:
            raise EvaluationError(f"scenario samples cannot be summarized: {error}") from error
        if rebuilt_summary != report["summary"]:
            raise EvaluationError("scenario summary differs from bound samples")
        performance_status = (
            "supported" if scenario_supports_claim(rebuilt_summary) else "inconclusive"
        )
        if status != performance_status:
            raise EvaluationError("scenario status differs from paired performance gate")
        independent = rebuilt_summary.get("independent_boots")
        order_counts = rebuilt_summary.get("target_order_counts")
        targets = rebuilt_summary.get("targets")
        functional_acceptance = rebuilt_summary.get("functional_acceptance")
        acceptance_receipts = (
            functional_acceptance.get("boot_receipts")
            if isinstance(functional_acceptance, dict)
            else None
        )
        expected_acceptance_bindings = [
            (
                sample["sample_id"],
                sample["binding"]["challenge"],
                sample["binding"]["source_receipts"]["agentos"],
            )
            for sample in samples
        ]
        functionally_complete = (
            type(independent) is int
            and independent >= MIN_SUPPORTED_BOOTS
            and independent == len(samples)
            and boot_orders == set(range(1, independent + 1))
            and rebuilt_summary.get("minimum_supported_boots") == MIN_SUPPORTED_BOOTS
            and rebuilt_summary.get("unique_challenges") == independent
            and rebuilt_summary.get("paired_success_rate") == 1.0
            and rebuilt_summary.get("target_order_balanced") is True
            and isinstance(order_counts, dict)
            and set(order_counts) == {"AB", "BA"}
            and sum(order_counts.values()) == independent
            and abs(order_counts["AB"] - order_counts["BA"]) <= 1
            and isinstance(targets, dict)
            and set(targets) == {"plain", "agentos"}
            and all(
                targets[target].get("successful_boots") == independent
                and targets[target].get("success_rate") == 1.0
                for target in ("plain", "agentos")
            )
            and isinstance(functional_acceptance, dict)
            and functional_acceptance.get("status") == "passed"
            and functional_acceptance.get("required_target") == "agentos"
            and functional_acceptance.get("required_modules")
            == list(REQUIRED_AGENTOS_MODULES)
            and functional_acceptance.get("verified_boots") == independent
            and isinstance(acceptance_receipts, list)
            and len(acceptance_receipts) == independent
            and all(
                isinstance(receipt, dict)
                and set(receipt) == {
                    "sample_id", "challenge", "module_receipt_sha256",
                    "binding_sha256", "raw_source_receipt_sha256",
                }
                and (
                    receipt["sample_id"], receipt["challenge"],
                    receipt["raw_source_receipt_sha256"],
                ) == expected
                and all(
                    isinstance(receipt[field], str)
                    and SHA256.fullmatch(receipt[field])
                    for field in (
                        "module_receipt_sha256", "binding_sha256",
                        "raw_source_receipt_sha256",
                    )
                )
                for receipt, expected in zip(
                    acceptance_receipts, expected_acceptance_bindings
                )
            )
        )
        if not functionally_complete:
            raise EvaluationError("scenario functional acceptance is incomplete")
        functional_status = "pass"
        performance = rebuilt_summary["paired_improvement"]
    independent = report["summary"].get("independent_boots")
    if type(independent) is not int or independent < 0:
        raise EvaluationError("scenario independent boot count is invalid")
    return {
        "report": report,
        "path": path.name,
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "functional_status": functional_status,
        "performance_status": performance_status,
        "performance": performance,
    }


def bind_scenario_plan(
    path: Path,
    scenario_record: dict[str, Any],
    micro_plan: dict[str, Any],
) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError("scenario plan is missing or unsafe")
    raw = path.read_bytes()
    value = strict_json_loads(raw)
    try:
        from evaluation_campaign import CampaignError, validate_scenario_campaign
    except ImportError as error:
        raise EvaluationError(f"scenario plan validator is unavailable: {error}") from error
    try:
        validate_scenario_campaign(value)
    except (CampaignError, KeyError, TypeError, ValueError) as error:
        raise EvaluationError(f"scenario plan is invalid: {error}") from error
    report = scenario_record["report"]
    if (
        value["phase"] != "collected"
        or value["report"]["status"] != "recorded"
        or value["report"]["sha256"] != scenario_record["sha256"]
        or value["run"]["id"] != micro_plan["run_id"]
        or value["run"]["commit"] != report["source_commit"]
        or value["run"]["environment_sha256"] != micro_plan["environment_sha256"]
    ):
        raise EvaluationError("scenario plan/report differs from the micro run binding")
    planned = {item["boot_id"]: item for item in value["boots"]}
    if len(planned) != len(report["samples"]):
        raise EvaluationError("scenario report boot count differs from scenario plan")
    for sample in report["samples"]:
        binding = sample["binding"]
        boot = planned.get(binding["boot_id"])
        expected_order = "AB" if boot and boot["target_order"] == "plain-agentos" else "BA"
        if (
            boot is None
            or boot["status"] != "passed"
            or boot["challenge"] != binding["challenge"]
            or binding["target_order"] != expected_order
        ):
            raise EvaluationError("scenario sample differs from its sealed boot plan")
    scenario_record["plan_path"] = path.name
    scenario_record["plan_sha256"] = sha256_bytes(raw)
    return scenario_record


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
    if fields["schema"] != "1" or fields["status"] != "measured":
        raise EvaluationError(f"unsupported marker schema/status at line {line_number}")
    for key in ("experiment", "variant", "cache"):
        _text(fields[key], f"marker {key}", TOKEN)
    for key in (
        "load", "pair", "operations", "dataset_size", "work_units",
        "records_examined", "result_items", "duration_us",
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
    if fields["schema"] != "1" or fields["experiment"] != "file_query" or fields["status"] != "measured":
        raise EvaluationError(f"diagnostic header is invalid at line {line_number}")
    if fields["cache"] not in {"cold-rebuild", "ready"}:
        raise EvaluationError(f"diagnostic cache is invalid at line {line_number}")
    for key in ("load", "operations", "work_units", "duration_us", "index_rebuild_records"):
        try:
            number = int(fields[key], 10)
        except ValueError as error:
            raise EvaluationError(f"diagnostic {key} is invalid at line {line_number}") from error
        if number < 0 or number > (1 << 53) - 1 or str(number) != fields[key]:
            raise EvaluationError(f"diagnostic {key} is invalid at line {line_number}")
        fields[key] = number
    if fields["load"] <= 0 or fields["operations"] != 1 or fields["work_units"] <= 0:
        raise EvaluationError(f"diagnostic work contract is invalid at line {line_number}")
    rebuild = fields["index_rebuild_records"]
    if (fields["cache"] == "cold-rebuild" and rebuild == 0) or (fields["cache"] == "ready" and rebuild != 0):
        raise EvaluationError(f"diagnostic readiness conflicts with rebuild at line {line_number}")
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
    """Build canonical synthetic receipts for Host regression fixtures."""
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


def _file_target_meta(load: int, pair: int, challenge: str) -> int:
    challenge_value = int(challenge, 16)
    mixed = challenge_value ^ (challenge_value >> 32)
    mixed ^= (pair * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
    return mixed % load


def _expected_workload(experiment: dict[str, Any], load: int, pair: int, challenge: str) -> str:
    value = 1469598103934665603
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_bytes(value, experiment["id"].encode("ascii"))
    operations = _operations_for(experiment, load)
    selector = (
        _file_target_meta(load, pair, challenge)
        if experiment["id"] == "file_query"
        else experiment["selector"]
    )
    for item in (load, pair, operations, selector):
        value = _fnv_u64(value, item)
    return f"{value:016x}"


def _expected_result(experiment: dict[str, Any], load: int, pair: int, challenge: str) -> str:
    experiment_id = experiment["id"]
    operations = _operations_for(experiment, load)
    value = _fnv_bytes(1469598103934665603, b"agentos-result-v1")
    value = _fnv_u64(value, int(challenge, 16))
    value = _fnv_bytes(value, experiment_id.encode("ascii"))
    value = _fnv_u64(value, load)
    value = _fnv_u64(value, pair)
    if experiment_id == "file_query":
        target = _file_target_meta(load, pair, challenge)
        code = f"{target:03d}"
        dependency = _fnv_bytes(1469598103934665603, b"ready")
        dependency = 1 << (dependency % 60)
        for _ in range(operations):
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


def _task3_semantic(challenge: str) -> str:
    rounds = 6
    value = _fnv_bytes(1469598103934665603, b"task3-context-v1")
    value = _fnv_u64(value, int(challenge, 16))
    challenge_value = int(challenge, 16)
    for item in range(rounds):
        for field in (
            _semantic_token("functional-context-v1", rounds, 0, item, challenge),
            challenge_value ^ item,
            item,
            19,
            0,
        ):
            value = _fnv_u64(value, field)
        value = _fnv_bytes(value, b"ctx-path")
        value = _fnv_bytes(value, b"ctx-ok")
    return f"{value:016x}"


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
        "marker_sha256": sha256_bytes(line.encode("utf-8")),
    }


def _validate_functional_task1(
    launcher: dict[str, Any],
    receipt: dict[str, Any],
    challenge: str,
) -> None:
    launch = launcher["values"]
    values = receipt["values"]
    if len(launch) != 5 or len(values) != 15:
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
        user_cache_offset, user_cache_size, direct_token,
    ) = values
    if (
        agent_pid <= 0
        or agent_pid == parent_pid
        or parent_pid != launch[0]
        or is_agent != 1
        or role != 4
        or agent_id <= 0
        or context_base != 0x3FFFFE8000
        or context_size != 6 * 4096
        or magic != 0x4147435458543031
        or version != 8
        or capacity != 128
        or resource_quota != capacity
        or loop_state not in {1, 2, 3}
        or user_cache_offset <= 0
        or user_cache_size < 8
        or user_cache_offset + user_cache_size > context_size
        or _as_u64(direct_token)
        != (int(challenge, 16) ^ agent_pid ^ context_base)
        or receipt["semantic"]
        != _functional_semantic("task1-semantic-v1", challenge, values)
    ):
        raise EvaluationError("Task1 Agent PCB/context receipt is inconsistent")


def _validate_functional_task2(
    receipt: dict[str, Any],
    challenge: str,
    task1: dict[str, Any],
) -> None:
    values = receipt["values"]
    if len(values) != 20:
        raise EvaluationError("Task2 functional receipt value count differs")
    (
        tool_count, callable_count, schema_hash, echo_sequence, echo_length,
        echo_arg0, echo_arg1, echo_payload_hash, query_sequence, query_used,
        query_agents, query_runnable, cap_sequence, cap_allowed, cap_role,
        cap_mask, unknown_sequence, unknown_status, bad_type_sequence,
        bad_type_status,
    ) = values
    agent_pid = task1["values"][0]
    expected_payload_hash = _fnv_bytes(1469598103934665603, b"eval-v2")
    if (
        tool_count != 25
        or callable_count != 23
        or _as_u64(schema_hash) != _task2_schema_fingerprint()
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
        or bad_type_sequence != 0
        or bad_type_status != -15
        or receipt["semantic"]
        != _functional_semantic("task2-semantic-v1", challenge, values)
    ):
        raise EvaluationError("Task2 structured-tool receipt is inconsistent")


def _validate_functional_task3(
    receipt: dict[str, Any],
    challenge: str,
) -> None:
    values = receipt["values"]
    if len(values) != 15:
        raise EvaluationError("Task3 functional receipt value count differs")
    (
        rounds, query_count, direct_count, first_sequence, rollback_sequence,
        active_after_rollback, old_branch, new_branch, clear_count, capacity,
        fifo_count, fifo_dropped, fifo_oldest, fifo_latest, eviction_policy,
    ) = values
    if (
        rounds != 6
        or query_count != rounds
        or direct_count != rounds
        or first_sequence != 1
        or rollback_sequence != first_sequence + 2
        or active_after_rollback != 3
        or old_branch <= 0
        or new_branch <= 0
        or new_branch == old_branch
        or clear_count != 0
        or capacity != 128
        or fifo_count != capacity
        or fifo_dropped != 5
        or fifo_oldest != 6
        or fifo_latest != capacity + fifo_dropped
        or fifo_latest - fifo_oldest + 1 != fifo_count
        or eviction_policy != 1
        or receipt["semantic"] != _task3_semantic(challenge)
    ):
        raise EvaluationError("Task3 Context Path receipt is inconsistent")


def _validate_functional_task4(
    receipt: dict[str, Any],
    challenge: str,
    sample_rows: list[dict[str, Any]],
) -> None:
    values = receipt["values"]
    if len(values) != 13:
        raise EvaluationError("Task4 functional receipt value count differs")
    (
        load, pair, target_meta, target_fid, scan_work, index_work,
        result_items, result_fingerprint, inum, incarnation, fs_generation,
        used_index, plan,
    ) = values
    pair_rows = [
        row for row in sample_rows
        if row.get("experiment") == "file_query"
        and row.get("load") == load
        and (row.get("inner_pair", row.get("pair"))) == pair
    ]
    by_role = {row.get("role"): row for row in pair_rows}
    expected_target = _file_target_meta(load, pair, challenge) if load > 0 else -1
    if (
        load != 96
        or pair != 7
        or set(by_role) != {"baseline", "treatment"}
        or target_meta != expected_target
        or target_fid != 2000 + target_meta
        or scan_work != by_role["baseline"]["work_units"]
        or index_work != by_role["treatment"]["work_units"]
        or result_items != by_role["treatment"]["result_items"]
        or _as_u64(result_fingerprint)
        != int(by_role["treatment"]["result_fingerprint"], 16)
        or by_role["baseline"]["result_fingerprint"]
        != by_role["treatment"]["result_fingerprint"]
        or inum <= 0
        or incarnation <= 0
        or fs_generation <= 0
        or used_index != 1
        or plan != 1
        or receipt["semantic"]
        != _functional_semantic("task4-semantic-v1", challenge, values)
    ):
        raise EvaluationError("Task4 file-query receipt is inconsistent with measured markers")


def _validate_functional_task5(
    receipt: dict[str, Any],
    challenge: str,
    task1: dict[str, Any],
) -> None:
    values = receipt["values"]
    if len(values) != 18:
        raise EvaluationError("Task5 functional receipt value count differs")
    (
        primary_pid, helper_pid, event_source, event_target, corr_id, event_id,
        event_tick, sleep_before, sleep_after, wake_before, wake_after,
        heartbeat_first_tick, heartbeat_second_tick, heartbeat_sleep_delta,
        heartbeat_wake_delta, stopped_timeout_status, helper_exit_status,
        stable_agents,
    ) = values
    expected_corr = _semantic_token("task5-event-v1", 2, 0, 0, challenge)
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
        or receipt["semantic"]
        != _functional_semantic("task5-semantic-v1", challenge, values)
    ):
        raise EvaluationError("Task5 sleep/wake lifecycle receipt is inconsistent")


def validate_functional_log(
    lines: list[str],
    challenge: str,
    sample_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate Task1-5 receipts and return their raw marker bindings."""
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
    measurement_lines = [
        row.get("source_line", row.get("line")) for row in sample_rows
    ]
    if (
        not measurement_lines
        or launcher["line"] >= min(measurement_lines)
        or receipts["task1"]["line"] <= max(measurement_lines)
        or launcher["line"] >= receipts["task1"]["line"]
    ):
        raise EvaluationError("functional probes are not outside the measured interval")
    _validate_functional_task1(launcher, receipts["task1"], challenge)
    _validate_functional_task2(receipts["task2"], challenge, receipts["task1"])
    _validate_functional_task3(receipts["task3"], challenge)
    _validate_functional_task4(receipts["task4"], challenge, sample_rows)
    _validate_functional_task5(receipts["task5"], challenge, receipts["task1"])
    ordered = [launcher, *(receipts[task] for task in FUNCTIONAL_TASKS)]
    return {
        "launcher": launcher,
        "tasks": receipts,
        "line_numbers": [item["line"] for item in ordered],
        "marker_sha256s": [item["marker_sha256"] for item in ordered],
    }


def extract_log(
    path: Path,
    source_ref: str,
    log_plan: dict[str, Any],
    suite: dict[str, Any],
    run_id: str,
    environment_sha256: str,
    run_plan_sha256: str,
    campaign_sha256: str,
    suite_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise EvaluationError(f"source log is missing or unsafe: {source_ref}")
    raw = path.read_bytes()
    if sha256_bytes(raw) != log_plan["sha256"]:
        raise EvaluationError(f"source log differs from run plan: {source_ref}")
    if log_plan["status"] != "supported":
        return [], {}
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
    expected_challenge_line = f"agenteval_ucore: challenge={log_plan['challenge']}"
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
    diagnostics: dict[int, dict[str, Any]] = {}
    for line_number, line in enumerate(lines, 1):
        if not line.startswith(DIAGNOSTIC_PREFIX):
            continue
        diagnostic = _parse_diagnostic(line, line_number)
        if diagnostic["load"] in diagnostics:
            raise EvaluationError(f"duplicate readiness diagnostic in {source_ref}")
        diagnostics[diagnostic["load"]] = diagnostic
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
            experiment, marker["load"], marker["pair"], log_plan["challenge"]
        ):
            raise EvaluationError(f"workload fingerprint is not challenge-bound at line {line_number}")
        if marker["result_fingerprint"] != _expected_result(
            experiment, marker["load"], marker["pair"], log_plan["challenge"]
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
            "schema_version": 1,
            "kind": "agentos-evaluation-metric-row",
            "status": "supported",
            "suite_id": suite["suite_id"],
            "run_id": run_id,
            "environment_sha256": environment_sha256,
            "experiment": marker["experiment"],
            "load": marker["load"],
            "inner_pair": marker["pair"],
            "variant": marker["variant"],
            "role": role,
            "target_id": f"{marker['experiment']}:{marker['variant']}",
            "cache": marker["cache"],
            "order": marker["order"],
            "operations": marker["operations"],
            "dataset_size": marker["dataset_size"],
            "work_units": marker["work_units"],
            "records_examined": marker["records_examined"],
            "result_items": marker["result_items"],
            "duration_us": marker["duration_us"],
            "per_operation_us": marker["duration_us"] / marker["operations"],
            "unit": experiment["unit"],
            "value": (
                marker["duration_us"] / marker["operations"]
                if experiment["unit"] == "us/query"
                else marker["duration_us"]
            ),
            "workload_fingerprint": marker["workload_fingerprint"],
            "result_fingerprint": marker["result_fingerprint"],
            "boot_id": log_plan["boot_id"],
            "commit": log_plan["commit"],
            "source_log": source_ref,
            "source_line": line_number,
            "source_log_bytes": len(raw),
            "source_log_sha256": log_plan["sha256"],
            "source_marker_sha256": sha256_bytes(line.encode("utf-8")),
            "run_plan_sha256": run_plan_sha256,
            "campaign_sha256": campaign_sha256,
            "kernel_sha256": log_plan["kernel_sha256"],
            "image_input_sha256": log_plan["image_input_sha256"],
            "image_final_sha256": log_plan["image_final_sha256"],
            "runner_log_sha256": log_plan["runner_log_sha256"],
            "command_argv": log_plan["command_argv"],
            "command_sha256": log_plan["command_sha256"],
            "suite_sha256": suite_sha256,
        })
        # Position is checked again with physical marker ordering below.
        rows[-1]["_position"] = position
    actual_physical_order: list[tuple[Any, ...]] = []
    for row in rows:
        actual_physical_order.append((
            row["source_line"], "sample", row["experiment"], row["load"],
            row["inner_pair"], row["role"],
        ))
    for load, diagnostic in diagnostics.items():
        actual_physical_order.append((
            diagnostic["line"], "diagnostic", "file_query", load, 0, "readiness",
        ))
    actual_sequence = [item[1:] for item in sorted(actual_physical_order)]
    expected_sequence: list[tuple[Any, ...]] = []
    for experiment in suite["experiments"]:
        for load in experiment["loads"]:
            if experiment["id"] == "file_query":
                expected_sequence.append(
                    ("diagnostic", "file_query", load, 0, "readiness")
                )
            for pair in range(1, suite["pairing"]["minimum_inner_pairs"] + 1):
                order = "AB" if (pair & 1) == (int(log_plan["challenge"], 16) & 1) else "BA"
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
    _validate_complete_boot(rows, suite, source_ref, log_plan["challenge"])
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
    file_query = experiments.get("file_query")
    expected_diagnostic_loads = set(file_query["loads"]) if file_query else set()
    if set(diagnostics) != expected_diagnostic_loads:
        raise EvaluationError(f"missing readiness diagnostic in {source_ref}")
    for load, diagnostic in diagnostics.items():
        first_sample = min(
            row["source_line"] for row in rows
            if row["experiment"] == "file_query" and row["load"] == load
        )
        if diagnostic["line"] >= first_sample:
            raise EvaluationError(f"readiness diagnostic follows samples in {source_ref}")
    for row in rows:
        del row["_position"]
    functional = validate_functional_log(lines, log_plan["challenge"], rows)
    if (
        challenge_lines[0][0] >= functional["launcher"]["line"]
        or functional["line_numbers"][-1] >= worker_lines[0]
    ):
        raise EvaluationError(
            f"functional receipt order differs from Guest lifecycle in {source_ref}"
        )
    for load, diagnostic in sorted(diagnostics.items()):
        line = lines[diagnostic["line"] - 1]
        rows.append({
            "schema_version": 1,
            "kind": "agentos-evaluation-diagnostic-row",
            "status": "supported",
            "suite_id": suite["suite_id"],
            "run_id": run_id,
            "environment_sha256": environment_sha256,
            "experiment": "file_query",
            "load": load,
            "metric": "index_readiness_duration",
            "unit": "us",
            "value": diagnostic["duration_us"],
            "cache": diagnostic["cache"],
            "operations": diagnostic["operations"],
            "work_units": diagnostic["work_units"],
            "duration_us": diagnostic["duration_us"],
            "index_rebuild_records": diagnostic["index_rebuild_records"],
            "boot_id": log_plan["boot_id"],
            "commit": log_plan["commit"],
            "source_log": source_ref,
            "source_line": diagnostic["line"],
            "source_log_bytes": len(raw),
            "source_log_sha256": log_plan["sha256"],
            "source_marker_sha256": sha256_bytes(line.encode("utf-8")),
            "run_plan_sha256": run_plan_sha256,
            "campaign_sha256": campaign_sha256,
            "suite_sha256": suite_sha256,
            "kernel_sha256": log_plan["kernel_sha256"],
            "image_input_sha256": log_plan["image_input_sha256"],
            "image_final_sha256": log_plan["image_final_sha256"],
            "runner_log_sha256": log_plan["runner_log_sha256"],
            "command_argv": log_plan["command_argv"],
            "command_sha256": log_plan["command_sha256"],
        })
    return rows, functional


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
    for combination in expected_combinations:
        experiment = experiments[combination[0]]
        expected_operations = _operations_for(experiment, combination[1])
        pairs = sorted(key[2] for key in grouped if key[:2] == combination)
        if len(pairs) < minimum or pairs != list(range(1, len(pairs) + 1)):
            raise EvaluationError(f"missing inner pair in {source_ref}: {combination}")
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
            for field in (
                "workload_fingerprint", "result_fingerprint", "operations",
                "dataset_size", "result_items",
            ):
                if len({row[field] for row in samples}) != 1:
                    raise EvaluationError(f"paired {field} mismatch in {source_ref}")
            if (
                samples[0]["operations"] != expected_operations
                or samples[0]["result_items"] != expected_operations
                or any(row["work_units"] <= 0 for row in samples)
            ):
                raise EvaluationError(f"sample work does not match configured load in {source_ref}")
            if combination[0] == "file_query":
                by_role = {row["role"]: row for row in samples}
                if samples[0]["dataset_size"] != combination[1]:
                    raise EvaluationError(f"file query dataset size differs from load in {source_ref}")
                if any(
                    row["work_units"] != row["records_examined"]
                    for row in samples
                ):
                    raise EvaluationError(f"file query work receipt is inconsistent in {source_ref}")
                if by_role["baseline"]["work_units"] < (
                    combination[1] * expected_operations
                ):
                    raise EvaluationError(f"file query work is not measured traversal in {source_ref}")
                if by_role["treatment"]["work_units"] != expected_operations:
                    raise EvaluationError(f"file query index work is not measured candidates in {source_ref}")
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
        if min(counts) == 0 or abs(counts[0] - counts[1]) > suite["pairing"]["maximum_order_imbalance"]:
            raise EvaluationError(f"same-order bias in {source_ref}: {combination}")


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])


def _bootstrap_interval(values: list[float], seed_text: str) -> tuple[float, float]:
    rng = random.Random(int(sha256_bytes(seed_text.encode("utf-8"))[:16], 16))
    estimates = []
    for _ in range(BOOTSTRAP_REPETITIONS):
        estimates.append(_median([values[rng.randrange(len(values))] for _ in values]))
    estimates.sort()
    lower = estimates[math.floor(0.025 * (len(estimates) - 1))]
    upper = estimates[math.ceil(0.975 * (len(estimates) - 1))]
    return float(lower), float(upper)


def _sign_test(improvements: list[float]) -> dict[str, Any]:
    wins = sum(value > 0 for value in improvements)
    losses = sum(value < 0 for value in improvements)
    ties = len(improvements) - wins - losses
    n = wins + losses
    numerator = sum(math.comb(n, count) for count in range(wins, n + 1)) if n else 1
    denominator = 1 << n if n else 1
    fraction = Fraction(numerator, denominator)
    return {
        "alternative": "treatment_better",
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "n": n,
        "p_value": float(fraction),
        "numerator": fraction.numerator,
        "denominator": fraction.denominator,
    }


def _headline_significance_threshold(suite: dict[str, Any]) -> float:
    family = suite["claim_family"]
    return float(family["familywise_alpha"]) / len(family["hypotheses"])


def _load_supports_headline_claim(
    result: dict[str, Any],
    claim_gate: dict[str, Any],
    baseline_durations: list[float],
    maximum_p_value: float,
) -> bool:
    return bool(
        result["status"] == "measured"
        and result["median"] > 0
        and result["ci_low"]
        >= claim_gate["minimum_absolute_improvement_us"]
        and result["relative_ci_low"] is not None
        and result["relative_ci_low"]
        >= claim_gate["minimum_relative_improvement_percent"]
        and min(baseline_durations)
        >= claim_gate["minimum_baseline_duration_us"]
        and result["sign_test"]["p_value"] <= maximum_p_value
    )


def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["source_log"], row["source_line"], row["kind"])


def evaluate(
    suite: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    rows: list[dict[str, Any]],
    scenario_record: dict[str, Any] | None = None,
    functional_boots: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    functional_boots = functional_boots or {}
    all_rows = rows
    sample_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    for row in all_rows:
        if row.get("kind") == "agentos-evaluation-metric-row":
            _exact(row, SAMPLE_ROW_FIELDS, "metric row")
            sample_rows.append(row)
        elif row.get("kind") == "agentos-evaluation-diagnostic-row":
            _exact(row, DIAGNOSTIC_ROW_FIELDS, "diagnostic row")
            diagnostic_rows.append(row)
        else:
            raise EvaluationError("unknown long-form evaluation row kind")
        if type(row["value"]) not in {int, float} or not math.isfinite(row["value"]):
            raise EvaluationError("metric value is not finite")
        if row["run_plan_sha256"] != plan_sha256:
            raise EvaluationError("metric row is rebound to another run plan")
        if (
            row["suite_id"] != suite["suite_id"]
            or row["suite_sha256"] != plan["suite_sha256"]
            or row["campaign_sha256"] != plan["campaign_sha256"]
            or row["run_id"] != plan["run_id"]
            or row["environment_sha256"] != plan["environment_sha256"]
            or row["status"] != "supported"
        ):
            raise EvaluationError("metric row binding differs from suite/run/campaign")
    rows = sample_rows
    supported_logs = [item for item in plan["logs"] if item["status"] == "supported"]
    supported_refs = {item["path"] for item in supported_logs}
    if set(functional_boots) != supported_refs:
        raise EvaluationError(
            "functional boot receipts do not cover every supported Guest log exactly"
        )
    for source_ref, boot_receipt in functional_boots.items():
        if (
            not isinstance(boot_receipt, dict)
            or set(boot_receipt.get("tasks", {})) != set(FUNCTIONAL_TASKS)
            or len(boot_receipt.get("line_numbers", [])) != 6
            or len(boot_receipt.get("marker_sha256s", [])) != 6
        ):
            raise EvaluationError(
                f"functional boot receipt is incomplete for {source_ref}"
            )
    bad_status = "failed" if any(item["status"] == "failed" for item in plan["logs"]) else (
        "unavailable" if any(item["status"] == "unavailable" for item in plan["logs"]) else None
    )
    if bad_status is None and len(supported_logs) < suite["pairing"]["minimum_boots"]:
        raise EvaluationError("fewer than seven independent supported Guest boots")
    by_boot: dict[tuple[str, int, str, str], list[float]] = {}
    by_inner_pair: dict[tuple[str, int, str, int], dict[str, float]] = {}
    evidence_ids: dict[str, str] = {}
    for index, log in enumerate(plan["logs"], 1):
        evidence_ids[log["path"]] = f"raw-boot-{index:03d}"
    for row in rows:
        key = (row["experiment"], row["load"], row["role"], row["boot_id"])
        by_boot.setdefault(key, []).append(float(row["value"]))
        pair_key = (row["experiment"], row["load"], row["boot_id"], row["inner_pair"])
        pair = by_inner_pair.setdefault(pair_key, {})
        if row["role"] in pair:
            raise EvaluationError(f"duplicate role in inner pair {pair_key}")
        pair[row["role"]] = float(row["value"])
    result_bindings: dict[tuple[str, int, int], dict[str, str]] = {}
    for row in rows:
        if row["role"] != "baseline":
            continue
        key = (row["experiment"], row["load"], row["inner_pair"])
        observed = result_bindings.setdefault(key, {})
        fingerprint = row["result_fingerprint"]
        if fingerprint in observed.values():
            raise EvaluationError(
                f"result fingerprint was reused across challenged boots: {key}"
            )
        observed[row["boot_id"]] = fingerprint

    targets: list[dict[str, Any]] = []
    commits = sorted({item["commit"] for item in plan["logs"]})
    commit = commits[0] if len(commits) == 1 else "mixed"
    for experiment in suite["experiments"]:
        for role in ("baseline", "treatment"):
            variant = experiment[role]
            targets.append({
                "id": f"{experiment['id']}:{variant['id']}",
                "label": variant["label"],
                "role": role,
                "commit": commit,
            })

    benchmarks: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    headline_alpha = _headline_significance_threshold(suite)
    for experiment in suite["experiments"]:
        estimates: list[dict[str, Any]] = []
        samples: list[dict[str, Any]] = []
        paired: list[dict[str, Any]] = []
        baseline_durations: dict[int, list[float]] = {}
        benchmark_status = bad_status or "measured"
        for load in experiment["loads"]:
            values: dict[str, list[tuple[str, float]]] = {"baseline": [], "treatment": []}
            for log in supported_logs:
                for role in values:
                    key = (experiment["id"], load, role, log["boot_id"])
                    if key not in by_boot:
                        raise EvaluationError(f"missing boot sample for {key}")
                    values[role].append((log["boot_id"], _median(by_boot[key])))
            baseline_durations[load] = [value for _, value in values["baseline"]]
            if benchmark_status != "measured":
                paired.append({
                    "load": load, "status": benchmark_status, "n": 0,
                    "median": None, "p95": None, "ci_low": None, "ci_high": None,
                    "relative_median_percent": None, "relative_ci_low": None,
                    "relative_ci_high": None,
                    "sign_test": None, "samples": [],
                })
                continue
            baseline_target = f"{experiment['id']}:{experiment['baseline']['id']}"
            treatment_target = f"{experiment['id']}:{experiment['treatment']['id']}"
            for role, target in (("baseline", baseline_target), ("treatment", treatment_target)):
                boot_values = [value for _, value in values[role]]
                lower, upper = _bootstrap_interval(
                    boot_values, f"{plan_sha256}:{experiment['id']}:{load}:{role}"
                )
                estimates.append({
                    "target_id": target, "load": load, "value": _median(boot_values),
                    "lower": lower, "upper": upper, "p95": _p95(boot_values),
                    "n": len(boot_values),
                })
                log_by_boot = {item["boot_id"]: item for item in supported_logs}
                for boot_id, value in values[role]:
                    source = log_by_boot[boot_id]["path"]
                    samples.append({
                        "target_id": target, "load": load, "value": value,
                        "trial": boot_id, "order": "boot-median", "boot_id": boot_id,
                        "evidence_id": evidence_ids[source],
                    })
            improvements: list[float] = []
            relative_boot_values: list[float] = []
            paired_samples: list[dict[str, Any]] = []
            relative_available = True
            for log in sorted(supported_logs, key=lambda item: item["boot_id"]):
                boot_id = log["boot_id"]
                pair_keys = sorted(
                    key for key in by_inner_pair
                    if key[:3] == (experiment["id"], load, boot_id)
                )
                if len(pair_keys) < suite["pairing"]["minimum_inner_pairs"]:
                    raise EvaluationError(f"missing paired inner samples for boot {boot_id}")
                pair_improvements: list[float] = []
                pair_relative: list[float] = []
                inner_pairs: list[dict[str, Any]] = []
                for pair_key in pair_keys:
                    pair = by_inner_pair[pair_key]
                    if set(pair) != {"baseline", "treatment"}:
                        raise EvaluationError(f"inner pair is incomplete: {pair_key}")
                    if experiment["direction"] == "lower_is_better":
                        improvement = pair["baseline"] - pair["treatment"]
                    else:
                        improvement = pair["treatment"] - pair["baseline"]
                    pair_improvements.append(improvement)
                    if pair["baseline"] == 0:
                        relative_available = False
                        pair_relative_percent = None
                    else:
                        pair_relative_percent = improvement / abs(pair["baseline"]) * 100.0
                        pair_relative.append(pair_relative_percent)
                    inner_pairs.append({
                        "pair": pair_key[3],
                        "baseline_value": pair["baseline"],
                        "treatment_value": pair["treatment"],
                        "value": improvement,
                        "relative_percent": pair_relative_percent,
                    })
                boot_improvement = _median(pair_improvements)
                improvements.append(boot_improvement)
                boot_relative = None
                if len(pair_relative) == len(pair_improvements):
                    boot_relative = _median(pair_relative)
                    relative_boot_values.append(boot_relative)
                paired_samples.append({
                    "trial": boot_id,
                    "baseline_value": _median(
                        [item["baseline_value"] for item in inner_pairs]
                    ),
                    "treatment_value": _median(
                        [item["treatment_value"] for item in inner_pairs]
                    ),
                    "value": boot_improvement,
                    "relative_percent": boot_relative,
                    "inner_pairs": inner_pairs,
                })
            improvement_low, improvement_high = _bootstrap_interval(
                improvements, f"{plan_sha256}:{experiment['id']}:{load}:paired"
            )
            relative = None
            relative_low = None
            relative_high = None
            if relative_available and len(relative_boot_values) == len(improvements):
                relative = _median(relative_boot_values)
                relative_low, relative_high = _bootstrap_interval(
                    relative_boot_values, f"{plan_sha256}:{experiment['id']}:{load}:paired-relative"
                )
            else:
                for sample in paired_samples:
                    sample["relative_percent"] = None
            paired.append({
                "load": load, "status": "measured", "n": len(improvements),
                "median": _median(improvements), "p95": _p95(improvements),
                "ci_low": improvement_low, "ci_high": improvement_high,
                "relative_median_percent": relative,
                "relative_ci_low": relative_low,
                "relative_ci_high": relative_high,
                "sign_test": _sign_test(improvements),
                "samples": paired_samples,
            })
        evidence = sorted(evidence_ids.values())
        benchmark_id = experiment["id"]
        diagnostic_summary: list[dict[str, Any]] = []
        if benchmark_id == "file_query":
            for load in experiment["loads"]:
                load_rows = sorted(
                    (
                        row for row in diagnostic_rows
                        if row["experiment"] == benchmark_id and row["load"] == load
                    ),
                    key=lambda row: row["boot_id"],
                )
                if benchmark_status == "measured":
                    if len(load_rows) != len(supported_logs):
                        raise EvaluationError(f"missing file-query diagnostic for load {load}")

                    def diagnostic_stat(field: str) -> dict[str, Any]:
                        values_for_field = [float(row[field]) for row in load_rows]
                        return {
                            "median": _median(values_for_field),
                            "p95": _p95(values_for_field),
                            "n": len(values_for_field),
                        }

                    diagnostic_summary.append({
                        "load": load,
                        "status": "measured",
                        "unit": "us",
                        "cache_states": sorted({row["cache"] for row in load_rows}),
                        "duration_us": diagnostic_stat("duration_us"),
                        "work_units": diagnostic_stat("work_units"),
                        "index_rebuild_records": diagnostic_stat("index_rebuild_records"),
                        "samples": [
                            {
                                "boot_id": row["boot_id"],
                                "cache": row["cache"],
                                "duration_us": row["duration_us"],
                                "work_units": row["work_units"],
                                "index_rebuild_records": row["index_rebuild_records"],
                                "evidence_id": evidence_ids[row["source_log"]],
                                "source_line": row["source_line"],
                            }
                            for row in load_rows
                        ],
                    })
                else:
                    diagnostic_summary.append({
                        "load": load, "status": benchmark_status, "unit": "us",
                        "cache_states": [], "duration_us": None, "work_units": None,
                        "index_rebuild_records": None, "samples": [],
                    })
        benchmark = {
            "id": benchmark_id,
            "label": experiment["label"],
            "task": experiment["task"],
            "status": benchmark_status,
            "baseline": f"{benchmark_id}:{experiment['baseline']['id']}",
            "treatment": f"{benchmark_id}:{experiment['treatment']['id']}",
            "unit": experiment["unit"],
            "direction": experiment["direction"],
            "claim_gate": experiment["claim_gate"],
            "loads": experiment["loads"],
            "estimates": estimates,
            "samples": samples,
            "paired": paired,
            "diagnostics": diagnostic_summary,
            "evidence_ids": evidence,
        }
        benchmarks.append(benchmark)
        significant = [
            item for item in paired
            if _load_supports_headline_claim(
                item,
                experiment["claim_gate"],
                baseline_durations[item["load"]],
                headline_alpha,
            )
        ]
        claim_status = (
            "unavailable" if benchmark_status != "measured"
            else "supported" if len(significant) == len(experiment["loads"])
            else "not_supported"
        )
        if claim_status == "supported":
            ranges = ", ".join(
                f"load {item['load']}: {item['relative_ci_low']:.2f}%..{item['relative_ci_high']:.2f}%"
                for item in paired
            )
            gate = experiment["claim_gate"]
            effect = (
                f"Paired median improvement-rate bootstrap 95% CIs: {ranges}; "
                f"every load clears {gate['minimum_absolute_improvement_us']} us / "
                f"{gate['minimum_relative_improvement_percent']}% MCID, "
                f"{gate['minimum_baseline_duration_us']} us timing window, and "
                f"one-sided sign-test p <= {headline_alpha:.7f} after "
                "Bonferroni correction across three headline claims."
            )
        elif claim_status == "not_supported":
            effect = (
                "At least one preregistered load does not clear the absolute/relative MCID, "
                "minimum timing window, paired-CI, and Bonferroni-adjusted sign-test gates."
            )
        else:
            effect = "The planned independent-boot evidence is unavailable or failed; no value was imputed."
        title = (
            f"{experiment['treatment']['label']} improves {experiment['label']}"
            if claim_status == "supported"
            else f"{experiment['label']} advantage did not clear the preregistered gate"
            if claim_status == "not_supported"
            else f"{experiment['label']} has no usable evidence in this run"
        )
        claims.append({
            "id": f"{benchmark_id}-advantage",
            "title": title,
            "status": claim_status,
            "effect": effect,
            "benchmark_id": benchmark_id,
            "evidence_ids": evidence,
        })

    evidence: list[dict[str, Any]] = []
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        rows_by_source.setdefault(row["source_log"], []).append(row)
    for item in plan["logs"]:
        source_rows = rows_by_source.get(item["path"], [])
        receipt_markers = [
            (row["source_line"], row["source_marker_sha256"])
            for row in source_rows
        ]
        functional = functional_boots.get(item["path"])
        if functional is not None:
            receipt_markers.extend(zip(
                functional["line_numbers"], functional["marker_sha256s"]
            ))
        receipt_markers.sort()
        evidence.append({
            "id": evidence_ids[item["path"]],
            "kind": "guest-raw-log",
            "label": item["boot_id"],
            "path": _safe_ref(f"raw/{item['path']}", "Guest evidence path"),
            "sha256": item["sha256"],
            "status": {
                "supported": "verified",
                "unavailable": "unavailable",
                "failed": "invalid",
            }[item["status"]],
            "source": f"{item['path']} ({item['boot_id']})",
            "receipt": {
                "bytes": source_rows[0]["source_log_bytes"] if source_rows else None,
                "line_numbers": [line for line, _ in receipt_markers],
                "marker_sha256s": [digest for _, digest in receipt_markers],
                "boot_id": item["boot_id"],
                "commit": item["commit"],
                "challenge": item["challenge"],
                "kernel_sha256": item["kernel_sha256"],
                "image_input_sha256": item["image_input_sha256"],
                "image_final_sha256": item["image_final_sha256"],
                "runner_log_sha256": item["runner_log_sha256"],
                "command_argv": item["command_argv"],
                "command_sha256": item["command_sha256"],
                "capture_status": item["status"],
                "detail": item["detail"],
            },
        })
    run_status = "failed" if bad_status == "failed" else "unavailable" if bad_status else "measured"
    evidence.insert(0, {
        "id": "run-plan",
        "kind": "evaluation-run-plan",
        "label": "Strict evaluation run plan",
        "path": "run-plan.json",
        "sha256": plan_sha256,
        "status": "verified",
        "source": "strict run-plan JSON",
        "receipt": {
            "run_id": plan["run_id"],
            "environment_sha256": plan["environment_sha256"],
            "campaign_sha256": plan["campaign_sha256"],
        },
    })
    if scenario_record is not None:
        report = scenario_record["report"]
        evidence.append({
            "id": "research-scenario-report",
            "kind": "research-platform-scenario",
            "label": "Research-platform independent-boot scenario",
            "path": scenario_record["path"],
            "sha256": scenario_record["sha256"],
            "status": {
                "supported": "verified",
                "inconclusive": "verified",
                "failed": "invalid",
            }[report["status"]],
            "source": f"{scenario_record['path']} ({report['summary']['independent_boots']} independent boots)",
            "receipt": {
                "bytes": scenario_record["bytes"],
                "report_sha256": report["report_sha256"],
                "run_id": report["run_id"],
                "commit": report["source_commit"],
                "scenario_plan_sha256": scenario_record["plan_sha256"],
            },
        })
        evidence.append({
            "id": "research-scenario-plan",
            "kind": "research-platform-scenario-plan",
            "label": "Sealed research-platform scenario plan",
            "path": scenario_record["plan_path"],
            "sha256": scenario_record["plan_sha256"],
            "status": "verified",
            "source": "collected scenario plan",
            "receipt": {
                "run_id": report["run_id"],
                "commit": report["source_commit"],
                "report_sha256": scenario_record["sha256"],
            },
        })
    scenarios = []
    benchmark_by_task = {item["task"]: item for item in benchmarks}
    functional_complete = (
        bad_status is None
        and len(supported_logs) == len(plan["logs"])
        and len(supported_logs) >= suite["pairing"]["minimum_boots"]
        and set(functional_boots) == supported_refs
    )
    functional_evidence = [
        evidence_ids[item["path"]] for item in supported_logs
    ]
    task_labels = {
        "task1": "Agent process and mapped Context acceptance",
        "task2": "Versioned structured-tool acceptance",
        "task3": "Context Path lifecycle acceptance",
        "task4": "Structured indexed-file-query acceptance",
        "task5": "Sleeping Agent Loop and multi-Agent acceptance",
    }
    for number in range(1, 7):
        task = f"task{number}"
        if task == "task6" and scenario_record is not None:
            report = scenario_record["report"]
            if (
                scenario_record["performance_status"] != report["status"]
                or scenario_record["performance"]
                != report["summary"].get("paired_improvement")
            ):
                raise EvaluationError(
                    "scenario conclusion differs from the verified raw report"
                )
            scenarios.append({
                "id": report["scenario_id"],
                "label": "Seeded research-platform workflow",
                "task": task,
                "functional_status": scenario_record["functional_status"],
                "performance_status": scenario_record["performance_status"],
                "performance": scenario_record["performance"],
                "evidence_ids": ["research-scenario-plan", "research-scenario-report"],
            })
            continue
        if task == "task6":
            scenarios.append({
                "id": "task6-coverage",
                "label": "Task 6 dynamic research scenario",
                "task": task,
                "functional_status": "unavailable",
                "performance_status": "unavailable",
                "performance": None,
                "evidence_ids": ["run-plan"],
            })
            continue
        benchmark = benchmark_by_task.get(task)
        # Functional status is derived only from the strictly verified
        # per-boot receipts.  Microbenchmarks remain benchmark evidence and
        # never masquerade as functional or research-scenario performance.
        scenarios.append({
            "id": f"{task}-functional",
            "label": task_labels[task],
            "task": task,
            "functional_status": "pass" if functional_complete else "unavailable",
            "performance_status": "unavailable",
            "performance": None,
            "evidence_ids": (
                functional_evidence
                if functional_complete
                else (["run-plan"] if benchmark is None else benchmark["evidence_ids"] or ["run-plan"])
            ),
        })
    return {
        "schema_version": 1,
        "kind": "agentos-evaluation-summary",
        "run": {
            "id": plan["run_id"],
            "suite_id": suite["suite_id"],
            "status": run_status,
            "commit": commit,
            "label": "AgentOS independent-boot mechanism evaluation",
            "evidence_grade": "E2-local-raw",
            "cache_policy": "preregistered per variant and checked in every marker",
            "conclusion": (
                "Each headline claim requires every preregistered load to pass, "
                f"with exact sign-test p <= {headline_alpha:.7f}; Bonferroni "
                "controls FWER 0.05 across the three headline claims."
            ),
            "environment_sha256": plan["environment_sha256"],
            "run_plan_sha256": plan_sha256,
            "campaign_sha256": plan["campaign_sha256"],
            "suite_sha256": plan["suite_sha256"],
        },
        "targets": targets,
        "benchmarks": benchmarks,
        "scenarios": scenarios,
        "methodology": {
            "design": "paired AB/BA within each independent Guest boot",
            "independent_unit": "guest_boot",
            "within_boot_aggregation": "median",
            "cache_policy": "explicit marker field checked against suite",
            "repetitions": {"minimum_boots": suite["pairing"]["minimum_boots"], "minimum_inner_pairs": suite["pairing"]["minimum_inner_pairs"]},
            "order_policy": "both AB and BA; imbalance at most one",
            "interval_method": f"deterministic percentile bootstrap ({BOOTSTRAP_REPETITIONS} resamples)",
            "p95_method": "nearest-rank",
            "sign_test": "exact one-sided; ties excluded",
            "multiple_testing": {
                "family_id": suite["claim_family"]["id"],
                "method": "Bonferroni",
                "familywise_alpha": suite["claim_family"]["familywise_alpha"],
                "hypothesis_count": len(suite["claim_family"]["hypotheses"]),
                "per_claim_alpha": headline_alpha,
                "headline_claims": suite["claim_family"]["hypotheses"],
                "load_gate": "intersection (every preregistered load must pass)",
            },
            "missing_data": "explicit unavailable/failed; no zero fill or imputation",
        },
        "evidence": evidence,
        "claims": claims,
    }


def build(
    suite_path: Path,
    plan_path: Path,
    source_root: Path,
    scenario_path: Path | None = None,
    scenario_plan_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    suite = load_suite(suite_path)
    plan, plan_hash = load_run_plan(plan_path)
    if sha256_file(suite_path) != plan["suite_sha256"]:
        raise EvaluationError("evaluation suite differs from the preregistered run plan")
    rows: list[dict[str, Any]] = []
    functional_boots: dict[str, dict[str, Any]] = {}
    for item in plan["logs"]:
        extracted_rows, functional = extract_log(
            _source_path(source_root, item["path"]), item["path"], item, suite,
            plan["run_id"], plan["environment_sha256"], plan_hash,
            plan["campaign_sha256"], plan["suite_sha256"],
        )
        rows.extend(extracted_rows)
        if functional:
            functional_boots[item["path"]] = functional
    rows.sort(key=_row_sort_key)
    if (scenario_path is None) != (scenario_plan_path is None):
        raise EvaluationError("scenario report and scenario plan must be supplied together")
    scenario = None
    if scenario_path is not None and scenario_plan_path is not None:
        scenario = bind_scenario_plan(
            scenario_plan_path,
            load_scenario_report(scenario_path, plan),
            plan,
        )
        scenario["path"] = _evidence_path(
            plan_path.parent, scenario_path, "scenario report evidence path"
        )
        scenario["plan_path"] = _evidence_path(
            plan_path.parent, scenario_plan_path, "scenario plan evidence path"
        )
    return evaluate(
        suite, plan, plan_hash, rows, scenario,
        functional_boots=functional_boots,
    ), rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "".join(
        json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    _atomic_write(path, value)


def _atomic_write(path: Path, value: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise EvaluationError(f"cannot read metrics JSONL: {error}") from error
    if not lines:
        return []
    if any(not line for line in lines):
        raise EvaluationError("metrics JSONL has blank lines")
    rows = []
    for line_number, line in enumerate(lines, 1):
        value = strict_json_loads(line)
        if isinstance(value, dict) and value.get("kind") == "agentos-evaluation-metric-row":
            fields = SAMPLE_ROW_FIELDS
        elif isinstance(value, dict) and value.get("kind") == "agentos-evaluation-diagnostic-row":
            fields = DIAGNOSTIC_ROW_FIELDS
        else:
            raise EvaluationError(f"metrics JSONL line {line_number} has an unknown kind")
        rows.append(_exact(value, fields, f"metrics JSONL line {line_number}"))
    return rows


def verify(
    suite_path: Path,
    plan_path: Path,
    source_root: Path,
    summary_path: Path,
    rows_path: Path,
    scenario_path: Path | None = None,
    scenario_plan_path: Path | None = None,
) -> dict[str, Any]:
    expected_summary, expected_rows = build(
        suite_path, plan_path, source_root, scenario_path, scenario_plan_path
    )
    actual_summary = _read_json(summary_path)
    actual_rows = read_jsonl(rows_path)
    if actual_rows != expected_rows:
        raise EvaluationError("metrics JSONL differs from raw Guest logs and run plan")
    if actual_summary != expected_summary:
        raise EvaluationError("summary differs from raw Guest logs and run plan")
    return expected_summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--suite", type=Path, required=True)
        sub.add_argument("--run-plan", type=Path, required=True)
        sub.add_argument("--source-root", type=Path, required=True)
        sub.add_argument("--summary", type=Path, required=True)
        sub.add_argument("--rows", type=Path, required=True)
        sub.add_argument("--scenario-report", type=Path)
        sub.add_argument("--scenario-plan", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            summary, rows = build(
                args.suite, args.run_plan, args.source_root,
                args.scenario_report, args.scenario_plan,
            )
            write_json(args.summary, summary)
            write_jsonl(args.rows, rows)
        else:
            verify(
                args.suite, args.run_plan, args.source_root, args.summary,
                args.rows, args.scenario_report, args.scenario_plan,
            )
    except EvaluationError as error:
        raise SystemExit(f"evaluation contract failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
