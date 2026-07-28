#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestTheme:
    name: str
    keywords: tuple[str, ...]
    evidence_tokens: tuple[str, ...]


TEST_THEMES: tuple[TestTheme, ...] = (
    TestTheme(
        "core_state_config",
        (
            "json_store",
            "deepseek_key",
            "reset_recovers",
            "documentation",
            "extended_platform",
            "platform_consistency",
            "status_semantics",
            "mature_capabilities",
            "mature_platform",
        ),
        (
            "state_catalog=passed",
            "startup_doctor=passed",
            "consistency=passed",
            "mature_capabilities=passed",
        ),
    ),
    TestTheme(
        "workflow_runtime",
        (
            "demo_recovers",
            "host_workflow",
            "workflow_",
            "execution_cache",
            "run_configurations",
            "run_state",
            "lifecycle_order",
            "execution_observer",
            "execution_control",
            "resource_budget",
            "run_telemetry",
            "runbooks",
            "worker_executor",
            "worker_pool",
            "worker_operations",
            "queue_dashboard",
            "scheduler",
            "user_space_index",
        ),
        (
            "workflow=passed",
            "workflow_runner_detail=passed",
            "execution_scale=passed",
            "operations_scale=passed",
            "runbook_service=passed",
        ),
    ),
    TestTheme(
        "research_workbench",
        (
            "usable_research",
            "research_studio",
            "workbench",
            "project_",
            "research_package",
            "research_object",
            "study_protocol",
            "research_protocols",
            "sop_execution",
            "protocol_",
            "literature",
            "bibtex",
            "systematic_review",
            "knowledge_base",
            "semantic_catalog",
            "notebook",
        ),
        (
            "usable_research=passed",
            "workbench=passed",
            "project_delivery=passed",
            "study_protocol=passed",
            "knowledge_index=passed",
        ),
    ),
    TestTheme(
        "data_lab_science",
        (
            "csv_summary",
            "data_",
            "dataset",
            "calculation",
            "sample_",
            "cohort",
            "study_design",
            "statistical_design",
            "lab_operations",
            "experiment_",
            "training_compliance",
            "analysis_results",
            "visualization",
            "fair_data",
            "publication",
            "model_registry",
            "charts",
            "evaluation_reports",
        ),
        (
            "data_pipeline=passed",
            "lab_governance_ops=passed",
            "statistical_design=passed",
            "experiment_scheduling=passed",
            "model_registry=passed",
            "analysis_results=passed",
        ),
    ),
    TestTheme(
        "agents_llm_compare",
        (
            "llm",
            "prompt_ops",
            "agent_coordination",
            "collaboration",
            "agentcompare",
            "compare_",
            "backend_scenario",
            "comparative_study",
            "decision_support",
            "agentos_",
            "worker",
        ),
        (
            "llm=passed",
            "llm_relay=passed",
            "agent_collaboration=passed",
            "agent_compare=passed",
            "decision_support=passed",
        ),
    ),
    TestTheme(
        "ui_api_delivery",
        (
            "dashboard",
            "web_ui",
            "routes",
            "api",
            "site",
            "export",
            "download",
            "bundle",
            "cards",
            "pages",
            "selects",
            "surface_coverage",
            "delivery",
            "package",
            "real_artifact",
        ),
        (
            "ui_export=passed",
            "host_web_export=passed",
            "static_site=passed",
            "export_package=passed",
            "reserved_research_surfaces=passed",
        ),
    ),
    TestTheme(
        "provenance_review_governance",
        (
            "provenance",
            "lineage",
            "review",
            "release",
            "governance",
            "privacy",
            "ethics",
            "access_governance",
            "reference_integrity",
            "evidence_traceability",
            "risk_register",
            "claim",
            "integrity",
            "coherence",
            "object_namespace",
            "report_validation",
            "result_review",
            "quality",
        ),
        (
            "provenance_view=passed",
            "provenance_query=passed",
            "review_dashboard=passed",
            "release_dossier=passed",
            "regulated_research=passed",
            "integrity_plane=passed",
            "coherence_plane=passed",
        ),
    ),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_host_dir(root: Path) -> Path:
    override = os.environ.get("HOST_PLATFORM_DIR")
    if override:
        return Path(override)
    return root.parent / "research-agent-platform-userland"


def collect_test_names(host_dir: Path) -> list[str]:
    test_file = host_dir / "tests" / "test_platform.py"
    if not test_file.exists():
        raise FileNotFoundError(f"host test file is missing: {test_file}")
    text = test_file.read_text(encoding="utf-8")
    return re.findall(r"^\s+def (test_[A-Za-z0-9_]+)\(", text, re.M)


def classify_test(name: str) -> list[str]:
    lowered = name.lower()
    return [
        theme.name
        for theme in TEST_THEMES
        if any(keyword in lowered for keyword in theme.keywords)
    ]


FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
U64_MASK = (1 << 64) - 1

EXPECTED_RUNTIME_ASSERTIONS = {
    "core_state_config": ("rp_agentos_kernel", "context_snapshot", "present"),
    "workflow_runtime": (
        "rp_backend_exec",
        "runtime_claim_protocol",
        "source-bound-v1",
    ),
    "research_workbench": (
        "rp_agentos_real_task",
        "report_answer",
        "kernel_context_record",
    ),
    "data_lab_science": (
        "rp_agentos_query",
        "metadata_source",
        "kernel_file_index",
    ),
    "agents_llm_compare": ("rp_audit", "evidence_generation", "runtime"),
    "ui_api_delivery": (
        "rp_agentos_timeline",
        "evidence_generation",
        "runtime",
    ),
    "provenance_review_governance": (
        "rp_prov_view",
        "evidence_generation",
        "runtime",
    ),
}

EXPECTED_RUNTIME_SOURCES = {
    theme: assertion[0] for theme, assertion in EXPECTED_RUNTIME_ASSERTIONS.items()
}

BACKEND_RUNTIME_CASES = {
    "workflow-contract": (
        "rp_wfio",
        "execution_plan",
        "workflow-migration-execution-plan:RUN-042:agentcompare",
    ),
    "retry-state": ("rp_retry_plan", "retry_stage", "align"),
    "kernel-context": ("rp_agentos_kernel", "context_snapshot", "present"),
    "kernel-file-query": ("rp_agentos_query", "metadata_source", "kernel_file_index"),
    "kernel-recovery": (
        "rp_agentos_recovery",
        "kernel_tool",
        "action_commit,artifact_update",
    ),
    "kernel-event": ("rp_agentos_timeline", "event_delivery", "kernel_agent_queue"),
    "kernel-audit": ("rp_agentos_audit", "audit_source", "kernel_ledger"),
    "kernel-edit": ("rp_agentos_conflict", "holder_write", "checked"),
}

COMPARATOR_RUNTIME_CASES = {
    "backend": ("rp_backend_exec", "runtime_cases_executed", "8"),
    "consistency": ("rp_consistency", "evidence_generation", "runtime"),
    "kernel-context": ("rp_agentos_kernel", "context_snapshot", "present"),
    "audit": ("rp_audit", "evidence_generation", "runtime"),
    "provenance": ("rp_prov_view", "evidence_generation", "runtime"),
}


def fnv1a64(data: bytes, initial: int = FNV_OFFSET) -> int:
    value = initial
    for byte in data:
        value ^= byte
        value = (value * FNV_PRIME) & U64_MASK
    return value


def parse_record(line: str) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    for item in line.split(";"):
        if item.count("=") != 1:
            return None
        key, value = item.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not value or key in fields:
            return None
        fields[key] = value
    return fields


def parse_positive_int(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def parse_nonnegative_int(value: str | None) -> int | None:
    if value is None or not value.isdecimal():
        return None
    return int(value)


def source_has_exact_field(data: bytes, key: str, value: str) -> bool:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    target = f"{key}={value}"
    matches = 0
    for line in text.splitlines():
        matches += sum(field == target for field in line.split(";"))
    return matches == 1


def fold_file_measurement(
    digest: int, source: str, source_hash: int, source_bytes: int
) -> int:
    digest = fnv1a64(source.encode("utf-8"), digest)
    return fnv1a64(
        source_hash.to_bytes(8, "little") + source_bytes.to_bytes(8, "little"),
        digest,
    )


def fold_backend_measurement(
    digest: int, case_name: str, source: str, source_hash: int, source_bytes: int
) -> int:
    digest = fnv1a64(case_name.encode("utf-8") + b"\0", digest)
    digest = fnv1a64(source.encode("utf-8") + b"\0", digest)
    return fnv1a64(
        source_hash.to_bytes(8, "little") + source_bytes.to_bytes(8, "little"),
        digest,
    )


def validate_comparator_runtime_evidence(state_dir: Path) -> list[str]:
    path = state_dir / "rp_agentcmp"
    if not path.is_file():
        return ["runtime comparator: rp_agentcmp is missing"]
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return ["runtime comparator: rp_agentcmp is not valid UTF-8"]

    errors: list[str] = []
    cases: dict[str, dict[str, str]] = {}
    summaries: list[dict[str, str]] = []
    for number, line in enumerate(lines, 1):
        if "runtime_compare_case=" not in line and "runtime_compare_cases=" not in line:
            continue
        record = parse_record(line)
        if record is None:
            errors.append(f"runtime comparator: rp_agentcmp line {number} is malformed")
            continue
        if "runtime_compare_case" in record:
            case_name = record["runtime_compare_case"]
            if case_name in cases:
                errors.append(f"runtime comparator: duplicate case {case_name}")
            cases[case_name] = record
        else:
            summaries.append(record)

    missing = set(COMPARATOR_RUNTIME_CASES) - set(cases)
    extra = set(cases) - set(COMPARATOR_RUNTIME_CASES)
    if missing:
        errors.append("runtime comparator: missing cases: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("runtime comparator: unknown cases: " + ", ".join(sorted(extra)))

    digest = FNV_OFFSET
    verified = 0
    for case_name, (source, key, value) in COMPARATOR_RUNTIME_CASES.items():
        source_path = state_dir / source
        record = cases.get(case_name)
        if not source_path.is_file():
            errors.append(f"runtime comparator: source is missing: {source}")
            continue
        data = source_path.read_bytes()
        source_hash = fnv1a64(data)
        source_bytes = len(data)
        digest = fold_file_measurement(digest, source, source_hash, source_bytes)
        if record is None:
            continue
        if (
            record.get("evidence_role") != "runtime_verified"
            or record.get("claim_protocol") != "exact-field-v1"
            or record.get("generation") != "runtime"
            or record.get("status") != "verified"
        ):
            errors.append(f"runtime comparator: {case_name} is not source-bound")
        if record.get("source") != source:
            errors.append(f"runtime comparator: {case_name} is bound to the wrong source")
        if (
            parse_positive_int(record.get("source_bytes")) != source_bytes
            or parse_positive_int(record.get("source_hash")) != source_hash
        ):
            errors.append(f"runtime comparator: {case_name} measurement is invalid")
        if not source_has_exact_field(data, key, value):
            errors.append(f"runtime comparator: {case_name} exact assertion failed")
        if (
            parse_positive_int(record.get("assertions_executed")) != 1
            or parse_positive_int(record.get("assertions_passed")) != 1
        ):
            errors.append(f"runtime comparator: {case_name} assertion counts are invalid")
        else:
            verified += 1

    if len(summaries) != 1:
        errors.append("runtime comparator: summary is missing or duplicated")
    else:
        summary = summaries[0]
        expected = len(COMPARATOR_RUNTIME_CASES)
        if (
            summary.get("evidence_role") != "runtime_verified"
            or summary.get("claim_protocol") != "exact-field-v1"
            or summary.get("evidence_generation") != "runtime"
            or summary.get("status") != "verified"
        ):
            errors.append("runtime comparator: summary is not source-bound")
        if (
            parse_positive_int(summary.get("runtime_compare_cases")) != expected
            or parse_positive_int(summary.get("runtime_assertions_executed")) != expected
            or parse_positive_int(summary.get("runtime_assertions_passed")) != verified
        ):
            errors.append("runtime comparator: summary counts are inconsistent")
        if parse_positive_int(summary.get("source_digest")) != digest:
            errors.append("runtime comparator: source_digest is inconsistent")
    return errors


def validate_backend_runtime_evidence(state_dir: Path) -> list[str]:
    path = state_dir / "rp_backend_exec"
    if not path.is_file():
        return ["workflow_runtime: rp_backend_exec is missing"]
    data = path.read_bytes()
    if not data or not data.endswith(b"\n"):
        return ["workflow_runtime: rp_backend_exec has an incomplete final record"]
    try:
        raw_lines = data.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return ["workflow_runtime: rp_backend_exec is not valid UTF-8"]

    errors: list[str] = []
    records: list[dict[str, str]] = []
    for number, line in enumerate(raw_lines, 1):
        record = parse_record(line)
        if record is None:
            errors.append(f"workflow_runtime: rp_backend_exec line {number} is not strict key/value data")
            continue
        records.append(record)
        if record.get("status") == "passed" or record.get("result") == "passed":
            errors.append(f"workflow_runtime: rp_backend_exec line {number} contains an unbound passed claim")

    protocol_records = [
        record
        for record in records
        if record.get("runtime_claim_protocol") == "source-bound-v1"
        and record.get("runtime_claim_scope") == "file"
    ]
    if len(protocol_records) != 1:
        errors.append("workflow_runtime: source-bound-v1 protocol marker is missing or duplicated")
    elif (
        protocol_records[0].get("evidence_role") != "demo_reference"
        or protocol_records[0].get("catalog_generation") != "demo_expected"
        or protocol_records[0].get("status") != "reference_ready"
    ):
        errors.append("workflow_runtime: protocol marker is not isolated as demo reference")

    runtime_cases: dict[str, dict[str, str]] = {}
    summaries: list[dict[str, str]] = []
    for record in records:
        if "runtime_case" in record:
            case_name = record["runtime_case"]
            if case_name in runtime_cases:
                errors.append(f"workflow_runtime: duplicate runtime case {case_name}")
            runtime_cases[case_name] = record
        elif "runtime_cases_executed" in record:
            summaries.append(record)
        elif record.get("generation") == "runtime" or record.get("evidence_generation") == "runtime":
            errors.append("workflow_runtime: unrecognized runtime claim is not source-bound")
        elif record.get("evidence_role") == "demo_reference" and (
            record.get("catalog_generation") != "demo_expected"
            or record.get("status") != "reference_ready"
        ):
            errors.append("workflow_runtime: demo catalog record is not reference-only")

    missing = set(BACKEND_RUNTIME_CASES) - set(runtime_cases)
    extra = set(runtime_cases) - set(BACKEND_RUNTIME_CASES)
    if missing:
        errors.append("workflow_runtime: missing runtime cases: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("workflow_runtime: unknown runtime cases: " + ", ".join(sorted(extra)))

    folded_digest = FNV_OFFSET
    verified_assertions = 0
    for case_name, (source, key, value) in BACKEND_RUNTIME_CASES.items():
        record = runtime_cases.get(case_name)
        if record is None:
            continue
        if (
            record.get("evidence_role") != "runtime_verified"
            or record.get("generation") != "runtime"
            or record.get("status") != "verified"
        ):
            errors.append(f"workflow_runtime: {case_name} is not a runtime-verified record")
        if record.get("source") != source:
            errors.append(f"workflow_runtime: {case_name} is bound to the wrong source")
            continue
        source_path = state_dir / source
        if not source_path.is_file():
            errors.append(f"workflow_runtime: {case_name} source is missing: {source}")
            continue
        source_data = source_path.read_bytes()
        source_hash = fnv1a64(source_data)
        source_bytes = len(source_data)
        if (
            parse_positive_int(record.get("source_bytes")) != source_bytes
            or parse_positive_int(record.get("source_hash")) != source_hash
        ):
            errors.append(f"workflow_runtime: {case_name} source measurement is invalid")
        if not source_has_exact_field(source_data, key, value):
            errors.append(f"workflow_runtime: {case_name} exact runtime assertion failed")
        executed = parse_positive_int(record.get("assertions_executed"))
        passed = parse_positive_int(record.get("assertions_passed"))
        if executed != 1 or passed != executed:
            errors.append(f"workflow_runtime: {case_name} assertion counts are invalid")
        else:
            verified_assertions += passed
        folded_digest = fold_backend_measurement(
            folded_digest, case_name, source, source_hash, source_bytes
        )

    if len(summaries) != 1:
        errors.append("workflow_runtime: runtime summary is missing or duplicated")
    else:
        summary = summaries[0]
        if (
            summary.get("evidence_role") != "runtime_verified"
            or summary.get("generation") != "runtime"
            or summary.get("status") != "verified"
        ):
            errors.append("workflow_runtime: runtime summary is not verified")
        expected_cases = len(BACKEND_RUNTIME_CASES)
        if (
            parse_positive_int(summary.get("runtime_cases_executed")) != expected_cases
            or parse_positive_int(summary.get("runtime_cases_verified")) != expected_cases
            or parse_positive_int(summary.get("runtime_assertions_executed")) != verified_assertions
            or parse_positive_int(summary.get("runtime_assertions_passed")) != verified_assertions
        ):
            errors.append("workflow_runtime: runtime summary counts are inconsistent")
        if parse_positive_int(summary.get("runtime_source_digest")) != folded_digest:
            errors.append("workflow_runtime: runtime_source_digest is inconsistent")
        request_id = parse_positive_int(summary.get("echo_request_id"))
        echo_status = parse_nonnegative_int(summary.get("echo_status"))
        context_sequence = parse_positive_int(summary.get("context_latest_sequence"))
        query_returned = parse_positive_int(summary.get("query_returned"))
        query_scanned = parse_positive_int(summary.get("query_scanned"))
        query_used_index = parse_nonnegative_int(summary.get("query_used_index"))
        edit_base = parse_nonnegative_int(summary.get("edit_base_version"))
        edit_current = parse_positive_int(summary.get("edit_current_version"))
        edit_active = parse_nonnegative_int(summary.get("edit_active"))
        if request_id is None or echo_status != 0 or context_sequence is None:
            errors.append("workflow_runtime: kernel echo/context observations are invalid")
        if query_returned is None or query_scanned is None or query_scanned < query_returned or query_used_index != 1:
            errors.append("workflow_runtime: indexed-query observation is invalid")
        if edit_base is None or edit_current != edit_base + 1 or edit_active != 0:
            errors.append("workflow_runtime: edit-commit observation is invalid")
    return errors


def read_runtime_manifest(state_dir: Path) -> tuple[dict[str, object], list[str]]:
    path = state_dir / "rp_tests"
    errors: list[str] = []
    if not path.is_file():
        return {}, ["rp_tests runtime manifest is missing"]

    header: dict[str, str] = {}
    assertion_sets: list[dict[str, str]] = []
    summary: dict[str, str] | None = None
    for number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
        if not raw_line:
            continue
        fields = parse_record(raw_line)
        if fields is None:
            errors.append(f"rp_tests line {number} is not a strict key/value record")
            continue
        if "assertion_set" in fields:
            assertion_sets.append(fields)
        elif "runtime_assertions_executed" in fields:
            if summary is not None:
                errors.append("rp_tests contains more than one runtime summary")
            summary = fields
        else:
            for key, value in fields.items():
                if key in header:
                    errors.append(f"rp_tests duplicates header key {key}")
                header[key] = value

    if header.get("suite") != "agentos-runtime-acceptance":
        errors.append("rp_tests suite is not agentos-runtime-acceptance")
    if header.get("manifest_version") != "1":
        errors.append("rp_tests manifest_version is not 1")
    if header.get("evidence_generation") != "runtime":
        errors.append("rp_tests evidence_generation is not runtime")
    if header.get("catalog_generation") != "demo_expected":
        errors.append("rp_tests catalog is not isolated as demo_expected")
    catalog_executed = parse_positive_int(header.get("catalog_assertions_executed"))
    catalog_passed = parse_positive_int(header.get("catalog_assertions_passed"))
    if catalog_executed is None or catalog_passed != catalog_executed:
        errors.append("rp_tests catalog assertion counts are invalid")

    expected_themes = {theme.name for theme in TEST_THEMES}
    seen_themes: set[str] = set()
    runtime_assertions = 0
    folded_digest = FNV_OFFSET
    for record in assertion_sets:
        theme = record.get("assertion_set", "")
        source = record.get("source", "")
        source_bytes = parse_positive_int(record.get("source_bytes"))
        source_hash = parse_positive_int(record.get("source_hash"))
        assertions = parse_positive_int(record.get("assertions"))
        if theme not in expected_themes:
            errors.append(f"unknown runtime assertion set: {theme or '<missing>'}")
        elif theme in seen_themes:
            errors.append(f"duplicate runtime assertion set: {theme}")
        seen_themes.add(theme)
        if record.get("generation") != "runtime" or record.get("status") != "verified":
            errors.append(f"{theme or '<missing>'}: runtime assertion is not verified")
        expected_assertion = EXPECTED_RUNTIME_ASSERTIONS.get(theme)
        expected_source = expected_assertion[0] if expected_assertion else None
        if expected_source is not None and source != expected_source:
            errors.append(
                f"{theme}: runtime source must be {expected_source}, not {source or '<missing>'}"
            )
        if record.get("claim_protocol") != "exact-field-v1":
            errors.append(
                f"{theme or '<missing>'}: claim protocol does not match the runtime source contract"
            )
        if assertions != 2:
            errors.append(f"{theme or '<missing>'}: assertions must equal 2")
        if not re.fullmatch(r"rp_[a-z0-9_]+", source):
            errors.append(f"{theme or '<missing>'}: invalid source identity")
            continue
        source_path = state_dir / source
        if not source_path.is_file():
            errors.append(f"{theme}: source file is missing: {source}")
            continue
        data = source_path.read_bytes()
        actual_hash = fnv1a64(data)
        if source_bytes != len(data) or source_hash != actual_hash:
            errors.append(f"{theme}: source measurement does not match {source}")
        if expected_assertion is not None and not source_has_exact_field(
            data, expected_assertion[1], expected_assertion[2]
        ):
            errors.append(f"{theme}: exact runtime assertion failed")
        if assertions == 2:
            runtime_assertions += assertions
        folded_digest = fold_file_measurement(
            folded_digest, source, actual_hash, len(data)
        )

    if any(record.get("assertion_set") == "workflow_runtime" for record in assertion_sets):
        errors.extend(validate_backend_runtime_evidence(state_dir))
    errors.extend(validate_comparator_runtime_evidence(state_dir))

    missing_themes = sorted(expected_themes - seen_themes)
    if missing_themes:
        errors.append("missing runtime assertion sets: " + ", ".join(missing_themes))
    declared_digest = parse_positive_int(header.get("runtime_source_digest"))
    if declared_digest != folded_digest:
        errors.append("rp_tests runtime_source_digest does not match measured sources")

    if summary is None:
        errors.append("rp_tests runtime summary is missing")
    else:
        executed = parse_positive_int(summary.get("runtime_assertions_executed"))
        passed = parse_positive_int(summary.get("runtime_assertions_passed"))
        set_count = parse_positive_int(summary.get("assertion_sets"))
        if summary.get("status") != "verified":
            errors.append("rp_tests runtime summary is not verified")
        if executed != runtime_assertions or passed != executed:
            errors.append("rp_tests runtime assertion totals are inconsistent")
        if set_count != len(expected_themes) or len(assertion_sets) != set_count:
            errors.append("rp_tests runtime assertion set count is inconsistent")

    return {
        "header": header,
        "assertion_sets": assertion_sets,
        "summary": summary,
        "themes": seen_themes,
    }, errors


def run_check(
    root: Path,
    host_dir: Path,
    require_host: bool,
    plain_state_dir: Path | None = None,
    agentos_state_dir: Path | None = None,
) -> dict[str, object]:
    if not host_dir.exists():
        if require_host:
            raise SystemExit(f"host platform is missing: {host_dir}")
        return {
            "status": "skipped",
            "reason": "host_platform_not_found",
            "host_dir": str(host_dir),
        }

    test_names = collect_test_names(host_dir)
    check_runtime_state = plain_state_dir is not None or agentos_state_dir is not None
    if check_runtime_state and (plain_state_dir is None or agentos_state_dir is None):
        raise ValueError("plain and AgentOS state directories must be supplied together")
    failures: list[str] = []
    runtime_manifest: dict[str, object] = {}
    runtime_manifest_errors: list[str] = []
    plain_reference_present = False
    if check_runtime_state:
        if not plain_state_dir.is_dir() or not agentos_state_dir.is_dir():
            raise ValueError("plain and AgentOS state directories must exist")
        plain_reference_present = (plain_state_dir / "rp_tests").is_file()
        runtime_manifest, runtime_manifest_errors = read_runtime_manifest(agentos_state_dir)
        failures.extend(f"AgentOS runtime manifest: {error}" for error in runtime_manifest_errors)
    theme_counts = {theme.name: 0 for theme in TEST_THEMES}
    unclassified: list[str] = []

    for test_name in test_names:
        matched = classify_test(test_name)
        if not matched:
            unclassified.append(test_name)
            continue
        for theme_name in matched:
            theme_counts[theme_name] += 1

    if not test_names:
        failures.append("host test file contains no test methods")
    if unclassified:
        failures.append("host tests are not mapped to themes: " + ", ".join(unclassified[:20]))

    theme_results: list[dict[str, object]] = []
    runtime_themes = runtime_manifest.get("themes", set())
    for theme in TEST_THEMES:
        runtime_verified = not check_runtime_state or theme.name in runtime_themes
        if theme_counts[theme.name] == 0:
            failures.append(f"{theme.name}: no host tests matched this theme")
        failed = theme_counts[theme.name] == 0 or not runtime_verified
        theme_results.append(
            {
                "name": theme.name,
                "host_tests": theme_counts[theme.name],
                "runtime_verified": runtime_verified,
                "evidence_role": "runtime_assertion_set" if check_runtime_state else "not_checked",
                "status": "failed" if failed else "ok",
            }
        )

    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(host_dir),
        "host_tests": len(test_names),
        "themes_ok": sum(1 for item in theme_results if item["status"] == "ok"),
        "themes_total": len(theme_results),
        "unclassified_tests": len(unclassified),
        "runtime_state_checked": check_runtime_state,
        "runtime_evidence_verified": check_runtime_state and not runtime_manifest_errors,
        "plain_evidence_role": "demo_reference",
        "plain_reference_present": plain_reference_present if check_runtime_state else None,
        "theme_results": theme_results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check host platform test themes against uCore platform evidence.")
    parser.add_argument("--host-dir", type=Path, default=None)
    parser.add_argument("--plain-state-dir", type=Path, default=None)
    parser.add_argument("--agentos-state-dir", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--require-host", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    host_dir = args.host_dir or default_host_dir(root)
    summary = run_check(root, host_dir, args.require_host, args.plain_state_dir, args.agentos_state_dir)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if summary["status"] == "skipped":
        print(f"host_test_alignment: status=skipped reason={summary['reason']} host_dir={summary['host_dir']}")
        return 0

    print(
        "host_test_alignment: "
        f"host_tests={summary['host_tests']} "
        f"themes_ok={summary['themes_ok']} "
        f"themes_total={summary['themes_total']} "
        f"unclassified_tests={summary['unclassified_tests']} "
        f"runtime_state_checked={int(bool(summary['runtime_state_checked']))} "
        f"runtime_evidence_verified={int(bool(summary['runtime_evidence_verified']))} "
        f"plain_evidence_role={summary['plain_evidence_role']} "
        f"status={summary['status']}"
    )
    if summary["status"] == "failed":
        for failure in summary["failures"]:
            print(f"host_test_alignment: failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
