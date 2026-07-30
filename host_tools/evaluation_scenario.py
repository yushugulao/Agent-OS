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
import os
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
    from .check_seeded_action_state import (
        CHALLENGE_RECEIPT_NAME,
        challenge_input_receipt,
        derive_challenge,
        seeded_actions,
    )
except ImportError:  # Direct execution from host_tools/.
    from strict_json import strict_json_loads
    from check_seeded_action_state import (
        CHALLENGE_RECEIPT_NAME,
        challenge_input_receipt,
        derive_challenge,
        seeded_actions,
    )


SCHEMA_VERSION = 1
SCENARIO_ID = "research-platform-seeded"
MIN_SUPPORTED_BOOTS = 7
BOOTSTRAP_REPETITIONS = 2_000
MIN_ABSOLUTE_IMPROVEMENT_MS = 10
MIN_BASELINE_MAKESPAN_MS = 50
MIN_RELATIVE_IMPROVEMENT_PERCENT = 5.0
VALID_STATUSES = frozenset({"supported", "inconclusive", "failed"})
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
AGENTOS_INIT_PHASE_MASK = (1 << 8) - 1
PLAIN_COMPLETION_PHASE_MASK = 1
AGENTOS_COMPLETION_PHASE_MASK = 3
AGENTOS_ACCEPTANCE_FILE = "rp_agentos_acceptance"
REQUIRED_AGENTOS_MODULES = (
    "context",
    "structured_tool",
    "metadata_query",
    "observation",
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


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _reject_link_components(path: Path, label: str) -> None:
    current = path.absolute()
    while True:
        if current.exists() and _is_link(current):
            raise ScenarioEvidenceError(f"{label} has a link-backed path component: {current}")
        if current.parent == current:
            return
        current = current.parent


def _require_directory(path: Path, label: str) -> Path:
    _reject_link_components(path, label)
    if _is_link(path) or not path.is_dir():
        raise ScenarioEvidenceError(f"{label} is missing or link-backed: {path}")
    return path


def _read_regular_file(path: Path, label: str, maximum_bytes: int | None = None) -> bytes:
    _reject_link_components(path, label)
    if _is_link(path) or not path.is_file():
        raise ScenarioEvidenceError(f"{label} is missing or link-backed: {path}")
    try:
        size = path.stat().st_size
        if maximum_bytes is not None and size > maximum_bytes:
            raise ScenarioEvidenceError(f"{label} exceeds the byte limit")
        data = path.read_bytes()
        if len(data) != size:
            raise ScenarioEvidenceError(f"{label} changed while it was read")
        return data
    except OSError as error:
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
        _reject_link_components(path, f"{target} {name} artifact")
        if _is_link(path) or not path.is_file():
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


def read_expected_programs() -> tuple[tuple[str, ...], dict[str, str]]:
    """Read and cross-check the two target manifests used by this adapter."""

    root = Path(__file__).resolve().parents[1]
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
    return int(value)


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
        if _is_link(entry) or not entry.is_file():
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


def _parse_agentos_acceptance(
    contents: dict[str, bytes], target: str
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
    if tuple(header) != ("schema", "module_count") or header != {
        "schema": "agentos_task6_acceptance_v2",
        "module_count": str(len(REQUIRED_AGENTOS_MODULES)),
    }:
        raise ScenarioEvidenceError("agentos functional acceptance header is invalid")

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
                "run_id",
                "stage",
                "returned",
                "used_index",
                "plan",
                "target_fid",
                "target_physical",
                "target_stage",
                "target_kind",
                "target_status",
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

    context, tool, metadata, observation = modules
    if (
        context["records"] < 2
        or context["request_id"] != 9001
        or context["tool_id"] != 1
        or context["record_sequence"] < 1
        or context["record_hash"] < 1
        or context["payload"] != "rp-agentos-orch"
        or context["result"] != "rp-agentos-orch"
        or context["followup_sequence"] <= context["record_sequence"]
        or context["followup_record_hash"] < 1
        or context["latest_sequence"] < context["followup_sequence"]
    ):
        raise ScenarioEvidenceError(
            "agentos context receipt is not bound to the echo record"
        )
    if (
        tool["request_id"] != 9001
        or tool["tool_id"] != 1
        or tool["request_payload"] != "rp-agentos-orch"
        or tool["arg0"] != 9001
        or tool["arg1"] != 9002
        or tool["result_version"] != 1
        or tool["result_status"] != 0
        or tool["result_tool_id"] != 1
        or tool["result_request_id"] != 9001
        or tool["result_payload"] != "rp-agentos-orch"
        or tool["result_value0"] != len("rp-agentos-orch")
        or tool["result_value1"] != 9001
        or tool["result_value2"] != 9002
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
        metadata["project"] != "lab-gene-x"
        or metadata["run_id"] != "RUN-042"
        or metadata["stage"] != "align"
        or metadata["returned"] < 1
        or metadata["used_index"] != 1
        or metadata["plan"] != 2
        or metadata["target_fid"] != 1
        or metadata["target_physical"] != "r42align"
        or metadata["target_stage"] != "align"
        or metadata["target_kind"] != "artifact"
        or metadata["target_status"] != "ok"
    ):
        raise ScenarioEvidenceError(
            "agentos metadata query receipt is not bound to the target hit"
        )
    if (
        observation["timeline_records"] < 1
        or observation["provenance_edges"] < 1
        or observation["ledger_records"] < 2
        or observation["ledger_hash"] < 1
        or observation["edge_kind"] != 1
        or observation["edge_tool_id"] != 1
        or observation["edge_status"] != 0
        or observation["source_sequence"] != context["record_sequence"]
        or observation["target_sequence"] != context["followup_sequence"]
        or observation["source_record_hash"] != context["record_hash"]
        or observation["target_record_hash"] != context["followup_record_hash"]
    ):
        raise ScenarioEvidenceError(
            "agentos observation receipt lacks bound provenance"
        )
    return {
        "schema": header["schema"],
        "required_modules": list(REQUIRED_AGENTOS_MODULES),
        "modules": modules,
    }, data


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


def _validate_challenge_outcome(
    contents: dict[str, bytes],
    outcome: dict[str, object],
    challenge: str,
) -> tuple[dict[str, object], str]:
    expected = derive_challenge(challenge)
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
        "operation": "normalize",
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
        "host_artifact_input": "reads_R1.fastq",
        "kind": "fastq",
        "sha256": expected.input_sha256,
        "bytes": "2048",
        "source": "upload",
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
    return bound, _binding_sha256(bound, "research-platform-outcome-v2")


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
    functional_acceptance, functional_acceptance_data = _parse_agentos_acceptance(
        contents, target
    )
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
    if summary["extracted_state_files"] != inventory["file_count"]:
        raise ScenarioEvidenceError(f"{target} run summary state count differs from inventory")
    log_receipt, _ = _log_receipt(target_dir / "ucore-run.log", target)
    outcome, _ = _normalized_outcome(contents)
    outcome, fingerprint = _validate_challenge_outcome(contents, outcome, challenge)

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
        "functional_acceptance": functional_receipt,
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


def _paired_improvement(samples: Sequence[dict[str, object]]) -> dict[str, object]:
    paired_samples: list[dict[str, object]] = []
    improvements: list[int] = []
    relative_improvements: list[float] = []
    relative_available = True
    for sample in samples:
        plain_ms = sample["targets"]["plain"]["makespan_ms"]
        agentos_ms = sample["targets"]["agentos"]["makespan_ms"]
        improvement_ms = plain_ms - agentos_ms
        relative = improvement_ms * 100.0 / plain_ms if plain_ms > 0 else None
        improvements.append(improvement_ms)
        if relative is None:
            relative_available = False
        else:
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
        relative_median = _median(relative_improvements)
        relative_ci_low, relative_ci_high = _bootstrap_interval(
            relative_improvements, f"{seed_sha256}:relative"
        )
    else:
        relative_median = None
        relative_ci_low = None
        relative_ci_high = None
    return {
        "direction": "plain_minus_agentos_positive_is_better",
        "lower_is_better": True,
        "unit": "ms",
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
        "bootstrap": {
            "method": "deterministic_percentile_median",
            "confidence": 0.95,
            "repetitions": BOOTSTRAP_REPETITIONS,
            "seed_sha256": seed_sha256,
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
            or set(acceptance) != {"schema", "required_modules", "modules"}
            or acceptance["schema"] != "agentos_task6_acceptance_v2"
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
    return {
        "status": "passed",
        "required_target": "agentos",
        "required_modules": list(REQUIRED_AGENTOS_MODULES),
        "verified_boots": len(samples),
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
        "targets": targets,
    }


def _supports_claim(summary: dict[str, object]) -> bool:
    paired = summary["paired_improvement"]
    return (
        paired["n"] >= MIN_SUPPORTED_BOOTS
        and summary["target_order_balanced"] is True
        and summary["paired_success_rate"] == 1.0
        and paired["ci_low"] >= MIN_ABSOLUTE_IMPROVEMENT_MS
        and paired["relative_ci_low"] is not None
        and paired["relative_ci_low"] >= MIN_RELATIVE_IMPROVEMENT_PERCENT
        and min(sample["plain_ms"] for sample in paired["samples"])
        >= MIN_BASELINE_MAKESPAN_MS
        and paired["sign_test"]["p_value"] <= 0.05
    )


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
        supported = _supports_claim(summary)
        status = "supported" if supported else "inconclusive"
        report = {**base, "status": status, "samples": samples, "summary": summary}
        report["report_sha256"] = _binding_sha256(report, "scenario-report-v1")
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
        report["report_sha256"] = _binding_sha256(report, "scenario-report-v1")
        return report


def _write_report(path: Path, report: dict[str, object]) -> None:
    if _is_link(path):
        raise ScenarioEvidenceError(f"output path is link-backed: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


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
