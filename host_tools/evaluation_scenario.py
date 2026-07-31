#!/usr/bin/env python3
"""Collect paired research-platform measurements from independent QEMU boots.

Guest ``rp_orch_timing`` records are diagnostic decomposition. The separate,
strict ``rp_workflow_timing`` record supplies the end-to-end makespan from
workflow entry through final inventory validation. The Host run duration is an
independent upper bound; run summaries and logs are not alternative timings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import statistics
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from .strict_json import strict_json_loads
    from .safe_host_paths import (
        atomic_write_bytes,
        read_regular_file,
        reject_link_components as reject_host_link_components,
        require_regular_file,
        require_safe_directory,
        walk_directory_tree_no_links,
    )
    from .check_seeded_action_state import (
        CHALLENGE_RECEIPT_NAME,
        TASK6_ARTIFACT_INPUT_STORAGE,
        TASK6_ARTIFACT_OUTPUT_STORAGE,
        TASK6_ARTIFACT_RECEIPT_SCHEMA,
        task6_fnv64,
        challenge_input_receipt,
        derive_challenge,
        seeded_actions,
        task6_artifact_payloads,
    )
except ImportError:  # Direct execution from host_tools/.
    from strict_json import strict_json_loads
    from safe_host_paths import (
        atomic_write_bytes,
        read_regular_file,
        reject_link_components as reject_host_link_components,
        require_regular_file,
        require_safe_directory,
        walk_directory_tree_no_links,
    )
    from check_seeded_action_state import (
        CHALLENGE_RECEIPT_NAME,
        TASK6_ARTIFACT_INPUT_STORAGE,
        TASK6_ARTIFACT_OUTPUT_STORAGE,
        TASK6_ARTIFACT_RECEIPT_SCHEMA,
        task6_fnv64,
        challenge_input_receipt,
        derive_challenge,
        seeded_actions,
        task6_artifact_payloads,
    )


SCHEMA_VERSION = 2
REPORT_BINDING_DOMAIN = "scenario-report-v2"
SCENARIO_ID = "research-platform-seeded"
MIN_SUPPORTED_BOOTS = 7
BOOTSTRAP_REPETITIONS = 2_000
MIN_ABSOLUTE_IMPROVEMENT_MS = 10
MIN_BASELINE_MAKESPAN_MS = 50
MIN_RELATIVE_IMPROVEMENT_PERCENT = 5.0
SCENARIO_ALPHA = 0.05
SCENARIO_DIRECTIONAL_ALPHA = SCENARIO_ALPHA / 2
SCENARIO_INFERENCE = {
    "method": "exact_directional_binomial_with_bonferroni",
    "success_unit": "paired_boot",
    "sample_policy": "full_n_including_non_wins",
    "alpha": SCENARIO_ALPHA,
    "multiplicity": "two_directions_within_task6_scenario",
    "directional_hypothesis_count": 2,
    "correction": "Bonferroni",
    "per_direction_alpha": SCENARIO_DIRECTIONAL_ALPHA,
}
SCENARIO_INTERPRETATION = {
    "design": "full-stack",
    "causal_attribution": "non-single-mechanism",
    "host_page_cache": "uncontrolled",
}
VALID_STATUSES = frozenset({"supported", "regressed", "inconclusive", "failed"})
STATE_NAME_RE = re.compile(r"rp_[a-z0-9_]+\Z")
PROGRAM_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
CANONICAL_UINT_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
PROGRAM_MANIFEST_ENTRY_RE = re.compile(
    r'^\s*APPLY\("(rp_[a-z0-9_]+)"\)\s*(?:\\)?\s*$'
)
ROLE_MANIFEST_ENTRY_RE = re.compile(
    r'^\s*APPLY\("(rp_[a-z0-9_]+)",\s*'
    r'"(orchestrator|recovery|artifact|investigator|sentinel)"\)\s*(?:\\)?\s*$'
)
ROLE_NUMBERS = {
    "sentinel": 1,
    "investigator": 2,
    "recovery": 3,
    "orchestrator": 4,
    "artifact": 5,
}
LEDGER_LINE_MAX = 255
WORKFLOW_TIMING_LINE_MAX = 511
MAX_PROGRAM_ELAPSED_MS = 3_600_000
MAX_WORKFLOW_ELAPSED_MS = 3_600_000
MAX_GUEST_CLOCK_MS = (1 << 63) - 1
MAX_JSON_BYTES = 1 << 20
MAX_STATE_FILE_BYTES = 8 << 20
MAX_LOG_BYTES = 64 << 20
MAX_RUNTIME_ARTIFACT_BYTES = 1 << 30
WORKFLOW_TIMING_KEYS = (
    "schema",
    "clock",
    "entry",
    "handoff",
    "init_phase_mask",
    "completion",
    "completion_phase_mask",
    "start_ms",
    "ready_ms",
    "steady_start_ms",
    "end_ms",
    "setup_elapsed_ms",
    "exec_elapsed_ms",
    "steady_elapsed_ms",
    "workflow_elapsed_ms",
)
AGENTOS_INIT_PHASE_MASK = (1 << 5) - 1
PLAIN_COMPLETION_PHASE_MASK = 1
AGENTOS_COMPLETION_PHASE_MASK = 3
AGENTOS_ACCEPTANCE_FILE = "rp_agentos_acceptance"
RESOURCE_STABILITY_FILE = "rp_resource_stability"
RESOURCE_STABILITY_LOAD_WORKFLOWS = 4
RESOURCE_STABILITY_TERMINAL_WORKFLOWS = 1
RESOURCE_STABILITY_WORKFLOWS = (
    RESOURCE_STABILITY_LOAD_WORKFLOWS + RESOURCE_STABILITY_TERMINAL_WORKFLOWS
)
RESOURCE_STABILITY_CHILD_ROUNDS = 12
RESOURCE_STABILITY_MEMORY_PAGES = 128
RESOURCE_STABILITY_FILE_OBJECTS = 12
RESOURCE_STABILITY_METADATA_OPS = 3
RESOURCE_STABILITY_MEASUREMENT_SCOPE = "post_workflow_acceptance"
RESOURCE_STABILITY_LINE_MAX = 8191
RESOURCE_STABILITY_REPORT_MAGIC = 0x52505354
RESOURCE_STABILITY_REPORT_VERSION = 2
RESOURCE_STABILITY_REPORT_SIZE = 224
RESOURCE_STABILITY_FNV_OFFSET = 1469598103934665603
RESOURCE_STABILITY_FNV_PRIME = 1099511628211
RESOURCE_STABILITY_RESOURCE_KINDS = (
    "process",
    "thread",
    "file_object",
    "fs_block",
    "fs_inode",
    "buffer_cache",
    "agent_state_page",
    "physical_page",
)
RESOURCE_STABILITY_GROWTH_BOUNDS = {
    "process": 0,
    "thread": 0,
    "file_object": 0,
    "fs_block": 32,
    "fs_inode": 0,
    "buffer_cache": 16,
    "agent_state_page": 0,
    "physical_page": 0,
}
RESOURCE_STABILITY_INTERPRETATION = {
    "timing_relationship": "excluded_from_task6_makespan",
    "verified_claim": "bounded_same_boot_configured_global_counter_reclamation",
    "causal_attribution": "resource_lifecycle_acceptance_not_performance_effect",
    "global_resource_observation": "configured_kind_aggregate_counters_only",
    "account_observation": "fresh_self_identity_only",
    "rate_budget_observation": "not_measured",
    "unmeasured_policy": "reported_as_not_measured_never_passed",
    "global_leak_freedom": "not_claimed",
}
UINT64_MAX = (1 << 64) - 1
UINT32_MAX = (1 << 32) - 1
AGENTOS_CONTEXT_RECEIPT_MAX = 4
AGENTOS_TIMELINE_RECEIPT_MAX = 4
AGENTOS_PROVENANCE_RECEIPT_MAX = 4
REQUIRED_AGENTOS_MODULES = (
    "context",
    "structured_tool",
    "metadata_query",
    "observation",
)
RESOURCE_STABILITY_RECORD_PREFIX_KEYS = (
    "workflow_index",
    "mode",
    "challenge_nonce",
    "lifecycle_id",
    "lifecycle_generation",
    "scope_id",
    "io_owner",
    "resource_account_slot",
    "resource_account_reserved",
    "resource_account_generation",
    "initial_cache_resident",
    "initial_leased",
    "initial_debt",
    "initial_waiters",
    "initial_debt_waiters",
    "initial_admission_waiters",
    "initial_context_lane_depth",
    "initial_context_lane_waiters",
    "initial_metadata_owned",
    "initial_metadata_waiters",
    "initial_agent_calls",
    "initial_context_records",
    "final_cache_resident",
    "final_leased",
    "final_debt",
    "final_waiters",
    "final_debt_waiters",
    "final_admission_waiters",
    "final_context_lane_depth",
    "final_context_lane_waiters",
    "final_metadata_owned",
    "final_metadata_waiters",
    "final_agent_calls",
    "final_context_records",
    "initial_completion_sequence",
    "final_completion_sequence",
    "process_rounds",
    "file_rounds",
    "memory_rounds",
    "metadata_rounds",
    "report_guard",
)
RESOURCE_STABILITY_GLOBAL_FREE_KEYS = (
    "ordinary_free_pages_before",
    "ordinary_free_pages_after",
    "reserved_free_pages_before",
    "reserved_free_pages_after",
    "stack_reserved_free_pages_before",
    "stack_reserved_free_pages_after",
)
RESOURCE_STABILITY_GLOBAL_RESOURCE_KEYS = tuple(
    f"{kind}_{field}"
    for kind in RESOURCE_STABILITY_RESOURCE_KINDS
    for field in (
        "ordinary_used_before",
        "ordinary_used_after",
        "ordinary_pending_before",
        "ordinary_pending_after",
        "reserved_used_before",
        "reserved_used_after",
        "reserved_pending_before",
        "reserved_pending_after",
    )
)
RESOURCE_STABILITY_RECORD_KEYS = (
    *RESOURCE_STABILITY_RECORD_PREFIX_KEYS,
    *RESOURCE_STABILITY_GLOBAL_FREE_KEYS,
    *RESOURCE_STABILITY_GLOBAL_RESOURCE_KEYS,
    "per_workflow_bound_status",
    "reaped",
    "pipe_eof",
    "status",
)

PLAIN_LEDGER_KEYS = (
    "program",
    "launcher",
    "ok",
    "code",
    "elapsed_ms",
)
AGENTOS_LEDGER_KEYS = (
    "program",
    "role",
    "launcher",
    "identity_source",
    "is_agent",
    "agent_role",
    "filesystem_domain",
    "filesystem_capabilities",
    "ok",
    "code",
    "elapsed_ms",
)
OUTCOME_RECORD_SPECS = (
    (
        "research_rerun",
        "rp_runner",
        "host_action_rerun",
        ("host_action_rerun", "parent", "status"),
    ),
    (
        "workflow_stage",
        "rp_stage_state",
        "host_workflow_stage_action",
        (
            "host_workflow_stage_action",
            "attempt",
            "status",
            "command",
            "duration_ms",
        ),
    ),
    (
        "artifact_derivation",
        "rp_artifact",
        "host_artifact_derive",
        ("host_artifact_derive", "output", "operation", "stage", "sha256"),
    ),
)
LLM_OUTCOME_KEYS = (
    "host_llm_response_id",
    "host_llm_response_request",
    "host_llm_response_provider",
    "host_llm_response_mode",
    "host_llm_response_summary",
    "host_llm_response_citations",
)


class ScenarioEvidenceError(ValueError):
    """Raised when a boot cannot be accepted as measurement evidence."""


@dataclass(frozen=True)
class ProgramTiming:
    name: str
    elapsed_ms: int


@dataclass(frozen=True)
class TargetMeasurement:
    target: str
    program_timings: tuple[ProgramTiming, ...]
    workflow_elapsed_ms: int
    outcome: dict[str, object]
    outcome_fingerprint: str
    raw_source_receipt: dict[str, object]
    challenge: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _binding_sha256(value: object, domain: str) -> str:
    return _sha256(domain.encode("ascii") + b"\0" + _canonical_json(value))


def _reject_link_components(path: Path, label: str) -> None:
    try:
        reject_host_link_components(path)
    except (OSError, ValueError) as error:
        raise ScenarioEvidenceError(
            f"{label} has a link-backed path component: {path}"
        ) from error


def _require_directory(path: Path, label: str) -> Path:
    try:
        return require_safe_directory(path)
    except (OSError, ValueError) as error:
        raise ScenarioEvidenceError(
            f"{label} is missing or link-backed: {path}"
        ) from error


def _read_regular_file(path: Path, label: str, maximum_bytes: int | None = None) -> bytes:
    try:
        return read_regular_file(path, maximum_bytes=maximum_bytes)
    except (OSError, ValueError) as error:
        raise ScenarioEvidenceError(f"cannot read {label}: {path}") from error


def _runtime_artifact_receipts(
    target_dir: Path, target: str, summary: dict[str, object]
) -> dict[str, dict[str, object]]:
    declared = summary.get("runtime_artifacts")
    expected_names = {"kernel", "image_input", "image_final"}
    if not isinstance(declared, dict) or set(declared) != expected_names:
        raise ScenarioEvidenceError(f"{target} run summary lacks runtime artifacts")
    receipts: dict[str, dict[str, object]] = {}
    for name in sorted(expected_names):
        record = declared[name]
        expected_path = f"artifacts/{name}"
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise ScenarioEvidenceError(f"{target} {name} artifact receipt is invalid")
        if record["path"] != expected_path:
            raise ScenarioEvidenceError(f"{target} {name} artifact path is invalid")
        path = target_dir / expected_path
        try:
            path = require_regular_file(
                path, nonempty=True, maximum_bytes=MAX_RUNTIME_ARTIFACT_BYTES
            )
        except (OSError, ValueError) as error:
            raise ScenarioEvidenceError(f"{target} {name} artifact is unavailable")
        size = path.stat().st_size
        if size <= 0 or size > MAX_RUNTIME_ARTIFACT_BYTES or record["bytes"] != size:
            raise ScenarioEvidenceError(f"{target} {name} artifact size differs")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if record["sha256"] != digest.hexdigest():
            raise ScenarioEvidenceError(f"{target} {name} artifact hash differs")
        receipts[name] = dict(record)
    return receipts


def _strict_json_value(data: bytes, label: str) -> object:
    if (
        not data
        or len(data) > MAX_JSON_BYTES
        or not data.endswith(b"\n")
        or data.startswith(b"\xef\xbb\xbf")
        or b"\r" in data
        or b"\0" in data
    ):
        raise ScenarioEvidenceError(f"{label} is empty, oversized, or non-canonical")
    try:
        value = strict_json_loads(data)
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ScenarioEvidenceError(f"{label} is not strict JSON") from error
    return value


def _strict_json_object(data: bytes, label: str) -> dict[str, object]:
    value = _strict_json_value(data, label)
    if not isinstance(value, dict):
        raise ScenarioEvidenceError(f"{label} is not an object")
    return value


def _parse_record(line: str, label: str) -> dict[str, str]:
    if not line:
        raise ScenarioEvidenceError(f"{label} is empty")
    result: dict[str, str] = {}
    for field in line.split(";"):
        if not field or field.count("=") != 1:
            raise ScenarioEvidenceError(f"{label} is not a strict key/value record")
        key, value = field.split("=", 1)
        if not key or not value or key in result:
            raise ScenarioEvidenceError(f"{label} has an empty or duplicate field")
        if re.fullmatch(r"[a-z][a-z0-9_]*", key) is None:
            raise ScenarioEvidenceError(f"{label} has an invalid field name")
        result[key] = value
    return result


def _parse_program_manifest(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    data = _read_regular_file(path, "program manifest")
    try:
        lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise ScenarioEvidenceError(f"program manifest is not UTF-8: {path}") from error
    program_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("#define RP_PLATFORM_PROGRAMS(APPLY)")
        ),
        None,
    )
    if program_start is None:
        raise ScenarioEvidenceError(f"program manifest has no platform list: {path}")
    programs: list[str] = []
    for line in lines[program_start + 1 :]:
        match = PROGRAM_MANIFEST_ENTRY_RE.fullmatch(line)
        if match is None:
            break
        programs.append(match.group(1))
        if not line.rstrip().endswith("\\"):
            break
    if not programs or len(programs) != len(set(programs)):
        raise ScenarioEvidenceError(f"program manifest list is empty or duplicated: {path}")

    role_start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("#define RP_AGENTOS_ROLE_PROGRAMS(APPLY)")
        ),
        None,
    )
    if role_start is None:
        raise ScenarioEvidenceError(f"program manifest has no role list: {path}")
    roles: dict[str, str] = {}
    for line in lines[role_start + 1 :]:
        match = ROLE_MANIFEST_ENTRY_RE.fullmatch(line)
        if match is None:
            break
        program, role = match.groups()
        if program in roles:
            raise ScenarioEvidenceError(f"program role is duplicated: {program}")
        roles[program] = role
        if not line.rstrip().endswith("\\"):
            break
    if not roles or set(roles) - set(programs):
        raise ScenarioEvidenceError(f"program role list is empty or inconsistent: {path}")
    return tuple(programs), roles


def read_expected_programs(
    source_tree: Path | None = None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Read and cross-check the two target manifests used by this adapter."""

    root = source_tree or Path(__file__).resolve().parents[1]
    agentos = _parse_program_manifest(root / "user" / "include" / "rp_program_manifest.h")
    plain = _parse_program_manifest(
        root / "baseline_ucore" / "user" / "include" / "rp_program_manifest.h"
    )
    if plain != agentos:
        raise ScenarioEvidenceError("plain and AgentOS program manifests differ")
    return agentos


def _read_ascii_lines(
    path: Path, label: str, *, line_max: int = LEDGER_LINE_MAX
) -> tuple[bytes, list[str]]:
    data = _read_regular_file(path, label, MAX_JSON_BYTES)
    if not data or not data.endswith(b"\n") or b"\r" in data or b"\0" in data:
        raise ScenarioEvidenceError(f"{label} is empty or non-canonical")
    try:
        lines = data[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeDecodeError as error:
        raise ScenarioEvidenceError(f"{label} is not canonical ASCII") from error
    if any(not line for line in lines):
        raise ScenarioEvidenceError(f"{label} contains an empty record")
    if any(len(line) > line_max for line in lines):
        raise ScenarioEvidenceError(f"{label} contains an overlong record")
    return data, lines


def _canonical_uint(value: str, label: str) -> int:
    if CANONICAL_UINT_RE.fullmatch(value) is None:
        raise ScenarioEvidenceError(f"{label} is not a canonical integer")
    parsed = int(value)
    if parsed > UINT64_MAX:
        raise ScenarioEvidenceError(f"{label} exceeds the Guest uint64 range")
    return parsed


def _parse_timing_ledger(
    state_dir: Path,
    target: str,
    expected_programs: tuple[str, ...],
    expected_roles: dict[str, str],
) -> tuple[tuple[ProgramTiming, ...], bytes]:
    ledger_path = state_dir / "rp_orch_timing"
    data, lines = _read_ascii_lines(ledger_path, f"{target} rp_orch_timing")
    if len(lines) < 3:
        raise ScenarioEvidenceError(f"{target} rp_orch_timing has no program records")

    orchestrator = _parse_record(lines[0], f"{target} ledger header 1")
    launcher = _parse_record(lines[1], f"{target} ledger header 2")
    expected_orchestrator = "rp_seed_orch" if target == "plain" else "rp_orch"
    expected_launcher = "fork_seeded" if target == "plain" else "mixed_attested"
    if orchestrator != {"orchestrator": expected_orchestrator}:
        raise ScenarioEvidenceError(f"{target} ledger has an invalid orchestrator header")
    if launcher != {"launcher": expected_launcher}:
        raise ScenarioEvidenceError(f"{target} ledger has an invalid launcher header")

    timings: list[ProgramTiming] = []
    seen: set[str] = set()
    expected_keys = PLAIN_LEDGER_KEYS if target == "plain" else AGENTOS_LEDGER_KEYS
    if len(lines) != len(expected_programs) + 2:
        raise ScenarioEvidenceError(f"{target} ledger program count differs from the manifest")
    for line_number, line in enumerate(lines[2:], 3):
        record = _parse_record(line, f"{target} ledger line {line_number}")
        if tuple(record) != expected_keys:
            raise ScenarioEvidenceError(
                f"{target} ledger line {line_number} has missing, reordered, or extra fields"
            )
        program = record["program"]
        if PROGRAM_NAME_RE.fullmatch(program) is None:
            raise ScenarioEvidenceError(f"{target} ledger line {line_number} has an invalid program")
        if program in seen:
            raise ScenarioEvidenceError(f"{target} ledger repeats program {program}")
        seen.add(program)
        expected_program = expected_programs[line_number - 3]
        if program != expected_program:
            raise ScenarioEvidenceError(
                f"{target} ledger expected {expected_program}, found {program}"
            )
        if record["ok"] != "1" or record["code"] != "0":
            raise ScenarioEvidenceError(f"{target} ledger reports failed program {program}")
        elapsed_ms = _canonical_uint(
            record["elapsed_ms"], f"{target} {program} elapsed_ms"
        )
        if elapsed_ms > MAX_PROGRAM_ELAPSED_MS:
            raise ScenarioEvidenceError(f"{target} {program} elapsed_ms exceeds the limit")
        if target == "plain":
            if record["launcher"] != "fork_seeded":
                raise ScenarioEvidenceError(f"plain ledger has an invalid launcher for {program}")
        else:
            for key in (
                "is_agent",
                "agent_role",
                "filesystem_domain",
                "filesystem_capabilities",
            ):
                _canonical_uint(record[key], f"agentos {program} {key}")
            if record["identity_source"] != "child_after_exec":
                raise ScenarioEvidenceError(f"agentos ledger lacks runtime identity for {program}")
            expected_role = expected_roles.get(program, "plain")
            expected_program_launcher = (
                "agent_create_role" if program in expected_roles else "agent_worker_create"
            )
            expected_is_agent = "1" if program in expected_roles else "0"
            expected_role_number = str(ROLE_NUMBERS.get(expected_role, 0))
            if record["launcher"] != expected_program_launcher:
                raise ScenarioEvidenceError(f"agentos ledger has an invalid launcher for {program}")
            if record["role"] != expected_role:
                raise ScenarioEvidenceError(f"agentos ledger has an invalid role for {program}")
            if (
                record["is_agent"] != expected_is_agent
                or record["agent_role"] != expected_role_number
            ):
                raise ScenarioEvidenceError(f"agentos ledger has invalid identity for {program}")
            if _canonical_uint(record["filesystem_domain"], "filesystem_domain") == 0:
                raise ScenarioEvidenceError(f"agentos ledger has no filesystem domain for {program}")
            if _canonical_uint(record["filesystem_capabilities"], "filesystem_capabilities") == 0:
                raise ScenarioEvidenceError(f"agentos ledger has no filesystem capabilities for {program}")
        timings.append(ProgramTiming(program, elapsed_ms))
    return tuple(timings), data


def _parse_workflow_timing(
    state_dir: Path,
    target: str,
    program_timings: tuple[ProgramTiming, ...],
    host_elapsed_seconds: float,
) -> tuple[dict[str, object], bytes]:
    data, lines = _read_ascii_lines(
        state_dir / "rp_workflow_timing",
        f"{target} rp_workflow_timing",
        line_max=WORKFLOW_TIMING_LINE_MAX,
    )
    if len(lines) != 1:
        raise ScenarioEvidenceError(
            f"{target} rp_workflow_timing must contain exactly one record"
        )
    record = _parse_record(lines[0], f"{target} workflow timing record")
    if tuple(record) != WORKFLOW_TIMING_KEYS:
        raise ScenarioEvidenceError(
            f"{target} has a missing or invalid workflow timing record"
        )
    expected_entry = "rp_seed_orch" if target == "plain" else "rp_agentos_orch"
    expected_handoff = "direct" if target == "plain" else "delegated_pipe_v1"
    expected_phase_mask = 0 if target == "plain" else AGENTOS_INIT_PHASE_MASK
    expected_completion = (
        "local_final_validation"
        if target == "plain"
        else "parent_wait_final_validation"
    )
    expected_completion_mask = (
        PLAIN_COMPLETION_PHASE_MASK
        if target == "plain"
        else AGENTOS_COMPLETION_PHASE_MASK
    )
    if (
        record["schema"] != "guest_workflow_timing_v3"
        or record["clock"] != "monotonic_mtime_ms"
        or record["entry"] != expected_entry
        or record["handoff"] != expected_handoff
        or record["completion"] != expected_completion
    ):
        raise ScenarioEvidenceError(
            f"{target} workflow timing does not cover its trusted entry and completion"
        )
    numeric_keys = (
        "init_phase_mask",
        "completion_phase_mask",
        "start_ms",
        "ready_ms",
        "steady_start_ms",
        "end_ms",
        "setup_elapsed_ms",
        "exec_elapsed_ms",
        "steady_elapsed_ms",
        "workflow_elapsed_ms",
    )
    values = {
        key: _canonical_uint(record[key], f"{target} {key}")
        for key in numeric_keys
    }
    if any(value > MAX_GUEST_CLOCK_MS for value in values.values()):
        raise ScenarioEvidenceError(f"{target} workflow timing exceeds the clock range")
    if values["init_phase_mask"] != expected_phase_mask:
        raise ScenarioEvidenceError(
            f"{target} workflow timing omits required initialization phases"
        )
    if values["completion_phase_mask"] != expected_completion_mask:
        raise ScenarioEvidenceError(
            f"{target} workflow timing omits required completion phases"
        )
    start_ms = values["start_ms"]
    ready_ms = values["ready_ms"]
    steady_start_ms = values["steady_start_ms"]
    end_ms = values["end_ms"]
    setup_elapsed_ms = values["setup_elapsed_ms"]
    exec_elapsed_ms = values["exec_elapsed_ms"]
    steady_elapsed_ms = values["steady_elapsed_ms"]
    workflow_elapsed_ms = values["workflow_elapsed_ms"]
    if not (start_ms <= ready_ms <= steady_start_ms <= end_ms):
        raise ScenarioEvidenceError(f"{target} workflow clock phases are not monotonic")
    if (
        setup_elapsed_ms != ready_ms - start_ms
        or exec_elapsed_ms != steady_start_ms - ready_ms
        or steady_elapsed_ms != end_ms - steady_start_ms
        or workflow_elapsed_ms != end_ms - start_ms
        or workflow_elapsed_ms
        != setup_elapsed_ms + exec_elapsed_ms + steady_elapsed_ms
    ):
        raise ScenarioEvidenceError(
            f"{target} workflow timing arithmetic does not bind the Guest entry"
        )
    if workflow_elapsed_ms <= 0:
        raise ScenarioEvidenceError(f"{target} workflow timing is empty")
    if target == "plain" and (ready_ms != steady_start_ms or exec_elapsed_ms != 0):
        raise ScenarioEvidenceError("plain workflow timing has a false exec phase")
    if target == "agentos" and setup_elapsed_ms <= 0:
        raise ScenarioEvidenceError(
            "agentos workflow timing does not include orchestrator initialization"
        )
    if workflow_elapsed_ms > MAX_WORKFLOW_ELAPSED_MS:
        raise ScenarioEvidenceError(
            f"{target} workflow_elapsed_ms exceeds the scenario timeout limit"
        )
    host_elapsed_ms = math.ceil(host_elapsed_seconds * 1000)
    if workflow_elapsed_ms > host_elapsed_ms:
        raise ScenarioEvidenceError(
            f"{target} workflow_elapsed_ms exceeds the Host-observed run duration"
        )
    program_elapsed_ms_total = sum(item.elapsed_ms for item in program_timings)
    if steady_elapsed_ms < program_elapsed_ms_total:
        raise ScenarioEvidenceError(
            f"{target} steady_elapsed_ms is less than the program timing total"
        )
    parsed = {
        "schema": record["schema"],
        "clock": record["clock"],
        "entry": record["entry"],
        "handoff": record["handoff"],
        "completion": record["completion"],
        **values,
    }
    return parsed, data


def _state_inventory(state_dir: Path) -> tuple[dict[str, object], dict[str, bytes]]:
    _require_directory(state_dir, "state-extracted directory")
    summary_path = state_dir / "extract-summary.json"
    summary_data = _read_regular_file(summary_path, "extract summary", MAX_JSON_BYTES)
    summary = _strict_json_object(summary_data, "extract summary")
    files = summary.get("files")
    if (
        not isinstance(files, list)
        or not all(isinstance(name, str) for name in files)
        or files != sorted(files)
        or len(files) != len(set(files))
        or any(STATE_NAME_RE.fullmatch(name) is None for name in files)
    ):
        raise ScenarioEvidenceError("extract summary has an invalid ordered inventory")
    count = summary.get("extracted_state_files")
    if type(count) is not int or count != len(files):
        raise ScenarioEvidenceError("extract summary count does not match its inventory")
    if summary.get("status") != "ready":
        raise ScenarioEvidenceError("extract summary is not ready")
    available_scopes = summary.get("available_scope_ids")
    selected_scope = summary.get("selected_scope_id")
    if summary.get("scope_layout") == "legacy":
        if available_scopes != [] or selected_scope is not None:
            raise ScenarioEvidenceError("legacy state has an invalid scope receipt")
    elif summary.get("scope_layout") == "selected":
        if (
            not isinstance(available_scopes, list)
            or len(available_scopes) != 1
            or type(available_scopes[0]) is not int
            or available_scopes[0] <= 0
            or selected_scope != available_scopes[0]
        ):
            raise ScenarioEvidenceError("selected state has an invalid scope receipt")
    else:
        raise ScenarioEvidenceError("extract summary has an unsupported scope layout")

    actual_names: list[str] = []
    for entry in state_dir.iterdir():
        try:
            require_regular_file(entry)
        except (OSError, ValueError):
            raise ScenarioEvidenceError(f"state inventory contains unsafe entry {entry.name}")
        actual_names.append(entry.name)
    expected_names = ["extract-summary.json", *files]
    if sorted(actual_names) != sorted(expected_names):
        raise ScenarioEvidenceError("extract summary does not describe the complete state directory")

    contents: dict[str, bytes] = {}
    entries: list[dict[str, object]] = []
    for name in files:
        data = _read_regular_file(
            state_dir / name, f"state file {name}", MAX_STATE_FILE_BYTES
        )
        contents[name] = data
        entries.append({"path": name, "bytes": len(data), "sha256": _sha256(data)})
    digest_input = {
        "schema": "sha256-state-inventory-v1",
        "files": entries,
    }
    receipt = {
        **digest_input,
        "extract_summary": {
            "path": "state-extracted/extract-summary.json",
            "bytes": len(summary_data),
            "sha256": _sha256(summary_data),
        },
        "file_count": len(entries),
        "sha256": _binding_sha256(digest_input, "scenario-state-inventory-v1"),
    }
    return receipt, contents


def _sealed_target_inventory(
    target_dir: Path, state_inventory: dict[str, object]
) -> dict[str, object]:
    """Bind every publishable target file and reject scratch/unknown paths."""
    entries = state_inventory.get("files")
    if not isinstance(entries, list):
        raise ScenarioEvidenceError("state inventory cannot seed the sealed inventory")
    state_names = {
        str(entry["path"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    allowed = {
        "actions.json",
        CHALLENGE_RECEIPT_NAME,
        "runner-summary.json",
        "ucore-build.log",
        "ucore-run.log",
        "ucore-run-summary.json",
        "artifacts/kernel",
        "artifacts/image_input",
        "artifacts/image_final",
        "host-input/rp_host_action_seed",
        "state-extracted/extract-summary.json",
        *{f"state-extracted/{name}" for name in state_names},
        *{f"state-next/{name}" for name in state_names},
        "state-next/rp_host_run_result",
    }
    allowed_directories = {
        "artifacts", "host-input", "state-extracted", "state-next"
    }
    target_dir = _require_directory(target_dir, "scenario target directory")
    try:
        directories, files = walk_directory_tree_no_links(
            target_dir,
            max_files=len(allowed),
            max_directories=len(allowed_directories) + 1,
            max_total_bytes=(3 * MAX_RUNTIME_ARTIFACT_BYTES) + MAX_LOG_BYTES,
            max_depth=2,
        )
    except (OSError, ValueError) as error:
        raise ScenarioEvidenceError("scenario target inventory is unsafe") from error
    for directory in directories:
        relative_base = directory.relative_to(target_dir).as_posix()
        if relative_base != "." and relative_base not in allowed_directories:
            raise ScenarioEvidenceError(
                f"scenario target contains an unknown directory: {relative_base}"
            )
    actual: list[str] = []
    for path in files:
        relative = path.relative_to(target_dir).as_posix()
        if relative not in allowed:
            raise ScenarioEvidenceError(
                f"scenario target contains an unknown or temporary file: {relative}"
            )
        actual.append(relative)
    actual.sort()
    records: list[dict[str, object]] = []
    for relative in actual:
        path = target_dir / relative
        size = path.stat().st_size
        maximum = (
            MAX_RUNTIME_ARTIFACT_BYTES
            if relative.startswith("artifacts/")
            else MAX_LOG_BYTES
            if relative in {"ucore-build.log", "ucore-run.log"}
            else MAX_STATE_FILE_BYTES
            if relative.startswith(("state-extracted/", "state-next/", "host-input/"))
            else MAX_JSON_BYTES
        )
        if size > maximum:
            raise ScenarioEvidenceError(
                f"scenario sealed member exceeds its byte limit: {relative}"
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        records.append({
            "path": relative,
            "bytes": size,
            "sha256": digest.hexdigest(),
        })
    body = {
        "schema": "scenario-sealed-inventory-v1",
        "files": records,
        "file_count": len(records),
    }
    return {
        **body,
        "sha256": _binding_sha256(body, "scenario-sealed-inventory-v1"),
    }


def _state_lines(contents: dict[str, bytes], file_name: str) -> list[str]:
    data = contents.get(file_name)
    if data is None:
        raise ScenarioEvidenceError(f"outcome source is missing: {file_name}")
    if not data or not data.endswith(b"\n") or b"\r" in data or b"\0" in data:
        raise ScenarioEvidenceError(f"outcome source is non-canonical: {file_name}")
    try:
        lines = data[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeDecodeError as error:
        raise ScenarioEvidenceError(f"outcome source is not ASCII: {file_name}") from error
    if any(not line for line in lines):
        raise ScenarioEvidenceError(f"outcome source contains an empty record: {file_name}")
    return lines


def _agentos_challenge_oracle(challenge: str) -> dict[str, object]:
    values = derive_challenge(challenge)
    suffix = str(int(challenge[3:]))
    request_id = 1_000_000_000_000 + int(suffix) * 4
    return {
        "workflow_id": values.workflow_id,
        "workflow_run_id": values.run_id,
        "rerun_id": values.rerun_id,
        "input_sha256": values.input_sha256,
        "derived_sha256": values.derived_sha256,
        "kernel_run_id": f"r{suffix}",
        "echo_payload": f"wf:{suffix}",
        "request_id": request_id,
        "followup_request_id": request_id + 1,
        "target_physical": f"a{suffix}",
        "target_summary": f"input-{values.input_sha256}",
    }


def _resource_stability_mix(hash_value: int, value: int) -> int:
    """Mirror the Guest's fixed-width, little-endian report hash step."""

    for _ in range(8):
        hash_value ^= value & 0xFF
        hash_value = (
            hash_value * RESOURCE_STABILITY_FNV_PRIME
        ) & UINT64_MAX
        value >>= 8
    return hash_value


def _resource_stability_nonce(challenge: str, workflow_index: int, mode: str) -> int:
    mode_value = {"load": 1, "terminal": 2}.get(mode)
    if mode_value is None:
        raise ScenarioEvidenceError("agentos resource stability mode is invalid")
    request_id = _agentos_challenge_oracle(challenge)["request_id"]
    if type(request_id) is not int:
        raise ScenarioEvidenceError("agentos challenge request id is invalid")
    hash_value = RESOURCE_STABILITY_FNV_OFFSET
    for value in (
        RESOURCE_STABILITY_REPORT_MAGIC,
        RESOURCE_STABILITY_REPORT_VERSION,
        request_id,
        workflow_index,
        mode_value,
    ):
        hash_value = _resource_stability_mix(hash_value, value)
    return hash_value or 1


def _resource_stability_report_guard(record: dict[str, object]) -> int:
    mode_value = {"load": 1, "terminal": 2}.get(record.get("mode"))
    if mode_value is None:
        raise ScenarioEvidenceError("agentos resource stability mode is invalid")
    hash_value = RESOURCE_STABILITY_FNV_OFFSET
    values = (
        RESOURCE_STABILITY_REPORT_MAGIC,
        RESOURCE_STABILITY_REPORT_VERSION,
        RESOURCE_STABILITY_REPORT_SIZE,
        record.get("workflow_index"),
        mode_value,
        *(record.get(key) for key in RESOURCE_STABILITY_RECORD_PREFIX_KEYS[2:-1]),
    )
    for value in values:
        if type(value) is not int or value < 0 or value > UINT64_MAX:
            raise ScenarioEvidenceError(
                "agentos resource stability report guard input is invalid"
            )
        hash_value = _resource_stability_mix(hash_value, value)
    return hash_value


def _resource_stability_plateau_or_reclamation(
    workflows: Sequence[dict[str, object]], kind: str
) -> bool:
    def after_total(workflow: dict[str, object]) -> int:
        ordinary = workflow[f"{kind}_ordinary_used_after"]
        reserved = workflow[f"{kind}_reserved_used_after"]
        if type(ordinary) is not int or type(reserved) is not int:
            raise ScenarioEvidenceError(
                "agentos resource stability sequence counter is invalid"
            )
        return ordinary + reserved

    load_workflows = workflows[:RESOURCE_STABILITY_LOAD_WORKFLOWS]
    if len(load_workflows) != RESOURCE_STABILITY_LOAD_WORKFLOWS:
        return False
    load_plateau = any(
        after_total(current) <= after_total(prior)
        for prior, current in zip(load_workflows, load_workflows[1:])
    )
    terminal_reclamation = (
        len(workflows) == RESOURCE_STABILITY_WORKFLOWS
        and after_total(workflows[-1]) < after_total(load_workflows[-1])
    )
    return load_plateau or terminal_reclamation


def _validate_agentos_acceptance_semantics(
    acceptance: object, challenge: str
) -> None:
    if not isinstance(acceptance, dict) or set(acceptance) != {
        "schema",
        "challenge_binding",
        "required_modules",
        "modules",
    }:
        raise ScenarioEvidenceError(
            "agentos functional acceptance does not verify every required module"
        )
    binding = acceptance["challenge_binding"]
    modules = acceptance["modules"]
    if (
        acceptance["schema"] != "agentos_task6_acceptance_v3"
        or acceptance["required_modules"] != list(REQUIRED_AGENTOS_MODULES)
        or not isinstance(binding, dict)
        or not isinstance(modules, list)
        or len(modules) != len(REQUIRED_AGENTOS_MODULES)
    ):
        raise ScenarioEvidenceError(
            "agentos functional acceptance does not verify every required module"
        )
    oracle = _agentos_challenge_oracle(challenge)
    if binding != {
        "workflow_id": oracle["workflow_id"],
        "workflow_run_id": oracle["workflow_run_id"],
        "input_sha256": oracle["input_sha256"],
        "derived_sha256": oracle["derived_sha256"],
        "workflow_outputs": "verified",
    }:
        raise ScenarioEvidenceError(
            "agentos functional acceptance header does not match the challenge oracle"
        )

    field_specs = (
        (
            "context",
            {
                "module", "operation", "status", "records", "latest_sequence",
                "request_id", "tool_id", "record_sequence", "record_hash",
                "payload", "result", "followup_sequence", "followup_record_hash",
            },
            {
                "records", "latest_sequence", "request_id", "tool_id",
                "record_sequence", "record_hash", "followup_sequence",
                "followup_record_hash",
            },
        ),
        (
            "structured_tool",
            {
                "module", "operation", "status", "request_id", "tool_id",
                "request_payload", "arg0", "arg1", "result_version",
                "result_status", "result_tool_id", "result_request_id",
                "result_payload", "result_value0", "result_value1",
                "result_value2", "result_sequence",
            },
            {
                "request_id", "tool_id", "arg0", "arg1", "result_version",
                "result_status", "result_tool_id", "result_request_id",
                "result_value0", "result_value1", "result_value2",
                "result_sequence",
            },
        ),
        (
            "metadata_query",
            {
                "module", "operation", "status", "project", "workflow_id",
                "workflow_run_id", "kernel_run_id", "input_sha256",
                "derived_sha256", "stage", "returned", "used_index", "plan",
                "target_fid", "target_physical", "target_stage", "target_kind",
                "target_status", "target_summary",
            },
            {"returned", "used_index", "plan", "target_fid"},
        ),
        (
            "observation",
            {
                "module", "operation", "status", "timeline_records",
                "provenance_edges", "ledger_records", "ledger_hash", "edge_kind",
                "edge_tool_id", "edge_status", "source_sequence", "target_sequence",
                "source_record_hash", "target_record_hash", "request_id",
                "workflow_id",
            },
            {
                "timeline_records", "provenance_edges", "ledger_records",
                "ledger_hash", "edge_kind", "edge_tool_id", "edge_status",
                "source_sequence", "target_sequence", "source_record_hash",
                "target_record_hash", "request_id",
            },
        ),
    )
    for module, (name, fields, numeric_fields) in zip(modules, field_specs):
        if (
            not isinstance(module, dict)
            or set(module) != fields
            or module.get("module") != name
            or module.get("status") != "verified"
        ):
            raise ScenarioEvidenceError(
                "agentos functional acceptance does not verify every required module"
            )
        for key in numeric_fields:
            value = module[key]
            if type(value) is not int or value < 0 or value > UINT64_MAX:
                raise ScenarioEvidenceError(
                    f"agentos {name} {key} is outside the Guest uint64 range"
                )

    context, tool, metadata, observation = modules
    if (
        context["operation"] != "context_snapshot"
        or context["records"] < 2
        or context["records"] > AGENTOS_CONTEXT_RECEIPT_MAX
        or context["request_id"] != oracle["request_id"]
        or context["tool_id"] != 1
        or context["record_sequence"] < 1
        or context["record_hash"] < 1
        or context["payload"] != oracle["echo_payload"]
        or context["result"] != oracle["echo_payload"]
        or context["followup_sequence"] <= context["record_sequence"]
        or context["followup_record_hash"] < 1
        or context["latest_sequence"] < context["followup_sequence"]
    ):
        raise ScenarioEvidenceError(
            "agentos context receipt does not match the challenge-derived echo record"
        )
    if (
        tool["operation"] != "agent_run_echo"
        or tool["request_id"] != oracle["request_id"]
        or tool["tool_id"] != 1
        or tool["request_payload"] != oracle["echo_payload"]
        or tool["arg0"] != oracle["request_id"]
        or tool["arg1"] != oracle["followup_request_id"]
        or tool["result_version"] != 1
        or tool["result_status"] != 0
        or tool["result_tool_id"] != 1
        or tool["result_request_id"] != oracle["request_id"]
        or tool["result_payload"] != oracle["echo_payload"]
        or tool["result_value0"] != len(str(oracle["echo_payload"]))
        or tool["result_value1"] != oracle["request_id"]
        or tool["result_value2"] != oracle["followup_request_id"]
        or tool["result_sequence"] < 1
    ):
        raise ScenarioEvidenceError(
            "agentos structured tool receipt does not prove echo semantics"
        )
    if context["record_sequence"] != tool["result_sequence"]:
        raise ScenarioEvidenceError(
            "agentos context receipt does not match the tool response"
        )
    if (
        metadata["operation"] != "file_query_stage_index"
        or metadata["project"] != "lab-gene-x"
        or metadata["workflow_id"] != oracle["workflow_id"]
        or metadata["workflow_run_id"] != oracle["workflow_run_id"]
        or metadata["kernel_run_id"] != oracle["kernel_run_id"]
        or metadata["input_sha256"] != oracle["input_sha256"]
        or metadata["derived_sha256"] != oracle["derived_sha256"]
        or metadata["stage"] != "align"
        or metadata["returned"] != 1
        or metadata["used_index"] != 1
        or metadata["plan"] != 2
        or metadata["target_fid"] != 101
        or metadata["target_physical"] != oracle["target_physical"]
        or metadata["target_stage"] != "align"
        or metadata["target_kind"] != "artifact"
        or metadata["target_status"] != "ok"
        or metadata["target_summary"] != oracle["target_summary"]
    ):
        raise ScenarioEvidenceError(
            "agentos metadata query receipt does not match the challenge-derived target hit"
        )
    if (
        observation["operation"] != "timeline_provenance_ledger"
        or observation["timeline_records"] < 1
        or observation["timeline_records"] > AGENTOS_TIMELINE_RECEIPT_MAX
        or observation["provenance_edges"] < 1
        or observation["provenance_edges"] > AGENTOS_PROVENANCE_RECEIPT_MAX
        or observation["ledger_records"] < 2
        or observation["ledger_hash"] < 1
        or observation["edge_kind"] != 1
        or observation["edge_tool_id"] != 1
        or observation["edge_status"] != 0
        or observation["source_sequence"] != context["record_sequence"]
        or observation["target_sequence"] != context["followup_sequence"]
        or observation["source_record_hash"] != context["record_hash"]
        or observation["target_record_hash"] != context["followup_record_hash"]
        or observation["request_id"] != oracle["request_id"]
        or observation["workflow_id"] != oracle["workflow_id"]
    ):
        raise ScenarioEvidenceError(
            "agentos observation receipt lacks challenge-bound provenance"
        )


def _parse_agentos_acceptance(
    contents: dict[str, bytes], target: str, challenge: str
) -> tuple[dict[str, object] | None, bytes | None]:
    data = contents.get(AGENTOS_ACCEPTANCE_FILE)
    if target == "plain":
        if data is not None:
            raise ScenarioEvidenceError(
                "plain state must not impersonate AgentOS functional acceptance"
            )
        return None, None
    lines = _state_lines(contents, AGENTOS_ACCEPTANCE_FILE)
    if len(lines) != len(REQUIRED_AGENTOS_MODULES) + 1:
        raise ScenarioEvidenceError(
            "agentos functional acceptance must contain every required module once"
        )
    header = _parse_record(lines[0], "agentos functional acceptance header")
    oracle = _agentos_challenge_oracle(challenge)
    if tuple(header) != (
        "schema",
        "module_count",
        "workflow_id",
        "workflow_run_id",
        "input_sha256",
        "derived_sha256",
        "workflow_outputs",
    ) or header != {
        "schema": "agentos_task6_acceptance_v3",
        "module_count": str(len(REQUIRED_AGENTOS_MODULES)),
        "workflow_id": oracle["workflow_id"],
        "workflow_run_id": oracle["workflow_run_id"],
        "input_sha256": oracle["input_sha256"],
        "derived_sha256": oracle["derived_sha256"],
        "workflow_outputs": "verified",
    }:
        raise ScenarioEvidenceError(
            "agentos functional acceptance header does not match the challenge oracle"
        )

    specs = (
        (
            "context",
            "context_snapshot",
            (
                "module",
                "operation",
                "status",
                "records",
                "latest_sequence",
                "request_id",
                "tool_id",
                "record_sequence",
                "record_hash",
                "payload",
                "result",
                "followup_sequence",
                "followup_record_hash",
            ),
            (
                "records",
                "latest_sequence",
                "request_id",
                "tool_id",
                "record_sequence",
                "record_hash",
                "followup_sequence",
                "followup_record_hash",
            ),
        ),
        (
            "structured_tool",
            "agent_run_echo",
            (
                "module",
                "operation",
                "status",
                "request_id",
                "tool_id",
                "request_payload",
                "arg0",
                "arg1",
                "result_version",
                "result_status",
                "result_tool_id",
                "result_request_id",
                "result_payload",
                "result_value0",
                "result_value1",
                "result_value2",
                "result_sequence",
            ),
            (
                "request_id",
                "tool_id",
                "arg0",
                "arg1",
                "result_version",
                "result_status",
                "result_tool_id",
                "result_request_id",
                "result_value0",
                "result_value1",
                "result_value2",
                "result_sequence",
            ),
        ),
        (
            "metadata_query",
            "file_query_stage_index",
            (
                "module",
                "operation",
                "status",
                "project",
                "workflow_id",
                "workflow_run_id",
                "kernel_run_id",
                "input_sha256",
                "derived_sha256",
                "stage",
                "returned",
                "used_index",
                "plan",
                "target_fid",
                "target_physical",
                "target_stage",
                "target_kind",
                "target_status",
                "target_summary",
            ),
            ("returned", "used_index", "plan", "target_fid"),
        ),
        (
            "observation",
            "timeline_provenance_ledger",
            (
                "module",
                "operation",
                "status",
                "timeline_records",
                "provenance_edges",
                "ledger_records",
                "ledger_hash",
                "edge_kind",
                "edge_tool_id",
                "edge_status",
                "source_sequence",
                "target_sequence",
                "source_record_hash",
                "target_record_hash",
                "request_id",
                "workflow_id",
            ),
            (
                "timeline_records",
                "provenance_edges",
                "ledger_records",
                "ledger_hash",
                "edge_kind",
                "edge_tool_id",
                "edge_status",
                "source_sequence",
                "target_sequence",
                "source_record_hash",
                "target_record_hash",
                "request_id",
            ),
        ),
    )
    modules: list[dict[str, object]] = []
    for line_number, (line, spec) in enumerate(zip(lines[1:], specs), 2):
        module, operation, keys, numeric_keys = spec
        record = _parse_record(line, f"agentos functional acceptance line {line_number}")
        if tuple(record) != keys:
            raise ScenarioEvidenceError(
                f"agentos {module} functional receipt has an invalid schema"
            )
        if (
            record["module"] != module
            or record["operation"] != operation
            or record["status"] != "verified"
        ):
            raise ScenarioEvidenceError(
                f"agentos {module} functional receipt is not verified"
            )
        normalized: dict[str, object] = {
            "module": module,
            "operation": operation,
            "status": "verified",
        }
        for key in keys[3:]:
            normalized[key] = (
                _canonical_uint(record[key], f"agentos {module} {key}")
                if key in numeric_keys
                else record[key]
            )
        modules.append(normalized)

    acceptance = {
        "schema": header["schema"],
        "challenge_binding": {
            "workflow_id": header["workflow_id"],
            "workflow_run_id": header["workflow_run_id"],
            "input_sha256": header["input_sha256"],
            "derived_sha256": header["derived_sha256"],
            "workflow_outputs": header["workflow_outputs"],
        },
        "required_modules": list(REQUIRED_AGENTOS_MODULES),
        "modules": modules,
    }
    _validate_agentos_acceptance_semantics(acceptance, challenge)
    return acceptance, data


def _validate_resource_stability_semantics(
    acceptance: object, challenge: str
) -> None:
    expected_header = {
        "schema",
        "measurement_scope",
        "timed_makespan_included",
        "claim_scope",
        "configured_kind_coverage",
        "account_coverage",
        "rate_budget_coverage",
        "global_leak_freedom",
        "challenge_suffix",
        "load_workflows",
        "terminal_workflows",
        "child_rounds_per_workflow",
        "memory_pages_per_round",
        "file_objects_per_round",
        "metadata_ops_per_round",
        "sequence_bound_status",
        "status",
        "global_policy",
        "workflows",
    }
    if not isinstance(acceptance, dict) or set(acceptance) != expected_header:
        raise ScenarioEvidenceError(
            "agentos resource stability receipt has an invalid schema"
        )
    suffix = str(int(challenge[3:]))
    if (
        acceptance["schema"] != "agentos_resource_stability_v3"
        or acceptance["measurement_scope"] != RESOURCE_STABILITY_MEASUREMENT_SCOPE
        or acceptance["timed_makespan_included"] is not False
        or acceptance["claim_scope"] != "configured_global_counter_reclamation"
        or acceptance["configured_kind_coverage"] != "measured_mask_only"
        or acceptance["account_coverage"] != "self_identity_only"
        or acceptance["rate_budget_coverage"] != "not_measured"
        or acceptance["global_leak_freedom"] != "not_claimed"
        or acceptance["challenge_suffix"] != suffix
        or acceptance["load_workflows"] != RESOURCE_STABILITY_LOAD_WORKFLOWS
        or acceptance["terminal_workflows"]
        != RESOURCE_STABILITY_TERMINAL_WORKFLOWS
        or acceptance["child_rounds_per_workflow"]
        != RESOURCE_STABILITY_CHILD_ROUNDS
        or acceptance["memory_pages_per_round"]
        != RESOURCE_STABILITY_MEMORY_PAGES
        or acceptance["file_objects_per_round"]
        != RESOURCE_STABILITY_FILE_OBJECTS
        or acceptance["metadata_ops_per_round"]
        != RESOURCE_STABILITY_METADATA_OPS
        or acceptance["sequence_bound_status"] != "verified"
        or not isinstance(acceptance["workflows"], list)
        or len(acceptance["workflows"]) != RESOURCE_STABILITY_WORKFLOWS
    ):
        raise ScenarioEvidenceError(
            "agentos resource stability receipt does not cover the registered workload"
        )

    policy = acceptance["global_policy"]
    if not isinstance(policy, dict) or tuple(policy) != (
        "measured_mask",
        "measured_mask_semantics",
        "snapshot_consistency",
        "coverage",
        "account_counter_coverage",
        "rate_budget_coverage",
        "free_pages_status",
        "terminal_workflow_pair_bound",
        "resources",
    ):
        raise ScenarioEvidenceError(
            "agentos resource stability global policy has an invalid schema"
        )
    measured_mask = policy["measured_mask"]
    if (
        type(measured_mask) is not int
        or measured_mask < 0
        or measured_mask > (1 << len(RESOURCE_STABILITY_RESOURCE_KINDS)) - 1
        or policy["measured_mask_semantics"]
        != "configured_global_resource_kind_counters_only"
        or policy["snapshot_consistency"] != "single_core_irq_coherent"
        or policy["coverage"] != "configured_global_kind_counters"
        or policy["account_counter_coverage"] != "not_measured"
        or policy["rate_budget_coverage"] != "not_measured"
        or policy["free_pages_status"] != "measured"
        or policy["terminal_workflow_pair_bound"] != 0
        or not isinstance(policy["resources"], list)
        or len(policy["resources"]) != len(RESOURCE_STABILITY_RESOURCE_KINDS)
    ):
        raise ScenarioEvidenceError(
            "agentos resource stability global policy is invalid"
        )
    for index, (kind, bound) in enumerate(RESOURCE_STABILITY_GROWTH_BOUNDS.items()):
        resource = policy["resources"][index]
        measured = (measured_mask & (1 << index)) != 0
        if (
            not isinstance(resource, dict)
            or tuple(resource)
            != (
                "kind",
                "status",
                "capacity",
                "per_workflow_growth_bound",
                "terminal_growth_bound",
            )
            or resource["kind"] != kind
            or resource["status"] != ("measured" if measured else "not_measured")
            or resource["per_workflow_growth_bound"] != bound
            or resource["terminal_growth_bound"] != bound
            or type(resource["capacity"]) is not int
            or (measured and resource["capacity"] <= 0)
            or (not measured and resource["capacity"] != 0)
        ):
            raise ScenarioEvidenceError(
                "agentos resource stability global policy resource is invalid"
            )
    fully_measured = measured_mask == (1 << len(RESOURCE_STABILITY_RESOURCE_KINDS)) - 1
    expected_status = "verified" if fully_measured else "partial"
    if acceptance["status"] != expected_status:
        raise ScenarioEvidenceError(
            "agentos resource stability cannot pass unmeasured resources"
        )

    lifecycle_keys: set[tuple[int, int]] = set()
    scope_ids: set[int] = set()
    io_owners: set[int] = set()
    account_handles: set[tuple[int, int]] = set()
    zero_fields = (
        "initial_leased",
        "initial_debt",
        "initial_waiters",
        "initial_debt_waiters",
        "initial_admission_waiters",
        "initial_context_lane_depth",
        "initial_context_lane_waiters",
        "initial_metadata_owned",
        "initial_metadata_waiters",
        "initial_agent_calls",
        "initial_context_records",
        "final_leased",
        "final_debt",
        "final_waiters",
        "final_debt_waiters",
        "final_admission_waiters",
        "final_context_lane_depth",
        "final_context_lane_waiters",
        "final_metadata_owned",
        "final_metadata_waiters",
    )
    for index, raw_record in enumerate(acceptance["workflows"]):
        if (
            not isinstance(raw_record, dict)
            or tuple(raw_record) != RESOURCE_STABILITY_RECORD_KEYS
        ):
            raise ScenarioEvidenceError(
                "agentos resource stability workflow record has an invalid schema"
            )
        record = raw_record
        if any(
            type(record[key]) is not int
            or record[key] < 0
            or record[key] > UINT64_MAX
            for key in RESOURCE_STABILITY_RECORD_KEYS
            if key not in {"mode", "per_workflow_bound_status", "status"}
        ):
            raise ScenarioEvidenceError(
                "agentos resource stability workflow contains an invalid integer"
            )
        expected_mode = (
            "load" if index < RESOURCE_STABILITY_LOAD_WORKFLOWS else "terminal"
        )
        expected_rounds = (
            RESOURCE_STABILITY_CHILD_ROUNDS if expected_mode == "load" else 0
        )
        lifecycle_key = (record["lifecycle_id"], record["lifecycle_generation"])
        account_handle = (
            record["resource_account_slot"],
            record["resource_account_generation"],
        )
        expected_nonce = _resource_stability_nonce(challenge, index, expected_mode)
        if (
            record["workflow_index"] != index
            or record["mode"] != expected_mode
            or record["status"] != "verified"
            or record["challenge_nonce"] != expected_nonce
            or record["report_guard"]
            != _resource_stability_report_guard(record)
            or record["lifecycle_id"] <= 0
            or record["lifecycle_generation"] <= 0
            or record["scope_id"] <= 0
            or record["io_owner"] != (0x80000000 | record["scope_id"])
            or record["resource_account_reserved"] != 0
            or record["resource_account_generation"] <= 0
            or any(record[field] != 0 for field in zero_fields)
            or record["final_agent_calls"] != record["initial_agent_calls"]
            or record["final_context_records"]
            != record["initial_context_records"]
            or record["process_rounds"] != expected_rounds
            or record["file_rounds"] != expected_rounds
            or record["memory_rounds"] != expected_rounds
            or record["metadata_rounds"] != expected_rounds
            or record["ordinary_free_pages_before"]
            != record["ordinary_free_pages_after"]
            or record["reserved_free_pages_before"]
            != record["reserved_free_pages_after"]
            or record["stack_reserved_free_pages_before"]
            != record["stack_reserved_free_pages_after"]
            or record["per_workflow_bound_status"]
            != ("verified" if fully_measured else "not_measured")
            or record["reaped"] != 1
            or record["pipe_eof"] != 1
            or (
                expected_mode == "load"
                and record["final_completion_sequence"]
                <= record["initial_completion_sequence"]
            )
            or (
                expected_mode == "terminal"
                and record["final_completion_sequence"]
                != record["initial_completion_sequence"]
            )
            or (
                expected_mode == "terminal"
                and record["final_cache_resident"]
                != record["initial_cache_resident"]
            )
            or lifecycle_key in lifecycle_keys
            or record["scope_id"] in scope_ids
            or record["io_owner"] in io_owners
            or account_handle in account_handles
        ):
            raise ScenarioEvidenceError(
                "agentos resource stability workflow is not challenge-bound, fresh, and quiescent"
            )
        for resource_index, resource in enumerate(policy["resources"]):
            kind = resource["kind"]
            ordinary_before = record[f"{kind}_ordinary_used_before"]
            ordinary_after = record[f"{kind}_ordinary_used_after"]
            reserved_before = record[f"{kind}_reserved_used_before"]
            reserved_after = record[f"{kind}_reserved_used_after"]
            ordinary_pending_before = record[
                f"{kind}_ordinary_pending_before"
            ]
            ordinary_pending_after = record[f"{kind}_ordinary_pending_after"]
            reserved_pending_before = record[
                f"{kind}_reserved_pending_before"
            ]
            reserved_pending_after = record[f"{kind}_reserved_pending_after"]
            values = (
                ordinary_before,
                ordinary_after,
                ordinary_pending_before,
                ordinary_pending_after,
                reserved_before,
                reserved_after,
                reserved_pending_before,
                reserved_pending_after,
            )
            measured = (measured_mask & (1 << resource_index)) != 0
            if not measured:
                if any(values):
                    raise ScenarioEvidenceError(
                        "unmeasured global resource contains fabricated values"
                    )
                continue
            capacity = resource["capacity"]
            before = ordinary_before + reserved_before
            after = ordinary_after + reserved_after
            pending_before = ordinary_pending_before + reserved_pending_before
            pending_after = ordinary_pending_after + reserved_pending_after
            bound = resource["per_workflow_growth_bound"]
            if (
                before > capacity
                or after > capacity
                or pending_before > capacity - before
                or pending_after > capacity - after
                or any(
                    (
                        ordinary_pending_before,
                        ordinary_pending_after,
                        reserved_pending_before,
                        reserved_pending_after,
                    )
                )
                or (
                    (expected_mode == "terminal" or bound == 0)
                    and (
                        ordinary_after != ordinary_before
                        or reserved_after != reserved_before
                    )
                )
                or (
                    expected_mode == "load"
                    and bound != 0
                    and (
                        ordinary_after < ordinary_before
                        or reserved_after < reserved_before
                        or ordinary_after
                        - ordinary_before
                        + reserved_after
                        - reserved_before
                        > bound
                    )
                )
            ):
                raise ScenarioEvidenceError(
                    "agentos configured global resource delta exceeds its registered bound"
                )
        lifecycle_keys.add(lifecycle_key)
        scope_ids.add(record["scope_id"])
        io_owners.add(record["io_owner"])
        account_handles.add(account_handle)

    first = acceptance["workflows"][0]
    terminal = acceptance["workflows"][-1]
    if any(
        first[f"{prefix}_before"] != terminal[f"{prefix}_after"]
        for prefix in (
            "ordinary_free_pages",
            "reserved_free_pages",
            "stack_reserved_free_pages",
        )
    ):
        raise ScenarioEvidenceError(
            "agentos resource stability free-page sequence does not recover"
        )
    for resource_index, resource in enumerate(policy["resources"]):
        if (measured_mask & (1 << resource_index)) == 0:
            continue
        kind = resource["kind"]
        bound = resource["terminal_growth_bound"]
        ordinary_before = first[f"{kind}_ordinary_used_before"]
        ordinary_after = terminal[f"{kind}_ordinary_used_after"]
        reserved_before = first[f"{kind}_reserved_used_before"]
        reserved_after = terminal[f"{kind}_reserved_used_after"]
        if (
            (bound == 0 and (
                ordinary_after != ordinary_before
                or reserved_after != reserved_before
            ))
            or (
                bound != 0
                and (
                    ordinary_after < ordinary_before
                    or reserved_after < reserved_before
                    or ordinary_after
                    - ordinary_before
                    + reserved_after
                    - reserved_before
                    > bound
                )
            )
        ):
            raise ScenarioEvidenceError(
                "agentos configured global resource terminal growth exceeds its registered bound"
            )
        if bound != 0 and not _resource_stability_plateau_or_reclamation(
            acceptance["workflows"], kind
        ):
            raise ScenarioEvidenceError(
                "agentos configured global resource sequence lacks plateau or reclamation"
            )


def _parse_resource_stability(
    contents: dict[str, bytes], target: str, challenge: str
) -> tuple[dict[str, object] | None, bytes | None]:
    data = contents.get(RESOURCE_STABILITY_FILE)
    if target == "plain":
        if data is not None:
            raise ScenarioEvidenceError(
                "plain state must not impersonate AgentOS resource stability"
            )
        return None, None
    lines = _state_lines(contents, RESOURCE_STABILITY_FILE)
    if any(len(line) > RESOURCE_STABILITY_LINE_MAX for line in lines):
        raise ScenarioEvidenceError(
            "agentos resource stability receipt contains an overlong record"
        )
    if len(lines) != RESOURCE_STABILITY_WORKFLOWS + 2:
        raise ScenarioEvidenceError(
            "agentos resource stability receipt has an invalid workflow count"
        )
    header = _parse_record(lines[0], "agentos resource stability header")
    header_keys = (
        "schema",
        "measurement_scope",
        "timed_makespan_included",
        "claim_scope",
        "configured_kind_coverage",
        "account_coverage",
        "rate_budget_coverage",
        "global_leak_freedom",
        "challenge_suffix",
        "load_workflows",
        "terminal_workflows",
        "child_rounds_per_workflow",
        "memory_pages_per_round",
        "file_objects_per_round",
        "metadata_ops_per_round",
        "sequence_bound_status",
        "status",
    )
    if tuple(header) != header_keys:
        raise ScenarioEvidenceError(
            "agentos resource stability header has an invalid schema"
        )
    numeric_header_keys = (
        "timed_makespan_included",
        "load_workflows",
        "terminal_workflows",
        "child_rounds_per_workflow",
        "memory_pages_per_round",
        "file_objects_per_round",
        "metadata_ops_per_round",
    )
    numeric_header = {
        key: _canonical_uint(header[key], f"agentos resource stability {key}")
        for key in numeric_header_keys
    }
    policy_record = _parse_record(lines[1], "agentos resource stability global policy")
    policy_keys = (
        "record",
        "measured_mask",
        "measured_mask_semantics",
        "snapshot_consistency",
        "coverage",
        "account_counter_coverage",
        "rate_budget_coverage",
        "free_pages_status",
        "terminal_workflow_pair_bound",
        *(
            key
            for kind in RESOURCE_STABILITY_RESOURCE_KINDS
            for key in (
                f"{kind}_status",
                f"{kind}_capacity",
                f"{kind}_per_workflow_growth_bound",
                f"{kind}_terminal_growth_bound",
            )
        ),
    )
    if tuple(policy_record) != policy_keys or policy_record["record"] != "global_policy":
        raise ScenarioEvidenceError(
            "agentos resource stability global policy has an invalid schema"
        )
    measured_mask = _canonical_uint(
        policy_record["measured_mask"], "agentos resource measured mask"
    )
    terminal_workflow_pair_bound = _canonical_uint(
        policy_record["terminal_workflow_pair_bound"],
        "agentos resource terminal workflow pair bound",
    )
    policy_resources: list[dict[str, object]] = []
    for kind in RESOURCE_STABILITY_RESOURCE_KINDS:
        policy_resources.append(
            {
                "kind": kind,
                "status": policy_record[f"{kind}_status"],
                "capacity": _canonical_uint(
                    policy_record[f"{kind}_capacity"],
                    f"agentos {kind} capacity",
                ),
                "per_workflow_growth_bound": _canonical_uint(
                    policy_record[f"{kind}_per_workflow_growth_bound"],
                    f"agentos {kind} per-workflow growth bound",
                ),
                "terminal_growth_bound": _canonical_uint(
                    policy_record[f"{kind}_terminal_growth_bound"],
                    f"agentos {kind} terminal growth bound",
                ),
            }
        )
    uint64_fields = {
        "challenge_nonce",
        "lifecycle_generation",
        "resource_account_generation",
        "initial_agent_calls",
        "initial_context_records",
        "final_agent_calls",
        "final_context_records",
        "initial_completion_sequence",
        "final_completion_sequence",
        "report_guard",
    }
    workflows: list[dict[str, object]] = []
    for line_number, line in enumerate(lines[2:], 3):
        record = _parse_record(
            line, f"agentos resource stability workflow line {line_number}"
        )
        if tuple(record) != RESOURCE_STABILITY_RECORD_KEYS:
            raise ScenarioEvidenceError(
                "agentos resource stability workflow record has an invalid schema"
            )
        normalized: dict[str, object] = {}
        for key in RESOURCE_STABILITY_RECORD_KEYS:
            if key in {"mode", "per_workflow_bound_status", "status"}:
                normalized[key] = record[key]
                continue
            value = _canonical_uint(
                record[key], f"agentos resource stability {key}"
            )
            if (
                key not in uint64_fields
                and key not in RESOURCE_STABILITY_GLOBAL_FREE_KEYS
                and key not in RESOURCE_STABILITY_GLOBAL_RESOURCE_KEYS
                and value > UINT32_MAX
            ):
                raise ScenarioEvidenceError(
                    f"agentos resource stability {key} exceeds the Guest uint32 range"
                )
            normalized[key] = value
        workflows.append(normalized)
    acceptance = {
        "schema": header["schema"],
        "measurement_scope": header["measurement_scope"],
        "timed_makespan_included": numeric_header["timed_makespan_included"] != 0,
        "claim_scope": header["claim_scope"],
        "configured_kind_coverage": header["configured_kind_coverage"],
        "account_coverage": header["account_coverage"],
        "rate_budget_coverage": header["rate_budget_coverage"],
        "global_leak_freedom": header["global_leak_freedom"],
        "challenge_suffix": header["challenge_suffix"],
        "load_workflows": numeric_header["load_workflows"],
        "terminal_workflows": numeric_header["terminal_workflows"],
        "child_rounds_per_workflow": numeric_header[
            "child_rounds_per_workflow"
        ],
        "memory_pages_per_round": numeric_header["memory_pages_per_round"],
        "file_objects_per_round": numeric_header["file_objects_per_round"],
        "metadata_ops_per_round": numeric_header["metadata_ops_per_round"],
        "sequence_bound_status": header["sequence_bound_status"],
        "status": header["status"],
        "global_policy": {
            "measured_mask": measured_mask,
            "measured_mask_semantics": policy_record["measured_mask_semantics"],
            "snapshot_consistency": policy_record["snapshot_consistency"],
            "coverage": policy_record["coverage"],
            "account_counter_coverage": policy_record[
                "account_counter_coverage"
            ],
            "rate_budget_coverage": policy_record["rate_budget_coverage"],
            "free_pages_status": policy_record["free_pages_status"],
            "terminal_workflow_pair_bound": terminal_workflow_pair_bound,
            "resources": policy_resources,
        },
        "workflows": workflows,
    }
    _validate_resource_stability_semantics(acceptance, challenge)
    return acceptance, data


def _unique_outcome_record(
    contents: dict[str, bytes], file_name: str, anchor: str, keys: tuple[str, ...]
) -> dict[str, str]:
    matches: list[dict[str, str]] = []
    for line_number, line in enumerate(_state_lines(contents, file_name), 1):
        if not line.startswith(anchor + "="):
            continue
        record = _parse_record(line, f"{file_name} line {line_number}")
        if tuple(record) != keys:
            raise ScenarioEvidenceError(f"{file_name} outcome {anchor} has an invalid schema")
        matches.append(record)
    if len(matches) != 1:
        raise ScenarioEvidenceError(
            f"{file_name} outcome {anchor} must occur exactly once, found {len(matches)}"
        )
    return matches[0]


def _normalized_outcome(contents: dict[str, bytes]) -> tuple[dict[str, object], str]:
    outcome: dict[str, object] = {}
    for label, file_name, anchor, keys in OUTCOME_RECORD_SPECS:
        outcome[label] = _unique_outcome_record(contents, file_name, anchor, keys)

    llm: dict[str, str] = {}
    for key in LLM_OUTCOME_KEYS:
        record = _unique_outcome_record(contents, "rp_llm_resp", key, (key,))
        llm[key] = record[key]
    outcome["llm_response"] = llm
    fingerprint = _binding_sha256(outcome, "research-platform-outcome-v1")
    return outcome, fingerprint


def _task6_artifact_provenance(
    contents: dict[str, bytes], challenge: str
) -> dict[str, object]:
    """Bind Guest-reported provenance to the extracted artifact bytes."""

    values = derive_challenge(challenge)
    expected_input, expected_output = task6_artifact_payloads(challenge)
    input_data = contents.get(TASK6_ARTIFACT_INPUT_STORAGE)
    output_data = contents.get(TASK6_ARTIFACT_OUTPUT_STORAGE)
    if input_data is None or output_data is None:
        raise ScenarioEvidenceError("Task6 artifact byte files are missing")
    if input_data != expected_input:
        raise ScenarioEvidenceError(
            "Task6 input artifact bytes do not match the Host challenge workload"
        )
    if output_data != expected_output:
        raise ScenarioEvidenceError(
            "Task6 derived artifact bytes do not match the registered transformation"
        )

    guest = _unique_outcome_record(
        contents,
        "rp_artifact",
        "task6_artifact_receipt",
        (
            "task6_artifact_receipt",
            "challenge",
            "input_storage",
            "input_bytes",
            "input_fnv64",
            "input_sha256",
            "output_storage",
            "output_bytes",
            "output_fnv64",
            "output_sha256",
            "operation",
        ),
    )
    expected_guest = {
        "task6_artifact_receipt": TASK6_ARTIFACT_RECEIPT_SCHEMA,
        "challenge": challenge,
        "input_storage": TASK6_ARTIFACT_INPUT_STORAGE,
        "input_bytes": str(len(input_data)),
        "input_fnv64": str(task6_fnv64(input_data)),
        "input_sha256": hashlib.sha256(input_data).hexdigest(),
        "output_storage": TASK6_ARTIFACT_OUTPUT_STORAGE,
        "output_bytes": str(len(output_data)),
        "output_fnv64": str(task6_fnv64(output_data)),
        "output_sha256": hashlib.sha256(output_data).hexdigest(),
        "operation": "normalize_ppm",
    }
    if guest != expected_guest:
        raise ScenarioEvidenceError(
            "Task6 Guest artifact receipt does not match the extracted bytes"
        )
    if (
        expected_guest["input_sha256"] != values.input_sha256
        or expected_guest["output_sha256"] != values.derived_sha256
    ):
        raise ScenarioEvidenceError(
            "Task6 artifact bytes do not match the challenge-bound action hashes"
        )
    body: dict[str, object] = {
        "schema": "task6-artifact-provenance-v1",
        "challenge": challenge,
        "operation": "normalize_ppm",
        "input": {
            "path": f"state-extracted/{TASK6_ARTIFACT_INPUT_STORAGE}",
            "logical_name": "raw-counts.csv",
            "bytes": len(input_data),
            "sha256": values.input_sha256,
            "fnv64": values.input_fnv64,
        },
        "output": {
            "path": f"state-extracted/{TASK6_ARTIFACT_OUTPUT_STORAGE}",
            "logical_name": "normalized-counts.csv",
            "bytes": len(output_data),
            "sha256": values.derived_sha256,
            "fnv64": values.derived_fnv64,
        },
        "guest_receipt": guest,
    }
    return {
        **body,
        "sha256": _binding_sha256(body, "task6-artifact-provenance-v1"),
    }


def validate_task6_artifact_provenance(
    value: object, challenge: str, corpus_path: Path
) -> None:
    """Replay a sealed Task6 receipt against an explicitly supplied C snapshot."""

    try:
        input_data, output_data = task6_artifact_payloads(
            challenge, fixture_path=corpus_path
        )
    except ValueError as error:
        raise ScenarioEvidenceError(
            f"Task6 source-C workload corpus is invalid: {error}"
        ) from error
    input_sha = hashlib.sha256(input_data).hexdigest()
    output_sha = hashlib.sha256(output_data).hexdigest()
    guest = {
        "task6_artifact_receipt": TASK6_ARTIFACT_RECEIPT_SCHEMA,
        "challenge": challenge,
        "input_storage": TASK6_ARTIFACT_INPUT_STORAGE,
        "input_bytes": str(len(input_data)),
        "input_fnv64": str(task6_fnv64(input_data)),
        "input_sha256": input_sha,
        "output_storage": TASK6_ARTIFACT_OUTPUT_STORAGE,
        "output_bytes": str(len(output_data)),
        "output_fnv64": str(task6_fnv64(output_data)),
        "output_sha256": output_sha,
        "operation": "normalize_ppm",
    }
    body: dict[str, object] = {
        "schema": "task6-artifact-provenance-v1",
        "challenge": challenge,
        "operation": "normalize_ppm",
        "input": {
            "path": f"state-extracted/{TASK6_ARTIFACT_INPUT_STORAGE}",
            "logical_name": "raw-counts.csv",
            "bytes": len(input_data),
            "sha256": input_sha,
            "fnv64": task6_fnv64(input_data),
        },
        "output": {
            "path": f"state-extracted/{TASK6_ARTIFACT_OUTPUT_STORAGE}",
            "logical_name": "normalized-counts.csv",
            "bytes": len(output_data),
            "sha256": output_sha,
            "fnv64": task6_fnv64(output_data),
        },
        "guest_receipt": guest,
    }
    expected = {
        **body,
        "sha256": _binding_sha256(body, "task6-artifact-provenance-v1"),
    }
    if value != expected:
        raise ScenarioEvidenceError(
            "Task6 artifact provenance differs from the source-C workload corpus"
        )


def _validate_challenge_outcome(
    contents: dict[str, bytes],
    outcome: dict[str, object],
    challenge: str,
) -> tuple[dict[str, object], str, dict[str, object]]:
    expected = derive_challenge(challenge)
    artifact_provenance = _task6_artifact_provenance(contents, challenge)
    expected_rerun = {
        "host_action_rerun": f"usable-run:{expected.rerun_id}",
        "parent": expected.run_id,
        "status": "completed",
    }
    if outcome.get("research_rerun") != expected_rerun:
        raise ScenarioEvidenceError("research rerun outcome does not match the Host challenge")

    expected_stage = {
        "host_workflow_stage_action": "align",
        "attempt": "2",
        "status": "failed",
        "command": "align_reads",
        "duration_ms": "1200",
    }
    if outcome.get("workflow_stage") != expected_stage:
        raise ScenarioEvidenceError("workflow stage outcome does not match the challenge oracle")
    run_record = _unique_outcome_record(
        contents,
        "rp_stage_state",
        "host_workflow_run_id",
        ("host_workflow_run_id",),
    )
    workflow_record = _unique_outcome_record(
        contents,
        "rp_stage_dag",
        "host_workflow_id",
        ("host_workflow_id",),
    )
    if run_record["host_workflow_run_id"] != expected.run_id:
        raise ScenarioEvidenceError("workflow run identity does not match the Host challenge")
    if workflow_record["host_workflow_id"] != expected.workflow_id:
        raise ScenarioEvidenceError("workflow identity does not match the Host challenge")

    expected_artifact = {
        "host_artifact_derive": "raw-counts.csv",
        "output": "normalized-counts.csv",
        "operation": "normalize_ppm",
        "stage": "analyze",
        "sha256": expected.derived_sha256,
    }
    if outcome.get("artifact_derivation") != expected_artifact:
        raise ScenarioEvidenceError("artifact derivation does not match the Host challenge")
    artifact_input = _unique_outcome_record(
        contents,
        "rp_artifact",
        "host_artifact_input",
        ("host_artifact_input", "kind", "sha256", "bytes", "source"),
    )
    expected_input = {
        "host_artifact_input": "raw-counts.csv",
        "kind": "counts_csv",
        "sha256": expected.input_sha256,
        "bytes": str(expected.input_bytes),
        "source": "host_challenge",
    }
    if artifact_input != expected_input:
        raise ScenarioEvidenceError("artifact input does not match the Host challenge")

    bound = {
        **outcome,
        "challenge": challenge,
        "workflow": {
            "run_id": expected.run_id,
            "workflow_id": expected.workflow_id,
        },
        "artifact_input": artifact_input,
    }
    return (
        bound,
        _binding_sha256(bound, "research-platform-outcome-v2"),
        artifact_provenance,
    )


def _normalize_target_order(value: object) -> str:
    if isinstance(value, list) and value == ["plain", "agentos"]:
        return "AB"
    if isinstance(value, list) and value == ["agentos", "plain"]:
        return "BA"
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "-")
        if normalized in {"ab", "plain-agentos"}:
            return "AB"
        if normalized in {"ba", "agentos-plain"}:
            return "BA"
    raise ScenarioEvidenceError("target_order must be AB/plain-agentos or BA/agentos-plain")


def _read_run_summary(
    path: Path, target: str, expected_commit: str
) -> tuple[dict[str, object], bytes]:
    data = _read_regular_file(path, f"{target} run summary", MAX_JSON_BYTES)
    summary = _strict_json_object(data, f"{target} run summary")
    required = {
        "passed": True,
        "returncode": 0,
        "build_returncode": 0,
        "guest_returncode": 0,
        "marker_seen": True,
        "failure_seen": False,
        "timed_out": False,
        "output_eof": True,
        "extract_status": "ready",
        "target_identity": target,
        "chapter": "platform_seeded" if target == "plain" else "platform_agentos",
        "init_proc": "rp_seed_orch" if target == "plain" else "rp_agentos_orch",
        "status": "ready",
    }
    for key, expected in required.items():
        if summary.get(key) != expected or type(summary.get(key)) is not type(expected):
            raise ScenarioEvidenceError(
                f"{target} run summary does not prove success: {key}"
            )
    if summary.get("source_commit") != expected_commit:
        raise ScenarioEvidenceError(
            f"{target} run summary is not bound to the planned source commit"
        )
    if summary.get("source_tree_clean") is not True:
        raise ScenarioEvidenceError(
            f"{target} run summary does not prove a clean tracked source tree"
        )
    extracted = summary.get("extracted_state_files")
    if type(extracted) is not int or extracted <= 0:
        raise ScenarioEvidenceError(f"{target} run summary has an invalid state count")
    embedded = summary.get("embedded_action_records")
    if type(embedded) is not int or embedded <= 0:
        raise ScenarioEvidenceError(f"{target} run summary has no embedded action records")
    elapsed = summary.get("elapsed_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed <= 0
    ):
        raise ScenarioEvidenceError(f"{target} run summary has invalid elapsed_seconds")
    for key in ("failure_line", "failure_reason", "failure_phase"):
        if summary.get(key) != "":
            raise ScenarioEvidenceError(f"{target} run summary records a failure: {key}")
    runner_terminated = summary.get("runner_terminated")
    signals = summary.get("runner_signals")
    raw_returncode = summary.get("guest_raw_returncode")
    if runner_terminated is False:
        if raw_returncode != 0 or signals != []:
            raise ScenarioEvidenceError(f"{target} run summary has inconsistent natural exit")
    elif runner_terminated is True:
        if raw_returncode in (0, None) or signals != [15]:
            raise ScenarioEvidenceError(f"{target} run summary has inconsistent runner termination")
    else:
        raise ScenarioEvidenceError(f"{target} run summary has invalid runner_terminated")
    return summary, data


def _read_challenge_input(
    target_dir: Path,
    target: str,
    summary: dict[str, object],
) -> tuple[str, dict[str, object]]:
    actions_path = target_dir / "actions.json"
    actions_data = _read_regular_file(actions_path, f"{target} Host actions", MAX_JSON_BYTES)
    actions = _strict_json_value(actions_data, f"{target} Host actions")
    if not isinstance(actions, list) or not all(isinstance(item, dict) for item in actions):
        raise ScenarioEvidenceError(f"{target} Host actions are not an object list")

    receipt_path = target_dir / CHALLENGE_RECEIPT_NAME
    receipt_data = _read_regular_file(
        receipt_path, f"{target} challenge input receipt", MAX_JSON_BYTES
    )
    receipt = _strict_json_object(receipt_data, f"{target} challenge input receipt")
    challenge = receipt.get("challenge")
    if not isinstance(challenge, str):
        raise ScenarioEvidenceError(f"{target} challenge input receipt has no challenge")
    try:
        expected_actions = seeded_actions(challenge)
    except ValueError as error:
        raise ScenarioEvidenceError(f"{target} challenge is invalid") from error
    if actions != expected_actions:
        raise ScenarioEvidenceError(
            f"{target} Host actions do not match the challenge-derived input"
        )
    expected_receipt = challenge_input_receipt(
        challenge,
        expected_actions,
        _sha256(actions_data),
    )
    if receipt != expected_receipt:
        raise ScenarioEvidenceError(f"{target} challenge input receipt is inconsistent")
    if summary.get("challenge") != challenge:
        raise ScenarioEvidenceError(f"{target} run summary challenge is inconsistent")
    if summary.get("challenge_receipt") != CHALLENGE_RECEIPT_NAME:
        raise ScenarioEvidenceError(f"{target} run summary names the wrong challenge receipt")
    if summary.get("challenge_receipt_sha256") != _sha256(receipt_data):
        raise ScenarioEvidenceError(f"{target} run summary does not bind the challenge bytes")
    if summary.get("challenge_binding_sha256") != receipt.get("receipt_sha256"):
        raise ScenarioEvidenceError(f"{target} run summary does not bind the challenge semantics")
    if summary.get("embedded_action_records") != len(expected_actions):
        raise ScenarioEvidenceError(f"{target} action count differs from the challenge input")

    source = {
        "challenge": challenge,
        "actions": {
            "path": "actions.json",
            "bytes": len(actions_data),
            "sha256": _sha256(actions_data),
        },
        "receipt": {
            "path": CHALLENGE_RECEIPT_NAME,
            "bytes": len(receipt_data),
            "sha256": _sha256(receipt_data),
            "binding_sha256": receipt["receipt_sha256"],
        },
    }
    source["sha256"] = _binding_sha256(source, "scenario-challenge-source-v1")
    return challenge, source


def _log_receipt(path: Path, target: str) -> tuple[dict[str, object], bytes]:
    data = _read_regular_file(path, f"{target} QEMU log", MAX_LOG_BYTES)
    if not data or b"\0" in data:
        raise ScenarioEvidenceError(f"{target} QEMU log is empty or binary")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ScenarioEvidenceError(f"{target} QEMU log is not UTF-8") from error
    marker = "rp_orch: passed" if target == "plain" else "rp_agentos_orch: passed"
    marker_count = sum(1 for line in text.splitlines() if line.strip() == marker)
    if marker_count != 1:
        raise ScenarioEvidenceError(
            f"{target} QEMU log must contain one complete pass marker, found {marker_count}"
        )
    return {"path": "ucore-run.log", "bytes": len(data), "sha256": _sha256(data)}, data


def _collect_target(
    target_dir: Path,
    target: str,
    expected_commit: str,
    expected_programs: tuple[str, ...],
    expected_roles: dict[str, str],
) -> tuple[TargetMeasurement, dict[str, object]]:
    _require_directory(target_dir, f"{target} run directory")
    state_dir = _require_directory(target_dir / "state-extracted", f"{target} state directory")
    inventory, contents = _state_inventory(state_dir)
    program_timings, ledger_data = _parse_timing_ledger(
        state_dir, target, expected_programs, expected_roles
    )
    summary, summary_data = _read_run_summary(
        target_dir / "ucore-run-summary.json", target, expected_commit
    )
    workflow_timing, workflow_timing_data = _parse_workflow_timing(
        state_dir,
        target,
        program_timings,
        float(summary["elapsed_seconds"]),
    )
    runtime_artifacts = _runtime_artifact_receipts(target_dir, target, summary)
    challenge, challenge_source = _read_challenge_input(target_dir, target, summary)
    functional_acceptance, functional_acceptance_data = _parse_agentos_acceptance(
        contents, target, challenge
    )
    resource_stability, resource_stability_data = _parse_resource_stability(
        contents, target, challenge
    )
    sealed_inventory = _sealed_target_inventory(target_dir, inventory)
    if summary["extracted_state_files"] != inventory["file_count"]:
        raise ScenarioEvidenceError(f"{target} run summary state count differs from inventory")
    log_receipt, _ = _log_receipt(target_dir / "ucore-run.log", target)
    outcome, _ = _normalized_outcome(contents)
    outcome, fingerprint, artifact_provenance = _validate_challenge_outcome(
        contents, outcome, challenge
    )

    if target == "agentos":
        assert functional_acceptance is not None
        assert functional_acceptance_data is not None
        functional_binding = {
            "schema": "agentos-task6-functional-binding-v1",
            "challenge": challenge,
            "challenge_source_sha256": challenge_source["sha256"],
            "run_summary_sha256": _sha256(summary_data),
            "state_inventory_sha256": inventory["sha256"],
            "module_receipt_sha256": _sha256(functional_acceptance_data),
            "required_modules": list(REQUIRED_AGENTOS_MODULES),
        }
        functional_receipt: dict[str, object] = {
            "required": True,
            "status": "verified",
            "path": f"state-extracted/{AGENTOS_ACCEPTANCE_FILE}",
            "bytes": len(functional_acceptance_data),
            "sha256": _sha256(functional_acceptance_data),
            "acceptance": functional_acceptance,
            "binding": {
                **functional_binding,
                "sha256": _binding_sha256(
                    functional_binding, "agentos-task6-functional-binding-v1"
                ),
            },
        }
    else:
        functional_receipt = {"required": False, "status": "not_applicable"}

    if target == "agentos":
        assert resource_stability is not None
        assert resource_stability_data is not None
        stability_binding = {
            "schema": "agentos-task6-resource-stability-binding-v1",
            "challenge": challenge,
            "challenge_source_sha256": challenge_source["sha256"],
            "run_summary_sha256": _sha256(summary_data),
            "state_inventory_sha256": inventory["sha256"],
            "resource_receipt_sha256": _sha256(resource_stability_data),
            "measurement_scope": RESOURCE_STABILITY_MEASUREMENT_SCOPE,
        }
        stability_receipt: dict[str, object] = {
            "required": True,
            "status": "verified",
            "path": f"state-extracted/{RESOURCE_STABILITY_FILE}",
            "bytes": len(resource_stability_data),
            "sha256": _sha256(resource_stability_data),
            "acceptance": resource_stability,
            "binding": {
                **stability_binding,
                "sha256": _binding_sha256(
                    stability_binding,
                    "agentos-task6-resource-stability-binding-v1",
                ),
            },
        }
    else:
        stability_receipt = {"required": False, "status": "not_applicable"}

    receipt = {
        "schema": "scenario-raw-source-receipt-v1",
        "state_inventory": inventory,
        "timing_ledger": {
            "path": "state-extracted/rp_orch_timing",
            "bytes": len(ledger_data),
            "sha256": _sha256(ledger_data),
        },
        "workflow_timing": {
            "path": "state-extracted/rp_workflow_timing",
            "bytes": len(workflow_timing_data),
            "sha256": _sha256(workflow_timing_data),
            "measurement": workflow_timing,
        },
        "qemu_log": log_receipt,
        "run_summary": {
            "path": "ucore-run-summary.json",
            "bytes": len(summary_data),
            "sha256": _sha256(summary_data),
        },
        "source_identity": {
            "commit": summary["source_commit"],
            "tracked_tree_clean": summary["source_tree_clean"],
        },
        "runtime_artifacts": runtime_artifacts,
        "challenge_source": challenge_source,
        "sealed_inventory": sealed_inventory,
        "artifact_provenance": artifact_provenance,
        "functional_acceptance": functional_receipt,
        "resource_stability": stability_receipt,
    }
    receipt["sha256"] = _binding_sha256(receipt, "scenario-raw-source-receipt-v1")
    return TargetMeasurement(
        target=target,
        program_timings=program_timings,
        workflow_elapsed_ms=int(workflow_timing["workflow_elapsed_ms"]),
        outcome=outcome,
        outcome_fingerprint=fingerprint,
        raw_source_receipt=receipt,
        challenge=challenge,
    ), summary


def _resolve_boot_order(
    supplied: object | None, plain_summary_order: object | None, agentos_summary_order: object | None
) -> str:
    declared: list[str] = []
    for value in (plain_summary_order, agentos_summary_order):
        if value is not None:
            declared.append(_normalize_target_order(value))
    if len(set(declared)) > 1:
        raise ScenarioEvidenceError("plain and AgentOS run summaries disagree on target_order")
    if supplied is not None:
        normalized = _normalize_target_order(supplied)
        if declared and normalized != declared[0]:
            raise ScenarioEvidenceError("CLI target_order disagrees with the run summaries")
        return normalized
    if not declared:
        raise ScenarioEvidenceError("target_order is absent from both summaries and the CLI")
    return declared[0]


def _collect_boot(
    boot_dir: Path,
    source_commit: str,
    run_id: str,
    boot_order: int,
    supplied_target_order: object | None,
    expected_programs: tuple[str, ...],
    expected_roles: dict[str, str],
) -> dict[str, object]:
    _require_directory(boot_dir, "boot directory")
    boot_id = boot_dir.name
    if IDENTIFIER_RE.fullmatch(boot_id) is None:
        raise ScenarioEvidenceError(f"invalid boot directory name: {boot_id!r}")
    plain, plain_summary = _collect_target(
        boot_dir / "plain", "plain", source_commit, expected_programs, expected_roles
    )
    agentos, agentos_summary = _collect_target(
        boot_dir / "agentos", "agentos", source_commit, expected_programs, expected_roles
    )
    target_order = _resolve_boot_order(
        supplied_target_order,
        plain_summary.get("target_order"),
        agentos_summary.get("target_order"),
    )
    if plain_summary["embedded_action_records"] != agentos_summary["embedded_action_records"]:
        raise ScenarioEvidenceError(f"boot {boot_id} targets used different action counts")
    if plain.challenge != agentos.challenge:
        raise ScenarioEvidenceError(f"boot {boot_id} targets used different challenges")

    plain_names = tuple(item.name for item in plain.program_timings)
    agentos_names = tuple(item.name for item in agentos.program_timings)
    if plain_names != agentos_names:
        if set(plain_names) == set(agentos_names):
            reason = "program order differs"
        else:
            reason = "program sets differ"
        raise ScenarioEvidenceError(f"boot {boot_id} {reason} between targets")
    if plain.outcome_fingerprint != agentos.outcome_fingerprint or plain.outcome != agentos.outcome:
        raise ScenarioEvidenceError(f"boot {boot_id} has unequal normalized outcomes")

    targets: dict[str, object] = {}
    for measurement in (plain, agentos):
        targets[measurement.target] = {
            "makespan_ms": measurement.workflow_elapsed_ms,
            "programs": [
                {"program": item.name, "elapsed_ms": item.elapsed_ms}
                for item in measurement.program_timings
            ],
            "raw_source_receipt": measurement.raw_source_receipt,
        }
    binding = {
        "source_commit": source_commit,
        "run_id": run_id,
        "boot_id": boot_id,
        "boot_order": boot_order,
        "target_order": target_order,
        "challenge": plain.challenge,
        "program_order": list(plain_names),
        "outcome_fingerprint": plain.outcome_fingerprint,
        "source_receipts": {
            target: targets[target]["raw_source_receipt"]["sha256"]
            for target in ("plain", "agentos")
        },
    }
    return {
        "sample_id": f"{run_id}:{boot_id}",
        "binding": {**binding, "sha256": _binding_sha256(binding, "scenario-sample-v1")},
        "outcome": plain.outcome,
        "outcome_fingerprint": plain.outcome_fingerprint,
        "targets": targets,
    }


def _percentile(values: Iterable[int], quantile: float) -> float | int:
    ordered = sorted(values)
    if not ordered:
        raise ScenarioEvidenceError("cannot summarize an empty sample")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 3)


def _median(values: Sequence[float | int]) -> float | int:
    if not values:
        raise ScenarioEvidenceError("cannot summarize an empty paired sample")
    result = statistics.median(values)
    return int(result) if isinstance(result, int) else float(result)


def _bootstrap_interval(
    values: Sequence[float | int], seed_text: str
) -> tuple[float | int, float | int]:
    if not values:
        raise ScenarioEvidenceError("cannot bootstrap an empty paired sample")
    seed = int(_sha256(seed_text.encode("utf-8"))[:16], 16)
    rng = random.Random(seed)
    estimates = [
        _median([values[rng.randrange(len(values))] for _ in values])
        for _ in range(BOOTSTRAP_REPETITIONS)
    ]
    estimates.sort()
    lower = estimates[math.floor(0.025 * (len(estimates) - 1))]
    upper = estimates[math.ceil(0.975 * (len(estimates) - 1))]
    return lower, upper


def _sign_test(improvements: Sequence[float | int]) -> dict[str, object]:
    wins = sum(value > 0 for value in improvements)
    losses = sum(value < 0 for value in improvements)
    ties = len(improvements) - wins - losses
    n = wins + losses
    numerator = sum(math.comb(n, count) for count in range(wins, n + 1)) if n else 1
    denominator = 1 << n if n else 1
    exact = Fraction(numerator, denominator)
    return {
        "alternative": "agentos_lower_makespan",
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "n": n,
        "p_value": float(exact),
        "numerator": exact.numerator,
        "denominator": exact.denominator,
    }


def _joint_mcid_sign_test(
    improvements: Sequence[float | int],
    relative_improvements: Sequence[float | None],
) -> dict[str, object]:
    """Return the exact full-n test for joint per-boot MCID exceedance."""
    if len(improvements) != len(relative_improvements):
        raise ScenarioEvidenceError("joint-MCID inputs must align one-for-one")
    n = len(improvements)
    wins = sum(
        absolute > MIN_ABSOLUTE_IMPROVEMENT_MS
        and relative is not None
        and relative > MIN_RELATIVE_IMPROVEMENT_PERCENT
        for absolute, relative in zip(improvements, relative_improvements)
    )
    exact = Fraction(
        sum(math.comb(n, count) for count in range(wins, n + 1)),
        1 << n,
    )
    return {
        "alternative": "joint_absolute_and_relative_mcid_exceeded",
        "absolute_mcid_ms": MIN_ABSOLUTE_IMPROVEMENT_MS,
        "relative_mcid_percent": MIN_RELATIVE_IMPROVEMENT_PERCENT,
        "success_rule": "both_strictly_greater_per_boot",
        "non_win_policy": "ties_missing_or_not_exceeding_either_mcid",
        "wins": wins,
        "non_wins": n - wins,
        "n": n,
        "p_value": float(exact),
        "numerator": exact.numerator,
        "denominator": exact.denominator,
    }


def _reverse_joint_mcid_sign_test(
    improvements: Sequence[float | int],
    relative_improvements: Sequence[float | None],
) -> dict[str, object]:
    """Mirror the registered MCID test for material AgentOS regressions."""
    if len(improvements) != len(relative_improvements):
        raise ScenarioEvidenceError("reverse joint-MCID inputs must align one-for-one")
    n = len(improvements)
    losses = sum(
        absolute < -MIN_ABSOLUTE_IMPROVEMENT_MS
        and relative is not None
        and relative < -MIN_RELATIVE_IMPROVEMENT_PERCENT
        for absolute, relative in zip(improvements, relative_improvements)
    )
    exact = Fraction(
        sum(math.comb(n, count) for count in range(losses, n + 1)),
        1 << n,
    )
    return {
        "alternative": "joint_absolute_and_relative_regression_mcid_exceeded",
        "absolute_mcid_ms": MIN_ABSOLUTE_IMPROVEMENT_MS,
        "relative_mcid_percent": MIN_RELATIVE_IMPROVEMENT_PERCENT,
        "success_rule": "both_strictly_less_than_negative_thresholds_per_boot",
        "non_loss_policy": "ties_missing_or_not_exceeding_either_reverse_mcid",
        "losses": losses,
        "non_losses": n - losses,
        "n": n,
        "p_value": float(exact),
        "numerator": exact.numerator,
        "denominator": exact.denominator,
    }


def _paired_improvement(samples: Sequence[dict[str, object]]) -> dict[str, object]:
    paired_samples: list[dict[str, object]] = []
    improvements: list[int] = []
    relative_improvements: list[float | None] = []
    relative_available = True
    for sample in samples:
        plain_ms = sample["targets"]["plain"]["makespan_ms"]
        agentos_ms = sample["targets"]["agentos"]["makespan_ms"]
        improvement_ms = plain_ms - agentos_ms
        relative = improvement_ms * 100.0 / plain_ms if plain_ms > 0 else None
        improvements.append(improvement_ms)
        if relative is None:
            relative_available = False
        relative_improvements.append(relative)
        paired_samples.append(
            {
                "sample_id": sample["sample_id"],
                "boot_id": sample["binding"]["boot_id"],
                "target_order": sample["binding"]["target_order"],
                "plain_ms": plain_ms,
                "agentos_ms": agentos_ms,
                "improvement_ms": improvement_ms,
                "relative_improvement_percent": relative,
            }
        )

    seed_sha256 = _binding_sha256(
        paired_samples, "scenario-paired-bootstrap-seed-v1"
    )
    ci_low, ci_high = _bootstrap_interval(
        improvements, f"{seed_sha256}:absolute"
    )
    if relative_available:
        available_relatives = [
            value for value in relative_improvements if value is not None
        ]
        relative_median = _median(available_relatives)
        relative_ci_low, relative_ci_high = _bootstrap_interval(
            available_relatives, f"{seed_sha256}:relative"
        )
    else:
        relative_median = None
        relative_ci_low = None
        relative_ci_high = None
    return {
        "direction": "plain_minus_agentos_positive_is_better",
        "lower_is_better": True,
        "unit": "ms",
        "paired_success_rate": 1.0,
        "inference": dict(SCENARIO_INFERENCE),
        "interpretation": dict(SCENARIO_INTERPRETATION),
        "claim_gate": {
            "minimum_absolute_improvement_ms": MIN_ABSOLUTE_IMPROVEMENT_MS,
            "minimum_baseline_makespan_ms": MIN_BASELINE_MAKESPAN_MS,
            "minimum_relative_improvement_percent": MIN_RELATIVE_IMPROVEMENT_PERCENT,
        },
        "n": len(paired_samples),
        "median": _median(improvements),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "relative_median_percent": relative_median,
        "relative_ci_low": relative_ci_low,
        "relative_ci_high": relative_ci_high,
        "sign_test": _sign_test(improvements),
        "mcid_sign_test": _joint_mcid_sign_test(
            improvements, relative_improvements
        ),
        "regression_mcid_sign_test": _reverse_joint_mcid_sign_test(
            improvements, relative_improvements
        ),
        "bootstrap": {
            "method": "deterministic_percentile_median",
            "confidence": 0.95,
            "repetitions": BOOTSTRAP_REPETITIONS,
            "seed_sha256": seed_sha256,
            "role": "descriptive_only",
        },
        "samples": paired_samples,
    }


def _functional_acceptance_summary(
    samples: Sequence[dict[str, object]],
) -> dict[str, object]:
    receipt_presence: list[bool] = []
    for sample in samples:
        try:
            targets = sample["targets"]
            receipt_presence.extend(
                "functional_acceptance" in targets[target]["raw_source_receipt"]
                for target in ("plain", "agentos")
            )
        except (KeyError, TypeError) as error:
            raise ScenarioEvidenceError(
                "scenario functional acceptance lacks bound target evidence"
            ) from error
    if receipt_presence and not any(receipt_presence):
        return {
            "status": "unavailable",
            "required_target": "agentos",
            "required_modules": list(REQUIRED_AGENTOS_MODULES),
            "verified_boots": 0,
            "boot_receipts": [],
        }
    if not receipt_presence or not all(receipt_presence):
        raise ScenarioEvidenceError(
            "scenario functional acceptance is only partially present"
        )

    boot_receipts: list[dict[str, object]] = []
    for sample in samples:
        try:
            sample_id = sample["sample_id"]
            challenge = sample["binding"]["challenge"]
            targets = sample["targets"]
            plain_raw = targets["plain"]["raw_source_receipt"]
            agentos_raw = targets["agentos"]["raw_source_receipt"]
        except (KeyError, TypeError) as error:
            raise ScenarioEvidenceError(
                "scenario functional acceptance lacks bound target evidence"
            ) from error
        if plain_raw.get("functional_acceptance") != {
            "required": False,
            "status": "not_applicable",
        }:
            raise ScenarioEvidenceError(
                "plain target has an invalid functional acceptance marker"
            )
        for target_name, raw in (("plain", plain_raw), ("agentos", agentos_raw)):
            if not isinstance(raw, dict) or not isinstance(raw.get("sha256"), str):
                raise ScenarioEvidenceError(
                    f"{target_name} functional evidence lacks a raw source receipt"
                )
            unsigned_raw = dict(raw)
            raw_sha = unsigned_raw.pop("sha256")
            if raw_sha != _binding_sha256(
                unsigned_raw, "scenario-raw-source-receipt-v1"
            ):
                raise ScenarioEvidenceError(
                    f"{target_name} raw source receipt binding differs"
                )
            if sample["binding"]["source_receipts"].get(target_name) != raw_sha:
                raise ScenarioEvidenceError(
                    f"{target_name} source receipt is not bound to the sample"
                )

        receipt = agentos_raw.get("functional_acceptance")
        if not isinstance(receipt, dict) or set(receipt) != {
            "required",
            "status",
            "path",
            "bytes",
            "sha256",
            "acceptance",
            "binding",
        }:
            raise ScenarioEvidenceError(
                "agentos functional acceptance receipt has an invalid schema"
            )
        acceptance = receipt["acceptance"]
        binding = receipt["binding"]
        if (
            receipt["required"] is not True
            or receipt["status"] != "verified"
            or receipt["path"]
            != f"state-extracted/{AGENTOS_ACCEPTANCE_FILE}"
            or type(receipt["bytes"]) is not int
            or receipt["bytes"] <= 0
            or not isinstance(receipt["sha256"], str)
            or not isinstance(acceptance, dict)
            or set(acceptance)
            != {"schema", "challenge_binding", "required_modules", "modules"}
            or acceptance["schema"] != "agentos_task6_acceptance_v3"
            or acceptance["required_modules"] != list(REQUIRED_AGENTOS_MODULES)
            or not isinstance(acceptance["modules"], list)
            or any(
                not isinstance(module, dict) for module in acceptance["modules"]
            )
            or [module.get("module") for module in acceptance["modules"]]
            != list(REQUIRED_AGENTOS_MODULES)
            or any(module.get("status") != "verified" for module in acceptance["modules"])
        ):
            raise ScenarioEvidenceError(
                "agentos functional acceptance does not verify every required module"
            )
        _validate_agentos_acceptance_semantics(acceptance, challenge)
        state_inventory = agentos_raw.get("state_inventory")
        if not isinstance(state_inventory, dict):
            raise ScenarioEvidenceError(
                "agentos functional acceptance lacks a state inventory"
            )
        inventory_entries = state_inventory.get("files", [])
        matching_entries = [
            entry
            for entry in inventory_entries
            if isinstance(entry, dict) and entry.get("path") == AGENTOS_ACCEPTANCE_FILE
        ]
        if len(matching_entries) != 1 or matching_entries[0] != {
            "path": AGENTOS_ACCEPTANCE_FILE,
            "bytes": receipt["bytes"],
            "sha256": receipt["sha256"],
        }:
            raise ScenarioEvidenceError(
                "agentos functional acceptance differs from the state inventory"
            )
        if not isinstance(binding, dict) or set(binding) != {
            "schema",
            "challenge",
            "challenge_source_sha256",
            "run_summary_sha256",
            "state_inventory_sha256",
            "module_receipt_sha256",
            "required_modules",
            "sha256",
        }:
            raise ScenarioEvidenceError(
                "agentos functional acceptance binding has an invalid schema"
            )
        unsigned_binding = dict(binding)
        binding_sha = unsigned_binding.pop("sha256")
        if (
            binding["schema"] != "agentos-task6-functional-binding-v1"
            or binding["challenge"] != challenge
            or binding["challenge_source_sha256"]
            != agentos_raw["challenge_source"]["sha256"]
            or binding["run_summary_sha256"]
            != agentos_raw["run_summary"]["sha256"]
            or binding["state_inventory_sha256"]
            != agentos_raw["state_inventory"]["sha256"]
            or binding["module_receipt_sha256"] != receipt["sha256"]
            or binding["required_modules"] != list(REQUIRED_AGENTOS_MODULES)
            or binding_sha
            != _binding_sha256(
                unsigned_binding, "agentos-task6-functional-binding-v1"
            )
        ):
            raise ScenarioEvidenceError(
                "agentos functional acceptance is not bound to challenge and raw evidence"
            )
        boot_receipts.append(
            {
                "sample_id": sample_id,
                "challenge": challenge,
                "module_receipt_sha256": receipt["sha256"],
                "binding_sha256": binding_sha,
                "raw_source_receipt_sha256": agentos_raw["sha256"],
            }
        )
    receipt_hashes = [receipt["module_receipt_sha256"] for receipt in boot_receipts]
    if len(receipt_hashes) != len(set(receipt_hashes)):
        raise ScenarioEvidenceError(
            "agentos functional acceptance replays a module receipt across boots"
        )
    return {
        "status": "passed",
        "required_target": "agentos",
        "required_modules": list(REQUIRED_AGENTOS_MODULES),
        "verified_boots": len(samples),
        "boot_receipts": boot_receipts,
    }


def _resource_stability_summary(
    samples: Sequence[dict[str, object]],
) -> dict[str, object]:
    unavailable_observation = {
        "coverage": "configured_global_kind_counters",
        "measured_mask_semantics": "configured_global_resource_kind_counters_only",
        "snapshot_consistency": "not_measured",
        "account_counters": "not_measured",
        "rate_budgets": "not_measured",
        "free_pages": {
            "status": "not_measured",
            "exact_pair_recovery": None,
            "exact_terminal_recovery": None,
        },
        "resources": [
            {
                "kind": kind,
                "status": "not_measured",
                "coverage": "configured_global_counter",
                "per_workflow_growth_bound": RESOURCE_STABILITY_GROWTH_BOUNDS[kind],
                "terminal_growth_bound": RESOURCE_STABILITY_GROWTH_BOUNDS[kind],
                "max_observed_per_workflow_growth": None,
                "terminal_observed_growth": None,
                "plateau_or_reclamation": None,
                "exact_terminal_recovery": None,
            }
            for kind in RESOURCE_STABILITY_RESOURCE_KINDS
        ],
    }
    presence: list[bool] = []
    for sample in samples:
        try:
            targets = sample["targets"]
            presence.extend(
                "resource_stability" in targets[target]["raw_source_receipt"]
                for target in ("plain", "agentos")
            )
        except (KeyError, TypeError) as error:
            raise ScenarioEvidenceError(
                "scenario resource stability lacks bound target evidence"
            ) from error
    if presence and not any(presence):
        return {
            "status": "unavailable",
            "required_target": "agentos",
            "measurement_scope": RESOURCE_STABILITY_MEASUREMENT_SCOPE,
            "verified_boots": 0,
            "load_workflows_per_boot": RESOURCE_STABILITY_LOAD_WORKFLOWS,
            "terminal_workflows_per_boot": RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
            "child_rounds_per_load_workflow": RESOURCE_STABILITY_CHILD_ROUNDS,
            "global_observation": unavailable_observation,
            "interpretation": dict(RESOURCE_STABILITY_INTERPRETATION),
            "boot_receipts": [],
        }
    if not presence or not all(presence):
        raise ScenarioEvidenceError(
            "scenario resource stability is only partially present"
        )

    boot_receipts: list[dict[str, object]] = []
    acceptances: list[dict[str, object]] = []
    for sample in samples:
        try:
            sample_id = sample["sample_id"]
            challenge = sample["binding"]["challenge"]
            targets = sample["targets"]
            plain_raw = targets["plain"]["raw_source_receipt"]
            agentos_raw = targets["agentos"]["raw_source_receipt"]
        except (KeyError, TypeError) as error:
            raise ScenarioEvidenceError(
                "scenario resource stability lacks bound target evidence"
            ) from error
        if not isinstance(plain_raw, dict) or not isinstance(agentos_raw, dict):
            raise ScenarioEvidenceError(
                "scenario resource stability lacks bound target evidence"
            )
        if plain_raw.get("resource_stability") != {
            "required": False,
            "status": "not_applicable",
        }:
            raise ScenarioEvidenceError(
                "plain target has an invalid resource stability marker"
            )
        for target_name, raw in (("plain", plain_raw), ("agentos", agentos_raw)):
            if not isinstance(raw, dict) or not isinstance(raw.get("sha256"), str):
                raise ScenarioEvidenceError(
                    f"{target_name} resource stability lacks a raw source receipt"
                )
            unsigned_raw = dict(raw)
            raw_sha = unsigned_raw.pop("sha256")
            if raw_sha != _binding_sha256(
                unsigned_raw, "scenario-raw-source-receipt-v1"
            ):
                raise ScenarioEvidenceError(
                    f"{target_name} raw source receipt binding differs"
                )
            if sample["binding"]["source_receipts"].get(target_name) != raw_sha:
                raise ScenarioEvidenceError(
                    f"{target_name} source receipt is not bound to the sample"
                )

        receipt = agentos_raw.get("resource_stability")
        if not isinstance(receipt, dict) or set(receipt) != {
            "required",
            "status",
            "path",
            "bytes",
            "sha256",
            "acceptance",
            "binding",
        }:
            raise ScenarioEvidenceError(
                "agentos resource stability receipt has an invalid schema"
            )
        acceptance = receipt["acceptance"]
        binding = receipt["binding"]
        if (
            receipt["required"] is not True
            or receipt["status"] != "verified"
            or receipt["path"]
            != f"state-extracted/{RESOURCE_STABILITY_FILE}"
            or type(receipt["bytes"]) is not int
            or receipt["bytes"] <= 0
            or not isinstance(receipt["sha256"], str)
        ):
            raise ScenarioEvidenceError(
                "agentos resource stability receipt is not verified"
            )
        _validate_resource_stability_semantics(acceptance, challenge)
        acceptances.append(acceptance)
        state_inventory = agentos_raw.get("state_inventory")
        if not isinstance(state_inventory, dict):
            raise ScenarioEvidenceError(
                "agentos resource stability lacks a state inventory"
            )
        inventory_entries = state_inventory.get("files", [])
        matching_entries = [
            entry
            for entry in inventory_entries
            if isinstance(entry, dict) and entry.get("path") == RESOURCE_STABILITY_FILE
        ]
        if len(matching_entries) != 1 or matching_entries[0] != {
            "path": RESOURCE_STABILITY_FILE,
            "bytes": receipt["bytes"],
            "sha256": receipt["sha256"],
        }:
            raise ScenarioEvidenceError(
                "agentos resource stability differs from the state inventory"
            )
        if not isinstance(binding, dict) or set(binding) != {
            "schema",
            "challenge",
            "challenge_source_sha256",
            "run_summary_sha256",
            "state_inventory_sha256",
            "resource_receipt_sha256",
            "measurement_scope",
            "sha256",
        }:
            raise ScenarioEvidenceError(
                "agentos resource stability binding has an invalid schema"
            )
        unsigned_binding = dict(binding)
        binding_sha = unsigned_binding.pop("sha256")
        if (
            binding["schema"]
            != "agentos-task6-resource-stability-binding-v1"
            or binding["challenge"] != challenge
            or binding["challenge_source_sha256"]
            != agentos_raw["challenge_source"]["sha256"]
            or binding["run_summary_sha256"]
            != agentos_raw["run_summary"]["sha256"]
            or binding["state_inventory_sha256"]
            != agentos_raw["state_inventory"]["sha256"]
            or binding["resource_receipt_sha256"] != receipt["sha256"]
            or binding["measurement_scope"]
            != RESOURCE_STABILITY_MEASUREMENT_SCOPE
            or binding_sha
            != _binding_sha256(
                unsigned_binding,
                "agentos-task6-resource-stability-binding-v1",
            )
        ):
            raise ScenarioEvidenceError(
                "agentos resource stability is not bound to challenge and raw evidence"
            )
        boot_receipts.append(
            {
                "sample_id": sample_id,
                "challenge": challenge,
                "resource_receipt_sha256": receipt["sha256"],
                "binding_sha256": binding_sha,
                "raw_source_receipt_sha256": agentos_raw["sha256"],
            }
        )
    receipt_hashes = [
        receipt["resource_receipt_sha256"] for receipt in boot_receipts
    ]
    if len(receipt_hashes) != len(set(receipt_hashes)):
        raise ScenarioEvidenceError(
            "agentos resource stability replays a receipt across boots"
        )
    resources: list[dict[str, object]] = []
    for resource_index, kind in enumerate(RESOURCE_STABILITY_RESOURCE_KINDS):
        measured = all(
            acceptance["global_policy"]["measured_mask"]
            & (1 << resource_index)
            for acceptance in acceptances
        )
        growth = (
            [
                workflow[f"{kind}_ordinary_used_after"]
                + workflow[f"{kind}_reserved_used_after"]
                - workflow[f"{kind}_ordinary_used_before"]
                - workflow[f"{kind}_reserved_used_before"]
                for acceptance in acceptances
                for workflow in acceptance["workflows"]
            ]
            if measured
            else []
        )
        terminal_growth = (
            [
                acceptance["workflows"][-1][f"{kind}_ordinary_used_after"]
                + acceptance["workflows"][-1][f"{kind}_reserved_used_after"]
                - acceptance["workflows"][0][f"{kind}_ordinary_used_before"]
                - acceptance["workflows"][0][f"{kind}_reserved_used_before"]
                for acceptance in acceptances
            ]
            if measured
            else []
        )
        plateau = (
            all(
                _resource_stability_plateau_or_reclamation(
                    acceptance["workflows"], kind
                )
                for acceptance in acceptances
            )
            if measured and RESOURCE_STABILITY_GROWTH_BOUNDS[kind] != 0
            else None
        )
        resources.append(
            {
                "kind": kind,
                "status": "measured" if measured else "not_measured",
                "coverage": "configured_global_counter",
                "per_workflow_growth_bound": RESOURCE_STABILITY_GROWTH_BOUNDS[kind],
                "terminal_growth_bound": RESOURCE_STABILITY_GROWTH_BOUNDS[kind],
                "max_observed_per_workflow_growth": max(growth)
                if growth
                else None,
                "terminal_observed_growth": max(terminal_growth)
                if terminal_growth
                else None,
                "plateau_or_reclamation": plateau,
                "exact_terminal_recovery": all(
                    value == 0 for value in terminal_growth
                )
                if measured
                else None,
            }
        )
    passed = all(acceptance["status"] == "verified" for acceptance in acceptances)
    return {
        "status": "passed" if passed else "partial",
        "required_target": "agentos",
        "measurement_scope": RESOURCE_STABILITY_MEASUREMENT_SCOPE,
        "verified_boots": sum(
            acceptance["status"] == "verified" for acceptance in acceptances
        ),
        "load_workflows_per_boot": RESOURCE_STABILITY_LOAD_WORKFLOWS,
        "terminal_workflows_per_boot": RESOURCE_STABILITY_TERMINAL_WORKFLOWS,
        "child_rounds_per_load_workflow": RESOURCE_STABILITY_CHILD_ROUNDS,
        "global_observation": {
            "coverage": "configured_global_kind_counters",
            "measured_mask_semantics": "configured_global_resource_kind_counters_only",
            "snapshot_consistency": "single_core_irq_coherent",
            "account_counters": "not_measured",
            "rate_budgets": "not_measured",
            "free_pages": {
                "status": "measured",
                "exact_pair_recovery": True,
                "exact_terminal_recovery": True,
            },
            "resources": resources,
        },
        "interpretation": dict(RESOURCE_STABILITY_INTERPRETATION),
        "boot_receipts": boot_receipts,
    }


def _summarize(samples: Sequence[dict[str, object]]) -> dict[str, object]:
    programs = samples[0]["binding"]["program_order"]
    targets: dict[str, object] = {}
    for target in ("plain", "agentos"):
        makespans = [sample["targets"][target]["makespan_ms"] for sample in samples]
        by_program: dict[str, list[int]] = {program: [] for program in programs}
        for sample in samples:
            rows = sample["targets"][target]["programs"]
            for row in rows:
                by_program[row["program"]].append(row["elapsed_ms"])
        targets[target] = {
            "successful_boots": len(samples),
            "success_rate": 1.0,
            "makespan_ms": {
                "p50": _percentile(makespans, 0.50),
                "p95": _percentile(makespans, 0.95),
                "min": min(makespans),
                "max": max(makespans),
            },
            "programs": {
                program: {
                    "p50_ms": _percentile(values, 0.50),
                    "p95_ms": _percentile(values, 0.95),
                }
                for program, values in by_program.items()
            },
        }
    order_counts = {
        order: sum(1 for sample in samples if sample["binding"]["target_order"] == order)
        for order in ("AB", "BA")
    }
    return {
        "independent_boots": len(samples),
        "minimum_supported_boots": MIN_SUPPORTED_BOOTS,
        "unique_challenges": len(
            {sample["binding"]["challenge"] for sample in samples}
        ),
        "paired_success_rate": 1.0,
        "target_order_counts": order_counts,
        "target_order_balanced": abs(order_counts["AB"] - order_counts["BA"]) <= 1,
        "paired_improvement": _paired_improvement(samples),
        "functional_acceptance": _functional_acceptance_summary(samples),
        "resource_stability": _resource_stability_summary(samples),
        "targets": targets,
    }


def _classify_claim(summary: dict[str, object]) -> str:
    """Apply the same preregistered eligibility and MCID gate in both directions."""
    try:
        paired = summary["paired_improvement"]
        samples = paired["samples"]
        improvements = [sample["improvement_ms"] for sample in samples]
        relatives = [sample["relative_improvement_percent"] for sample in samples]
        forward_test = _joint_mcid_sign_test(improvements, relatives)
        reverse_test = _reverse_joint_mcid_sign_test(improvements, relatives)
        if (
            paired["mcid_sign_test"] != forward_test
            or paired["regression_mcid_sign_test"] != reverse_test
        ):
            raise ScenarioEvidenceError(
                "paired performance directional MCID statistics differ from samples"
            )
        if type(paired["n"]) is not int or paired["n"] != len(samples):
            raise ScenarioEvidenceError(
                "paired performance sample count differs from samples"
            )
        eligible = (
            paired["n"] >= MIN_SUPPORTED_BOOTS
            and summary["target_order_balanced"] is True
            and summary["paired_success_rate"] == 1.0
            and min(sample["plain_ms"] for sample in samples)
            >= MIN_BASELINE_MAKESPAN_MS
        )
        if not eligible:
            return "inconclusive"
        forward_significant = Fraction(
            forward_test["numerator"], forward_test["denominator"]
        ) <= Fraction(1, 40)
        reverse_significant = Fraction(
            reverse_test["numerator"], reverse_test["denominator"]
        ) <= Fraction(1, 40)
        if forward_significant and reverse_significant:
            raise ScenarioEvidenceError(
                "forward and reverse joint-MCID conclusions cannot both be significant"
            )
        if forward_significant:
            return "supported"
        if reverse_significant:
            return "regressed"
        return "inconclusive"
    except ScenarioEvidenceError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise ScenarioEvidenceError(
            "paired performance summary is structurally invalid"
        ) from error


def _supports_claim(summary: dict[str, object]) -> bool:
    """Compatibility predicate for callers that only need the positive claim."""
    return _classify_claim(summary) == "supported"


def collect_scenario(
    boot_dirs: Sequence[Path],
    *,
    source_commit: str,
    run_id: str,
    target_orders: Sequence[object | None] | None = None,
) -> dict[str, object]:
    """Return a fail-closed scenario report without fabricating missing samples."""

    base = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": SCENARIO_ID,
        "source_commit": source_commit,
        "run_id": run_id,
    }
    try:
        if COMMIT_RE.fullmatch(source_commit) is None:
            raise ScenarioEvidenceError("source_commit must be a lowercase full Git commit")
        if IDENTIFIER_RE.fullmatch(run_id) is None:
            raise ScenarioEvidenceError("run_id is invalid")
        if not boot_dirs:
            raise ScenarioEvidenceError("at least one boot directory is required")
        if target_orders is not None and len(target_orders) != len(boot_dirs):
            raise ScenarioEvidenceError("target_orders must align one-for-one with boot directories")
        boot_ids = [Path(path).name for path in boot_dirs]
        if len(boot_ids) != len(set(boot_ids)):
            raise ScenarioEvidenceError("boot directory names must be unique")
        manifest_programs, expected_roles = read_expected_programs()

        samples: list[dict[str, object]] = []
        observed_programs: list[str] | None = None
        observed_challenges: set[str] = set()
        for index, raw_boot in enumerate(boot_dirs, 1):
            supplied = None if target_orders is None else target_orders[index - 1]
            sample = _collect_boot(
                Path(raw_boot),
                source_commit,
                run_id,
                index,
                supplied,
                manifest_programs,
                expected_roles,
            )
            programs = sample["binding"]["program_order"]
            challenge = sample["binding"]["challenge"]
            if challenge in observed_challenges:
                raise ScenarioEvidenceError(
                    "challenge values must be unique across independent boots"
                )
            observed_challenges.add(challenge)
            if observed_programs is None:
                observed_programs = programs
            elif programs != observed_programs:
                raise ScenarioEvidenceError("program order differs between independent boots")
            samples.append(sample)
        summary = _summarize(samples)
        status = _classify_claim(summary)
        report = {**base, "status": status, "samples": samples, "summary": summary}
        report["report_sha256"] = _binding_sha256(report, REPORT_BINDING_DOMAIN)
        return report
    except (OSError, ScenarioEvidenceError, ValueError) as error:
        report = {
            **base,
            "status": "failed",
            "samples": [],
            "summary": {
                "independent_boots": 0,
                "minimum_supported_boots": MIN_SUPPORTED_BOOTS,
                "paired_success_rate": 0.0,
            },
            "errors": [str(error)],
        }
        report["report_sha256"] = _binding_sha256(report, REPORT_BINDING_DOMAIN)
        return report


def _write_report(path: Path, report: dict[str, object]) -> None:
    try:
        atomic_write_bytes(
            path,
            (
                json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
                + "\n"
            ).encode("utf-8"),
        )
    except (OSError, ValueError) as error:
        raise ScenarioEvidenceError(f"output path is link-backed: {path}") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect paired research-platform scenario measurements."
    )
    parser.add_argument("--commit", required=True, help="Full lowercase source commit.")
    parser.add_argument("--run-id", required=True, help="Stable experiment run identifier.")
    parser.add_argument(
        "--boot", type=Path, action="append", required=True, help="Independent boot directory."
    )
    parser.add_argument(
        "--target-order",
        choices=("AB", "BA"),
        action="append",
        help="Per-boot target order; repeat in the same order as --boot.",
    )
    parser.add_argument("--json-out", type=Path, help="Optional JSON output path.")
    args = parser.parse_args(argv)
    report = collect_scenario(
        args.boot,
        source_commit=args.commit,
        run_id=args.run_id,
        target_orders=args.target_order,
    )
    if args.json_out is not None:
        _write_report(args.json_out, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
