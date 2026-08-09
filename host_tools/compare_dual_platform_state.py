#!/usr/bin/env python3
"""比较从 plain uCore 与 AgentOS-uCore 抽取的研究平台状态。"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from check_host_platform_alignment import (
    parse_canonical_mainflow_telemetry,
    read_expected_programs,
    read_program_ledger,
    source_has_unique_exact_field,
    source_records_are_canonical,
)
from check_host_test_alignment import (
    FNV_OFFSET,
    fnv1a64,
    parse_record,
)
from dual_state_evidence_contract import (
    AGENTOS_EVIDENCE_REQUIREMENTS,
    AGENTOS_MAINFLOW_FACTS,
    AGENTOS_MAINFLOW_STAGES,
    AGENTOS_REQUIRED_AGENT_ROLES,
    BACKEND_REPORT_CASES,
    BACKEND_QUERY_COMMON_FIELDS,
    BACKEND_QUERY_EXPECTED_BACKENDS,
    BACKEND_QUERY_EXPECTED_COMMON,
    BACKEND_QUERY_RECEIPT_FIELDS,
    HOST_RUN_RESULT_STATE_NAME,
    MAIN_FLOW_SOURCE_SPECS,
    RUN_RESULT_IDENTITIES,
    SCENARIO_EVIDENCE_SPECS,
    expected_scenario_rows,
)
from reference_catalog_contract import (
    ReferenceCatalogError,
    ReferenceRecordIdentity,
    allowed_file_identities,
    allowed_observation_identities,
    allowed_record_identities,
    expected_reference_identities,
    match_record_identity,
)
from research_state_manifest import (
    GUEST_STATE_RECEIPT_SCHEMA,
    StateManifestError,
    guest_state_inventory_sha256,
)


GOOD_STATUS = {"ready", "passed", "ok"}
REFERENCE_ROLE = "demo_reference"
REFERENCE_GENERATION = "demo_expected"
REFERENCE_STATUS = "reference_ready"
REFERENCE_FILE_ROLE_KEY = "evidence_file_role"
REFERENCE_FILE_GENERATION_KEY = "evidence_file_generation"
REFERENCE_FILE_STATUS_KEY = "evidence_file_status"
RUNTIME_ROLE = "runtime_verified"
RUNTIME_GENERATION = "runtime"
RUNTIME_STATUS = "verified"
STATE_SOURCE_RE = re.compile(r"rp_[a-z0-9_]+\Z")
PROGRAM_OBSERVATION_KEYS = (
    "evidence_role",
    "evidence_generation",
    "observation_source",
    "program_source",
    "program_source_bytes",
    "program_source_hash",
    "program_names_digest",
    "programs_observed",
    "status",
)
FILE_EVIDENCE_ENVELOPE_KEYS = {
    REFERENCE_FILE_ROLE_KEY,
    REFERENCE_FILE_GENERATION_KEY,
    REFERENCE_FILE_STATUS_KEY,
}
ADAPTED_ANCHORS = {
    ("rp_agentcmp", "backend_runner_checks"),
}
AGENTOS_REQUIRED_AGENT_PROGRAMS = set(AGENTOS_REQUIRED_AGENT_ROLES)
AGENTOS_ROLE_NUMBERS = {
    "sentinel": 1,
    "investigator": 2,
    "recovery": 3,
    "orchestrator": 4,
    "artifact": 5,
}
RUNNER_TICK_STATUS_UNAVAILABLE = "unavailable"
RUNNER_TICK_REASON_PLAIN_ZERO = "plain_runtime_cases_zero"
U64_MAX = (1 << 64) - 1
MAINFLOW_RUNTIME_SPECS = {
    spec.stage: (
        spec.source,
        spec.claim_key,
        spec.claim_value,
        spec.source_status,
    )
    for spec in MAIN_FLOW_SOURCE_SPECS
}


@dataclass(frozen=True)
class StateLine:
    file_name: str
    line_no: int
    anchor: str
    status: str
    text: str


def read_summary(state_dir: Path) -> dict[str, object]:
    try:
        state_root = state_dir.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"missing state directory: {state_dir}") from error
    if not state_root.is_dir():
        raise ValueError(f"state directory is not a directory: {state_dir}")

    summary_path = state_root / "extract-summary.json"
    if summary_path.is_symlink() or not summary_path.is_file():
        raise ValueError(f"missing or unsafe extract summary: {summary_path}")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid extract summary: {summary_path}") from error
    if not isinstance(summary, dict):
        raise ValueError("extract summary is not an object")

    listed = summary.get("files")
    if (
        not isinstance(listed, list)
        or not all(isinstance(item, str) for item in listed)
        or len(listed) != len(set(listed))
        or any(STATE_SOURCE_RE.fullmatch(item) is None for item in listed)
    ):
        raise ValueError("extract summary has an invalid files inventory")
    if HOST_RUN_RESULT_STATE_NAME in listed:
        raise ValueError("Host run result must not appear in Guest state inventory")

    actual: set[str] = set()
    for path in state_root.iterdir():
        if STATE_SOURCE_RE.fullmatch(path.name) is None:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"state inventory contains an unsafe entry: {path.name}")
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise ValueError(
                f"state inventory contains an unsafe entry: {path.name}"
            ) from error
        if resolved.parent != state_root:
            raise ValueError(f"state inventory entry escapes its directory: {path.name}")
        actual.add(path.name)

    inventory = set(listed)
    if inventory != actual:
        raise ValueError(
            "extract summary files inventory differs from the state directory: "
            f"missing={sorted(actual - inventory)} extra={sorted(inventory - actual)}"
        )
    count = summary.get("extracted_state_files")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(actual):
        raise ValueError("extract summary file count differs from its inventory")
    if summary.get("status") != "ready":
        raise ValueError("extract summary is not ready")
    return summary


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require_file_text(state_dir: Path, file_name: str) -> str:
    path = state_dir / file_name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe required state file: {path}")
    return read_text(path)


def parse_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in line.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        fields[key] = value.strip()
    return fields


def parse_evidence_fields(line: str, location: str) -> dict[str, str]:
    fields = parse_record(line)
    if fields is None:
        raise ValueError(f"{location}: evidence record is not canonical")
    return fields


def parse_key_value_path(path: Path, label: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError(f"{label} cannot be read") from error
    if (
        not data
        or len(data) > 64 * 1024
        or not data.endswith(b"\n")
        or b"\r" in data
        or b"\x00" in data
    ):
        raise ValueError(f"{label} has an invalid size or encoding")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
    values: dict[str, str] = {}
    for line_no, raw in enumerate(text.splitlines(), 1):
        if not raw or raw.count("=") != 1:
            raise ValueError(f"{label} has a malformed record at line {line_no}")
        key, value = raw.split("=", 1)
        if not key or key.strip() != key or value.strip() != value or key in values:
            raise ValueError(f"{label} has a non-canonical record at line {line_no}")
        values[key] = value
    return values


def top_level_fields(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or ";" in line or line.count("=") != 1:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in FILE_EVIDENCE_ENVELOPE_KEYS and key in values:
            raise ValueError(
                f"duplicate file evidence envelope key in {path.name}: {key}"
            )
        values[key] = value
    return values


def is_reference_file(path: Path, target: str) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"reference product is missing or unsafe: {path.name}")
    fields = top_level_fields(path)
    claims_reference = any(key in fields for key in FILE_EVIDENCE_ENVELOPE_KEYS)
    if not claims_reference:
        return False
    if (
        fields.get(REFERENCE_FILE_ROLE_KEY) != REFERENCE_ROLE
        or fields.get(REFERENCE_FILE_GENERATION_KEY) != REFERENCE_GENERATION
        or fields.get(REFERENCE_FILE_STATUS_KEY) != REFERENCE_STATUS
    ):
        raise ValueError(f"reference product has an incomplete file envelope: {path.name}")
    if path.name not in allowed_file_identities(target):
        raise ValueError(f"unauthorized {target} reference product: {path.name}")
    return True


def evidence_location(file_name: str = "", line_no: int = 0) -> str:
    if file_name and line_no > 0:
        return f"{file_name}:{line_no}"
    return file_name or "evidence record"


def canonical_positive_int(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"[1-9][0-9]*", value):
        return None
    return int(value)


def measure_program_inventory(state_dir: Path, target: str = "plain") -> dict[str, int]:
    root = Path(__file__).resolve().parents[1]
    programs, roles, manifest_errors = read_expected_programs(root)
    measured, ledger_errors = read_program_ledger(
        state_dir,
        programs,
        roles,
        target,
        "seeded" if target == "plain" else "standard",
    )
    errors = manifest_errors + ledger_errors
    if errors:
        raise ValueError("rp_orch_timing validation failed: " + "; ".join(errors))
    return measured


def classify_reference_record(
    fields: dict[str, str],
    target: str,
    state_dir: Path | None = None,
    file_name: str = "",
    line_no: int = 0,
) -> str | None:
    if fields.get("evidence_role") != REFERENCE_ROLE:
        return None

    location = evidence_location(file_name, line_no)
    catalog_claim = "catalog_generation" in fields or fields.get("status") == REFERENCE_STATUS
    observation_claim = (
        "evidence_generation" in fields
        or "observation_source" in fields
        or fields.get("status") == "reference_observed"
    )
    if catalog_claim and observation_claim:
        raise ValueError(f"{location}: reference record mixes catalog and runtime observation envelopes")
    if catalog_claim:
        if (
            fields.get("catalog_generation") != REFERENCE_GENERATION
            or fields.get("status") != REFERENCE_STATUS
        ):
            raise ValueError(f"{location}: catalog reference has an incomplete evidence envelope")
        try:
            identity = match_record_identity(target, file_name, fields)
        except ReferenceCatalogError as error:
            raise ValueError(f"{location}: {error}") from error
        return identity.canonical()
    if not observation_claim:
        raise ValueError(f"{location}: reference record has an unknown evidence envelope")
    if tuple(fields) != PROGRAM_OBSERVATION_KEYS:
        raise ValueError(f"{location}: Guest observation has an invalid program inventory schema")
    if (
        fields.get("evidence_generation") != RUNTIME_GENERATION
        or fields.get("observation_source") != "guest_runtime"
        or fields.get("program_source") != "rp_orch_timing"
        or fields.get("status") != "reference_observed"
    ):
        raise ValueError(f"{location}: Guest observation has an incomplete evidence envelope")
    declared = {
        key: canonical_positive_int(fields.get(key))
        for key in (
            "program_source_bytes",
            "program_source_hash",
            "program_names_digest",
            "programs_observed",
        )
    }
    if any(value is None for value in declared.values()):
        raise ValueError(f"{location}: Guest observation has non-canonical measurements")
    if state_dir is not None:
        try:
            measured = measure_program_inventory(state_dir)
        except ValueError as error:
            raise ValueError(
                f"{location}: Guest observation has an invalid program source: {error}"
            ) from error
        for key, actual in measured.items():
            if declared[key] != actual:
                raise ValueError(f"{location}: Guest observation {key} is not source-bound")
    identity = ReferenceRecordIdentity(file_name, "program_source=rp_orch_timing")
    if identity not in allowed_observation_identities(target):
        raise ValueError(f"{location}: unauthorized {target} reference observation")
    return "observation:" + identity.canonical().removeprefix("record:")


def is_reference_record(
    fields: dict[str, str],
    target: str,
    state_dir: Path | None = None,
    file_name: str = "",
    line_no: int = 0,
) -> bool:
    return (
        classify_reference_record(
            fields, target, state_dir, file_name, line_no
        )
        is not None
    )


def read_inventory_source(
    state_dir: Path, source: str, allowed_sources: set[str]
) -> bytes:
    source_name = Path(source)
    if (
        not STATE_SOURCE_RE.fullmatch(source)
        or source_name.is_absolute()
        or source_name.name != source
        or len(source_name.parts) != 1
    ):
        raise ValueError(f"state source is not a state-file name: {source}")
    if source not in allowed_sources:
        raise ValueError(f"state source is outside the state inventory: {source}")
    state_root = state_dir.resolve(strict=True)
    lexical_path = state_root / source
    if lexical_path.is_symlink():
        raise ValueError(f"state source is missing or unsafe: {source}")
    try:
        source_path = lexical_path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise ValueError(f"state source is missing or unsafe: {source}") from error
    if source_path.parent != state_root or not source_path.is_file():
        raise ValueError(f"state source escapes the state directory: {source}")
    return source_path.read_bytes()


def is_source_bound_runtime_record(
    state_dir: Path,
    fields: dict[str, str],
    allowed_sources: set[str] | None = None,
) -> bool:
    if fields.get("evidence_role") != RUNTIME_ROLE:
        return False
    generation = fields.get("generation", fields.get("evidence_generation", ""))
    if generation != RUNTIME_GENERATION or fields.get("status") != RUNTIME_STATUS:
        raise ValueError("runtime record is not generation-bound and verified")
    source = fields.get("source", "")
    if not source:
        return False
    if allowed_sources is None:
        summary_sources = read_summary(state_dir).get("files", [])
        if not isinstance(summary_sources, list) or not all(
            isinstance(item, str) for item in summary_sources
        ):
            raise ValueError("extract summary has an invalid files inventory")
        allowed_sources = set(summary_sources)
    data = read_inventory_source(state_dir, source, allowed_sources)
    try:
        source_bytes = int(fields.get("source_bytes", "0"))
        source_hash = int(fields.get("source_hash", "0"))
        executed = int(fields.get("assertions_executed", "0"))
        passed = int(fields.get("assertions_passed", "0"))
    except ValueError as error:
        raise ValueError("runtime record has nonnumeric source evidence") from error
    if (
        source_bytes != len(data)
        or source_hash != fnv1a64(data)
        or executed <= 0
        or passed != executed
    ):
        raise ValueError(f"runtime record is not source-bound: {source}")
    return True


def parse_state_fields(raw: str, file_name: str, line_no: int) -> dict[str, str]:
    fields = parse_fields(raw.strip())
    if fields.get("evidence_role") == REFERENCE_ROLE:
        return parse_evidence_fields(raw.strip(), evidence_location(file_name, line_no))
    return fields


def validate_reference_inventory(
    state_dir: Path, files: set[str], target: str
) -> dict[str, object]:
    reference_files: set[str] = set()
    reference_records: set[str] = set()
    guest_source_bound_runtime_records = 0
    runtime_identities: set[tuple[str, str, str]] = set()
    for file_name in sorted(files):
        path = state_dir / file_name
        if file_name == "extract-summary.json" or not path.is_file():
            continue
        file_reference = is_reference_file(path, target)
        if file_reference:
            reference_files.add(f"file:{file_name}")
        for line_no, raw in enumerate(read_text(path).splitlines(), 1):
            fields = parse_state_fields(raw, file_name, line_no)
            if not fields:
                continue
            identity = classify_reference_record(
                fields, target, state_dir, file_name, line_no
            )
            if identity is not None:
                if identity in reference_records:
                    raise ValueError(f"duplicate {target} reference identity: {identity}")
                reference_records.add(identity)
                continue
            if fields.get("evidence_role") == RUNTIME_ROLE:
                if file_reference:
                    raise ValueError(
                        f"{target} reference product contains runtime evidence: {file_name}"
                    )
                if is_source_bound_runtime_record(state_dir, fields, files):
                    identity_key = next(
                        (
                            key
                            for key in (
                                "runtime_case",
                                "runtime_compare_case",
                                "runner_case",
                                "stage",
                                "case",
                            )
                            if fields.get(key)
                        ),
                        "source",
                    )
                    identity = (
                        file_name,
                        identity_key,
                        fields.get(identity_key, fields.get("source", "")),
                    )
                    if identity in runtime_identities:
                        raise ValueError(
                            "duplicate source-bound runtime evidence: "
                            f"{file_name}:{identity_key}={identity[2]}"
                        )
                    runtime_identities.add(identity)
                    guest_source_bound_runtime_records += 1
    observed = reference_files | reference_records
    expected = set(expected_reference_identities(target))
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ValueError(
            f"{target} reference identity inventory differs: "
            f"missing={missing} extra={extra}"
        )
    return {
        "reference_products": len(reference_files),
        "reference_records": len(reference_records),
        "reference_identities": sorted(observed),
        "guest_source_bound_runtime_records": guest_source_bound_runtime_records,
    }


def collect_evidence_counts(
    state_dir: Path, files: set[str], target: str
) -> dict[str, object]:
    return validate_reference_inventory(state_dir, files, target)


def line_anchor(fields: dict[str, str]) -> str:
    if not fields:
        return ""
    first_key = next(iter(fields))
    if first_key == "status":
        return "status"
    return f"{first_key}={fields[first_key]}"


def collect_good_status_lines(
    state_dir: Path, files: set[str], target: str
) -> list[StateLine]:
    result: list[StateLine] = []
    for file_name in sorted(files):
        path = state_dir / file_name
        if file_name == "extract-summary.json" or not path.is_file():
            continue
        if is_reference_file(path, target):
            continue
        for index, raw in enumerate(read_text(path).splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            fields = parse_state_fields(line, file_name, index)
            if (
                is_reference_record(fields, target, state_dir, file_name, index)
                or fields.get("evidence_role") == RUNTIME_ROLE
            ):
                continue
            status = fields.get("status", "").lower()
            if status not in GOOD_STATUS:
                continue
            anchor = line_anchor(fields)
            if not anchor:
                continue
            result.append(StateLine(file_name, index, anchor, status, line))
    return result


def collect_agentos_status_index(
    state_dir: Path, files: set[str]
) -> dict[tuple[str, str], set[str]]:
    index: dict[tuple[str, str], set[str]] = {}
    for file_name in sorted(files):
        path = state_dir / file_name
        if file_name == "extract-summary.json" or not path.is_file():
            continue
        if is_reference_file(path, "agentos"):
            continue
        for line_no, raw in enumerate(read_text(path).splitlines(), 1):
            fields = parse_state_fields(raw, file_name, line_no)
            if (
                is_reference_record(
                    fields, "agentos", state_dir, file_name, line_no
                )
                or fields.get("evidence_role") == RUNTIME_ROLE
            ):
                continue
            status = fields.get("status", "").lower()
            if status not in GOOD_STATUS:
                continue
            anchor = line_anchor(fields)
            if not anchor:
                continue
            index.setdefault((file_name, anchor), set()).add(status)
    return index


def first_key(anchor: str) -> str:
    return anchor.split("=", 1)[0]


def has_good_record_with_key(state_dir: Path, file_name: str, key: str) -> bool:
    path = state_dir / file_name
    if not path.is_file():
        return False
    for raw in read_text(path).splitlines():
        fields = parse_fields(raw.strip())
        if fields.get("status", "").lower() in GOOD_STATUS and first_key(line_anchor(fields)) == key:
            return True
    return False


def collect_plain_costs(state_dir: Path, target: str) -> set[str]:
    return {
        row["plain_cost"]
        for row in collect_backend_reports(state_dir, target)
        if row.get("plain_cost")
    }


def collect_backend_reports(
    state_dir: Path, target: str
) -> list[dict[str, str]]:
    path = state_dir / "rp_backend_exec"
    if not path.is_file():
        return []
    file_reference = is_reference_file(path, target)
    reports: list[dict[str, str]] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(read_text(path).splitlines(), 1):
        fields = parse_state_fields(raw, path.name, line_no)
        if not fields.get("runner_report"):
            continue
        if file_reference:
            reference_record = (
                fields.get("status") == REFERENCE_STATUS
                and fields.get("runner_report") in BACKEND_REPORT_CASES[target]
            )
        else:
            reference_record = is_reference_record(
                fields, target, state_dir, path.name, line_no
            )
        if not reference_record:
            raise ValueError(
                f"{target} backend report is not an authorized reference: "
                f"{fields.get('runner_report', '')}"
            )
        case = fields.get("runner_report", "")
        if (
            case in seen
            or not fields.get("plain_cost")
            or not fields.get("agentos_replace")
            or not fields.get("risk")
        ):
            raise ValueError(f"{target} backend report is duplicate or incomplete: {case}")
        seen.add(case)
        reports.append(
            {
                "case": fields.get("runner_report", ""),
                "plain_cost": fields.get("plain_cost", ""),
                "agentos_replace": fields.get("agentos_replace", ""),
                "risk": fields.get("risk", ""),
                "status": fields.get("status", ""),
            }
        )
    expected = set(BACKEND_REPORT_CASES[target])
    if seen != expected:
        raise ValueError(
            f"{target} backend report identity set differs: "
            f"missing={sorted(expected - seen)} extra={sorted(seen - expected)}"
        )
    return reports


def collect_cost_replacements(plain_dir: Path, agentos_dir: Path) -> list[dict[str, object]]:
    plain_reports = collect_backend_reports(plain_dir, "plain")
    agentos_reports = collect_backend_reports(agentos_dir, "agentos")
    plain_by_cost = {row["plain_cost"]: row for row in plain_reports if row.get("plain_cost")}
    rows: list[dict[str, object]] = []
    for row in agentos_reports:
        plain_cost = row.get("plain_cost", "")
        if not plain_cost:
            continue
        plain_row = plain_by_cost.get(plain_cost, {})
        rows.append(
            {
                "case": row.get("case", ""),
                "plain_cost": plain_cost,
                "agentos_replace": row.get("agentos_replace", ""),
                "risk": row.get("risk", ""),
                "plain_case": plain_row.get("case", ""),
                "preserved_from_plain": 1 if plain_cost in plain_by_cost else 0,
                "status": row.get("status", ""),
            }
        )
    return rows


def declared_nonnegative_int(path: Path, key: str) -> int:
    values: list[str] = []
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if ";" in line or line.count("=") != 1:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            values.append(value.strip())
    if len(values) != 1 or not re.fullmatch(r"0|[1-9][0-9]*", values[0]):
        raise ValueError(f"{path.name} must declare one canonical {key}")
    return int(values[0])


def collect_runner_tick_evidence(plain_dir: Path) -> dict[str, str]:
    declared = {
        declared_nonnegative_int(plain_dir / file_name, "runtime_cases")
        for file_name in ("rp_backend", "rp_backend_exec")
    }
    if declared != {0}:
        raise ValueError("Plain runner evidence requires runtime_cases=0")
    return {
        "status": RUNNER_TICK_STATUS_UNAVAILABLE,
        "reason": RUNNER_TICK_REASON_PLAIN_ZERO,
    }


def _canonical_u64(value: str, target: str, field: str) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise ValueError(f"{target} backend query receipt has a non-canonical {field}")
    if len(value) > 20:
        raise ValueError(f"{target} backend query receipt {field} exceeds uint64")
    parsed = int(value)
    if parsed > U64_MAX:
        raise ValueError(f"{target} backend query receipt {field} exceeds uint64")
    return parsed


def collect_backend_query_receipt(
    state_dir: Path, target: str
) -> dict[str, object]:
    if target not in BACKEND_QUERY_EXPECTED_BACKENDS:
        raise ValueError(f"unknown backend query receipt target: {target}")
    text = require_file_text(state_dir, "rp_backend")
    candidates = [
        (line_no, raw)
        for line_no, raw in enumerate(text.splitlines(), 1)
        if "query_workload=" in raw
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"{target} rp_backend must contain exactly one backend query receipt"
        )
    line_no, raw = candidates[0]
    parts = raw.split(";")
    if any(part.count("=") != 1 for part in parts):
        raise ValueError(
            f"{target} backend query receipt is malformed at rp_backend:{line_no}"
        )
    pairs = [part.split("=", 1) for part in parts]
    if tuple(key for key, _value in pairs) != BACKEND_QUERY_RECEIPT_FIELDS:
        raise ValueError(
            f"{target} backend query receipt field order differs at rp_backend:{line_no}"
        )
    if any(not value or value.strip() != value for _key, value in pairs):
        raise ValueError(
            f"{target} backend query receipt has a non-canonical value at "
            f"rp_backend:{line_no}"
        )

    raw_values = dict(pairs)
    numeric_fields = {
        "dataset_records",
        "query_operations",
        "query_matches",
        "records_examined",
    }
    receipt: dict[str, object] = {}
    for field in BACKEND_QUERY_RECEIPT_FIELDS:
        value = raw_values[field]
        if field in numeric_fields:
            receipt[field] = _canonical_u64(value, target, field)
        elif field == "result_digest":
            _canonical_u64(value, target, field)
            receipt[field] = value
        else:
            receipt[field] = value
    return receipt


def compare_backend_query_receipts(
    plain_dir: Path, agentos_dir: Path
) -> dict[str, dict[str, object]]:
    receipts = {
        "plain": collect_backend_query_receipt(plain_dir, "plain"),
        "agentos": collect_backend_query_receipt(agentos_dir, "agentos"),
    }
    plain = receipts["plain"]
    agentos = receipts["agentos"]
    for target, row in receipts.items():
        if row["backend"] != BACKEND_QUERY_EXPECTED_BACKENDS[target]:
            raise ValueError(f"{target} backend query receipt names the wrong backend")
    if any(plain[field] != agentos[field] for field in BACKEND_QUERY_COMMON_FIELDS):
        raise ValueError("plain and AgentOS backend query receipt common fields differ")
    if any(
        plain[field] != expected
        for field, expected in BACKEND_QUERY_EXPECTED_COMMON.items()
    ):
        raise ValueError("backend query receipt workload differs from the release contract")

    plain_examined = int(plain["records_examined"])
    agentos_examined = int(agentos["records_examined"])
    expected_plain = int(plain["dataset_records"]) * int(plain["query_operations"])
    if plain_examined != expected_plain:
        raise ValueError("plain backend query receipt does not account for the full scan")
    if not int(agentos["query_matches"]) <= agentos_examined < plain_examined:
        raise ValueError("AgentOS backend query receipt does not prove an index reduction")
    return receipts


def verify_backend_costs(plain_dir: Path, agentos_dir: Path) -> int:
    plain_costs = collect_plain_costs(plain_dir, "plain")
    agentos_costs = collect_plain_costs(agentos_dir, "agentos")
    if not plain_costs:
        raise ValueError("plain backend report has no tagged reference cost catalog")
    missing = sorted(plain_costs - agentos_costs)
    if missing:
        raise ValueError("AgentOS backend report is missing plain_cost items: " + ", ".join(missing))
    return len(plain_costs)


def _canonical_count(values: dict[str, str], key: str, label: str) -> int:
    value = values.get(key, "")
    if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise ValueError(f"{label} has a non-canonical {key}")
    return int(value)


def verify_run_result(
    plain_path: Path,
    agentos_path: Path,
    *,
    plain_state_dir: Path,
    agentos_state_dir: Path,
    plain_state_files: int,
    agentos_state_files: int,
) -> int:
    plain = parse_key_value_path(plain_path, "plain Host run result")
    agentos = parse_key_value_path(agentos_path, "AgentOS Host run result")
    if plain_path.resolve(strict=True) == agentos_path.resolve(strict=True):
        raise ValueError("plain and AgentOS Host run results must be independent")
    for target, label, values, state_dir, expected_files in (
        ("plain", "plain", plain, plain_state_dir, plain_state_files),
        ("agentos", "AgentOS", agentos, agentos_state_dir, agentos_state_files),
    ):
        chapter, init_proc = RUN_RESULT_IDENTITIES[target]
        if values.get("host_runner") != "plain_ucore_action_runner":
            raise ValueError(f"{label} Host run result has an invalid producer")
        if (
            values.get("target") != target
            or values.get("chapter") != chapter
            or values.get("init_proc") != init_proc
        ):
            raise ValueError(f"{label} Host run result has an invalid target identity")
        if values.get("status") != "ready":
            raise ValueError(f"{label} Host run result is not ready")
        if values.get("passed") != "1":
            raise ValueError(f"{label} Host run did not pass")
        if values.get("qemu_orch_passed") != "1":
            raise ValueError(f"{label} Host run result is missing qemu_orch_passed=1")
        success_fields = {
            "build_returncode": "0",
            "guest_returncode": "0",
            "failure_phase": "",
            "failure_reason": "",
            "qemu_timed_out": "0",
            "qemu_output_eof": "1",
        }
        natural_exit = (
            values.get("guest_raw_returncode") == "0"
            and values.get("qemu_runner_terminated") == "0"
            and values.get("qemu_runner_signals") == ""
        )
        controlled_exit = (
            re.fullmatch(r"-?[1-9][0-9]*", values.get("guest_raw_returncode", ""))
            is not None
            and values.get("qemu_runner_terminated") == "1"
            and values.get("qemu_runner_signals") == "15"
        )
        if (
            any(values.get(key) != value for key, value in success_fields.items())
            or not (natural_exit or controlled_exit)
        ):
            raise ValueError(f"{label} Host run result has contradictory success fields")
        if _canonical_count(values, "extracted_state_files", label) != expected_files:
            raise ValueError(f"{label} Host run result does not bind the Guest state count")
        try:
            receipt_files, receipt_sha256 = guest_state_inventory_sha256(state_dir)
        except StateManifestError as error:
            raise ValueError(f"{label} Guest state receipt input is unsafe: {error}") from error
        if receipt_files != expected_files:
            raise ValueError(f"{label} Guest state summary contradicts its inventory")
        if values.get("guest_state_receipt_schema") != GUEST_STATE_RECEIPT_SCHEMA:
            raise ValueError(f"{label} Host run result has an unsupported Guest state receipt schema")
        if _canonical_count(values, "guest_state_files", label) != receipt_files:
            raise ValueError(f"{label} Host run result does not bind the Guest state count")
        digest = values.get("guest_state_sha256", "")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"{label} Host run result has a non-canonical Guest state digest")
        if digest != receipt_sha256:
            raise ValueError(f"{label} Host run result does not bind the Guest state contents")
    plain_actions = _canonical_count(plain, "embedded_action_records", "plain")
    agentos_actions = _canonical_count(
        agentos, "embedded_action_records", "AgentOS"
    )
    if plain_actions != agentos_actions:
        raise ValueError("embedded action record count differs between plain and AgentOS")
    return plain_actions


def _read_bound_text(path: Path, label: str, limit: int) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or unsafe")
    data = path.read_bytes()
    if not data or len(data) > limit or b"\x00" in data:
        raise ValueError(f"{label} has an invalid size")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error


def verify_action_inputs(
    expected_actions: int,
    plain_log: Path,
    agentos_log: Path,
    seeded_summary: Path,
) -> None:
    def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"seeded action summary has a duplicate field: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> object:
        raise ValueError(f"seeded action summary has a non-finite value: {value}")

    try:
        seeded = json.loads(_read_bound_text(
            seeded_summary, "seeded action summary", 2 * 1024 * 1024
        ), object_pairs_hook=strict_object, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise ValueError("seeded action summary is invalid JSON") from error
    kinds = seeded.get("action_kinds") if isinstance(seeded, dict) else None
    if (
        not isinstance(seeded, dict)
        or seeded.get("status") != "ready"
        or seeded.get("action") != "/actions/research/rerun"
        or type(seeded.get("action_count")) is not int
        or seeded.get("action_count") != expected_actions
        or not isinstance(kinds, list)
        or len(kinds) != expected_actions
        or any(not isinstance(kind, str) or not kind for kind in kinds)
        or len(set(kinds)) != expected_actions
    ):
        raise ValueError("seeded action summary differs from the Host run receipts")
    patterns = (
        re.compile(r"rp_web_export: host_reader_actions=(0|[1-9][0-9]*)\Z"),
        re.compile(r"rp_compare_plain: host_actions=(0|[1-9][0-9]*) verified\Z"),
    )
    ansi = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    for path, label in ((plain_log, "plain Guest log"), (agentos_log, "AgentOS Guest log")):
        lines = [ansi.sub("", line).rstrip("\r") for line in _read_bound_text(
            path, label, 32 * 1024 * 1024
        ).splitlines()]
        for pattern in patterns:
            matches = [match for line in lines if (match := pattern.fullmatch(line))]
            if len(matches) != 1 or int(matches[0].group(1)) != expected_actions:
                raise ValueError(f"{label} action marker differs from the Host run receipts")


def verify_agentos_evidence(agentos_dir: Path) -> int:
    checked = 0
    for file_name, tokens in AGENTOS_EVIDENCE_REQUIREMENTS.items():
        text = require_file_text(agentos_dir, file_name)
        for token in tokens:
            if token not in text:
                raise ValueError(f"AgentOS evidence file {file_name} missing token: {token}")
            checked += 1
    return checked


def collect_scenario_evidence(agentos_dir: Path) -> list[dict[str, object]]:
    texts = {
        file_name: require_file_text(agentos_dir, file_name)
        for file_name in AGENTOS_EVIDENCE_REQUIREMENTS
    }
    rows = expected_scenario_rows()
    for row in rows:
        expected_matches = len(row["tokens"])
        matched = [
            item
            for item in row["tokens"]
            if item["token"] in texts[item["source"]]
        ]
        row["tokens"] = matched
        row["matched"] = len(matched)
        row["sources"] = sorted({item["source"] for item in matched})
        row["status"] = "ready" if len(matched) == expected_matches else "partial"
    return rows




def verify_agentos_mainflow_stages(agentos_dir: Path) -> int:
    summary_sources = read_summary(agentos_dir).get("files", [])
    if not isinstance(summary_sources, list) or not all(
        isinstance(item, str) for item in summary_sources
    ):
        raise ValueError("extract summary has an invalid files inventory")
    allowed_sources = set(summary_sources)
    mainflow_data = read_inventory_source(
        agentos_dir, "rp_agentos_mainflow", allowed_sources
    )
    telemetry_records = parse_canonical_mainflow_telemetry(
        mainflow_data, label="AgentOS mainflow telemetry"
    )
    telemetry_by_stage = {record["stage"]: True for record in telemetry_records}

    verified = 0
    for spec in MAIN_FLOW_SOURCE_SPECS:
        if not telemetry_by_stage[spec.stage]:
            raise ValueError(
                f"AgentOS mainflow telemetry fields are not canonical: {spec.stage}"
            )
        source_data = read_inventory_source(
            agentos_dir, spec.source, allowed_sources
        )
        if not source_has_unique_exact_field(
            source_data, spec.claim_key, spec.claim_value
        ):
            raise ValueError(
                f"AgentOS mainflow Host-derived claim failed: {spec.stage}"
            )
        if not source_has_unique_exact_field(
            source_data, "status", spec.source_status
        ):
            raise ValueError(
                f"AgentOS mainflow Host-derived source status failed: {spec.stage}"
            )
        if not source_records_are_canonical(source_data):
            raise ValueError(
                f"AgentOS mainflow Host-derived source is not canonical: {spec.stage}"
            )
        verified += 1
    return verified


def verify_agentos_mainflow_facts(agentos_dir: Path) -> int:
    text = require_file_text(agentos_dir, "rp_agentos_mainflow")
    missing = [token for token in AGENTOS_MAINFLOW_FACTS if token not in text]
    if missing:
        raise ValueError("AgentOS mainflow is missing kernel fact records: " + ",".join(missing))
    return len(AGENTOS_MAINFLOW_FACTS)


def verify_orch_timing(
    state_dir: Path,
    label: str,
    required_agent_roles: dict[str, str] | None = None,
) -> tuple[int, int, int]:
    target = "agentos" if required_agent_roles else "plain"
    measurement = measure_program_inventory(state_dir, target)
    records = [
        parse_record(line)
        for line in require_file_text(state_dir, "rp_orch_timing").splitlines()[2:]
    ]
    program_count = measurement["programs_observed"]
    agent_launcher_count = sum(
        record is not None and record.get("launcher") == "agent_create_role"
        for record in records
    )
    support_launcher_count = program_count - agent_launcher_count
    if program_count < 60:
        raise ValueError(f"{label} timing records too few: {program_count}")
    if required_agent_roles and support_launcher_count == 0:
        raise ValueError(f"{label} timing records do not show delegated workers")
    return program_count, agent_launcher_count, support_launcher_count


def compare_state(
    plain_dir: Path,
    agentos_dir: Path,
    min_common_files: int,
    *,
    plain_run_result: Path,
    agentos_run_result: Path,
    plain_log: Path,
    agentos_log: Path,
    seeded_summary: Path,
) -> dict[str, object]:
    plain_summary = read_summary(plain_dir)
    agentos_summary = read_summary(agentos_dir)
    plain_files = set(plain_summary.get("files", []))
    agentos_files = set(agentos_summary.get("files", []))
    missing_files = sorted(plain_files - agentos_files)
    common_files = plain_files & agentos_files
    if missing_files:
        raise ValueError("AgentOS state is missing plain files: " + ", ".join(missing_files[:20]))
    if len(common_files) < min_common_files:
        raise ValueError(f"common state files too few: {len(common_files)} < {min_common_files}")
    if int(agentos_summary.get("extracted_state_files", 0)) < int(plain_summary.get("extracted_state_files", 0)):
        raise ValueError("AgentOS extracted fewer state files than plain uCore")

    plain_evidence = collect_evidence_counts(plain_dir, plain_files, "plain")
    agentos_evidence = collect_evidence_counts(agentos_dir, agentos_files, "agentos")
    plain_good_lines = collect_good_status_lines(plain_dir, plain_files, "plain")
    agentos_index = collect_agentos_status_index(agentos_dir, agentos_files)
    missing_status: list[StateLine] = []
    for line in plain_good_lines:
        if line.file_name == "rp_backend_exec":
            continue
        key = first_key(line.anchor)
        if (line.file_name, key) in ADAPTED_ANCHORS:
            if not has_good_record_with_key(agentos_dir, line.file_name, key):
                missing_status.append(line)
            continue
        statuses = agentos_index.get((line.file_name, line.anchor), set())
        if line.status not in statuses and not statuses:
            missing_status.append(line)
    if missing_status:
        details = [
            f"{line.file_name}:{line.line_no}:{line.anchor}:status={line.status}"
            for line in missing_status[:20]
        ]
        raise ValueError("AgentOS state is missing plain compatibility records: " + ", ".join(details))

    preserved_costs = verify_backend_costs(plain_dir, agentos_dir)
    cost_replacements = collect_cost_replacements(plain_dir, agentos_dir)
    runner_tick_evidence = collect_runner_tick_evidence(plain_dir)
    backend_query_receipts = compare_backend_query_receipts(plain_dir, agentos_dir)
    embedded_action_records = verify_run_result(
        plain_run_result,
        agentos_run_result,
        plain_state_dir=plain_dir,
        agentos_state_dir=agentos_dir,
        plain_state_files=int(plain_summary.get("extracted_state_files", 0)),
        agentos_state_files=int(agentos_summary.get("extracted_state_files", 0)),
    )
    verify_action_inputs(
        embedded_action_records, plain_log, agentos_log, seeded_summary
    )
    agentos_evidence_checks = verify_agentos_evidence(agentos_dir)
    scenario_evidence = collect_scenario_evidence(agentos_dir)
    host_derived_mainflow_stages = verify_agentos_mainflow_stages(agentos_dir)
    agentos_mainflow_facts = verify_agentos_mainflow_facts(agentos_dir)
    plain_timing_records, plain_agent_launches, plain_fork_launches = verify_orch_timing(
        plain_dir, "plain"
    )
    (
        agentos_timing_records,
        agentos_agent_launches,
        agentos_worker_launches,
    ) = verify_orch_timing(
        agentos_dir,
        "AgentOS",
        required_agent_roles=AGENTOS_REQUIRED_AGENT_ROLES,
    )
    if agentos_timing_records < plain_timing_records:
        raise ValueError("AgentOS timing record count is less than plain uCore")
    return {
        "plain_files": int(plain_summary.get("extracted_state_files", 0)),
        "agentos_files": int(agentos_summary.get("extracted_state_files", 0)),
        "common_files": len(common_files),
        "agentos_extra_files": len(agentos_files - plain_files),
        "checked_compatibility_records": len(plain_good_lines),
        "plain_reference_products": plain_evidence["reference_products"],
        "agentos_reference_products": agentos_evidence["reference_products"],
        "plain_reference_records": plain_evidence["reference_records"],
        "agentos_reference_records": agentos_evidence["reference_records"],
        "plain_reference_identities": plain_evidence["reference_identities"],
        "agentos_reference_identities": agentos_evidence["reference_identities"],
        "guest_source_bound_runtime_records": agentos_evidence[
            "guest_source_bound_runtime_records"
        ],
        "preserved_plain_costs": preserved_costs,
        "cost_replacements": cost_replacements,
        "cost_replacement_count": len(cost_replacements),
        "runner_tick_status": runner_tick_evidence["status"],
        "runner_tick_reason": runner_tick_evidence["reason"],
        "backend_query_receipts": backend_query_receipts,
        "embedded_action_records": embedded_action_records,
        "run_result_match": 1,
        "agentos_evidence_checks": agentos_evidence_checks,
        "scenario_evidence": scenario_evidence,
        "agentos_mainflow_verification_origin": "host_inventory",
        "host_derived_mainflow_stages": host_derived_mainflow_stages,
        "agentos_mainflow_facts": agentos_mainflow_facts,
        "plain_timing_records": plain_timing_records,
        "plain_agent_launches": plain_agent_launches,
        "plain_fork_launches": plain_fork_launches,
        "agentos_timing_records": agentos_timing_records,
        "agentos_agent_launches": agentos_agent_launches,
        "agentos_worker_launches": agentos_worker_launches,
        "status": "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare plain and AgentOS research-platform state files.")
    parser.add_argument("--plain-dir", type=Path, required=True)
    parser.add_argument("--agentos-dir", type=Path, required=True)
    parser.add_argument("--plain-run-result", type=Path, required=True)
    parser.add_argument("--agentos-run-result", type=Path, required=True)
    parser.add_argument("--plain-log", type=Path, required=True)
    parser.add_argument("--agentos-log", type=Path, required=True)
    parser.add_argument("--seeded-summary", type=Path, required=True)
    parser.add_argument("--min-common-files", type=int, default=240)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    summary = compare_state(
        args.plain_dir,
        args.agentos_dir,
        args.min_common_files,
        plain_run_result=args.plain_run_result,
        agentos_run_result=args.agentos_run_result,
        plain_log=args.plain_log,
        agentos_log=args.agentos_log,
        seeded_summary=args.seeded_summary,
    )
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        "dual_platform_state_compare: plain_files={plain_files} agentos_files={agentos_files} common_files={common_files} agentos_extra_files={agentos_extra_files} checked_compatibility_records={checked_compatibility_records} guest_source_bound_runtime_records={guest_source_bound_runtime_records} preserved_plain_costs={preserved_plain_costs} embedded_action_records={embedded_action_records} run_result_match={run_result_match} agentos_evidence_checks={agentos_evidence_checks} host_derived_mainflow_stages={host_derived_mainflow_stages} agentos_mainflow_facts={agentos_mainflow_facts} plain_timing_records={plain_timing_records} plain_agent_launches={plain_agent_launches} plain_fork_launches={plain_fork_launches} agentos_timing_records={agentos_timing_records} agentos_agent_launches={agentos_agent_launches} agentos_worker_launches={agentos_worker_launches} status={status}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
