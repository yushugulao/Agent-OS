#!/usr/bin/env python3
"""Offline replay for Host-derived dual-platform evidence."""
from __future__ import annotations

import re

from dual_state_evidence_contract import (
    AGENTOS_REQUIRED_AGENT_ROLES,
    AGENTOS_ROLE_NUMBERS,
    AGENTOS_EVIDENCE_REQUIREMENTS,
    BACKEND_REPORT_ARTIFACTS,
    BACKEND_REPORT_CASES,
    MAIN_FLOW_SOURCE_ARTIFACTS,
    MAIN_FLOW_SOURCE_SPECS,
    MAIN_FLOW_TELEMETRY_ARTIFACT,
    PLATFORM_PROGRAMS,
    PROGRAM_LEDGER_ARTIFACTS,
    fnv1a64,
)
from evidence_semantic_common import (
    EvidenceSemanticError,
    ValidationContext,
    _json,
    _regular_bytes,
)
from dual_state_archive import validate_state_archives
from check_host_platform_alignment import (
    CAPABILITY_GROUPS,
    collect_source_names,
    parse_canonical_mainflow_telemetry,
    runtime_candidates,
)


ALIGNMENT_FIELDS = {
    "status", "host_dir", "host_modules", "tracked_host_modules",
    "untracked_host_modules", "plain_sources", "agentos_sources",
    "plain_state_files", "agentos_state_files", "runtime_state_checked",
    "runtime_evidence_verified", "program_inventory_verified",
    "mainflow_host_verified", "mainflow_verification_origin",
    "mainflow_host_stages", "mainflow_host_assertions_executed",
    "mainflow_host_assertions_passed", "mainflow_host_telemetry_sequence",
    "mainflow_host_telemetry_source", "mainflow_host_telemetry_bytes",
    "mainflow_host_telemetry_hash",
    "mainflow_host_sources", "plain_programs_observed",
    "agentos_programs_observed", "plain_evidence_role", "groups_ok",
    "groups_total", "groups", "failures", "untracked_host_module_sample",
}
SOURCE_FIELDS = {
    "stage", "source", "claim_key", "claim_value", "source_status",
    "source_bytes", "source_hash", "claim_verified", "status_verified",
    "telemetry_fields", "telemetry_verified",
}
GROUP_FIELDS = {
    "name", "host_modules", "plain_sources", "agentos_sources",
    "reader_keywords", "status", "missing_host", "missing_plain",
    "missing_agentos", "missing_reader", "plain_runtime_hits",
    "agentos_runtime_hits",
}
FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


def _canonical_lines(raw: bytes, label: str) -> list[str]:
    if not raw or not raw.endswith(b"\n") or b"\r" in raw or b"\0" in raw:
        raise EvidenceSemanticError(f"{label} is not canonical complete text")
    try:
        lines = raw[:-1].decode("ascii", errors="strict").split("\n")
    except UnicodeDecodeError as error:
        raise EvidenceSemanticError(f"{label} is not canonical ASCII") from error
    if any(not line for line in lines):
        raise EvidenceSemanticError(f"{label} contains an empty record")
    return lines


def _records(raw: bytes, label: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for number, line in enumerate(_canonical_lines(raw, label), 1):
        record: dict[str, str] = {}
        for item in line.split(";"):
            if item.count("=") != 1:
                raise EvidenceSemanticError(f"{label} line {number} is malformed")
            key, value = item.split("=", 1)
            if not FIELD_NAME.fullmatch(key) or not value or key in record:
                raise EvidenceSemanticError(f"{label} line {number} is malformed")
            record[key] = value
        result.append(record)
    return result


def _has_unique_field(records: list[dict[str, str]], key: str, value: str) -> bool:
    return [record[key] for record in records if key in record] == [value]


def _validate_telemetry(ctx: ValidationContext) -> tuple[tuple[str, ...], bytes]:
    raw = _regular_bytes(
        ctx.raw_dir / MAIN_FLOW_TELEMETRY_ARTIFACT, "Mainflow telemetry source"
    )
    try:
        records = parse_canonical_mainflow_telemetry(
            raw, label="Mainflow telemetry source"
        )
    except ValueError as error:
        raise EvidenceSemanticError(str(error)) from error
    return tuple(record["stage"] for record in records), raw


def _validate_sources(ctx: ValidationContext, rows: object) -> None:
    if not isinstance(rows, list) or len(rows) != len(MAIN_FLOW_SOURCE_SPECS):
        raise EvidenceSemanticError("Host-derived Mainflow source inventory differs")
    for row, spec in zip(rows, MAIN_FLOW_SOURCE_SPECS):
        if not isinstance(row, dict) or set(row) != SOURCE_FIELDS:
            raise EvidenceSemanticError("Host-derived Mainflow source schema differs")
        telemetry_fields = [
            {"key": key, "value": value} for key, value in spec.telemetry_fields
        ]
        raw = _regular_bytes(
            ctx.raw_dir / MAIN_FLOW_SOURCE_ARTIFACTS[spec.source],
            f"Mainflow source {spec.source}",
        )
        records = _records(raw, f"Mainflow source {spec.source}")
        expected = {
            "stage": spec.stage,
            "source": spec.source,
            "claim_key": spec.claim_key,
            "claim_value": spec.claim_value,
            "source_status": spec.source_status,
            "source_bytes": len(raw),
            "source_hash": fnv1a64(raw),
            "claim_verified": True,
            "status_verified": True,
            "telemetry_fields": telemetry_fields,
            "telemetry_verified": True,
        }
        if row != expected:
            raise EvidenceSemanticError(f"Host-derived Mainflow receipt differs: {spec.stage}")
        if not _has_unique_field(records, spec.claim_key, spec.claim_value) or not _has_unique_field(
            records, "status", spec.source_status
        ):
            raise EvidenceSemanticError(f"Mainflow source fields differ: {spec.stage}")


def _validate_evidence_requirements(ctx: ValidationContext) -> None:
    for source, tokens in AGENTOS_EVIDENCE_REQUIREMENTS.items():
        artifact = (
            MAIN_FLOW_TELEMETRY_ARTIFACT
            if source == "rp_agentos_mainflow"
            else MAIN_FLOW_SOURCE_ARTIFACTS[source]
        )
        records = _records(
            _regular_bytes(ctx.raw_dir / artifact, f"AgentOS evidence source {source}"),
            f"AgentOS evidence source {source}",
        )
        for token in tokens:
            key, expected = token.split("=", 1)
            if not _has_unique_field(records, key, expected):
                raise EvidenceSemanticError(f"AgentOS evidence source token differs: {source}:{key}")


def _validate_cost_sources(ctx: ValidationContext, state: dict[str, object]) -> None:
    reports: dict[str, list[dict[str, str]]] = {}
    for target in ("plain", "agentos"):
        raw = _regular_bytes(
            ctx.raw_dir / BACKEND_REPORT_ARTIFACTS[target], f"{target} backend report"
        )
        all_lines = _canonical_lines(raw, f"{target} backend report")
        lines = [
            line for line in all_lines
            if any(item.startswith("runner_report=") for item in line.split(";"))
        ]
        rows = [
            _records((line + "\n").encode("ascii"), f"{target} backend report")[0]
            for line in lines
        ]
        base = {"runner_report", "plain_cost", "agentos_replace", "risk", "status"}
        envelope = {"evidence_role", "catalog_generation"} if target == "agentos" else set()
        provenance_invalid = (
            any(row.get("evidence_role") != "demo_reference" or
                row.get("catalog_generation") != "demo_expected" for row in rows)
            if target == "agentos" else any(
                [line for line in all_lines if line.startswith(key + "=")] != [key + "=" + expected]
                for key, expected in (
                    ("evidence_file_role", "demo_reference"),
                    ("evidence_file_generation", "demo_expected"),
                    ("evidence_file_status", "reference_ready"),
                )
            )
        )
        cases = [row.get("runner_report") for row in rows]
        if (
            set(cases) != set(BACKEND_REPORT_CASES[target])
            or len(cases) != len(set(cases))
            or any(set(row) != base | envelope for row in rows)
            or any(row.get("status") != "reference_ready" for row in rows)
            or provenance_invalid
        ):
            raise EvidenceSemanticError(f"{target} backend cost source differs")
        reports[target] = rows
    plain_by_cost = {row["plain_cost"]: row["runner_report"] for row in reports["plain"]}
    expected = [{
        "case": row["runner_report"], "plain_cost": row["plain_cost"],
        "agentos_replace": row["agentos_replace"], "risk": row["risk"],
        "plain_case": plain_by_cost.get(row["plain_cost"], ""),
        "preserved_from_plain": int(row["plain_cost"] in plain_by_cost),
        "status": row["status"],
    } for row in reports["agentos"]]
    if state.get("cost_replacements") != expected:
        raise EvidenceSemanticError("cost replacement rows differ from raw backend reports")


def _validate_groups(
    value: dict[str, object], inventories: dict[str, set[str]]
) -> None:
    groups = value.get("groups")
    if (
        not isinstance(groups, list)
        or len(groups) != len(CAPABILITY_GROUPS)
        or value.get("groups_total") != len(groups)
    ):
        raise EvidenceSemanticError("Host alignment group inventory differs")
    for group, contract in zip(groups, CAPABILITY_GROUPS):
        if not isinstance(group, dict) or set(group) != GROUP_FIELDS:
            raise EvidenceSemanticError("Host alignment group schema differs")
        missing = ("missing_host", "missing_plain", "missing_agentos", "missing_reader")
        plain_hits = [
            name for name in runtime_candidates(contract, contract.plain_sources)
            if name in inventories["plain"]
        ]
        agentos_hits = [
            name for name in runtime_candidates(contract, contract.agentos_sources)
            if name in inventories["agentos"]
        ]
        if (
            group.get("name") != contract.name or group.get("status") != "ok"
            or group.get("host_modules") != len(contract.host_modules)
            or group.get("plain_sources") != len(contract.plain_sources)
            or group.get("agentos_sources") != len(contract.agentos_sources)
            or group.get("reader_keywords") != len(contract.reader_keywords)
            or any(group.get(field) != [] for field in missing)
            or not plain_hits or group.get("plain_runtime_hits") != plain_hits
            or not agentos_hits or group.get("agentos_runtime_hits") != agentos_hits
        ):
            raise EvidenceSemanticError("Host alignment group semantics differ")
    if value.get("groups_ok") != len(groups):
        raise EvidenceSemanticError("Host alignment group aggregate differs")


def _validate_program_ledger(
    ctx: ValidationContext, target: str
) -> tuple[dict[str, int], tuple[str, ...]]:
    raw = _regular_bytes(
        ctx.raw_dir / PROGRAM_LEDGER_ARTIFACTS[target], f"{target} program ledger"
    )
    rows = _records(raw, f"{target} program ledger")
    plain = target == "plain"
    expected_headers = (
        ({"orchestrator": "rp_seed_orch"}, {"launcher": "fork_seeded"})
        if plain else
        ({"orchestrator": "rp_orch"}, {"launcher": "mixed_attested"})
    )
    if len(rows) < 3 or tuple(rows[:2]) != expected_headers:
        raise EvidenceSemanticError(f"{target} program ledger headers differ")
    plain_fields = {"program", "launcher", "ok", "code", "elapsed_ms"}
    agent_fields = plain_fields | {
        "role", "identity_source", "is_agent", "agent_role",
        "filesystem_domain", "filesystem_capabilities",
    }
    programs: list[str] = []
    agent_launches = 0
    for row in rows[2:]:
        program = row.get("program")
        if (
            set(row) != (plain_fields if plain else agent_fields)
            or not isinstance(program, str)
            or re.fullmatch(r"rp_[a-z0-9_]+", program) is None
            or program in programs
            or row.get("ok") != "1"
            or row.get("code") != "0"
            or re.fullmatch(r"0|[1-9][0-9]*", row.get("elapsed_ms", "")) is None
        ):
            raise EvidenceSemanticError(f"{target} program ledger row differs")
        programs.append(program)
        if plain:
            if row.get("launcher") != "fork_seeded":
                raise EvidenceSemanticError("plain program launcher differs")
            continue
        role = AGENTOS_REQUIRED_AGENT_ROLES.get(program)
        is_agent = role is not None
        expected = {
            "launcher": "agent_create_role" if is_agent else "agent_worker_create",
            "role": role if is_agent else "plain",
            "identity_source": "child_after_exec",
            "is_agent": "1" if is_agent else "0",
            "agent_role": str(AGENTOS_ROLE_NUMBERS[role]) if is_agent else "0",
        }
        if any(row.get(key) != value for key, value in expected.items()) or any(
            re.fullmatch(r"[1-9][0-9]*", row.get(key, "")) is None
            for key in ("filesystem_domain", "filesystem_capabilities")
        ):
            raise EvidenceSemanticError("AgentOS program identity differs")
        agent_launches += int(is_agent)
    if tuple(programs) != PLATFORM_PROGRAMS:
        raise EvidenceSemanticError(f"{target} program identities differ from the trusted manifest")
    digest = 1469598103934665603
    for program in programs:
        digest = fnv1a64(program.encode("ascii") + b"\0", digest)
    return {
        "program_source_bytes": len(raw),
        "program_source_hash": fnv1a64(raw),
        "program_names_digest": digest,
        "programs_observed": len(programs),
        "agent_launches": agent_launches,
    }, tuple(programs)


def validate_program_ledgers(
    ctx: ValidationContext,
    state: dict[str, object],
    plain_receipt: dict[str, int],
    agentos_receipt: dict[str, int],
) -> None:
    plain, plain_programs = _validate_program_ledger(ctx, "plain")
    agentos, agentos_programs = _validate_program_ledger(ctx, "agentos")
    receipt_fields = {
        "program_source_bytes", "program_source_hash", "program_names_digest",
        "programs_observed",
    }
    if (
        plain_programs != agentos_programs
        or {key: plain[key] for key in receipt_fields} != plain_receipt
        or {key: agentos[key] for key in receipt_fields} != agentos_receipt
        or state["plain_timing_records"] != len(plain_programs)
        or state["plain_agent_launches"] != 0
        or state["plain_fork_launches"] != len(plain_programs)
        or state["agentos_timing_records"] != len(agentos_programs)
        or state["agentos_agent_launches"] != agentos["agent_launches"]
        or state["agentos_worker_launches"]
        != len(agentos_programs) - agentos["agent_launches"]
    ):
        raise EvidenceSemanticError("program ledger receipts or launch aggregates differ")


def validate_dual_alignment(
    ctx: ValidationContext,
    state: dict[str, object],
    reader: dict[str, object],
    plain_programs: int,
    agentos_programs: int,
    inventories: dict[str, set[str]],
) -> None:
    value = _json(ctx.raw_dir / "host-platform-alignment.json", "Host alignment")
    if not isinstance(value, dict) or set(value) != ALIGNMENT_FIELDS:
        raise EvidenceSemanticError("Host alignment schema differs")
    required_true = (
        "runtime_state_checked", "runtime_evidence_verified",
        "program_inventory_verified", "mainflow_host_verified",
    )
    if (
        value.get("status") != "ready"
        or any(value.get(field) is not True for field in required_true)
        or value.get("failures") != []
        or value.get("untracked_host_module_sample") != []
        or value.get("untracked_host_modules") != 0
        or value.get("mainflow_verification_origin") != "host_inventory"
        or value.get("plain_evidence_role") != "demo_reference"
        or value.get("plain_state_files") != state["plain_files"]
        or value.get("agentos_state_files") != state["agentos_files"]
        or reader["plain_state_files"] != state["plain_files"]
        or reader["agentos_state_files"] != state["agentos_files"]
        or value.get("plain_programs_observed") != plain_programs
        or value.get("agentos_programs_observed") != agentos_programs
        or value.get("mainflow_host_stages") != len(MAIN_FLOW_SOURCE_SPECS)
        or value.get("mainflow_host_assertions_executed") != 2 * len(MAIN_FLOW_SOURCE_SPECS)
        or value.get("mainflow_host_assertions_passed")
        != value.get("mainflow_host_assertions_executed")
        or state["host_derived_mainflow_stages"] != value.get("mainflow_host_stages")
        or state["agentos_mainflow_verification_origin"]
        != value.get("mainflow_verification_origin")
    ):
        raise EvidenceSemanticError("Host alignment claims differ from raw evidence")
    tracked = {module for group in CAPABILITY_GROUPS for module in group.host_modules}
    if (
        value.get("host_modules") != len(tracked)
        or value.get("tracked_host_modules") != len(tracked)
        or value.get("plain_sources")
        != len(collect_source_names(ctx.repo_root, "baseline_ucore/user/src"))
        or value.get("agentos_sources")
        != len(collect_source_names(ctx.repo_root, "user/src"))
    ):
        raise EvidenceSemanticError("Host alignment inventory counters differ")
    sequence, telemetry_raw = _validate_telemetry(ctx)
    if (
        value.get("mainflow_host_telemetry_source") != "rp_agentos_mainflow"
        or value.get("mainflow_host_telemetry_bytes") != len(telemetry_raw)
        or value.get("mainflow_host_telemetry_hash") != fnv1a64(telemetry_raw)
        or value.get("mainflow_host_telemetry_sequence") != list(sequence)
    ):
        raise EvidenceSemanticError("Host alignment telemetry sequence differs")
    _validate_sources(ctx, value.get("mainflow_host_sources"))
    _validate_evidence_requirements(ctx)
    _validate_cost_sources(ctx, state)
    _validate_groups(value, inventories)


def validate_complete_dual_state(
    ctx: ValidationContext, state: dict[str, object]
) -> dict[str, set[str]]:
    return validate_state_archives(ctx, state)


__all__ = [
    "validate_complete_dual_state", "validate_dual_alignment", "validate_program_ledgers",
]
