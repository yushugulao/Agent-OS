#!/usr/bin/env python3
"""Render host-readable pages from plain uCore research platform state files."""

from __future__ import annotations

import argparse
import html
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlparse


PAGE_SPECS = [
    ("index.html", "Home", "rp_api_home", ["rp_ui_home", "rp_web_bundle"]),
    ("run.html", "Run Detail", "rp_api_run", ["rp_ui_run", "rp_runner", "rp_artifact"]),
    ("workflow.html", "Workflow", "rp_stage_state", ["rp_stage_dag", "rp_cache_index", "rp_retry_plan", "rp_run_events", "rp_worker", "rp_execobs"]),
    ("workbench.html", "Workbench", "rp_runner", ["rp_report_text", "rp_revision", "rp_package", "rp_review_pack", "rp_nbexec", "rp_uresrun"]),
    ("studio.html", "Studio", "rp_studio", ["rp_runner", "rp_package", "rp_review_pack", "rp_actionio", "rp_web_bundle"]),
    ("operations.html", "Operations", "rp_opsboard", ["rp_runner", "rp_package", "rp_review_dashboard", "rp_studyproto", "rp_runbooks", "rp_projectrel"]),
    ("project.html", "Project", "rp_package", ["rp_runner", "rp_review_pack", "rp_actionio", "rp_web_bundle", "rp_projectrel", "rp_studyproto"]),
    ("project-review.html", "Project Review", "rp_web_bundle", ["rp_package", "rp_review_pack", "rp_runner", "rp_actionio", "rp_projectrel", "rp_studyproto"]),
    ("agents.html", "Agents", "rp_api_agents", ["rp_ui_agent", "rp_agents", "rp_decisions"]),
    ("evidence.html", "Evidence", "rp_api_evidence", ["rp_ui_evidence", "rp_evidence", "rp_package"]),
    ("review.html", "Review", "rp_review_dashboard", ["rp_review_pack", "rp_review2", "rp_revision", "rp_package", "rp_report_text"]),
    ("compare.html", "Compare", "rp_api_compare", ["rp_ui_compare", "rp_agentcmp", "rp_consistency", "rp_backend", "rp_backend_exec", "rp_study", "rp_studyproto", "rp_opsboard", "rp_control", "rp_integrity", "rp_coherence", "host_seeded_action", "rp_agentos_kernel", "rp_agentos_mainflow", "rp_agentos_query", "rp_agentos_recovery", "rp_agentos_timeline", "rp_agentos_collab_ack", "rp_agentos_audit", "rp_agentos_workbench", "rp_agentos_package", "rp_agentos_real_task", "rp_agentos_conflict"]),
    ("artifacts.html", "Artifacts", "rp_api_artifacts", ["rp_artifact", "rp_artifact_manifest", "rp_package"]),
    ("delivery.html", "Delivery", "rp_package", ["rp_nbexec", "rp_uresrun", "rp_artifact_manifest", "rp_review_pack"]),
    ("data.html", "Data", "rp_api_data", ["rp_input", "rp_ingest_files", "rp_dataset_snapshot", "rp_data_preview", "rp_data_quality", "rp_data_transform", "rp_dataset_collection"]),
    ("services.html", "Services", "rp_api_bio", ["rp_api_labres", "rp_api_pub", "rp_api_know", "rp_api_runtime", "rp_bioop", "rp_labresop", "rp_pubop", "rp_knowop", "rp_runop", "rp_runbooks", "rp_studyproto", "rp_opsboard"]),
    ("api-catalog.html", "API Catalog", "rp_api_catalog", ["rp_web_routes", "rp_api_action", "rp_web_bundle"]),
    ("review-board.html", "Review Board", "rp_reviewboard", ["rp_reviewops", "rp_review_dashboard", "rp_dossier", "rp_package", "rp_opsboard"]),
    ("control-plane.html", "Control Plane", "rp_control", ["rp_opsboard", "rp_review_dashboard", "rp_agentcmp", "rp_web_bundle"]),
    ("integrity.html", "Integrity", "rp_integrity", ["rp_review_dashboard", "rp_agentcmp", "rp_package", "rp_report_text", "rp_artifact_manifest"]),
    ("coherence.html", "Coherence", "rp_coherence", ["rp_review_dashboard", "rp_agentcmp", "rp_package", "rp_stage_state", "rp_backend_exec"]),
    ("publication.html", "Publication", "rp_publication", ["rp_pubplan", "rp_peerresp", "rp_api_pub", "rp_pubop", "rp_review_dashboard", "rp_package"]),
    ("calculations.html", "Calculations", "rp_calculation", ["rp_calc_files", "rp_calc_parse", "rp_calc_export", "rp_agentcmp", "rp_review_dashboard"]),
    ("real-task.html", "Real Task", "rp_realtask", ["rp_realdata", "rp_realreport", "rp_realbundle", "rp_agentcmp", "rp_review_dashboard"]),
    ("analysis-results.html", "Analysis Results", "rp_analysisres", ["rp_anplan", "rp_anrun", "rp_resulttbl", "rp_statres", "rp_anfig", "rp_interp", "rp_agentcmp", "rp_review_dashboard"]),
    ("decision-support.html", "Decision Support", "rp_decsupport", ["rp_decopt", "rp_deccrit", "rp_decscore", "rp_decpacket", "rp_agentcmp", "rp_review_dashboard"]),
    ("usable-research.html", "Usable Research", "rp_usable", ["rp_usabletpl", "rp_usableds", "rp_usablelib", "rp_usabledag", "rp_usableops", "rp_agentcmp", "rp_review_dashboard"]),
    ("usable-project.html", "Usable Project", "rp_usableproj", ["rp_usableboot", "rp_usablescaf", "rp_usablelaunch", "rp_usablepack", "rp_agentcmp", "rp_review_dashboard"]),
    ("experiment-campaigns.html", "Experiment Campaigns", "rp_campaign", ["rp_trials", "rp_camp_rank", "rp_resreview", "rp_agentcmp", "rp_review_dashboard"]),
    ("statistical-design.html", "Statistical Design", "rp_stdesign", ["rp_power", "rp_random", "rp_blind", "rp_streview", "rp_agentcmp", "rp_review_dashboard"]),
    ("model-registry.html", "Model Registry", "rp_modelreg", ["rp_modelver", "rp_modeleval", "rp_modeldep", "rp_modelserve", "rp_agentcmp", "rp_review_dashboard"]),
    ("systematic-review.html", "Systematic Review", "rp_sysreview", ["rp_syssearch", "rp_sysscreen", "rp_sysextract", "rp_syssynth", "rp_sysprisma", "rp_agentcmp", "rp_review_dashboard"]),
    ("experiment-schedule.html", "Experiment Schedule", "rp_expsched", ["rp_schedtask", "rp_schedbook", "rp_schedconf", "rp_schedexec", "rp_agentcmp", "rp_review_dashboard"]),
    ("training-compliance.html", "Training Compliance", "rp_traincomp", ["rp_trainreq", "rp_trainrec", "rp_trainassess", "rp_trainauth", "rp_traingap", "rp_agentcmp", "rp_review_dashboard"]),
    ("release-dossier.html", "Release Dossier", "rp_reldossier", ["rp_reldsec", "rp_relattest", "rp_relpack", "rp_agentcmp", "rp_review_dashboard"]),
    ("mature.html", "Mature Platforms", "rp_mature", ["rp_mature_refs", "rp_mature_map", "rp_mature_checks", "rp_agentcmp", "rp_review_dashboard"]),
    ("provenance.html", "Provenance", "rp_prov_view", ["rp_prov_edges", "rp_evidence_packet", "rp_timeline_view", "rp_agentcmp", "rp_review_dashboard"]),
    ("provenance-queries.html", "Provenance Queries", "rp_prov_query", ["rp_prov_specs", "rp_prov_exec", "rp_prov_query_pkg", "rp_agentcmp", "rp_review_dashboard"]),
    ("llm.html", "LLM Relay", "rp_llm_resp", ["rp_llm_req", "rp_llmeval", "rp_llm_guard", "rp_relay", "rp_prompt", "rp_llm_packets"]),
    ("actions.html", "Actions", "rp_api_action", ["rp_actionio", "rp_host_run_result", "rp_web_routes", "rp_web_bundle"]),
]


def parse_state_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for part in line.split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                key, value = part.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def load_state(state_dir: Path) -> dict[str, dict[str, object]]:
    state: dict[str, dict[str, object]] = {}
    for path in sorted(state_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        state[path.name] = {
            "text": text,
            "values": parse_state_text(text),
            "lines": [line for line in text.splitlines() if line.strip()],
        }
    return state


def state_values(state: dict[str, dict[str, object]], name: str) -> dict[str, str]:
    item = state.get(name)
    if not item:
        return {}
    return item["values"]  # type: ignore[return-value]


def state_lines(state: dict[str, dict[str, object]], name: str) -> list[str]:
    item = state.get(name)
    if not item:
        return []
    return item["lines"]  # type: ignore[return-value]


def split_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def reader_contract(state: dict[str, dict[str, object]]) -> dict[str, object]:
    bundle = state_values(state, "rp_web_bundle")
    payload_files = split_list(bundle.get("reader_payload_files", ""))
    refresh_files = split_list(bundle.get("reader_refresh_files", ""))
    return {
        "contract": bundle.get("reader_contract", ""),
        "version": bundle.get("reader_contract_version", ""),
        "ready": bundle.get("reader_ready", ""),
        "views": bundle.get("reader_views", ""),
        "actions": bundle.get("reader_actions", ""),
        "payload_files": payload_files,
        "refresh_files": refresh_files,
        "required_sections": split_list(bundle.get("reader_required_sections", "")),
        "event_stream": bundle.get("reader_event_stream", ""),
        "fallback": bundle.get("reader_fallback", ""),
        "state_source": bundle.get("reader_state_source", ""),
        "missing_payload_files": [name for name in payload_files if name not in state],
        "missing_refresh_files": [name for name in refresh_files if name not in state],
    }


def validate_contract(contract: dict[str, object]) -> list[str]:
    problems: list[str] = []
    if contract.get("contract") != "host_plain_ucore_v2":
        problems.append("reader_contract is not host_plain_ucore_v2")
    if contract.get("ready") != "1":
        problems.append("reader_ready is not 1")
    if contract.get("missing_payload_files"):
        problems.append("missing payload files: " + ",".join(contract["missing_payload_files"]))  # type: ignore[index]
    if contract.get("missing_refresh_files"):
        problems.append("missing refresh files: " + ",".join(contract["missing_refresh_files"]))  # type: ignore[index]
    return problems


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return {}
    return {}


def load_optional_json(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    return read_json_file(path)


def write_optional_api(api_dir: Path, name: str, data: dict[str, object]) -> int:
    if not data:
        return 0
    write_json(api_dir / f"{name}.json", {"name": name, "values": data, "lines": []})
    return 1


def safe_field(value: object) -> str:
    return str(value).replace(";", ",").replace("\n", " ")


def alignment_state_item(kind: str, data: dict[str, object]) -> dict[str, object]:
    lines: list[str] = []
    if kind == "platform":
        fields = [
            ("host_platform_alignment", "summary"),
            ("status", data.get("status", "")),
            ("host_modules", data.get("host_modules", "")),
            ("tracked_host_modules", data.get("tracked_host_modules", "")),
            ("plain_sources", data.get("plain_sources", "")),
            ("agentos_sources", data.get("agentos_sources", "")),
            ("runtime_state_checked", int(bool(data.get("runtime_state_checked", False)))),
            ("groups_ok", data.get("groups_ok", "")),
            ("groups_total", data.get("groups_total", "")),
            ("untracked_host_modules", data.get("untracked_host_modules", "")),
        ]
        lines.append(";".join(f"{key}={safe_field(value)}" for key, value in fields))
        groups = data.get("groups", [])
        if isinstance(groups, list):
            for item in groups:
                if not isinstance(item, dict):
                    continue
                group_fields = [
                    ("capability_group", item.get("name", "")),
                    ("status", item.get("status", "")),
                    ("host_modules", item.get("host_modules", "")),
                    ("plain_sources", item.get("plain_sources", "")),
                    ("agentos_sources", item.get("agentos_sources", "")),
                    ("reader_keywords", item.get("reader_keywords", "")),
                    ("plain_runtime_hits", ",".join(str(v) for v in item.get("plain_runtime_hits", []) if v)),
                    ("agentos_runtime_hits", ",".join(str(v) for v in item.get("agentos_runtime_hits", []) if v)),
                ]
                lines.append(";".join(f"{key}={safe_field(value)}" for key, value in group_fields))
    elif kind == "tests":
        fields = [
            ("host_test_alignment", "summary"),
            ("status", data.get("status", "")),
            ("host_tests", data.get("host_tests", "")),
            ("themes_ok", data.get("themes_ok", "")),
            ("themes_total", data.get("themes_total", "")),
            ("unclassified_tests", data.get("unclassified_tests", "")),
        ]
        lines.append(";".join(f"{key}={safe_field(value)}" for key, value in fields))
        themes = data.get("theme_results", [])
        if isinstance(themes, list):
            for item in themes:
                if not isinstance(item, dict):
                    continue
                theme_fields = [
                    ("test_theme", item.get("name", "")),
                    ("status", item.get("status", "")),
                    ("host_tests", item.get("host_tests", "")),
                    ("evidence_tokens", item.get("evidence_tokens", "")),
                    ("missing_plain", ",".join(str(v) for v in item.get("missing_plain", []) if v)),
                    ("missing_agentos", ",".join(str(v) for v in item.get("missing_agentos", []) if v)),
                ]
                lines.append(";".join(f"{key}={safe_field(value)}" for key, value in theme_fields))
    elif kind == "surface":
        fields = [
            ("host_surface_alignment", "summary"),
            ("status", data.get("status", "")),
            ("host_api_routes", data.get("host_api_routes", "")),
            ("host_action_routes", data.get("host_action_routes", "")),
            ("host_download_refs", data.get("host_download_refs", "")),
            ("runtime_state_checked", int(bool(data.get("runtime_state_checked", False)))),
        ]
        for label, key in (("plain_source", "plain_source"), ("agentos_source", "agentos_source"), ("plain_runtime", "plain_runtime"), ("agentos_runtime", "agentos_runtime")):
            item = data.get(key, {})
            if not isinstance(item, dict):
                continue
            fields.extend(
                [
                    (f"{label}_api_routes", item.get("host_api_routes", "")),
                    (f"{label}_action_routes", item.get("host_action_routes", "")),
                    (f"{label}_reader_actions", item.get("reader_actions", "")),
                ]
            )
        lines.append(";".join(f"{key}={safe_field(value)}" for key, value in fields))
        for route_key, line_key in (("api_prefixes", "api_prefix"), ("action_prefixes", "action_prefix")):
            values = data.get(route_key, [])
            if isinstance(values, list):
                for value in values:
                    lines.append(f"{line_key}={safe_field(value)};status=tracked")
    text = "\n".join(lines) + ("\n" if lines else "")
    return {
        "text": text,
        "values": parse_state_text(text),
        "lines": [line for line in text.splitlines() if line.strip()],
    }


def seeded_action_state_item(data: dict[str, object]) -> dict[str, object]:
    def target_fields(name: str) -> list[tuple[str, object]]:
        target = data.get(name, {})
        if not isinstance(target, dict):
            target = {}
        prepare = target.get("prepare", {})
        if not isinstance(prepare, dict):
            prepare = {}
        run = target.get("run", {})
        if not isinstance(run, dict):
            run = {}
        failures = target.get("failures", [])
        failure_count = len(failures) if isinstance(failures, list) else 0
        return [
            ("seeded_action_target", name),
            ("status", target.get("status", "")),
            ("prepare_actions", prepare.get("actions", "")),
            ("prepare_accepted", prepare.get("accepted", "")),
            ("run_status", run.get("status", "")),
            ("run_passed", int(bool(run.get("passed", False)))),
            ("embedded_action_records", run.get("embedded_action_records", "")),
            ("extracted_state_files", run.get("extracted_state_files", "")),
            ("failures", failure_count),
        ]

    plain = data.get("plain", {})
    agentos = data.get("agentos", {})
    plain_status = plain.get("status", "") if isinstance(plain, dict) else ""
    agentos_status = agentos.get("status", "") if isinstance(agentos, dict) else ""
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    action_kinds = data.get("action_kinds", "")
    if isinstance(action_kinds, list):
        action_kinds = ",".join(str(item) for item in action_kinds)
    lines = [
        ";".join(
            f"{key}={safe_field(value)}"
            for key, value in [
                ("host_seeded_action", "summary"),
                ("status", data.get("status", "")),
                ("action", data.get("action", "")),
                ("action_count", data.get("action_count", "")),
                ("action_kinds", action_kinds),
                ("plain_status", plain_status),
                ("agentos_status", agentos_status),
            ]
        ),
        ";".join(
            f"{key}={safe_field(value)}"
            for key, value in [
                ("seeded_action_coverage", "host_routes"),
                ("status", coverage.get("status", "")),
                ("host_action_routes", coverage.get("host_action_routes", "")),
                ("host_action_kinds", coverage.get("host_action_kinds", "")),
                ("seeded_known_routes", coverage.get("seeded_known_routes", "")),
                ("seeded_host_kinds", coverage.get("seeded_host_kinds", "")),
                ("seeded_extra_routes", len(coverage.get("seeded_extra_routes", [])) if isinstance(coverage.get("seeded_extra_routes", []), list) else ""),
                ("uncovered_host_kinds", len(coverage.get("uncovered_host_kinds", [])) if isinstance(coverage.get("uncovered_host_kinds", []), list) else ""),
            ]
        ),
        ";".join(f"{key}={safe_field(value)}" for key, value in target_fields("plain")),
        ";".join(f"{key}={safe_field(value)}" for key, value in target_fields("agentos")),
    ]
    text = "\n".join(lines) + "\n"
    return {
        "text": text,
        "values": parse_state_text(text),
        "lines": [line for line in text.splitlines() if line.strip()],
    }


def read_jsonl_file(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                records.append(data)
        except json.JSONDecodeError:
            continue
    return records


def metric_value(state: dict[str, dict[str, object]], sources: list[tuple[str, str]], default: str = "n/a") -> str:
    for name, key in sources:
        value = state_values(state, name).get(key, "")
        if value:
            return value
    return default


def render_metric_cards(metrics: list[tuple[str, object, str]]) -> str:
    cards = []
    for label, value, source in metrics:
        cards.append(
            "<article class='metric'><span>{}</span><strong>{}</strong><small>{}</small></article>".format(
                html.escape(label),
                html.escape(str(value)),
                html.escape(source),
            )
        )
    return "<section class='metrics'>{}</section>".format("".join(cards))


def render_summary_panel(title: str, items: list[tuple[str, object, str]]) -> str:
    rows = []
    for label, value, source in items:
        rows.append(
            "<article class='summary-item'><span>{}</span><strong>{}</strong><small>{}</small></article>".format(
                html.escape(label),
                html.escape(str(value)),
                html.escape(source),
            )
        )
    return "<section class='panel summary-panel'><h2>{}</h2><div class='summary-grid'>{}</div></section>".format(
        html.escape(title),
        "".join(rows),
    )


def parse_kv_record(line: str) -> dict[str, str]:
    record: dict[str, str] = {}
    for part in line.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            record[key.strip()] = value.strip()
    return record


def state_records(state: dict[str, dict[str, object]], name: str, prefix_key: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in state_lines(state, name):
        record = parse_kv_record(line)
        if prefix_key in record:
            rows.append(record)
    return rows


def first_record_by_key(records: list[dict[str, str]], key: str, value: str) -> dict[str, str]:
    for record in records:
        if record.get(key, "") == value:
            return record
    return {}


def first_value(state: dict[str, dict[str, object]], name: str, key: str) -> str:
    for line in state_lines(state, name):
        record = parse_kv_record(line)
        if key in record and record[key]:
            return record[key]
    return ""


def state_prefixed_lines(state: dict[str, dict[str, object]], name: str, prefixes: tuple[str, ...]) -> list[str]:
    rows: list[str] = []
    for line in state_lines(state, name):
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in prefixes):
            rows.append(stripped)
    return rows


def key_value_rows(state: dict[str, dict[str, object]], names: tuple[str, ...], prefixes: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in names:
        for line in state_prefixed_lines(state, name, prefixes):
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            rows.append({"state_file": name, "key": key, "value": value})
    return rows


def backend_case_narratives(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    reports = {row.get("runner_report", ""): row for row in state_records(state, "rp_backend_exec", "runner_report")}
    rows: list[dict[str, str]] = []
    for detail in state_records(state, "rp_backend_exec", "runner_detail"):
        case = detail.get("runner_detail", "")
        report = reports.get(case, {})
        rows.append(
            {
                "runner_narrative": case,
                "summary": "{}:{}:{}:{}".format(
                    detail.get("req", ""),
                    detail.get("obs", ""),
                    detail.get("act", ""),
                    detail.get("review", ""),
                ),
                "plain_cost": report.get("plain_cost", ""),
                "agentos_replace": report.get("agentos_replace", ""),
                "next": report.get("status", ""),
            }
        )
    return rows


def agentos_kernel_outputs(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    specs = [
        (
            "entry",
            "rp_agentos_kernel",
            ("context_snapshot", "agent_timeline", "agent_provenance", "agent_ledger", "file_meta_service"),
            ("kernel_agent", "research_platform"),
        ),
        (
            "query",
            "rp_agentos_query",
            ("metadata_source", "align_query", "report_query", "tool"),
            ("capability", "query"),
        ),
        (
            "recovery",
            "rp_agentos_recovery",
            ("kernel_tool", "context_snapshot"),
            ("stage", "run_id"),
        ),
        (
            "timeline",
            "rp_agentos_timeline",
            ("event_delivery", "wait", "heartbeat", "timeline_snapshot"),
            ("run_id",),
        ),
        (
            "collaboration",
            "rp_agentos_collab_ack",
            ("delivery", "permission_control"),
            ("agent", "event"),
        ),
        (
            "audit",
            "rp_agentos_audit",
            ("audit_source", "context_source", "provenance_source", "record_hash"),
            (),
        ),
        (
            "workbench",
            "rp_agentos_workbench",
            ("file_verify", "context_snapshot", "candidate_source"),
            ("workbench", "report_file"),
        ),
        (
            "package",
            "rp_agentos_package",
            ("package_trace", "ledger", "context_snapshot", "report_metadata"),
            ("package",),
        ),
        (
            "real_task",
            "rp_agentos_real_task",
            ("report_answer", "answer_audit", "report_metadata", "context_snapshot"),
            ("task",),
        ),
        (
            "edit_conflict",
            "rp_agentos_conflict",
            ("edit_lease", "holder_write", "version_commit", "stale_write_policy"),
            ("edit_target", "resource_identity"),
        ),
    ]
    rows: list[dict[str, str]] = []
    for stage, name, service_keys, detail_keys in specs:
        values = state_values(state, name)
        if not values:
            continue
        services = [values.get(key, "") for key in service_keys if values.get(key, "")]
        details = [f"{key}={values[key]}" for key in detail_keys if values.get(key, "")]
        rows.append(
            {
                "kernel_stage": stage,
                "state_file": name,
                "kernel_services": ",".join(services),
                "details": ";".join(details),
                "status": values.get("status", ""),
            }
        )
    return rows


def operations_report_narrative(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    runner = state_values(state, "rp_runner")
    package = state_values(state, "rp_package")
    op_rows = state_records(state, "rp_review_pack", "operations_handoff")
    workbench_rows = state_records(state, "rp_review_pack", "workbench_handoff")
    project_rows = state_records(state, "rp_review_pack", "project_handoff")
    op = op_rows[0] if op_rows else {}
    workbench = workbench_rows[0] if workbench_rows else {}
    project = project_rows[0] if project_rows else {}
    rows = [
        {
            "operation_section": "operations_report",
            "source": op.get("operations_handoff", "rp_runner+rp_package"),
            "detail": "report={};tasks={};next={}".format(
                op.get("report", package.get("host_action_operations_report", "")),
                op.get("tasks", runner.get("workbench_tasks", "")),
                op.get("next", runner.get("workbench_next_task", "")),
            ),
            "status": op.get("status", package.get("status", runner.get("status", ""))),
        },
        {
            "operation_section": "execution_plan",
            "source": "rp_package",
            "detail": "plan={};quality={};repair={}".format(
                op.get("plan", package.get("host_action_operations_next", "")),
                op.get("quality", package.get("host_action_quality_gate", "")),
                op.get("repair", package.get("host_action_quality_repair_execute", "")),
            ),
            "status": op.get("status", package.get("status", "")),
        },
        {
            "operation_section": "workbench_delivery",
            "source": workbench.get("workbench_handoff", "rp_runner+rp_package"),
            "detail": "workbench={};task={};manifest={};verified={};missing={};bundle={}".format(
                workbench.get("workbench", runner.get("host_action_workbench_id", runner.get("workbench", ""))),
                workbench.get("task", runner.get("host_action_workbench_task", "")),
                workbench.get("manifest", package.get("host_action_workbench_manifest", "")),
                workbench.get("verified", package.get("host_action_workbench_verified_files", "")),
                workbench.get("missing", package.get("host_action_workbench_missing_files", "")),
                workbench.get("bundle", package.get("host_action_workbench_bundle", "")),
            ),
            "status": workbench.get("status", package.get("host_action_workbench_completion", "")),
        },
        {
            "operation_section": "project_followup",
            "source": project.get("project_handoff", "rp_package"),
            "detail": "project={};space={};note={};action_item={};answer={};repair={}".format(
                project.get("project", package.get("host_action_project_id", "")),
                project.get("space", package.get("host_action_project_space", "")),
                project.get("note", package.get("host_action_project_note", "")),
                project.get("action_item", package.get("host_action_project_action_item", "")),
                project.get("answer", package.get("host_action_project_answer", "")),
                project.get("repair", package.get("host_action_project_repair", "")),
            ),
            "status": project.get("status", package.get("status", "")),
        },
        {
            "operation_section": "backend_evidence",
            "source": "rp_backend_exec",
            "detail": "backend={};plain_costs={};agentos_replacements={};risks={}".format(
                op.get("backend", runner.get("backend_evidence_report", "")),
                runner.get("plain_costs", ""),
                runner.get("agentos_replacements", ""),
                runner.get("risks", ""),
            ),
            "status": runner.get("status", ""),
        },
    ]
    return [row for row in rows if any(value for key, value in row.items() if key != "source")]


def first_source_record(state: dict[str, dict[str, object]], name: str, prefixes: tuple[str, ...]) -> str:
    rows = state_prefixed_lines(state, name, prefixes)
    return rows[0] if rows else ""


def source_line_for_keys(state: dict[str, dict[str, object]], name: str, key_spec: str) -> str:
    keys = [key.strip() for key in key_spec.split(",") if key.strip()]
    if not keys:
        return ""
    for line in state_lines(state, name):
        record = parse_kv_record(line)
        if any(key in record for key in keys):
            return line.strip()
    return ""


def state_reference_file(reference: str) -> str:
    return reference.split(":", 1)[0].strip()


def source_line_for_reference(state: dict[str, dict[str, object]], reference: str) -> str:
    name, _, target = reference.partition(":")
    name = name.strip()
    target = target.strip()
    lines = [line.strip() for line in state_lines(state, name) if line.strip()]
    if not lines:
        return ""
    if not target:
        return lines[0]
    for line in lines:
        record = parse_kv_record(line)
        if any(value == target for value in record.values()):
            return line
        if target in line:
            return line
    return ""


def artifact_source_map(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    fields = (
        "input",
        "prepared",
        "artifact",
        "metrics",
        "chart",
        "failure",
        "retry",
        "event",
        "manifest",
        "report",
        "review",
        "review_pack",
        "llm_quality",
        "delivery",
    )
    rows: list[dict[str, str]] = []
    for path in state_records(state, "rp_artifact_manifest", "artifact_review_path"):
        path_name = path.get("artifact_review_path", "")
        for field in fields:
            reference = path.get(field, "")
            if not reference:
                continue
            rows.append(
                {
                    "artifact_path": path_name,
                    "field": field,
                    "reference": reference,
                    "state_file": state_reference_file(reference),
                    "source_line": source_line_for_reference(state, reference),
                    "status": path.get("status", ""),
                }
            )
    return rows


def split_state_references(value: str) -> list[str]:
    refs: list[str] = []
    for chunk in value.replace("+", ",").split(","):
        reference = chunk.strip()
        if reference.startswith("rp_"):
            refs.append(reference)
    return refs


def review_source_map(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    specs = (
        ("dashboard_section", "rp_review_dashboard", "section", ("source",)),
        ("dashboard_gate", "rp_review_dashboard", "gate", ("source",)),
        ("dashboard_handoff", "rp_review_dashboard", "handoff", ("artifact",)),
        ("dashboard_backend", "rp_review_dashboard", "backend_review_evidence", ("backend_review_evidence", "review_pack")),
        ("pack_evidence", "rp_review_pack", "evidence", ("source",)),
        ("pack_backend", "rp_review_pack", "backend_evidence_review", ("backend_evidence_review", "source")),
        ("pack_action", "rp_review_pack", "action", ("artifact",)),
        ("pack_bridge", "rp_review_pack", "bridge", ("delivery", "operations", "project")),
        ("pack_operations", "rp_review_pack", "operations_handoff", ("operations_handoff", "backend")),
        ("pack_workbench", "rp_review_pack", "workbench_handoff", ("workbench_handoff",)),
        ("pack_project", "rp_review_pack", "project_handoff", ("project_handoff",)),
        ("pack_quality", "rp_review_pack", "host_relay_quality", ("source",)),
    )
    rows: list[dict[str, str]] = []
    for source_kind, file_name, record_key, fields in specs:
        for record in state_records(state, file_name, record_key):
            record_name = record.get(record_key, "")
            for field in fields:
                for reference in split_state_references(record.get(field, "")):
                    rows.append(
                        {
                            "source_kind": source_kind,
                            "record": record_name,
                            "field": field,
                            "reference": reference,
                            "state_file": state_reference_file(reference),
                            "source_line": source_line_for_reference(state, reference),
                            "status": record.get("status", ""),
                        }
                    )
    return rows


def record_label(line: str) -> str:
    first = line.split(";", 1)[0].strip()
    if "=" in first:
        return first
    return "record"


def delivery_source_map(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_file in ("rp_package", "rp_nbexec", "rp_uresrun"):
        for line in state_lines(state, source_file):
            record = parse_kv_record(line)
            if not record:
                continue
            label = record_label(line)
            for field, value in record.items():
                for reference in split_state_references(value):
                    rows.append(
                        {
                            "delivery_record": label,
                            "field": field,
                            "reference": reference,
                            "state_file": state_reference_file(reference),
                            "source_line": source_line_for_reference(state, reference),
                            "source_file": source_file,
                            "status": record.get("status", ""),
                        }
                    )
    return rows


def service_execution_records(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source_file in ("rp_bioop", "rp_labresop", "rp_pubop", "rp_knowop", "rp_runop"):
        for key in ("service_exec", "request", "route", "result", "op"):
            for record in state_records(state, source_file, key):
                item = dict(record)
                if "kind" not in item:
                    item["kind"] = "operation"
                item["source_file"] = source_file
                rows.append(item)
    return rows


def delivery_action_records(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    delivery_keys = {
        "delivery_files",
        "delivery_file",
        "delivery_check",
        "delivery_manifest",
        "downloadable_units",
        "evidence_bundle_entries",
        "host_manifest_bundle",
        "host_manifest_notebook_format",
        "host_action_bundle_contents",
        "host_action_export_bundle_name",
        "host_action_notebook_format",
        "host_action_workbench_bundle",
        "host_action_workbench_manifest",
        "host_action_workbench_verified_files",
        "host_action_workbench_missing_files",
        "host_action_workbench_package",
        "host_action_workbench_file_verify",
        "host_action_workbench_completion",
        "host_action_workbench_outputs",
        "review_pack_bridge",
        "review_page",
        "package_manifest",
        "notebook_export",
        "notebook_download",
        "host_action_protocol_title",
    }
    for source_file in ("rp_package", "rp_nbexec", "rp_uresrun", "rp_artifact_manifest", "rp_review_pack", "rp_web_bundle"):
        for line in state_lines(state, source_file):
            record = parse_kv_record(line)
            if not record:
                continue
            if not any(key in delivery_keys or key.startswith("host_action_export_") for key in record):
                continue
            item = dict(record)
            item["record"] = record_label(line)
            item["source_file"] = source_file
            rows.append(item)
    return rows


def operations_source_files(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    specs = [
        ("operations_report", "rp_review_pack", ("operations_handoff=",), "run.html,review.html"),
        ("operations_report", "rp_package", ("host_action_operations_report=",), "run.html,review.html"),
        ("operations_report", "rp_runner", ("workbench_tasks=", "workbench_next_task="), "run.html,review.html"),
        ("execution_plan", "rp_package", ("host_action_operations_next=", "host_action_quality_gate=", "host_action_quality_repair_execute="), "run.html,review.html,project.html"),
        ("workbench_delivery", "rp_review_pack", ("workbench_handoff=",), "run.html,review.html"),
        ("workbench_delivery", "rp_runner", ("host_action_workbench_id=", "host_action_workbench_task="), "run.html,review.html"),
        ("workbench_delivery", "rp_package", ("host_action_workbench_manifest=", "host_action_workbench_bundle="), "run.html,review.html,artifacts.html"),
        ("project_followup", "rp_review_pack", ("project_handoff=",), "project.html,review.html"),
        ("project_followup", "rp_package", ("host_action_project_id=", "host_action_project_space=", "host_action_project_action_item="), "project.html,review.html"),
        ("backend_evidence", "rp_backend_exec", ("runner_report=",), "run.html,compare.html,review.html"),
        ("backend_evidence", "rp_runner", ("backend_evidence_report=",), "run.html,review.html"),
        ("backend_evidence", "rp_report_text", ("backend_evidence_report=",), "run.html,review.html"),
        ("backend_evidence", "rp_review_pack", ("backend_evidence_review=",), "review.html"),
    ]
    rows: list[dict[str, str]] = []
    for section, name, prefixes, page_view in specs:
        record = first_source_record(state, name, prefixes)
        if not record:
            continue
        parsed = parse_kv_record(record)
        rows.append(
            {
                "operation_section": section,
                "state_file": name,
                "record": record,
                "rendered_page": page_view,
                "status": parsed.get("status", "present"),
            }
        )
    return rows


def report_source_map(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    platform_rows: dict[str, dict[str, str]] = {}
    for record in state_records(state, "rp_report_text", "report_source"):
        section = record.get("report_source", "")
        if not section:
            continue
        state_file = record.get("state_file", "")
        source_key = record.get("source_key", "")
        platform_rows[section] = {
            "report_section": section,
            "state_file": state_file,
            "source_line": source_line_for_keys(state, state_file, source_key) or record.get("source_line", source_key),
            "linked_sources": record.get("linked_sources", ""),
            "review_page": record.get("review_page", "run.html,review.html"),
            "status": record.get("status", ""),
        }

    specs = [
        ("run_setup", "rp_report_text", ("host_report_run_id=", "host_report_title=", "host_report_question=", "host_report_provider="), "rp_input,rp_api_run"),
        ("workflow", "rp_stage_state", ("host_workflow_run_id=", "host_workflow_engine=", "stage=align;"), "rp_stage_dag,rp_run_events,rp_retry_plan"),
        ("artifacts", "rp_artifact_manifest", ("artifact_review_path=raw_to_report", "artifact_review_path=quality_to_package"), "rp_artifact,rp_stage_log,rp_chart_data"),
        ("llm", "rp_llm_resp", ("host_relay_response=", "host_llm_response_summary="), "rp_llm_req,rp_llm_packets,rp_llmeval,rp_llm_guard"),
        ("review", "rp_review_dashboard", ("section=llm", "gate=llm_packet_guard", "decision="), "rp_review_pack,rp_review2,rp_revision"),
        ("workbench", "rp_report_text", ("host_report_workbench=", "host_report_workbench_question=", "host_report_workbench_manifest="), "rp_runner,rp_package,rp_revision"),
        ("backend", "rp_report_text", ("backend_evidence_report=",), "rp_backend_exec,rp_agentcmp,rp_study"),
        ("delivery", "rp_package", ("delivery_file=report_md", "host_relay_delivery_file=", "evidence_bundle_entries="), "rp_artifact_manifest,rp_review_pack"),
    ]
    rows: list[dict[str, str]] = []
    for section, name, prefixes, linked_sources in specs:
        record = first_source_record(state, name, prefixes)
        if not record:
            continue
        parsed = parse_kv_record(record)
        rows.append(
            {
                "report_section": section,
                "state_file": platform_rows.get(section, {}).get("state_file") or name,
                "source_line": platform_rows.get(section, {}).get("source_line") or record,
                "linked_sources": platform_rows.get(section, {}).get("linked_sources") or linked_sources,
                "review_page": platform_rows.get(section, {}).get("review_page") or "run.html,review.html",
                "status": platform_rows.get(section, {}).get("status") or parsed.get("status", "present"),
            }
        )
    known_sections = {row["report_section"] for row in rows}
    for section, row in platform_rows.items():
        if section not in known_sections:
            rows.append(row)
    return rows


def command_output_for_stage(state: dict[str, dict[str, object]], stage: str) -> dict[str, str]:
    for row in state_records(state, "rp_stage_state", "command"):
        command = row.get("command", "")
        if command.startswith(stage + ":"):
            return row
    return {}


def event_for_stage(state: dict[str, dict[str, object]], stage: str, preferred_status: str) -> str:
    fallback = ""
    for row in state_records(state, "rp_run_events", "event"):
        if row.get("stage") != stage:
            continue
        event = row.get("event", "")
        if row.get("status") == preferred_status:
            return event
        if not fallback:
            fallback = event
    return fallback


def stage_from_cache_key(cache_key: str) -> str:
    if ":" in cache_key:
        return cache_key.split(":", 1)[0]
    return cache_key


def cache_action(cache_state: str) -> str:
    if cache_state == "hit":
        return "reuse_cached_artifact"
    if cache_state == "refreshed":
        return "refresh_artifact_record"
    if cache_state == "miss":
        return "execute_stage"
    return cache_state


def stage_control_action(stage_state: str, has_retry: bool, cache_state: str) -> str:
    if has_retry:
        return "rerun_selected_stage"
    if cache_state == "hit" or stage_state == "cached":
        return "reuse_cached_artifact"
    if stage_state in {"accepted", "ready"}:
        return "approve_stage_output"
    if stage_state == "recovered":
        return "verify_recovered_output"
    if stage_state == "done":
        return "record_stage_output"
    return stage_state


def workflow_control_view(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    worker = state_values(state, "rp_worker")
    if worker:
        rows.append(
            {
                "control_view": "worker_pool",
                "source": "rp_worker",
                "worker_slots": worker.get("host_workflow_worker_slots", worker.get("workers", "")),
                "ready_workers": worker.get("ready", ""),
                "busy_workers": worker.get("busy", ""),
                "stalled_workers": worker.get("stalled", ""),
                "queue_depth": worker.get("host_workflow_queue_depth", worker.get("queue_actions", "")),
                "heartbeats": worker.get("heartbeats", ""),
                "action": "assign_ready_workers",
                "status": worker.get("status", ""),
            }
        )

    retry = state_values(state, "rp_retry_plan")
    retry_stage = retry.get("retry_stage", "")
    host_retry_stage = retry.get("host_workflow_retry_stage", "")
    cache_values = state_values(state, "rp_cache_index")
    cache_by_stage: dict[str, dict[str, str]] = {}
    for cache in state_records(state, "rp_cache_index", "cache_key"):
        stage = stage_from_cache_key(cache.get("cache_key", ""))
        if stage:
            cache_by_stage[stage] = cache
        rows.append(
            {
                "control_view": "cache_decision",
                "source": "rp_cache_index",
                "stage": stage,
                "cache_key": cache.get("cache_key", ""),
                "cache_state": cache.get("state", cache.get("cache_result", "")),
                "cache_policy": cache_values.get("host_workflow_cache_policy", cache_values.get("cache_policy", "")),
                "input": cache.get("source", ""),
                "action": cache_action(cache.get("state", cache.get("cache_result", ""))),
                "status": cache.get("state", cache.get("cache_result", "")),
            }
        )

    if retry_stage:
        rows.append(
            {
                "control_view": "retry_decision",
                "source": "rp_retry_plan",
                "stage": retry_stage,
                "attempts": retry.get("attempts", retry.get("host_workflow_next_attempt", "")),
                "failure": retry.get("host_workflow_retry_reason", retry.get("failure_reason", "")),
                "input": retry.get("rerun_inputs", ""),
                "output": retry.get("rerun_outputs", ""),
                "dedupe_key": retry.get("dedupe_key", ""),
                "skip": retry.get("skip_stages", ""),
                "action": retry.get("host_workflow_retry_decision", "minimal_rerun" if retry.get("minimal_rerun") else "retry"),
                "status": retry.get("status", ""),
            }
        )

    if host_retry_stage:
        rows.append(
            {
                "control_view": "host_retry_decision",
                "source": "rp_retry_plan",
                "stage": host_retry_stage,
                "failure": retry.get("host_workflow_retry_reason", ""),
                "action": retry.get("host_workflow_retry_decision", "rerun_stage"),
                "status": retry.get("status", ""),
            }
        )

    worker_slots = worker.get("host_workflow_worker_slots", worker.get("workers", ""))
    for stage in state_records(state, "rp_stage_state", "stage"):
        stage_name = stage.get("stage", "")
        cache = cache_by_stage.get(stage_name, {})
        stage_state = stage.get("state", "")
        has_retry = stage_name == retry_stage
        rows.append(
            {
                "control_view": "stage_assignment",
                "source": "rp_stage_state",
                "stage": stage_name,
                "worker_slots": worker_slots,
                "attempts": stage.get("attempts", ""),
                "state": stage_state,
                "cache_state": cache.get("state", ""),
                "event": event_for_stage(state, stage_name, stage_state),
                "action": stage_control_action(stage_state, has_retry, cache.get("state", "")),
                "status": stage_state,
            }
        )

    stage_state = state_values(state, "rp_stage_state")
    execobs = state_values(state, "rp_execobs")
    if stage_state.get("host_workflow_run_id") or execobs.get("host_workflow_observer_events"):
        rows.append(
            {
                "control_view": "host_workflow_control",
                "source": "rp_stage_state+rp_execobs",
                "workflow": stage_state.get("host_workflow_id", ""),
                "run_id": stage_state.get("host_workflow_run_id", ""),
                "engine": stage_state.get("host_workflow_engine", ""),
                "stage": stage_state.get("host_workflow_retry_stage", ""),
                "cache_state": stage_state.get("host_workflow_cache_hit_stage", ""),
                "worker_slots": stage_state.get("host_workflow_worker_slots", worker.get("host_workflow_worker_slots", "")),
                "queue_depth": stage_state.get("host_workflow_queue_depth", worker.get("host_workflow_queue_depth", "")),
                "observer_events": execobs.get("host_workflow_observer_events", ""),
                "action": "observe_host_workflow",
                "status": "ready",
            }
        )
    return rows


def workflow_evidence_links(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    retry = state_values(state, "rp_retry_plan")
    retry_stage = retry.get("retry_stage", "")
    for stage in state_records(state, "rp_stage_state", "stage"):
        stage_name = stage.get("stage", "")
        status = stage.get("state", "")
        command = command_output_for_stage(state, stage_name)
        rows.append(
            {
                "evidence_view": "stage_evidence",
                "stage": stage_name,
                "input": stage.get("input", ""),
                "output": command.get("output", ""),
                "event": event_for_stage(state, stage_name, status),
                "retry": "rp_retry_plan" if stage_name == retry_stage else "",
                "failure": retry.get("failure_reason", "") if stage_name == retry_stage else "",
                "log": "rp_stage_log" if stage_name == retry_stage else "",
                "manifest": "rp_artifact_manifest",
                "report": "rp_report_text" if stage_name in {"review", "package"} else "",
                "review": "rp_review_dashboard" if stage_name in {"review", "package"} else "",
                "status": status,
            }
        )

    for provenance in state_records(state, "rp_artifact", "provenance"):
        rows.append(
            {
                "evidence_view": "artifact_provenance",
                "stage": provenance.get("stage", ""),
                "artifact": provenance.get("provenance", ""),
                "event": provenance.get("event", ""),
                "retry": provenance.get("retry", ""),
                "cache": provenance.get("cache", ""),
                "review": provenance.get("review_gate", ""),
                "llm_quality": provenance.get("llm_quality", ""),
                "status": provenance.get("status", ""),
            }
        )

    for path in state_records(state, "rp_artifact_manifest", "artifact_review_path"):
        rows.append(
            {
                "evidence_view": "artifact_review_path",
                "path": path.get("artifact_review_path", ""),
                "input": path.get("input", ""),
                "prepared": path.get("prepared", ""),
                "artifact": path.get("artifact", path.get("metrics", path.get("failure", ""))),
                "retry": path.get("retry", ""),
                "event": path.get("event", ""),
                "report": path.get("report", ""),
                "review": path.get("review", path.get("review_pack", "")),
                "delivery": path.get("delivery", ""),
                "status": path.get("status", ""),
            }
        )

    review = state_values(state, "rp_review_dashboard")
    package = state_values(state, "rp_package")
    if review or package:
        rows.append(
            {
                "evidence_view": "review_delivery",
                "source": "rp_review_dashboard+rp_package",
                "report": "rp_report_text",
                "review": review.get("decision", review.get("status", "")),
                "delivery": package.get("status", ""),
                "status": review.get("status", package.get("status", "")),
            }
        )
    return rows


def llm_relay_flow(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    requests = state_records(state, "rp_llm_req", "host_relay_request")
    packets = state_records(state, "rp_llm_packets", "host_relay_packet")
    responses = state_records(state, "rp_llm_resp", "host_relay_response")
    evals = state_records(state, "rp_llmeval", "host_relay_eval")
    guards = state_records(state, "rp_llm_guard", "host_relay_guard")
    replays = state_records(state, "rp_relay", "host_relay_replay")
    prompts = state_records(state, "rp_prompt", "host_relay_prompt_route")

    request_ids: list[str] = []
    for record in requests:
        request_id = record.get("host_relay_request", "")
        if request_id and request_id not in request_ids:
            request_ids.append(request_id)
    for record in packets:
        request_id = record.get("host_relay_packet", record.get("host_llm_packet_request", ""))
        if request_id and request_id not in request_ids:
            request_ids.append(request_id)
    for key in ("rp_llm_req", "rp_llm_packets", "rp_api_runtime"):
        request_id = first_value(state, key, "host_llm_request_id")
        if request_id and request_id not in request_ids:
            request_ids.append(request_id)
    request_id = first_value(state, "rp_llm_packets", "host_llm_packet_request")
    if request_id and request_id not in request_ids:
        request_ids.append(request_id)

    rows: list[dict[str, str]] = []
    for request_id in request_ids:
        request = first_record_by_key(requests, "host_relay_request", request_id)
        packet = first_record_by_key(packets, "host_relay_packet", request_id)
        response_id = packet.get("response", first_value(state, "rp_llm_packets", "host_llm_packet_response"))
        response = first_record_by_key(responses, "request", request_id)
        if not response and response_id:
            response = first_record_by_key(responses, "host_relay_response", response_id)
        if not response_id:
            response_id = response.get("host_relay_response", first_value(state, "rp_llm_resp", "host_llm_response_id"))
        eval_record = first_record_by_key(evals, "host_relay_eval", request_id)
        guard = first_record_by_key(guards, "host_relay_guard", request_id)
        replay = first_record_by_key(replays, "host_relay_replay", request_id)
        prompt = first_record_by_key(prompts, "host_relay_prompt_route", request_id)
        rows.append(
            {
                "flow": request_id,
                "route": request.get("route", prompt.get("route", first_value(state, "rp_llm_packets", "host_llm_packet_route"))),
                "provider": request.get("provider", first_value(state, "rp_llm_req", "host_llm_provider")),
                "response": response_id,
                "summary": response.get("summary", first_value(state, "rp_llm_resp", "host_llm_response_summary")),
                "quality": eval_record.get("status", ""),
                "checks": eval_record.get("passed", eval_record.get("checks", "")),
                "guard": guard.get("status", ""),
                "secret": guard.get("secret_in_packet", packet.get("secret_in_packet", first_value(state, "rp_llm_packets", "secret_in_packet"))),
                "replay": replay.get("status", ""),
                "prompt_hash": request.get("prompt_hash", prompt.get("prompt_hash", packet.get("prompt_hash", ""))),
                "outputs": "rp_llm_req,rp_llm_packets,rp_llm_resp,rp_llmeval,rp_llm_guard,rp_relay,rp_prompt",
                "status": response.get("status", eval_record.get("status", guard.get("status", packet.get("status", "")))),
            }
        )
    return rows


def workflow_execution_view(state: dict[str, dict[str, object]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = (
        state_records(state, "rp_execobs", "execution_view")
        + state_records(state, "rp_execobs", "host_execution_view")
    )
    for stage in state_records(state, "rp_stage_state", "stage"):
        stage_name = stage.get("stage", "")
        command = command_output_for_stage(state, stage_name)
        status = stage.get("state", "")
        row = {
            "execution_view": "stage_summary",
            "stage": stage_name,
            "order": stage.get("order", ""),
            "attempts": stage.get("attempts", ""),
            "state": status,
            "input": stage.get("input", ""),
            "output": command.get("output", ""),
            "cache": command.get("cache", ""),
            "retry": "rp_retry_plan" if stage_name == state_values(state, "rp_retry_plan").get("retry_stage", "") else "",
            "failure": state_values(state, "rp_retry_plan").get("failure_reason", "") if stage_name == state_values(state, "rp_retry_plan").get("retry_stage", "") else "",
            "event": event_for_stage(state, stage_name, status),
            "status": status,
        }
        rows.append(row)
    if rows:
        rows.append(
            {
                "execution_view": "control_summary",
                "dag": "rp_stage_dag",
                "cache": "rp_cache_index",
                "retry": "rp_retry_plan",
                "events": "rp_run_events",
                "workers": "rp_worker",
                "observer": "rp_execobs",
                "status": state_values(state, "rp_execobs").get("status", state_values(state, "rp_stage_state").get("status", "")),
            }
        )
    stage_state = state_values(state, "rp_stage_state")
    execobs = state_values(state, "rp_execobs")
    if stage_state.get("host_workflow_run_id") or execobs.get("host_workflow_observer_events"):
        rows.append(
            {
                "host_execution_view": "workflow_run",
                "workflow": stage_state.get("host_workflow_id", ""),
                "run_id": stage_state.get("host_workflow_run_id", ""),
                "engine": stage_state.get("host_workflow_engine", ""),
                "retry_stage": stage_state.get("host_workflow_retry_stage", state_values(state, "rp_retry_plan").get("host_workflow_retry_stage", "")),
                "cache_hit": stage_state.get("host_workflow_cache_hit_stage", state_values(state, "rp_cache_index").get("host_workflow_cache_hit_stage", "")),
                "worker_slots": stage_state.get("host_workflow_worker_slots", state_values(state, "rp_worker").get("host_workflow_worker_slots", "")),
                "queue_depth": stage_state.get("host_workflow_queue_depth", state_values(state, "rp_worker").get("host_workflow_queue_depth", "")),
                "observer_events": execobs.get("host_workflow_observer_events", ""),
                "status": "ready",
            }
        )
    return rows


def render_record_panel(
    title: str,
    columns: list[tuple[str, str]],
    rows: list[dict[str, str]],
    empty_text: str = "No matching records",
) -> str:
    head = "".join("<th>{}</th>".format(html.escape(label)) for label, _ in columns)
    body = []
    for row in rows:
        body.append(
            "<tr>{}</tr>".format(
                "".join("<td>{}</td>".format(html.escape(row.get(key, ""))) for _, key in columns)
            )
        )
    if not body:
        body.append("<tr><td colspan='{}'>{}</td></tr>".format(len(columns), html.escape(empty_text)))
    return "<section class='panel'><h2>{}</h2><table><tr>{}</tr>{}</table></section>".format(
        html.escape(title),
        head,
        "".join(body),
    )


def render_line_panel(title: str, rows: list[tuple[str, str]], empty_text: str = "No matching records") -> str:
    body = []
    for label, value in rows:
        body.append(
            "<tr><th>{}</th><td>{}</td></tr>".format(
                html.escape(label),
                html.escape(value),
            )
        )
    if not body:
        body.append("<tr><td colspan='2'>{}</td></tr>".format(html.escape(empty_text)))
    return "<section class='panel'><h2>{}</h2><table>{}</table></section>".format(
        html.escape(title),
        "".join(body),
    )


def render_page_summary(file_name: str, state: dict[str, dict[str, object]]) -> str:
    home_items = [
        ("Plain Target", "root unchanged uCore", "rp_api_home"),
        ("Plain Workflow", metric_value(state, [("rp_backend", "plain_cases"), ("rp_agentcmp", "backend_runner_checks")]), "rp_backend"),
        ("AgentOS Target", metric_value(state, [("rp_agentos_kernel", "mode"), ("rp_backend", "agentos")]), "rp_agentos_kernel"),
        ("AgentOS Flow", "{} stages".format(len(state_records(state, "rp_agentos_mainflow", "stage"))), "rp_agentos_mainflow"),
        ("Shared Run", metric_value(state, [("rp_agentos_timeline", "run_id"), ("rp_report_text", "host_report_run_id"), ("rp_input", "host_action_run_id")]), "rp_report_text"),
        ("Visible Comparison", "compare.html", "rp_api_compare"),
    ]
    report_items = [
        ("Run", metric_value(state, [("rp_report_text", "host_report_run_id"), ("rp_input", "host_action_run_id")]), "rp_report_text"),
        ("Reviewer", metric_value(state, [("rp_report_text", "host_report_reviewer"), ("rp_review2", "host_action_reviewer")]), "rp_review2"),
        ("Decision", metric_value(state, [("rp_report_text", "host_report_review_decision"), ("rp_review2", "host_action_review_decision")]), "rp_review2"),
        ("Revision Targets", metric_value(state, [("rp_report_text", "host_report_revision_targets"), ("rp_revision", "host_action_revision_targets")]), "rp_revision"),
        ("Bundle", metric_value(state, [("rp_report_text", "host_report_bundle"), ("rp_package", "host_action_export_bundle_name")]), "rp_package"),
        ("Compare Profile", metric_value(state, [("rp_report_text", "host_report_compare_profile"), ("rp_agentcmp", "host_action_compare_profile")]), "rp_agentcmp"),
        ("Backend Evidence", metric_value(state, [("rp_report_text", "backend_evidence_report"), ("rp_runner", "backend_evidence_report")]), "rp_report_text"),
    ]
    evidence_items = [
        ("Manifest Run", metric_value(state, [("rp_artifact_manifest", "host_manifest_run_id"), ("rp_report_text", "host_report_run_id")]), "rp_artifact_manifest"),
        ("Notebook Format", metric_value(state, [("rp_artifact_manifest", "host_manifest_notebook_format"), ("rp_nbexec", "host_action_notebook_format")]), "rp_nbexec"),
        ("Bundle", metric_value(state, [("rp_artifact_manifest", "host_manifest_bundle"), ("rp_package", "host_action_export_bundle_name")]), "rp_package"),
        ("Contents", metric_value(state, [("rp_package", "host_action_bundle_contents")]), "rp_package"),
        ("Evidence Entries", metric_value(state, [("rp_package", "evidence_bundle_entries")]), "rp_package"),
        ("Manifest Records", metric_value(state, [("rp_artifact_manifest", "manifest_records")]), "rp_artifact_manifest"),
    ]
    delivery_items = [
        ("Delivery Files", metric_value(state, [("rp_package", "delivery_files")]), "rp_package"),
        ("Delivery Checks", metric_value(state, [("rp_web_bundle", "delivery_checks")]), "rp_web_bundle"),
        ("Evidence Entries", metric_value(state, [("rp_package", "evidence_bundle_entries"), ("rp_web_bundle", "evidence_bundle_entries")]), "rp_package"),
        ("Bundle", metric_value(state, [("rp_package", "host_action_export_bundle_name"), ("rp_artifact_manifest", "host_manifest_bundle")]), "rp_package"),
        ("Notebook", metric_value(state, [("rp_nbexec", "host_action_notebook_format"), ("rp_web_bundle", "notebook_export")]), "rp_nbexec"),
        ("Download Units", metric_value(state, [("rp_web_bundle", "downloadable_units")]), "rp_web_bundle"),
    ]
    workflow_items = [
        ("Workflow", metric_value(state, [("rp_stage_state", "host_workflow_id"), ("rp_plan", "workflow")]), "rp_stage_state"),
        ("Run", metric_value(state, [("rp_stage_state", "host_workflow_run_id")]), "rp_stage_state"),
        ("Engine", metric_value(state, [("rp_stage_state", "host_workflow_engine")]), "rp_stage_state"),
        ("Retry Stage", metric_value(state, [("rp_retry_plan", "host_workflow_retry_stage"), ("rp_retry_plan", "retry_stage")]), "rp_retry_plan"),
        ("Cache Hit", metric_value(state, [("rp_stage_state", "host_workflow_cache_hit_stage"), ("rp_cache_index", "host_workflow_cache_hit_stage")]), "rp_cache_index"),
        ("Worker Slots", metric_value(state, [("rp_stage_state", "host_workflow_worker_slots"), ("rp_worker", "host_workflow_worker_slots")]), "rp_worker"),
        ("Queue Depth", metric_value(state, [("rp_stage_state", "host_workflow_queue_depth"), ("rp_worker", "host_workflow_queue_depth")]), "rp_worker"),
    ]
    workbench_items = [
        ("Workbench", metric_value(state, [("rp_runner", "host_action_workbench_id"), ("rp_report_text", "host_report_workbench"), ("rp_uresrun", "host_action_workbench")]), "rp_runner"),
        ("Title", metric_value(state, [("rp_runner", "host_action_workbench_title"), ("rp_report_text", "host_report_workbench_title")]), "rp_runner"),
        ("Question", metric_value(state, [("rp_runner", "host_action_workbench_question"), ("rp_report_text", "host_report_workbench_question")]), "rp_runner"),
        ("Task", metric_value(state, [("rp_runner", "host_action_workbench_task"), ("rp_report_text", "host_report_workbench_task")]), "rp_runner"),
        ("Readiness", metric_value(state, [("rp_runner", "host_action_workbench_readiness"), ("rp_package", "host_action_workbench_readiness")]), "rp_runner"),
        ("Manifest", metric_value(state, [("rp_package", "host_action_workbench_manifest"), ("rp_report_text", "host_report_workbench_manifest"), ("rp_uresrun", "host_action_workbench_manifest")]), "rp_package"),
        ("Bundle", metric_value(state, [("rp_package", "host_action_workbench_bundle"), ("rp_report_text", "host_report_workbench_bundle"), ("rp_uresrun", "host_action_workbench_bundle")]), "rp_package"),
        ("Notebook", metric_value(state, [("rp_nbexec", "host_action_notebook_format"), ("rp_web_bundle", "notebook_export")]), "rp_nbexec"),
    ]
    studio_rows = state_records(state, "rp_studio", "studio_session")
    studio_record = studio_rows[-1] if studio_rows else {}
    studio_items = [
        ("Session", studio_record.get("studio_session") or metric_value(state, [("rp_studio", "latest_session")]), "rp_studio"),
        ("Title", studio_record.get("title") or metric_value(state, [("rp_studio", "host_action_studio_title")]), "rp_studio"),
        ("Goal", studio_record.get("goal") or metric_value(state, [("rp_studio", "host_action_studio_goal")]), "rp_studio"),
        ("Direction", studio_record.get("direction") or metric_value(state, [("rp_studio", "host_action_studio_direction")]), "rp_studio"),
        ("Workbench", studio_record.get("workbench") or metric_value(state, [("rp_studio", "host_action_studio_workbench")]), "rp_studio"),
        ("Run", studio_record.get("run") or metric_value(state, [("rp_studio", "host_action_studio_run")]), "rp_studio"),
        ("Answer", studio_record.get("answer") or metric_value(state, [("rp_studio", "host_action_studio_answer")]), "rp_studio"),
        ("Decision", studio_record.get("decision") or metric_value(state, [("rp_studio", "host_action_studio_decision")]), "rp_studio"),
    ]
    data_items = [
        ("Ingested Files", metric_value(state, [("rp_ingest_files", "files"), ("rp_api_data", "ingested_files")]), "rp_ingest_files"),
        ("Snapshots", metric_value(state, [("rp_dataset_snapshot", "snapshots"), ("rp_api_data", "dataset_snapshots")]), "rp_dataset_snapshot"),
        ("Previews", metric_value(state, [("rp_data_preview", "previews"), ("rp_api_data", "previews")]), "rp_data_preview"),
        ("Quality Checks", metric_value(state, [("rp_data_quality", "passed"), ("rp_api_data", "quality_checks")]), "rp_data_quality"),
        ("Transforms", metric_value(state, [("rp_data_transform", "transforms"), ("rp_api_data", "transforms")]), "rp_data_transform"),
        ("Collection Items", metric_value(state, [("rp_dataset_collection", "items"), ("rp_api_data", "collection_items")]), "rp_dataset_collection"),
        ("Manifest", metric_value(state, [("rp_api_data", "host_action_file_manifest"), ("rp_ingest_files", "host_file_manifest")]), "rp_api_data"),
        ("Verified", metric_value(state, [("rp_api_data", "host_action_file_verified"), ("rp_data_quality", "host_file_verify_verified")]), "rp_api_data"),
    ]
    project_rows = state_records(state, "rp_review_pack", "project_handoff")
    project_record = project_rows[0] if project_rows else {}
    project_items = [
        ("Project", project_record.get("project") or metric_value(state, [("rp_package", "host_action_project_id"), ("rp_runner", "host_action_project_id")]), "rp_review_pack"),
        ("Space", project_record.get("space") or metric_value(state, [("rp_package", "host_action_project_space"), ("rp_runner", "host_action_project_space")]), "rp_package"),
        ("Note", project_record.get("note") or metric_value(state, [("rp_package", "host_action_project_note"), ("rp_runner", "host_action_project_note")]), "rp_package"),
        ("Action Item", project_record.get("action_item") or metric_value(state, [("rp_package", "host_action_project_action_item"), ("rp_runner", "host_action_project_action_item")]), "rp_package"),
        ("Answer", project_record.get("answer") or metric_value(state, [("rp_package", "host_action_project_answer"), ("rp_runner", "host_action_project_answer")]), "rp_package"),
        ("Repair", project_record.get("repair") or metric_value(state, [("rp_package", "host_action_project_repair"), ("rp_runner", "host_action_project_repair")]), "rp_package"),
        ("Search", project_record.get("search") or metric_value(state, [("rp_package", "host_action_research_search"), ("rp_runner", "host_action_research_search")]), "rp_package"),
        ("Quality", metric_value(state, [("rp_package", "host_action_quality_gate"), ("rp_runner", "host_action_quality_gate")]), "rp_package"),
        ("Project Delivery", metric_value(state, [("rp_projectrel", "project_delivery_checks")]), "rp_projectrel"),
        ("Study Protocols", metric_value(state, [("rp_studyproto", "study_protocol_checks")]), "rp_studyproto"),
    ]
    release_rows = state_records(state, "rp_web_bundle", "release_gate")
    release_record = release_rows[-1] if release_rows else {}
    snapshot_rows = state_records(state, "rp_web_bundle", "project_snapshot")
    snapshot_record = snapshot_rows[-1] if snapshot_rows else {}
    reproducibility_rows = state_records(state, "rp_web_bundle", "reproducibility_audit")
    reproducibility_record = reproducibility_rows[-1] if reproducibility_rows else {}
    delivery_rows = state_records(state, "rp_web_bundle", "project_delivery")
    delivery_record = delivery_rows[-1] if delivery_rows else {}
    project_review_items = [
        ("Project", metric_value(state, [("rp_web_bundle", "project"), ("rp_package", "host_action_project_id"), ("rp_runner", "host_action_project_id")]), "rp_web_bundle"),
        ("Release Gate", release_record.get("decision") or metric_value(state, [("rp_projectrel", "release_gate"), ("rp_web_bundle", "host_action_project_release_gate")]), "rp_projectrel"),
        ("Snapshot", snapshot_record.get("status") or metric_value(state, [("rp_projectrel", "project_snapshot"), ("rp_web_bundle", "host_action_project_snapshot")]), "rp_projectrel"),
        ("Reproducibility", reproducibility_record.get("decision") or metric_value(state, [("rp_projectrel", "reproducibility_audit"), ("rp_web_bundle", "host_action_project_reproducibility")]), "rp_projectrel"),
        ("Provenance", metric_value(state, [("rp_projectrel", "provenance_graph"), ("rp_web_bundle", "host_action_project_provenance_graph"), ("rp_web_bundle", "provenance_graph")]), "rp_projectrel"),
        ("Delivery", delivery_record.get("decision") or metric_value(state, [("rp_projectrel", "project_delivery"), ("rp_web_bundle", "host_action_project_delivery")]), "rp_projectrel"),
        ("Package Intake", metric_value(state, [("rp_projectrel", "package_intake"), ("rp_web_bundle", "host_action_project_package_intake"), ("rp_web_bundle", "package_intake")]), "rp_projectrel"),
        ("Package Index", metric_value(state, [("rp_projectrel", "package_index"), ("rp_web_bundle", "package_index")]), "rp_projectrel"),
        ("Study Launches", metric_value(state, [("rp_studyproto", "study_protocol_launches")]), "rp_studyproto"),
        ("Reproduction Package", metric_value(state, [("rp_studyproto", "study_protocol_reproduction_packages")]), "rp_studyproto"),
    ]
    compare_items = [
        ("Payload Applied", metric_value(state, [("rp_api_compare", "host_action_payload_applied")]), "rp_api_compare"),
        ("Run", metric_value(state, [("rp_api_compare", "host_action_run_id")]), "rp_api_compare"),
        ("Reviewer", metric_value(state, [("rp_api_compare", "host_action_reviewer")]), "rp_api_compare"),
        ("Revision Targets", metric_value(state, [("rp_api_compare", "host_action_revision_targets")]), "rp_api_compare"),
        ("Bundle", metric_value(state, [("rp_api_compare", "host_action_bundle")]), "rp_api_compare"),
        ("Compare Profile", metric_value(state, [("rp_api_compare", "host_action_compare_profile")]), "rp_api_compare"),
        ("Portability Checks", metric_value(state, [("rp_agentcmp", "workflow_portability_checks")]), "rp_agentcmp"),
        ("Backend Checks", metric_value(state, [("rp_agentcmp", "portability_backend_checks")]), "rp_agentcmp"),
        ("Backend Runner", metric_value(state, [("rp_agentcmp", "backend_runner_checks")]), "rp_agentcmp"),
        ("Study Protocol Checks", metric_value(state, [("rp_agentcmp", "study_protocol_checks"), ("rp_studyproto", "study_protocol_checks")]), "rp_studyproto"),
        ("Integrity Checks", metric_value(state, [("rp_agentcmp", "integrity_plane_checks"), ("rp_integrity", "integrity_checks")]), "rp_integrity"),
        ("Publication Checks", metric_value(state, [("rp_agentcmp", "publication_checks"), ("rp_publication", "publication_checks")]), "rp_publication"),
        ("Systematic Review Checks", metric_value(state, [("rp_agentcmp", "systematic_review_checks"), ("rp_sysreview", "systematic_review_checks")]), "rp_sysreview"),
        ("Experiment Scheduling Checks", metric_value(state, [("rp_agentcmp", "experiment_scheduling_checks"), ("rp_expsched", "experiment_scheduling_checks")]), "rp_expsched"),
        ("Training Compliance Checks", metric_value(state, [("rp_agentcmp", "training_compliance_checks"), ("rp_traincomp", "training_compliance_checks")]), "rp_traincomp"),
        ("Analysis Results Checks", metric_value(state, [("rp_agentcmp", "analysis_results_checks"), ("rp_analysisres", "analysis_results_checks")]), "rp_analysisres"),
        ("Decision Support Checks", metric_value(state, [("rp_agentcmp", "decision_support_checks"), ("rp_decsupport", "decision_support_checks")]), "rp_decsupport"),
        ("Usable Research Checks", metric_value(state, [("rp_agentcmp", "usable_research_checks"), ("rp_usable", "usable_research_checks")]), "rp_usable"),
        ("Mature Capability", metric_value(state, [("rp_agentcmp", "mature_capability_checks"), ("rp_mature", "capability_checks")]), "rp_mature"),
        ("AgentOS Flow Stages", len(state_records(state, "rp_agentos_mainflow", "stage")), "rp_agentos_mainflow"),
        ("AgentOS Metadata", metric_value(state, [("rp_agentos_query", "metadata_source"), ("rp_agentos_mainflow", "metadata_query")]), "rp_agentos_query"),
        ("AgentOS Recovery", metric_value(state, [("rp_agentos_recovery", "kernel_tool"), ("rp_agentos_mainflow", "failure_recovery")]), "rp_agentos_recovery"),
        ("AgentOS Events", metric_value(state, [("rp_agentos_timeline", "event_delivery"), ("rp_agentos_collab_ack", "delivery")]), "rp_agentos_timeline"),
        ("AgentOS Audit", metric_value(state, [("rp_agentos_audit", "audit_source"), ("rp_agentos_package", "ledger")]), "rp_agentos_audit"),
        ("AgentOS Real Task", metric_value(state, [("rp_agentos_real_task", "report_answer"), ("rp_agentos_mainflow", "real_task_context")]), "rp_agentos_real_task"),
        ("AgentOS Edit Lease", metric_value(state, [("rp_agentos_conflict", "edit_lease"), ("rp_agentos_mainflow", "edit_lease")]), "rp_agentos_conflict"),
    ]
    llm_items = [
        ("Relay", metric_value(state, [("rp_llm_resp", "host_relay_process"), ("rp_relay", "mode")]), "rp_llm_resp"),
        ("Quality", metric_value(state, [("rp_llmeval", "host_relay_eval_batch")]), "rp_llmeval"),
        ("Guard", metric_value(state, [("rp_llm_guard", "host_relay_guard_batch")]), "rp_llm_guard"),
        ("Replay", metric_value(state, [("rp_relay", "host_relay_replay_batch")]), "rp_relay"),
        ("Routes", metric_value(state, [("rp_prompt", "host_relay_prompt_batch"), ("rp_prompt", "routes")]), "rp_prompt"),
        ("Delivery Checks", metric_value(state, [("rp_agentcmp", "llm_delivery_checks")]), "rp_agentcmp"),
        ("Runtime", metric_value(state, [("rp_api_runtime", "host_llm_relay_quality")]), "rp_api_runtime"),
    ]
    service_items = [
        ("Bio Ops", metric_value(state, [("rp_bioop", "ops")]), "rp_bioop"),
        ("Lab Ops", metric_value(state, [("rp_labresop", "ops")]), "rp_labresop"),
        ("Publication Ops", metric_value(state, [("rp_pubop", "ops")]), "rp_pubop"),
        ("Knowledge Ops", metric_value(state, [("rp_knowop", "ops")]), "rp_knowop"),
        ("Runtime Ops", metric_value(state, [("rp_runop", "ops")]), "rp_runop"),
        ("Runbook Steps", metric_value(state, [("rp_runbooks", "runbook_steps")]), "rp_runbooks"),
        ("Study Protocols", metric_value(state, [("rp_studyproto", "study_protocols")]), "rp_studyproto"),
        ("Operations Checks", metric_value(state, [("rp_opsboard", "operations_board_checks")]), "rp_opsboard"),
        ("Service Files", metric_value(state, [("rp_web_bundle", "research_service_files"), ("rp_api_compare", "bio_service_files")]), "rp_web_bundle"),
    ]
    api_catalog_items = [
        ("Host API Routes", metric_value(state, [("rp_api_catalog", "host_api_routes")]), "rp_api_catalog"),
        ("Host Actions", metric_value(state, [("rp_api_catalog", "host_action_routes"), ("rp_api_action", "actions")]), "rp_api_catalog"),
        ("Host Pages", metric_value(state, [("rp_api_catalog", "host_page_routes"), ("rp_web_routes", "host_page_routes")]), "rp_api_catalog"),
        ("Host Dynamic Pages", metric_value(state, [("rp_api_catalog", "host_dynamic_page_prefixes"), ("rp_web_routes", "host_dynamic_page_prefixes")]), "rp_api_catalog"),
        ("Host Downloads", metric_value(state, [("rp_api_catalog", "host_download_routes"), ("rp_web_routes", "host_download_routes")]), "rp_api_catalog"),
        ("Reader GET Routes", metric_value(state, [("rp_api_catalog", "ucore_get_routes"), ("rp_web_routes", "get_routes")]), "rp_api_catalog"),
        ("Reader Dynamic Prefixes", metric_value(state, [("rp_api_catalog", "ucore_dynamic_page_prefixes"), ("rp_web_routes", "ucore_dynamic_page_prefixes")]), "rp_api_catalog"),
        ("Reader Downloads", metric_value(state, [("rp_api_catalog", "ucore_download_routes"), ("rp_web_routes", "ucore_download_routes")]), "rp_api_catalog"),
        ("Grouped Routes", metric_value(state, [("rp_api_catalog", "api_grouped_routes")]), "rp_api_catalog"),
        ("API Groups", metric_value(state, [("rp_api_catalog", "api_group_count")]), "rp_api_catalog"),
        ("Reader Payloads", metric_value(state, [("rp_api_catalog", "reader_api_payloads"), ("rp_web_bundle", "api_payloads")]), "rp_api_catalog"),
        ("Reader Views", metric_value(state, [("rp_api_catalog", "reader_views"), ("rp_web_bundle", "reader_views")]), "rp_api_catalog"),
        ("Usable Research APIs", metric_value(state, [("rp_api_catalog", "usable_research_api_routes")]), "rp_api_catalog"),
        ("Domain APIs", metric_value(state, [("rp_api_catalog", "domain_api_routes")]), "rp_api_catalog"),
        ("Lab Research APIs", metric_value(state, [("rp_api_catalog", "lab_research_api_routes")]), "rp_api_catalog"),
        ("Workflow APIs", metric_value(state, [("rp_api_catalog", "workflow_api_routes")]), "rp_api_catalog"),
        ("Data APIs", metric_value(state, [("rp_api_catalog", "data_api_routes")]), "rp_api_catalog"),
        ("Route State", metric_value(state, [("rp_web_routes", "routes")]), "rp_web_routes"),
        ("POST Actions", metric_value(state, [("rp_web_routes", "post_routes")]), "rp_web_routes"),
        ("Projection", metric_value(state, [("rp_api_catalog", "reader_projection")]), "rp_api_catalog"),
    ]
    operations_items = [
        ("Provider", metric_value(state, [("rp_opsboard", "provider_health"), ("rp_startup", "provider_health")]), "rp_opsboard"),
        ("Pending Reviews", metric_value(state, [("rp_opsboard", "pending_reviews")]), "rp_opsboard"),
        ("Workbench Actions", metric_value(state, [("rp_opsboard", "active_workbench_actions")]), "rp_opsboard"),
        ("Plan Items", metric_value(state, [("rp_opsboard", "active_plan_items")]), "rp_opsboard"),
        ("Action Items", metric_value(state, [("rp_opsboard", "active_action_items")]), "rp_opsboard"),
        ("Handoffs", metric_value(state, [("rp_opsboard", "ready_handoffs")]), "rp_opsboard"),
        ("Latest Runs", metric_value(state, [("rp_opsboard", "latest_runs")]), "rp_opsboard"),
        ("Exports", metric_value(state, [("rp_opsboard", "export_formats")]), "rp_opsboard"),
    ]
    review_items = [
        ("Run", metric_value(state, [("rp_review_dashboard", "run"), ("rp_report_text", "host_report_run_id")]), "rp_review_dashboard"),
        ("Sections", metric_value(state, [("rp_review_dashboard", "sections")]), "rp_review_dashboard"),
        ("Decision", metric_value(state, [("rp_review_dashboard", "decision")]), "rp_review_dashboard"),
        ("Evidence Pack", metric_value(state, [("rp_review_pack", "pack")]), "rp_review_pack"),
        ("Human Review", metric_value(state, [("rp_review2", "decision"), ("rp_report_text", "host_report_review_decision")]), "rp_review2"),
        ("Delivery", metric_value(state, [("rp_package", "latest_delivery_status"), ("rp_package", "status")]), "rp_package"),
        ("Bridge", metric_value(state, [("rp_review_pack", "bridge"), ("rp_package", "review_pack_bridge")]), "rp_review_pack"),
        ("Handoff Checks", metric_value(state, [("rp_agentcmp", "review_handoff_checks")]), "rp_agentcmp"),
        ("Host Relay Quality", metric_value(state, [("rp_review_pack", "host_relay_quality"), ("rp_review_dashboard", "host_relay_quality")]), "rp_review_pack"),
    ]
    review_board_items = [
        ("Decision", metric_value(state, [("rp_reviewboard", "decision")]), "rp_reviewboard"),
        ("Requests", metric_value(state, [("rp_reviewboard", "review_requests")]), "rp_reviewboard"),
        ("Votes", metric_value(state, [("rp_reviewboard", "review_votes")]), "rp_reviewboard"),
        ("Signoffs", metric_value(state, [("rp_reviewboard", "review_signoffs")]), "rp_reviewboard"),
        ("Assignments", metric_value(state, [("rp_reviewboard", "review_assignments")]), "rp_reviewboard"),
        ("Filters", metric_value(state, [("rp_reviewboard", "review_filters")]), "rp_reviewboard"),
        ("Workloads", metric_value(state, [("rp_reviewboard", "review_workloads")]), "rp_reviewboard"),
        ("Package", metric_value(state, [("rp_reviewboard", "review_package")]), "rp_reviewboard"),
    ]
    control_plane_items = [
        ("Approvals", metric_value(state, [("rp_control", "approvals")]), "rp_control"),
        ("Notifications", metric_value(state, [("rp_control", "notifications")]), "rp_control"),
        ("Queue Items", metric_value(state, [("rp_control", "run_queue_items")]), "rp_control"),
        ("Plugin Runs", metric_value(state, [("rp_control", "plugin_runs")]), "rp_control"),
        ("Workspaces", metric_value(state, [("rp_control", "workspaces")]), "rp_control"),
        ("Users", metric_value(state, [("rp_control", "users")]), "rp_control"),
        ("Permissions", metric_value(state, [("rp_control", "permissions")]), "rp_control"),
        ("Control Actions", metric_value(state, [("rp_control", "control_actions")]), "rp_control"),
    ]
    integrity_items = [
        ("Checks", metric_value(state, [("rp_integrity", "integrity_checks")]), "rp_integrity"),
        ("Evidence", metric_value(state, [("rp_integrity", "evidence_checks")]), "rp_integrity"),
        ("References", metric_value(state, [("rp_integrity", "reference_checks")]), "rp_integrity"),
        ("Namespace", metric_value(state, [("rp_integrity", "namespace_checks")]), "rp_integrity"),
        ("Status Semantics", metric_value(state, [("rp_integrity", "status_checks")]), "rp_integrity"),
        ("Review Alignment", metric_value(state, [("rp_integrity", "review_alignment_checks")]), "rp_integrity"),
        ("Errors", metric_value(state, [("rp_integrity", "errors")]), "rp_integrity"),
        ("Decision", metric_value(state, [("rp_integrity", "decision")]), "rp_integrity"),
    ]
    coherence_items = [
        ("Checks", metric_value(state, [("rp_coherence", "coherence_checks")]), "rp_coherence"),
        ("Delivery", metric_value(state, [("rp_coherence", "delivery_checks")]), "rp_coherence"),
        ("Run State", metric_value(state, [("rp_coherence", "run_state_checks")]), "rp_coherence"),
        ("Lifecycle", metric_value(state, [("rp_coherence", "lifecycle_checks")]), "rp_coherence"),
        ("Workflow Lint", metric_value(state, [("rp_coherence", "workflow_lint_checks")]), "rp_coherence"),
        ("Tool Protocol", metric_value(state, [("rp_coherence", "tool_protocol_checks")]), "rp_coherence"),
        ("Errors", metric_value(state, [("rp_coherence", "errors")]), "rp_coherence"),
        ("Decision", metric_value(state, [("rp_coherence", "decision")]), "rp_coherence"),
    ]
    publication_items = [
        ("Checks", metric_value(state, [("rp_publication", "publication_checks")]), "rp_publication"),
        ("Targets", metric_value(state, [("rp_publication", "targets")]), "rp_publication"),
        ("Submissions", metric_value(state, [("rp_publication", "submissions")]), "rp_publication"),
        ("Reviews", metric_value(state, [("rp_publication", "review_rounds")]), "rp_publication"),
        ("Responses", metric_value(state, [("rp_publication", "response_packages")]), "rp_publication"),
        ("Response Items", metric_value(state, [("rp_publication", "response_items"), ("rp_peerresp", "items")]), "rp_peerresp"),
        ("Decisions", metric_value(state, [("rp_publication", "decisions")]), "rp_publication"),
        ("Status", metric_value(state, [("rp_publication", "status")]), "rp_publication"),
    ]
    systematic_review_items = [
        ("Checks", metric_value(state, [("rp_sysreview", "systematic_review_checks"), ("rp_agentcmp", "systematic_review_checks")]), "rp_sysreview"),
        ("Protocol", metric_value(state, [("rp_sysreview", "protocol")]), "rp_sysreview"),
        ("Search Results", metric_value(state, [("rp_syssearch", "results")]), "rp_syssearch"),
        ("Screened", metric_value(state, [("rp_sysscreen", "screening_decisions")]), "rp_sysscreen"),
        ("Included", metric_value(state, [("rp_sysprisma", "included"), ("rp_sysscreen", "full_text_included")]), "rp_sysprisma"),
        ("Extractions", metric_value(state, [("rp_sysextract", "extractions")]), "rp_sysextract"),
        ("Risk of Bias", metric_value(state, [("rp_sysextract", "risk_of_bias")]), "rp_sysextract"),
        ("PRISMA", metric_value(state, [("rp_sysprisma", "flow")]), "rp_sysprisma"),
    ]
    experiment_schedule_items = [
        ("Checks", metric_value(state, [("rp_expsched", "experiment_scheduling_checks"), ("rp_agentcmp", "experiment_scheduling_checks")]), "rp_expsched"),
        ("Schedule", metric_value(state, [("rp_expsched", "schedule")]), "rp_expsched"),
        ("Tasks", metric_value(state, [("rp_schedtask", "tasks"), ("rp_agentcmp", "tasks")]), "rp_schedtask"),
        ("Bookings", metric_value(state, [("rp_schedbook", "bookings"), ("rp_agentcmp", "bookings")]), "rp_schedbook"),
        ("Conflicts", metric_value(state, [("rp_schedconf", "conflicts"), ("rp_agentcmp", "conflicts")]), "rp_schedconf"),
        ("Executions", metric_value(state, [("rp_schedexec", "execution_records"), ("rp_agentcmp", "executions")]), "rp_schedexec"),
        ("Owner", metric_value(state, [("rp_expsched", "owner")]), "rp_expsched"),
        ("Status", metric_value(state, [("rp_expsched", "status")]), "rp_expsched"),
    ]
    training_compliance_items = [
        ("Checks", metric_value(state, [("rp_traincomp", "training_compliance_checks"), ("rp_agentcmp", "training_compliance_checks")]), "rp_traincomp"),
        ("Requirements", metric_value(state, [("rp_traincomp", "requirements"), ("rp_trainreq", "requirements")]), "rp_trainreq"),
        ("Training Records", metric_value(state, [("rp_traincomp", "training_records"), ("rp_trainrec", "records")]), "rp_trainrec"),
        ("Competency", metric_value(state, [("rp_traincomp", "competency_assessments"), ("rp_trainassess", "assessments")]), "rp_trainassess"),
        ("Authorizations", metric_value(state, [("rp_traincomp", "active_authorizations"), ("rp_trainauth", "active_authorizations")]), "rp_trainauth"),
        ("Open Gaps", metric_value(state, [("rp_traincomp", "open_gaps"), ("rp_traingap", "open")]), "rp_traingap"),
        ("Resolved Gaps", metric_value(state, [("rp_traincomp", "resolved_gaps"), ("rp_traingap", "resolved")]), "rp_traingap"),
        ("Status", metric_value(state, [("rp_traincomp", "status")]), "rp_traincomp"),
    ]
    analysis_results_items = [
        ("Checks", metric_value(state, [("rp_analysisres", "analysis_results_checks"), ("rp_agentcmp", "analysis_results_checks")]), "rp_analysisres"),
        ("Plans", metric_value(state, [("rp_analysisres", "analysis_plans"), ("rp_anplan", "plans")]), "rp_anplan"),
        ("Runs", metric_value(state, [("rp_analysisres", "analysis_runs"), ("rp_anrun", "runs")]), "rp_anrun"),
        ("Tables", metric_value(state, [("rp_analysisres", "result_tables"), ("rp_resulttbl", "tables")]), "rp_resulttbl"),
        ("Statistics", metric_value(state, [("rp_analysisres", "statistical_results"), ("rp_statres", "statistics")]), "rp_statres"),
        ("Figures", metric_value(state, [("rp_analysisres", "figures"), ("rp_anfig", "figures")]), "rp_anfig"),
        ("Interpretations", metric_value(state, [("rp_analysisres", "interpretations"), ("rp_interp", "interpretations")]), "rp_interp"),
        ("Status", metric_value(state, [("rp_analysisres", "status")]), "rp_analysisres"),
    ]
    decision_support_items = [
        ("Checks", metric_value(state, [("rp_decsupport", "decision_support_checks"), ("rp_agentcmp", "decision_support_checks")]), "rp_decsupport"),
        ("Options", metric_value(state, [("rp_decsupport", "options"), ("rp_decopt", "options")]), "rp_decopt"),
        ("Criteria", metric_value(state, [("rp_decsupport", "criteria"), ("rp_deccrit", "criteria")]), "rp_deccrit"),
        ("Scores", metric_value(state, [("rp_decsupport", "scores"), ("rp_decscore", "scores")]), "rp_decscore"),
        ("Packets", metric_value(state, [("rp_decsupport", "review_packets")]), "rp_decpacket"),
        ("Selected", metric_value(state, [("rp_decsupport", "recommended_option"), ("rp_decpacket", "recommended_option")]), "rp_decsupport"),
        ("Hybrid Score", metric_value(state, [("rp_decsupport", "weighted_score_agentos_ucore_hybrid")]), "rp_decsupport"),
        ("Status", metric_value(state, [("rp_decsupport", "status")]), "rp_decsupport"),
    ]
    usable_research_items = [
        ("Checks", metric_value(state, [("rp_usable", "usable_research_checks"), ("rp_agentcmp", "usable_research_checks")]), "rp_usable"),
        ("Templates", metric_value(state, [("rp_usable", "templates"), ("rp_usabletpl", "templates")]), "rp_usabletpl"),
        ("Datasets", metric_value(state, [("rp_usable", "datasets"), ("rp_usableds", "datasets")]), "rp_usableds"),
        ("Library Sources", metric_value(state, [("rp_usable", "library_sources"), ("rp_usablelib", "library_sources")]), "rp_usablelib"),
        ("DAG Stages", metric_value(state, [("rp_usable", "dag_stages")]), "rp_usabledag"),
        ("Plan Queue", metric_value(state, [("rp_usable", "plan_queue_rows"), ("rp_agentcmp", "plan_queue")]), "rp_usableops"),
        ("Action Queue", metric_value(state, [("rp_usable", "action_queue_rows"), ("rp_agentcmp", "action_queue")]), "rp_usableops"),
        ("Handoffs", metric_value(state, [("rp_usable", "handoff_packages"), ("rp_agentcmp", "handoffs")]), "rp_usableops"),
        ("Status", metric_value(state, [("rp_usable", "status")]), "rp_usable"),
    ]
    usable_project_items = [
        ("Checks", metric_value(state, [("rp_usableproj", "usable_project_checks"), ("rp_agentcmp", "usable_project_checks")]), "rp_usableproj"),
        ("Scaffold Templates", metric_value(state, [("rp_usableproj", "scaffold_templates"), ("rp_agentcmp", "scaffold_templates")]), "rp_usablescaf"),
        ("Scaffold Files", metric_value(state, [("rp_usableproj", "scaffold_files")]), "rp_usablescaf"),
        ("Project Launches", metric_value(state, [("rp_usableproj", "project_launches"), ("rp_agentcmp", "project_launches")]), "rp_usablelaunch"),
        ("Project Bundles", metric_value(state, [("rp_usableproj", "project_bundles"), ("rp_agentcmp", "project_bundles")]), "rp_usablepack"),
        ("Doctor Checks", metric_value(state, [("rp_usableproj", "platform_doctor_checks"), ("rp_agentcmp", "doctor_checks")]), "rp_usableboot"),
        ("Operations Sections", metric_value(state, [("rp_usableproj", "operations_digest_sections")]), "rp_usablelaunch"),
        ("Status", metric_value(state, [("rp_usableproj", "status")]), "rp_usableproj"),
    ]
    mature_items = [
        ("Profiles", metric_value(state, [("rp_mature", "reference_platforms"), ("rp_mature_refs", "profiles")]), "rp_mature"),
        ("Mappings", metric_value(state, [("rp_mature", "capability_mappings"), ("rp_mature_map", "mappings")]), "rp_mature"),
        ("Checks", metric_value(state, [("rp_mature", "capability_checks"), ("rp_mature_checks", "checks")]), "rp_mature_checks"),
        ("Errors", metric_value(state, [("rp_mature", "errors"), ("rp_mature_checks", "errors")]), "rp_mature_checks"),
        ("Warnings", metric_value(state, [("rp_mature", "warnings"), ("rp_mature_checks", "warnings")]), "rp_mature_checks"),
        ("Decision", metric_value(state, [("rp_mature", "decision")]), "rp_mature"),
        ("AgentOS Targets", metric_value(state, [("rp_mature_map", "agentos_targets")]), "rp_mature_map"),
        ("Status", metric_value(state, [("rp_mature", "status")]), "rp_mature"),
    ]
    provenance_items = [
        ("Checks", metric_value(state, [("rp_prov_view", "provenance_view_checks"), ("rp_agentcmp", "provenance_view_checks")]), "rp_prov_view"),
        ("Timeline Views", metric_value(state, [("rp_prov_view", "timeline_views"), ("rp_timeline_view", "views")]), "rp_timeline_view"),
        ("Subgraphs", metric_value(state, [("rp_prov_view", "subgraphs")]), "rp_prov_view"),
        ("Edges", metric_value(state, [("rp_prov_view", "subgraph_edges"), ("rp_prov_edges", "edges")]), "rp_prov_edges"),
        ("Evidence Packets", metric_value(state, [("rp_prov_view", "evidence_packets"), ("rp_evidence_packet", "packets")]), "rp_evidence_packet"),
        ("Decision Packets", metric_value(state, [("rp_prov_view", "decision_packets")]), "rp_prov_view"),
        ("AgentOS Mapping", metric_value(state, [("rp_prov_view", "agentos_mapping")]), "rp_prov_view"),
        ("Status", metric_value(state, [("rp_prov_view", "status")]), "rp_prov_view"),
    ]
    if file_name == "index.html":
        return render_summary_panel("Dual Target Overview", home_items)
    if file_name == "run.html":
        return render_summary_panel("Research Output", report_items)
    if file_name in ("evidence.html", "artifacts.html"):
        return render_summary_panel("Evidence Package", evidence_items)
    if file_name == "delivery.html":
        return render_summary_panel("Delivery Package", delivery_items)
    if file_name == "workflow.html":
        return render_summary_panel("Workflow Runner", workflow_items)
    if file_name == "workbench.html":
        return render_summary_panel("Research Workbench", workbench_items)
    if file_name == "studio.html":
        return render_summary_panel("Research Studio", studio_items)
    if file_name == "data.html":
        return render_summary_panel("Data Pipeline", data_items)
    if file_name == "project.html":
        return render_summary_panel("Project Space", project_items)
    if file_name == "project-review.html":
        return render_summary_panel("Project Delivery Review", project_review_items)
    if file_name == "review.html":
        return render_summary_panel("Review Dashboard", review_items)
    if file_name == "compare.html":
        return render_summary_panel("Compare Summary", compare_items)
    if file_name == "llm.html":
        return render_summary_panel("Relay Quality", llm_items)
    if file_name == "services.html":
        return render_summary_panel("Service Execution", service_items)
    if file_name == "api-catalog.html":
        return render_summary_panel("API Catalog", api_catalog_items)
    if file_name == "operations.html":
        return render_summary_panel("Research Operations", operations_items)
    if file_name == "review-board.html":
        return render_summary_panel("Formal Review Board", review_board_items)
    if file_name == "control-plane.html":
        return render_summary_panel("Platform Control Plane", control_plane_items)
    if file_name == "integrity.html":
        return render_summary_panel("Integrity Plane", integrity_items)
    if file_name == "coherence.html":
        return render_summary_panel("Coherence Plane", coherence_items)
    if file_name == "publication.html":
        return render_summary_panel("Publication Workflow", publication_items)
    if file_name == "systematic-review.html":
        return render_summary_panel("Systematic Review", systematic_review_items)
    if file_name == "experiment-schedule.html":
        return render_summary_panel("Experiment Schedule", experiment_schedule_items)
    if file_name == "training-compliance.html":
        return render_summary_panel("Training Compliance", training_compliance_items)
    if file_name == "analysis-results.html":
        return render_summary_panel("Analysis Results", analysis_results_items)
    if file_name == "decision-support.html":
        return render_summary_panel("Decision Support", decision_support_items)
    if file_name == "usable-research.html":
        return render_summary_panel("Usable Research", usable_research_items)
    if file_name == "usable-project.html":
        return render_summary_panel("Usable Project Lifecycle", usable_project_items)
    if file_name == "mature.html":
        return render_summary_panel("Mature Platform Mapping", mature_items)
    if file_name == "provenance.html":
        return render_summary_panel("Provenance Timeline", provenance_items)
    return ""


def render_detail_panel(file_name: str, state: dict[str, dict[str, object]]) -> str:
    agent_items = [
        ("Agents", metric_value(state, [("rp_agents", "agents"), ("rp_api_agents", "agents")]), "rp_agents"),
        ("Messages", metric_value(state, [("rp_agents", "messages"), ("rp_agent_run", "agent_messages")]), "rp_agents"),
        ("Decisions", metric_value(state, [("rp_decisions", "decisions"), ("rp_agent_run", "agent_decisions")]), "rp_decisions"),
        ("Handoffs", metric_value(state, [("rp_handoff", "handoffs"), ("rp_agentcmp", "handoffs")]), "rp_handoff"),
        ("Deliberation Items", metric_value(state, [("rp_deliberation", "items"), ("rp_metrics", "deliberation_items")]), "rp_deliberation"),
        ("Decision Records", metric_value(state, [("rp_ui_agent", "decision_records"), ("rp_api_agents", "records")]), "rp_ui_agent"),
    ]
    evidence_items = [
        ("Claims", metric_value(state, [("rp_claimrec", "claim"), ("rp_evidence", "claims"), ("rp_api_evidence", "claims")]), "rp_evidence"),
        ("Evidence Links", metric_value(state, [("rp_lit", "evidence_links"), ("rp_evidence", "evidence_links")]), "rp_lit"),
        ("Critical Paths", metric_value(state, [("rp_provpath", "critical_paths"), ("rp_api_evidence", "provenance_paths")]), "rp_provpath"),
        ("Screening Decisions", metric_value(state, [("rp_lit", "screening_decisions"), ("rp_ui_evidence", "screening_decisions")]), "rp_lit"),
        ("Evidence Protocol", metric_value(state, [("rp_knowledge", "evidence_protocol"), ("rp_ui_evidence", "evidence_protocol")]), "rp_knowledge"),
        ("Bundle Entries", metric_value(state, [("rp_package", "evidence_bundle_entries")]), "rp_package"),
    ]
    compare_items = [
        ("File Scans", metric_value(state, [("rp_api_compare", "file_scans"), ("rp_ui_compare", "pain_file_scans")]), "rp_api_compare"),
        ("State Convention", metric_value(state, [("rp_api_compare", "state_convention"), ("rp_ui_compare", "pain_state_convention")]), "rp_api_compare"),
        ("User Permission", metric_value(state, [("rp_api_compare", "user_permission_only"), ("rp_ui_compare", "pain_user_permissions")]), "rp_api_compare"),
        ("Context Trusted", metric_value(state, [("rp_api_compare", "context_trusted")]), "rp_api_compare"),
        ("Rebuild Steps", metric_value(state, [("rp_api_compare", "rebuild_steps"), ("rp_ui_compare", "pain_rebuild_steps")]), "rp_api_compare"),
        ("Test Cases", metric_value(state, [("rp_agentcmp", "test_cases"), ("rp_tests", "tests")]), "rp_agentcmp"),
        ("Portability Checks", metric_value(state, [("rp_agentcmp", "workflow_portability_checks")]), "rp_agentcmp"),
        ("Backend Checks", metric_value(state, [("rp_agentcmp", "portability_backend_checks")]), "rp_agentcmp"),
        ("Backend Runner", metric_value(state, [("rp_agentcmp", "backend_runner_checks")]), "rp_agentcmp"),
        ("Integrity Checks", metric_value(state, [("rp_agentcmp", "integrity_plane_checks"), ("rp_integrity", "integrity_checks")]), "rp_integrity"),
        ("Coherence Checks", metric_value(state, [("rp_agentcmp", "coherence_plane_checks"), ("rp_coherence", "coherence_checks")]), "rp_coherence"),
        ("Publication Checks", metric_value(state, [("rp_agentcmp", "publication_checks"), ("rp_publication", "publication_checks")]), "rp_publication"),
        ("Calculation Checks", metric_value(state, [("rp_agentcmp", "calculation_checks"), ("rp_calculation", "calculation_checks")]), "rp_calculation"),
        ("Real Task Checks", metric_value(state, [("rp_agentcmp", "real_task_checks"), ("rp_realtask", "real_task_checks")]), "rp_realtask"),
        ("Analysis Results Checks", metric_value(state, [("rp_agentcmp", "analysis_results_checks"), ("rp_analysisres", "analysis_results_checks")]), "rp_analysisres"),
        ("Decision Support Checks", metric_value(state, [("rp_agentcmp", "decision_support_checks"), ("rp_decsupport", "decision_support_checks")]), "rp_decsupport"),
        ("Usable Research Checks", metric_value(state, [("rp_agentcmp", "usable_research_checks"), ("rp_usable", "usable_research_checks")]), "rp_usable"),
        ("Experiment Campaign Checks", metric_value(state, [("rp_agentcmp", "experiment_campaign_checks"), ("rp_campaign", "campaign_checks")]), "rp_campaign"),
        ("Statistical Design Checks", metric_value(state, [("rp_agentcmp", "statistical_design_checks"), ("rp_stdesign", "statistical_design_checks")]), "rp_stdesign"),
        ("Model Registry Checks", metric_value(state, [("rp_agentcmp", "model_registry_service_checks"), ("rp_modelreg", "model_registry_service_checks")]), "rp_modelreg"),
        ("Systematic Review Checks", metric_value(state, [("rp_agentcmp", "systematic_review_checks"), ("rp_sysreview", "systematic_review_checks")]), "rp_sysreview"),
        ("Experiment Scheduling Checks", metric_value(state, [("rp_agentcmp", "experiment_scheduling_checks"), ("rp_expsched", "experiment_scheduling_checks")]), "rp_expsched"),
        ("Training Compliance Checks", metric_value(state, [("rp_agentcmp", "training_compliance_checks"), ("rp_traincomp", "training_compliance_checks")]), "rp_traincomp"),
        ("Release Dossier Checks", metric_value(state, [("rp_agentcmp", "release_dossier_checks"), ("rp_reldossier", "release_dossier_checks")]), "rp_reldossier"),
        ("Mature Capability", metric_value(state, [("rp_agentcmp", "mature_capability_checks"), ("rp_mature", "capability_checks")]), "rp_mature"),
        ("Provenance View", metric_value(state, [("rp_agentcmp", "provenance_view_checks"), ("rp_prov_view", "provenance_view_checks")]), "rp_prov_view"),
        ("Provenance Queries", metric_value(state, [("rp_agentcmp", "provenance_query_checks"), ("rp_prov_query", "provenance_query_checks")]), "rp_prov_query"),
    ]
    if "host_platform_alignment" in state:
        compare_items.extend(
            [
                ("Host Modules", metric_value(state, [("host_platform_alignment", "host_modules")]), "host_platform_alignment"),
                ("Capability Groups", "{}/{}".format(metric_value(state, [("host_platform_alignment", "groups_ok")]), metric_value(state, [("host_platform_alignment", "groups_total")])), "host_platform_alignment"),
            ]
        )
    if "host_test_alignment" in state:
        compare_items.extend(
            [
                ("Host Test Methods", metric_value(state, [("host_test_alignment", "host_tests")]), "host_test_alignment"),
                ("Test Themes", "{}/{}".format(metric_value(state, [("host_test_alignment", "themes_ok")]), metric_value(state, [("host_test_alignment", "themes_total")])), "host_test_alignment"),
            ]
        )
    if "host_surface_alignment" in state:
        compare_items.extend(
            [
                ("宿主机 API 路由数", metric_value(state, [("host_surface_alignment", "host_api_routes")]), "host_surface_alignment"),
                ("宿主机 action 路由数", metric_value(state, [("host_surface_alignment", "host_action_routes")]), "host_surface_alignment"),
            ]
        )
    if "host_seeded_action" in state:
        compare_items.extend(
            [
                ("宿主机 action 实测", metric_value(state, [("host_seeded_action", "action")]), "host_seeded_action"),
                ("预置 action 数量", metric_value(state, [("host_seeded_action", "action_count")]), "host_seeded_action"),
                ("预置 action 状态", metric_value(state, [("host_seeded_action", "status")]), "host_seeded_action"),
            ]
        )
    integrity_detail_items = [
        ("Evidence Contracts", metric_value(state, [("rp_integrity", "evidence_contracts")]), "rp_integrity"),
        ("Reference Contracts", metric_value(state, [("rp_integrity", "reference_contracts")]), "rp_integrity"),
        ("Report Source Checks", metric_value(state, [("rp_integrity", "report_source_checks")]), "rp_integrity"),
        ("Package Trace Checks", metric_value(state, [("rp_integrity", "package_trace_checks")]), "rp_integrity"),
        ("Warnings", metric_value(state, [("rp_integrity", "warnings")]), "rp_integrity"),
        ("Report", metric_value(state, [("rp_integrity", "integrity_report")]), "rp_integrity"),
    ]
    coherence_detail_items = [
        ("Delivery Contracts", metric_value(state, [("rp_coherence", "delivery_contracts")]), "rp_coherence"),
        ("Run State Contracts", metric_value(state, [("rp_coherence", "run_state_contracts")]), "rp_coherence"),
        ("Lifecycle Contracts", metric_value(state, [("rp_coherence", "lifecycle_contracts")]), "rp_coherence"),
        ("Report Validation", metric_value(state, [("rp_coherence", "report_validation_checks")]), "rp_coherence"),
        ("Agent Coordination", metric_value(state, [("rp_coherence", "agent_coordination_checks")]), "rp_coherence"),
        ("Report", metric_value(state, [("rp_coherence", "coherence_report")]), "rp_coherence"),
    ]
    publication_detail_items = [
        ("Checklist Items", metric_value(state, [("rp_pubplan", "checklist_items")]), "rp_pubplan"),
        ("Addressed Items", metric_value(state, [("rp_peerresp", "addressed")]), "rp_peerresp"),
        ("Open Items", metric_value(state, [("rp_peerresp", "needs_revision")]), "rp_peerresp"),
        ("Response Letter", metric_value(state, [("rp_peerresp", "response_letter")]), "rp_peerresp"),
        ("API", metric_value(state, [("rp_api_pub", "publication_workflow")]), "rp_api_pub"),
        ("Operation", metric_value(state, [("rp_pubop", "op")]), "rp_pubop"),
    ]
    analysis_results_detail_items = [
        ("Plan", metric_value(state, [("rp_anplan", "plan")]), "rp_anplan"),
        ("Latest Run", metric_value(state, [("rp_anrun", "run"), ("rp_analysisres", "manual_run")]), "rp_anrun"),
        ("Result Table", metric_value(state, [("rp_resulttbl", "table")]), "rp_resulttbl"),
        ("Statistical Result", metric_value(state, [("rp_statres", "stat")]), "rp_statres"),
        ("Figure", metric_value(state, [("rp_anfig", "figure")]), "rp_anfig"),
        ("Interpretation", metric_value(state, [("rp_interp", "interpretation")]), "rp_interp"),
        ("Review Dashboard", metric_value(state, [("rp_review_dashboard", "subsection")]), "rp_review_dashboard"),
        ("Package", metric_value(state, [("rp_package", "analysis_results")]), "rp_package"),
    ]
    decision_support_detail_items = [
        ("Decision", metric_value(state, [("rp_decsupport", "decision"), ("rp_decpacket", "decision")]), "rp_decsupport"),
        ("Target", metric_value(state, [("rp_decsupport", "target")]), "rp_decsupport"),
        ("Recommended", metric_value(state, [("rp_decsupport", "recommended_option"), ("rp_decpacket", "recommended_option")]), "rp_decsupport"),
        ("Option Scores", metric_value(state, [("rp_decpacket", "option_scores")]), "rp_decpacket"),
        ("Evidence", metric_value(state, [("rp_decpacket", "evidence"), ("rp_decsupport", "evidence_sources")]), "rp_decpacket"),
        ("Review Dashboard", metric_value(state, [("rp_review_dashboard", "subsection")]), "rp_review_dashboard"),
        ("Package", metric_value(state, [("rp_package", "decision_support")]), "rp_package"),
    ]
    usable_research_detail_items = [
        ("Entry", metric_value(state, [("rp_usable", "entry")]), "rp_usable"),
        ("Project", metric_value(state, [("rp_usable", "project")]), "rp_usable"),
        ("Run", metric_value(state, [("rp_usable", "run_id")]), "rp_usable"),
        ("Selected Template", metric_value(state, [("rp_usabletpl", "selected_template")]), "rp_usabletpl"),
        ("Next Action", metric_value(state, [("rp_usable", "next_action")]), "rp_usable"),
        ("Package", metric_value(state, [("rp_package", "usable_research")]), "rp_package"),
        ("Reader Page", metric_value(state, [("rp_web_bundle", "usable_research_page")]), "rp_web_bundle"),
        ("Review Dashboard", metric_value(state, [("rp_review_dashboard", "subsection")]), "rp_review_dashboard"),
    ]
    usable_project_detail_items = [
        ("Configuration", metric_value(state, [("rp_usableproj", "configuration")]), "rp_usableproj"),
        ("Next User Path", metric_value(state, [("rp_usableproj", "next_user_path")]), "rp_usableproj"),
        ("Startup Guide", metric_value(state, [("rp_usableboot", "startup_guide")]), "rp_usableboot"),
        ("Doctor", metric_value(state, [("rp_usableboot", "doctor")]), "rp_usableboot"),
        ("Scaffold", metric_value(state, [("rp_usablescaf", "scaffold")]), "rp_usablescaf"),
        ("Launch", metric_value(state, [("rp_usablelaunch", "launch")]), "rp_usablelaunch"),
        ("Project Bundle", metric_value(state, [("rp_usablepack", "bundle")]), "rp_usablepack"),
        ("Package Intake", metric_value(state, [("rp_usablepack", "intake")]), "rp_usablepack"),
        ("Reader Page", metric_value(state, [("rp_web_bundle", "usable_project_page")]), "rp_web_bundle"),
    ]
    mature_detail_items = [
        ("Profile Checks", metric_value(state, [("rp_mature", "profile_checks")]), "rp_mature"),
        ("Store Checks", metric_value(state, [("rp_mature", "store_checks")]), "rp_mature"),
        ("Surface Checks", metric_value(state, [("rp_mature", "surface_checks")]), "rp_mature"),
        ("Ratio Checks", metric_value(state, [("rp_mature", "ratio_checks")]), "rp_mature"),
        ("Coverage", metric_value(state, [("rp_mature", "coverage")]), "rp_mature"),
        ("AgentOS Adaptation", metric_value(state, [("rp_mature", "agentos_adaptation")]), "rp_mature"),
    ]
    provenance_detail_items = [
        ("Timeline Events", metric_value(state, [("rp_prov_view", "timeline_events"), ("rp_timeline", "events")]), "rp_timeline"),
        ("Subgraph Edges", metric_value(state, [("rp_prov_view", "subgraph_edges"), ("rp_prov_edges", "edges")]), "rp_prov_edges"),
        ("Evidence Packets", metric_value(state, [("rp_evidence_packet", "packets")]), "rp_evidence_packet"),
        ("Reader Page", metric_value(state, [("rp_prov_view", "reader_page")]), "rp_prov_view"),
        ("Kernel Timeline", metric_value(state, [("rp_prov_view", "agentos_kernel_timeline")]), "rp_prov_view"),
        ("Kernel Provenance", metric_value(state, [("rp_prov_view", "agentos_kernel_provenance")]), "rp_prov_view"),
    ]
    llm_items = [
        ("Requests", metric_value(state, [("rp_llm_resp", "requests"), ("rp_llmq", "queued")]), "rp_llm_resp"),
        ("Responses", metric_value(state, [("rp_llm_resp", "responses")]), "rp_llm_resp"),
        ("Mode", metric_value(state, [("rp_llm_resp", "mode")]), "rp_llm_resp"),
        ("Secret Material", metric_value(state, [("rp_llm_hostreq", "secret_material")]), "rp_llm_hostreq"),
        ("Packet Secret", metric_value(state, [("rp_llm_packets", "secret_in_packet")]), "rp_llm_packets"),
        ("Native Checks", metric_value(state, [("rp_agentcmp", "llm_delivery_checks")]), "rp_agentcmp"),
        ("Agent Decision", metric_value(state, [("rp_agent_run", "host_relay_agent_decision")]), "rp_agent_run"),
    ]
    review_items = [
        ("Workflow", metric_value(state, [("rp_review_dashboard", "section")]), "rp_review_dashboard"),
        ("Required Files", metric_value(state, [("rp_review_dashboard", "gate")]), "rp_review_dashboard"),
        ("Pack Action", metric_value(state, [("rp_review_pack", "action")]), "rp_review_pack"),
        ("Pack Bridge", metric_value(state, [("rp_review_pack", "bridge"), ("rp_package", "review_pack_bridge")]), "rp_review_pack"),
        ("Native Checks", metric_value(state, [("rp_agentcmp", "review_handoff_checks")]), "rp_agentcmp"),
        ("Revision", metric_value(state, [("rp_revision", "final_status")]), "rp_revision"),
        ("Review Threads", metric_value(state, [("rp_review2", "review_threads")]), "rp_review2"),
        ("Action Items", metric_value(state, [("rp_review2", "action_items")]), "rp_review2"),
        ("Report", metric_value(state, [("rp_report_text", "status")]), "rp_report_text"),
    ]
    if file_name == "agents.html":
        return render_summary_panel("Agent Detail", agent_items)
    if file_name == "evidence.html":
        return render_summary_panel("Evidence Detail", evidence_items)
    if file_name == "review.html":
        return render_summary_panel("Review State", review_items)
    if file_name == "compare.html":
        return render_summary_panel("Compare Metrics", compare_items)
    if file_name == "integrity.html":
        return render_summary_panel("Integrity Detail", integrity_detail_items)
    if file_name == "coherence.html":
        return render_summary_panel("Coherence Detail", coherence_detail_items)
    if file_name == "publication.html":
        return render_summary_panel("Publication Detail", publication_detail_items)
    if file_name == "analysis-results.html":
        return render_summary_panel("Analysis Result Detail", analysis_results_detail_items)
    if file_name == "decision-support.html":
        return render_summary_panel("Decision Support Detail", decision_support_detail_items)
    if file_name == "usable-research.html":
        return render_summary_panel("Usable Research Detail", usable_research_detail_items)
    if file_name == "usable-project.html":
        return render_summary_panel("Usable Project Detail", usable_project_detail_items)
    if file_name == "mature.html":
        return render_summary_panel("Mature Capability Detail", mature_detail_items)
    if file_name == "provenance.html":
        return render_summary_panel("Provenance Detail", provenance_detail_items)
    if file_name == "llm.html":
        return render_summary_panel("Relay State", llm_items)
    return ""


def render_grouped_details(file_name: str, state: dict[str, dict[str, object]]) -> list[str]:
    if file_name == "run.html":
        return [
            render_record_panel(
                "Workflow Execution View",
                [
                    ("View", "execution_view"),
                    ("Host View", "host_execution_view"),
                    ("Workflow", "workflow"),
                    ("Run", "run_id"),
                    ("Engine", "engine"),
                    ("Stage", "stage"),
                    ("Order", "order"),
                    ("Attempts", "attempts"),
                    ("State", "state"),
                    ("Input", "input"),
                    ("Output", "output"),
                    ("Cache", "cache"),
                    ("Cache Hit", "cache_hit"),
                    ("Retry", "retry"),
                    ("Retry Stage", "retry_stage"),
                    ("Failure", "failure"),
                    ("Worker", "worker"),
                    ("Worker Slots", "worker_slots"),
                    ("Queue Depth", "queue_depth"),
                    ("Event", "event"),
                    ("Observer Events", "observer_events"),
                    ("Status", "status"),
                ],
                workflow_execution_view(state),
            ),
            render_record_panel(
                "Workflow Control View",
                [
                    ("View", "control_view"),
                    ("Source", "source"),
                    ("Workflow", "workflow"),
                    ("Run", "run_id"),
                    ("Engine", "engine"),
                    ("Stage", "stage"),
                    ("Attempts", "attempts"),
                    ("State", "state"),
                    ("Cache Key", "cache_key"),
                    ("Cache State", "cache_state"),
                    ("Cache Policy", "cache_policy"),
                    ("Input", "input"),
                    ("Output", "output"),
                    ("Dedupe Key", "dedupe_key"),
                    ("Skip", "skip"),
                    ("Worker Slots", "worker_slots"),
                    ("Ready Workers", "ready_workers"),
                    ("Busy Workers", "busy_workers"),
                    ("Stalled Workers", "stalled_workers"),
                    ("Queue Depth", "queue_depth"),
                    ("Heartbeats", "heartbeats"),
                    ("Event", "event"),
                    ("Observer Events", "observer_events"),
                    ("Action", "action"),
                    ("Status", "status"),
                ],
                workflow_control_view(state),
            ),
            render_record_panel(
                "Workflow Evidence Links",
                [
                    ("View", "evidence_view"),
                    ("Source", "source"),
                    ("Path", "path"),
                    ("Stage", "stage"),
                    ("Input", "input"),
                    ("Prepared", "prepared"),
                    ("Output", "output"),
                    ("Artifact", "artifact"),
                    ("Event", "event"),
                    ("Retry", "retry"),
                    ("Failure", "failure"),
                    ("Cache", "cache"),
                    ("Log", "log"),
                    ("Manifest", "manifest"),
                    ("Report", "report"),
                    ("Review", "review"),
                    ("LLM Quality", "llm_quality"),
                    ("Delivery", "delivery"),
                    ("Status", "status"),
                ],
                workflow_evidence_links(state),
            ),
            render_record_panel(
                "Report Source Map",
                [
                    ("Report Section", "report_section"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Linked Sources", "linked_sources"),
                    ("Review Page", "review_page"),
                    ("Status", "status"),
                ],
                report_source_map(state),
            ),
            render_record_panel(
                "Backend Evidence In Report",
                [
                    ("Report", "backend_evidence_report"),
                    ("Plain Costs", "plain_costs"),
                    ("AgentOS Replacements", "agentos_replacements"),
                    ("Status", "status"),
                ],
                state_records(state, "rp_report_text", "backend_evidence_report"),
            ),
            render_record_panel(
                "Backend Evidence In Runner",
                [
                    ("Runner", "backend_evidence_report"),
                    ("Plain Costs", "plain_costs"),
                    ("AgentOS Replacements", "agentos_replacements"),
                    ("Risks", "risks"),
                    ("Status", "status"),
                ],
                state_records(state, "rp_runner", "backend_evidence_report"),
            ),
            render_record_panel(
                "Backend Case Narratives",
                [
                    ("Case", "runner_narrative"),
                    ("Summary", "summary"),
                    ("Plain Cost", "plain_cost"),
                    ("AgentOS Replace", "agentos_replace"),
                    ("Next", "next"),
                ],
                backend_case_narratives(state),
            ),
            render_record_panel(
                "Operations Report Narrative",
                [("Section", "operation_section"), ("Source", "source"), ("Detail", "detail"), ("Status", "status")],
                operations_report_narrative(state),
            ),
            render_record_panel(
                "Operations Source Files",
                [("Section", "operation_section"), ("State File", "state_file"), ("Record", "record"), ("Rendered Page", "rendered_page"), ("Status", "status")],
                operations_source_files(state),
            ),
        ]
    if file_name == "workflow.html":
        return [
            render_record_panel(
                "Workflow Execution View",
                [
                    ("View", "execution_view"),
                    ("Host View", "host_execution_view"),
                    ("Workflow", "workflow"),
                    ("Run", "run_id"),
                    ("Engine", "engine"),
                    ("Stage", "stage"),
                    ("Order", "order"),
                    ("Attempts", "attempts"),
                    ("State", "state"),
                    ("Input", "input"),
                    ("Output", "output"),
                    ("Cache", "cache"),
                    ("Cache Hit", "cache_hit"),
                    ("Retry", "retry"),
                    ("Retry Stage", "retry_stage"),
                    ("Failure", "failure"),
                    ("Worker", "worker"),
                    ("Worker Slots", "worker_slots"),
                    ("Queue Depth", "queue_depth"),
                    ("Event", "event"),
                    ("Observer Events", "observer_events"),
                    ("Status", "status"),
                ],
                workflow_execution_view(state),
            ),
            render_record_panel(
                "Workflow Control View",
                [
                    ("View", "control_view"),
                    ("Source", "source"),
                    ("Workflow", "workflow"),
                    ("Run", "run_id"),
                    ("Engine", "engine"),
                    ("Stage", "stage"),
                    ("Attempts", "attempts"),
                    ("State", "state"),
                    ("Cache Key", "cache_key"),
                    ("Cache State", "cache_state"),
                    ("Cache Policy", "cache_policy"),
                    ("Input", "input"),
                    ("Output", "output"),
                    ("Dedupe Key", "dedupe_key"),
                    ("Skip", "skip"),
                    ("Worker Slots", "worker_slots"),
                    ("Ready Workers", "ready_workers"),
                    ("Busy Workers", "busy_workers"),
                    ("Stalled Workers", "stalled_workers"),
                    ("Queue Depth", "queue_depth"),
                    ("Heartbeats", "heartbeats"),
                    ("Event", "event"),
                    ("Observer Events", "observer_events"),
                    ("Action", "action"),
                    ("Status", "status"),
                ],
                workflow_control_view(state),
            ),
            render_record_panel(
                "Workflow Evidence Links",
                [
                    ("View", "evidence_view"),
                    ("Source", "source"),
                    ("Path", "path"),
                    ("Stage", "stage"),
                    ("Input", "input"),
                    ("Prepared", "prepared"),
                    ("Output", "output"),
                    ("Artifact", "artifact"),
                    ("Event", "event"),
                    ("Retry", "retry"),
                    ("Failure", "failure"),
                    ("Cache", "cache"),
                    ("Log", "log"),
                    ("Manifest", "manifest"),
                    ("Report", "report"),
                    ("Review", "review"),
                    ("LLM Quality", "llm_quality"),
                    ("Delivery", "delivery"),
                    ("Status", "status"),
                ],
                workflow_evidence_links(state),
            ),
        ]
    if file_name == "workbench.html":
        return [
            render_record_panel(
                "Workbench Task State",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_runner", "rp_report_text", "rp_api_compare"),
                    (
                        "workbench_tasks=",
                        "workbench_next_task=",
                        "host_action_workbench_id=",
                        "host_action_workbench_title=",
                        "host_action_workbench_literature_query=",
                        "host_action_workbench_question=",
                        "host_action_workbench_evidence_query=",
                        "host_action_workbench_answer=",
                        "host_action_workbench_answer_audit=",
                        "host_action_workbench_readiness=",
                        "host_action_workbench_task=",
                        "host_action_workbench_task_status=",
                        "host_action_workbench_step_limit=",
                        "host_report_workbench=",
                        "host_report_workbench_title=",
                        "host_report_workbench_question=",
                        "host_report_workbench_task=",
                    ),
                ),
            ),
            render_record_panel(
                "Workbench Writing Outputs",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_runner", "rp_revision", "rp_report_text", "rp_package", "rp_artifact_manifest"),
                    (
                        "host_action_workbench_note",
                        "host_action_workbench_brief",
                        "host_action_workbench_evidence_dossier",
                        "host_action_workbench_dossier_format=",
                        "host_action_workbench_evidence_graph",
                        "host_action_workbench_graph_format=",
                        "host_action_workbench_citations",
                        "host_action_workbench_citation_format=",
                        "host_action_workbench_manuscript",
                        "host_action_workbench_audit_scope=",
                        "host_action_workbench_revision",
                        "host_action_workbench_runbook",
                        "host_action_workbench_timeline",
                        "host_report_workbench_note_title=",
                    ),
                ),
            ),
            render_record_panel(
                "Workbench File Package",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_package", "rp_artifact_manifest", "rp_review_pack", "rp_nbexec", "rp_uresrun", "rp_web_bundle"),
                    (
                        "workbench_handoff=",
                        "host_action_workbench_outputs=",
                        "host_action_workbench_manifest",
                        "host_action_workbench_sha_records=",
                        "host_action_workbench_verified_files=",
                        "host_action_workbench_missing_files=",
                        "host_action_workbench_bundle=",
                        "host_action_workbench_package=",
                        "host_action_workbench_completion=",
                        "host_action_notebook",
                        "host_manifest_workbench",
                        "workbench=rp_runner",
                        "workbench_export=",
                    ),
                ),
            ),
            render_record_panel(
                "Workbench Review Board",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_runner", "rp_revision", "rp_package", "rp_review_pack"),
                    (
                        "host_action_workbench_task_board",
                        "host_action_workbench_board_filter=",
                        "host_action_workbench_task_board_row=",
                        "host_action_workbench_row_id=",
                        "host_action_workbench_row_status=",
                        "host_action_workbench_handoff=",
                        "host_action_workbench_handoff_scope=",
                        "host_action_workbench_completion=",
                        "workbench_handoff=",
                    ),
                ),
            ),
        ]
    if file_name == "agents.html":
        return [
            render_record_panel(
                "Agent Roster",
                [("Agent", "agent"), ("Role", "role"), ("State", "state"), ("Messages", "msg")],
                state_records(state, "rp_agents", "agent"),
            ),
            render_record_panel(
                "Decision Flow",
                [("Decision", "decision"), ("Actor", "actor"), ("Choice", "choice"), ("Basis", "basis")],
                state_records(state, "rp_decisions", "decision"),
            ),
            render_record_panel(
                "Handoff Flow",
                [("Handoff", "handoff"), ("Artifact", "artifact"), ("Status", "status")],
                state_records(state, "rp_handoff", "handoff"),
            ),
        ]
    if file_name == "evidence.html":
        paths = [(line.split("=", 1)[0], line.split("=", 1)[1]) for line in state_prefixed_lines(state, "rp_provpath", ("path",)) if "=" in line]
        protocol_rows = []
        for line in state_prefixed_lines(state, "rp_knowledge", ("literature_search_id=", "screening_decisions=", "evidence_extractions=", "evidence_protocol=", "prisma_flow=", "evidence_synthesis=")):
            key, value = line.split("=", 1)
            protocol_rows.append((key, value))
        return [
            render_record_panel(
                "Claim Records",
                [("Claim", "claim"), ("Kind", "kind"), ("Source", "source"), ("Evidence", "evidence"), ("Status", "status")],
                state_records(state, "rp_claimrec", "claim"),
            ),
            render_line_panel("Provenance Paths", paths),
            render_line_panel("Evidence Protocol Files", protocol_rows),
            render_record_panel(
                "Artifact Review Path",
                [
                    ("Path", "artifact_review_path"),
                    ("Input", "input"),
                    ("Prepared", "prepared"),
                    ("Artifact", "artifact"),
                    ("Metrics", "metrics"),
                    ("Chart", "chart"),
                    ("Failure", "failure"),
                    ("Retry", "retry"),
                    ("Event", "event"),
                    ("Report", "report"),
                    ("Review", "review"),
                    ("Review Pack", "review_pack"),
                    ("Delivery", "delivery"),
                    ("Status", "status"),
                ],
                state_records(state, "rp_artifact_manifest", "artifact_review_path"),
            ),
        ]
    if file_name == "delivery.html":
        return [
            render_record_panel(
                "Delivery Files",
                [("Delivery File", "delivery_file"), ("Path", "path"), ("Required", "required"), ("Exists", "exists"), ("Status", "status")],
                state_records(state, "rp_package", "delivery_file"),
            ),
            render_record_panel(
                "Delivery Package Records",
                [
                    ("Record", "record"),
                    ("Source File", "source_file"),
                    ("Delivery Files", "delivery_files"),
                    ("Evidence Entries", "evidence_bundle_entries"),
                    ("Bundle", "host_action_export_bundle_name"),
                    ("Notebook", "host_action_notebook_format"),
                    ("Manifest", "host_action_workbench_manifest"),
                    ("Verified", "host_action_workbench_verified_files"),
                    ("Missing", "host_action_workbench_missing_files"),
                    ("Status", "status"),
                ],
                delivery_action_records(state),
            ),
            render_record_panel(
                "Delivery Source Map",
                [
                    ("Delivery Record", "delivery_record"),
                    ("Field", "field"),
                    ("Reference", "reference"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Source File", "source_file"),
                    ("Status", "status"),
                ],
                delivery_source_map(state),
            ),
            render_record_panel(
                "Review Pack Delivery",
                [("Bridge", "bridge"), ("Delivery", "delivery"), ("Operations", "operations"), ("Project", "project"), ("Status", "status")],
                state_records(state, "rp_review_pack", "bridge"),
            ),
            render_record_panel(
                "Workbench Delivery",
                [("Source", "workbench_handoff"), ("Workbench", "workbench"), ("Task", "task"), ("Manifest", "manifest"), ("Verified", "verified"), ("Missing", "missing"), ("Bundle", "bundle"), ("Status", "status")],
                state_records(state, "rp_review_pack", "workbench_handoff"),
            ),
        ]
    if file_name == "services.html":
        return [
            render_record_panel(
                "Service Operation Records",
                [
                    ("Kind", "kind"),
                    ("Execution", "service_exec"),
                    ("Operation", "op"),
                    ("Request", "request"),
                    ("Route", "route"),
                    ("Result", "result"),
                    ("Service", "service"),
                    ("Source", "source"),
                    ("Input", "input"),
                    ("Output", "output"),
                    ("Worker", "worker"),
                    ("Capability", "capability"),
                    ("Records", "records"),
                    ("Source File", "source_file"),
                    ("Status", "status"),
                ],
                service_execution_records(state),
            ),
            render_record_panel(
                "Bio Service Files",
                [("Operation", "op"), ("Request", "request"), ("Result", "result"), ("Records", "records"), ("Status", "status")],
                state_records(state, "rp_bioop", "op"),
            ),
            render_record_panel(
                "Lab Service Files",
                [("Operation", "op"), ("Request", "request"), ("Result", "result"), ("Items", "items"), ("Status", "status")],
                state_records(state, "rp_labresop", "op"),
            ),
            render_record_panel(
                "Publication Service Files",
                [("Operation", "op"), ("Request", "request"), ("Result", "result"), ("Checks", "checks"), ("Status", "status")],
                state_records(state, "rp_pubop", "op"),
            ),
            render_record_panel(
                "Knowledge Service Files",
                [("Operation", "op"), ("Request", "request"), ("Result", "result"), ("Answers", "answers"), ("Status", "status")],
                state_records(state, "rp_knowop", "op"),
            ),
            render_record_panel(
                "Runtime Service Files",
                [("Operation", "op"), ("Request", "request"), ("Result", "result"), ("Workers", "workers"), ("Status", "status")],
                state_records(state, "rp_runop", "op"),
            ),
        ]
    if file_name == "operations.html":
        return [
            render_record_panel(
                "Operations Queue",
                [("Queue", "queue"), ("Items", "items"), ("Next", "next"), ("Status", "status")],
                state_records(state, "rp_opsboard", "queue"),
            ),
            render_record_panel(
                "Plan Queue",
                [("Plan", "plan_queue"), ("Items", "items"), ("Next", "next"), ("Status", "status")],
                state_records(state, "rp_opsboard", "plan_queue"),
            ),
            render_record_panel(
                "Action Items",
                [("Action", "action_item"), ("Owner", "owner"), ("Priority", "priority"), ("Status", "status")],
                state_records(state, "rp_opsboard", "action_item"),
            ),
            render_record_panel(
                "Operation Results",
                [("Advance", "advance_result"), ("Execute", "execute_result"), ("Selected", "selected"), ("Effect", "effect"), ("Status", "status")],
                state_records(state, "rp_opsboard", "advance_result") + state_records(state, "rp_opsboard", "execute_result"),
            ),
            render_record_panel(
                "Operations Handoff",
                [("Handoff", "handoff"), ("Artifact", "artifact"), ("Status", "status")],
                state_records(state, "rp_opsboard", "handoff"),
            ),
        ]
    if file_name == "review-board.html":
        return [
            render_record_panel(
                "Board Requests",
                [("Request", "request"), ("Target", "target"), ("Roles", "roles"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "request"),
            ),
            render_record_panel(
                "Votes",
                [("Vote", "vote"), ("Reviewer", "reviewer"), ("Role", "role"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "vote"),
            ),
            render_record_panel(
                "Signoffs",
                [("Signoff", "signoff"), ("Signer", "signer"), ("Role", "role"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "signoff"),
            ),
            render_record_panel(
                "Board Decision",
                [("Decision", "decision_record"), ("Approvals", "approvals"), ("Blockers", "blockers_open"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "decision_record"),
            ),
            render_record_panel(
                "Assignments",
                [("Assignment", "assignment"), ("Reviewer", "reviewer"), ("Role", "role"), ("Priority", "priority"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "assignment"),
            ),
            render_record_panel(
                "Filters And Workloads",
                [("Filter", "filter"), ("Workload", "workload"), ("Owner", "owner"), ("Open", "open"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "filter") + state_records(state, "rp_reviewboard", "workload"),
            ),
            render_record_panel(
                "Review Package",
                [("Package", "review_package"), ("Files", "files"), ("Status", "status")],
                state_records(state, "rp_reviewboard", "review_package"),
            ),
        ]
    if file_name == "control-plane.html":
        return [
            render_record_panel(
                "Approval Flow",
                [("Approval", "approval"), ("Target", "target"), ("State", "state"), ("Actor", "actor"), ("Status", "status")],
                state_records(state, "rp_control", "approval"),
            ),
            render_record_panel(
                "Notification Delivery",
                [("Subscription", "subscription"), ("Notification", "notification"), ("Target", "target"), ("Event", "event"), ("Delivered", "delivered"), ("Status", "status")],
                state_records(state, "rp_control", "subscription") + state_records(state, "rp_control", "notification"),
            ),
            render_record_panel(
                "Run Queue",
                [("Queue", "queue"), ("Run", "run"), ("Priority", "priority"), ("State", "state"), ("Worker", "worker"), ("Status", "status")],
                state_records(state, "rp_control", "queue"),
            ),
            render_record_panel(
                "Plugin Tools",
                [("Plugin", "plugin"), ("Plugin Run", "plugin_run"), ("Tool", "tool"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_control", "plugin") + state_records(state, "rp_control", "plugin_run"),
            ),
            render_record_panel(
                "Workspace Access",
                [("Workspace", "workspace"), ("User", "user"), ("Grant", "grant"), ("Role", "role"), ("Status", "status")],
                state_records(state, "rp_control", "workspace") + state_records(state, "rp_control", "user") + state_records(state, "rp_control", "grant"),
            ),
            render_record_panel(
                "Saved Views And API Token",
                [("Saved View", "saved_view"), ("Kind", "kind"), ("Query", "query"), ("API Token", "api_token"), ("Owner", "owner"), ("Status", "status")],
                state_records(state, "rp_control", "saved_view") + state_records(state, "rp_control", "api_token"),
            ),
            render_record_panel(
                "Permission Checks",
                [("Permission", "permission"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_control", "permission"),
            ),
            render_record_panel(
                "Control Report",
                [("Report", "control_report"), ("Approvals", "approvals"), ("Notifications", "notifications"), ("Queue Items", "queue_items"), ("Plugin Runs", "plugin_runs"), ("Status", "status")],
                state_records(state, "rp_control", "control_report"),
            ),
        ]
    if file_name == "integrity.html":
        return [
            render_record_panel(
                "Evidence Traceability",
                [("Check", "evidence_check"), ("Source", "source"), ("Target", "target"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_integrity", "evidence_check"),
            ),
            render_record_panel(
                "Evidence Contracts",
                [("Contract", "evidence_contract"), ("Required", "required"), ("Status", "status")],
                state_records(state, "rp_integrity", "evidence_contract"),
            ),
            render_record_panel(
                "Reference Integrity",
                [("Check", "reference_check"), ("Source", "source"), ("Target", "target"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_integrity", "reference_check"),
            ),
            render_record_panel(
                "Reference Contracts",
                [("Contract", "reference_contract"), ("Source", "source"), ("Target", "target"), ("Field", "field"), ("Status", "status")],
                state_records(state, "rp_integrity", "reference_contract"),
            ),
            render_record_panel(
                "Namespace Checks",
                [("Check", "namespace_check"), ("Value", "value"), ("Scope", "scope"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_integrity", "namespace_check"),
            ),
            render_record_panel(
                "Status Semantics",
                [("Check", "status_check"), ("Source", "source"), ("Allowed", "allowed"), ("Result", "result")],
                state_records(state, "rp_integrity", "status_check"),
            ),
            render_record_panel(
                "Review Alignment",
                [("Alignment", "review_alignment"), ("Source", "source"), ("Target", "target"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_integrity", "review_alignment"),
            ),
            render_record_panel(
                "Report Sources",
                [("Check", "report_source_check"), ("Source", "source"), ("Target", "target"), ("Source Key", "source_key"), ("Status", "status")],
                state_records(state, "rp_integrity", "report_source_check"),
            ),
            render_record_panel(
                "Package Trace",
                [("Trace", "package_trace"), ("Source", "source"), ("Target", "target"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_integrity", "package_trace"),
            ),
            render_record_panel(
                "Integrity Report",
                [("Report", "integrity_report"), ("Checks", "checks"), ("Errors", "errors"), ("Warnings", "warnings"), ("Status", "status")],
                state_records(state, "rp_integrity", "integrity_report"),
            ),
        ]
    if file_name == "coherence.html":
        return [
            render_record_panel(
                "Delivery Contracts",
                [("Contract", "delivery_contract"), ("Primary", "primary"), ("Related", "related"), ("Status", "status")],
                state_records(state, "rp_coherence", "delivery_contract"),
            ),
            render_record_panel(
                "Delivery Checks",
                [("Check", "delivery_check"), ("Source", "source"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "delivery_check"),
            ),
            render_record_panel(
                "Run State Contracts",
                [("Contract", "run_state_contract"), ("Source", "source"), ("Expected", "expected"), ("Status", "status")],
                state_records(state, "rp_coherence", "run_state_contract"),
            ),
            render_record_panel(
                "Run State Checks",
                [("Check", "run_state_check"), ("Source", "source"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "run_state_check"),
            ),
            render_record_panel(
                "Lifecycle Contracts",
                [("Contract", "lifecycle_contract"), ("Order", "order"), ("Status", "status")],
                state_records(state, "rp_coherence", "lifecycle_contract"),
            ),
            render_record_panel(
                "Lifecycle Checks",
                [("Check", "lifecycle_check"), ("Source", "source"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "lifecycle_check"),
            ),
            render_record_panel(
                "Workflow Lint",
                [("Check", "workflow_lint"), ("Source", "source"), ("Expected", "expected"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "workflow_lint"),
            ),
            render_record_panel(
                "Tool Protocol",
                [("Check", "tool_validation"), ("Tools", "tools"), ("Source", "source"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "tool_validation"),
            ),
            render_record_panel(
                "Report Validation",
                [("Check", "report_validation"), ("Source", "source"), ("Target", "target"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "report_validation"),
            ),
            render_record_panel(
                "Agent Coordination",
                [("Check", "agent_coordination"), ("Source", "source"), ("Target", "target"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_coherence", "agent_coordination"),
            ),
            render_record_panel(
                "Coherence Report",
                [("Report", "coherence_report"), ("Checks", "checks"), ("Errors", "errors"), ("Warnings", "warnings"), ("Status", "status")],
                state_records(state, "rp_coherence", "coherence_report"),
            ),
        ]
    if file_name == "publication.html":
        return [
            render_record_panel(
                "Journal Targets",
                [("Target", "journal_target"), ("Name", "name"), ("Article", "article"), ("Requirements", "requirements"), ("Status", "status")],
                state_records(state, "rp_publication", "journal_target"),
            ),
            render_record_panel(
                "Submission Packages",
                [("Submission", "submission"), ("Target", "target"), ("Package", "package"), ("Artifacts", "artifacts"), ("Status", "status")],
                state_records(state, "rp_publication", "submission"),
            ),
            render_record_panel(
                "Peer Review Rounds",
                [("Round", "review_round"), ("Submission", "submission"), ("Reviewer", "reviewer"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_publication", "review_round"),
            ),
            render_record_panel(
                "Revision Tasks",
                [("Task", "revision_task"), ("Review", "review"), ("Section", "section"), ("Assignee", "assignee"), ("Status", "status")],
                state_records(state, "rp_publication", "revision_task"),
            ),
            render_record_panel(
                "Peer Review Response Packages",
                [("Package", "response_package"), ("Review", "review"), ("Items", "items"), ("Addressed", "addressed"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_publication", "response_package"),
            ),
            render_record_panel(
                "Response Items",
                [("Item", "response_item"), ("Package", "package"), ("Point", "point"), ("Revision", "revision"), ("Status", "status")],
                state_records(state, "rp_publication", "response_item"),
            ),
            render_record_panel(
                "Publication Decisions",
                [("Decision", "publication_decision"), ("Submission", "submission"), ("Result", "decision"), ("Approved By", "approved_by"), ("Status", "status")],
                state_records(state, "rp_publication", "publication_decision"),
            ),
            render_record_panel(
                "Journal Requirements",
                [("Requirement", "journal_requirement"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_pubplan", "journal_requirement"),
            ),
            render_record_panel(
                "Response Letter Items",
                [("Item", "response_item"), ("Reply", "reply"), ("Status", "status")],
                state_records(state, "rp_peerresp", "response_item"),
            ),
        ]
    if file_name == "mature.html":
        return [
            render_record_panel(
                "Reference Platforms",
                [("Profile", "profile"), ("Name", "name"), ("Concepts", "concepts"), ("Status", "status")],
                state_records(state, "rp_mature_refs", "profile"),
            ),
            render_record_panel(
                "Capability Mappings",
                [("Mapping", "mapping"), ("Profile", "profile"), ("Concept", "concept"), ("Services", "services"), ("State", "state"), ("Status", "status")],
                state_records(state, "rp_mature_map", "mapping"),
            ),
            render_record_panel(
                "Mature Checks",
                [("Check", "check"), ("Target", "target"), ("Result", "result"), ("Status", "status")],
                state_records(state, "rp_mature_checks", "check"),
            ),
            render_record_panel(
                "AgentOS Adaptation",
                [("Adaptation", "agentos_adaptation"), ("Status", "status")],
                state_records(state, "rp_mature", "agentos_adaptation"),
            ),
        ]
    if file_name == "provenance.html":
        return [
            render_record_panel(
                "Timeline Views",
                [("View", "view"), ("Events", "events"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_timeline_view", "view"),
            ),
            render_record_panel(
                "Timeline Events",
                [("Event", "timeline_event"), ("Tick", "tick"), ("Actor", "actor"), ("Artifact", "artifact"), ("Status", "status")],
                state_records(state, "rp_timeline_view", "timeline_event"),
            ),
            render_record_panel(
                "Provenance Edges",
                [("Edge", "edge"), ("Source", "source"), ("Target", "target"), ("Kind", "kind"), ("Stage", "stage"), ("Status", "status")],
                state_records(state, "rp_prov_edges", "edge"),
            ),
            render_record_panel(
                "Evidence Packets",
                [("Packet", "packet"), ("Run", "run"), ("Sources", "sources"), ("Checks", "checks"), ("Status", "status")],
                state_records(state, "rp_evidence_packet", "packet"),
            ),
            render_record_panel(
                "Provenance Summary",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_prov_view", "rp_agentcmp", "rp_review_dashboard"),
                    (
                        "provenance_view_checks=",
                        "timeline_views=",
                        "subgraphs=",
                        "subgraph_edges=",
                        "evidence_packets=",
                        "agentos_mapping=",
                        "agentos_kernel_timeline=",
                    ),
                ),
            ),
        ]
    if file_name == "analysis-results.html":
        return [
            render_record_panel(
                "Analysis Plans",
                [("Plan", "plan"), ("Project", "project"), ("Run", "run_id"), ("Dataset", "dataset"), ("Question", "question"), ("Status", "status")],
                state_records(state, "rp_anplan", "plan"),
            ),
            render_record_panel(
                "Analysis Runs",
                [("Run", "run"), ("Plan", "plan"), ("Mode", "mode"), ("Input", "input"), ("Output", "output"), ("Status", "status")],
                state_records(state, "rp_anrun", "run"),
            ),
            render_record_panel(
                "Result Tables",
                [("Table", "table"), ("Run", "run"), ("Rows", "rows"), ("Columns", "columns"), ("Export", "export"), ("Status", "status")],
                state_records(state, "rp_resulttbl", "table"),
            ),
            render_record_panel(
                "Statistical Results",
                [("Stat", "stat"), ("Run", "run"), ("Method", "method"), ("p", "p_value"), ("Effect", "effect"), ("Status", "status")],
                state_records(state, "rp_statres", "stat"),
            ),
            render_record_panel(
                "Analysis Figures",
                [("Figure", "figure"), ("Run", "run"), ("Kind", "kind"), ("Path", "path"), ("Status", "status")],
                state_records(state, "rp_anfig", "figure"),
            ),
            render_record_panel(
                "Interpretations",
                [("Interpretation", "interpretation"), ("Run", "run"), ("Conclusion", "conclusion"), ("Reviewer", "reviewer"), ("Status", "status")],
                state_records(state, "rp_interp", "interpretation"),
            ),
        ]
    if file_name == "decision-support.html":
        return [
            render_record_panel(
                "Decision Options",
                [("Option", "option"), ("Benefit", "benefit"), ("Cost", "cost"), ("Recommendation", "recommendation"), ("Status", "status")],
                state_records(state, "rp_decopt", "option"),
            ),
            render_record_panel(
                "Decision Criteria",
                [("Criterion", "criterion"), ("Weight", "weight"), ("Description", "description"), ("Status", "status")],
                state_records(state, "rp_deccrit", "criterion"),
            ),
            render_record_panel(
                "Decision Scores",
                [("Score", "score"), ("Option", "option"), ("Criterion", "criterion"), ("Value", "value"), ("Rationale", "rationale")],
                state_records(state, "rp_decscore", "score"),
            ),
            render_record_panel(
                "Review Packet",
                [("Packet", "packet"), ("Decision", "decision"), ("Recommended", "recommended_option"), ("Evidence", "evidence"), ("Status", "status")],
                state_records(state, "rp_decpacket", "packet"),
            ),
        ]
    if file_name == "usable-research.html":
        return [
            render_record_panel(
                "Research Templates",
                [("Template", "template"), ("Name", "name"), ("Question", "question"), ("Tags", "tags"), ("Status", "status")],
                state_records(state, "rp_usabletpl", "template"),
            ),
            render_record_panel(
                "Reusable Datasets",
                [("Dataset", "dataset"), ("Rows", "rows"), ("Columns", "columns"), ("Tags", "tags"), ("Quality", "quality"), ("Status", "status")],
                state_records(state, "rp_usableds", "dataset"),
            ),
            render_record_panel(
                "Library Sources",
                [("Source", "source"), ("Title", "title"), ("Kind", "kind"), ("Tags", "tags"), ("Status", "status")],
                state_records(state, "rp_usablelib", "source"),
            ),
            render_record_panel(
                "Research DAG",
                [("Stage", "stage"), ("Order", "order"), ("Depends", "depends"), ("Artifact", "artifact"), ("Agent", "agent"), ("Status", "status")],
                state_records(state, "rp_usabledag", "stage"),
            ),
            render_record_panel(
                "Workbench Queues",
                [("Queue", "queue"), ("Rows", "rows"), ("Ready", "ready"), ("Needs Action", "needs_action"), ("Status", "status")],
                state_records(state, "rp_usableops", "queue"),
            ),
            render_record_panel(
                "Handoff Packages",
                [("Handoff", "handoff"), ("Files", "files"), ("Missing", "required_missing"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_usableops", "handoff"),
            ),
        ]
    if file_name == "usable-project.html":
        return [
            render_record_panel(
                "Startup And Doctor",
                [("Config", "config"), ("Provider", "provider"), ("Checks", "checks"), ("Passed", "passed"), ("Status", "status")],
                state_records(state, "rp_usableboot", "config") + state_records(state, "rp_usableboot", "doctor"),
            ),
            render_record_panel(
                "Project Scaffold",
                [("Template", "template"), ("Files", "files"), ("Includes", "includes"), ("Project", "project"), ("Status", "status")],
                state_records(state, "rp_usablescaf", "template") + state_records(state, "rp_usablescaf", "scaffold"),
            ),
            render_record_panel(
                "Scaffold Files",
                [("File", "file"), ("Kind", "kind"), ("Rows", "rows"), ("Entries", "entries"), ("Status", "status")],
                state_records(state, "rp_usablescaf", "file"),
            ),
            render_record_panel(
                "Project Launches And Operations",
                [("Launch", "launch"), ("Operation", "operation"), ("Run", "run"), ("Sections", "sections"), ("Status", "status")],
                state_records(state, "rp_usablelaunch", "launch") + state_records(state, "rp_usablelaunch", "operation"),
            ),
            render_record_panel(
                "Bundles And Package Actions",
                [("Bundle", "bundle"), ("Intake", "intake"), ("Action Plan", "action_plan"), ("Action Execution", "action_execution"), ("Status", "status")],
                state_records(state, "rp_usablepack", "bundle") + state_records(state, "rp_usablepack", "intake") + state_records(state, "rp_usablepack", "action_plan") + state_records(state, "rp_usablepack", "action_execution"),
            ),
        ]
    if file_name == "data.html":
        return [
            render_record_panel(
                "Ingested Input Files",
                [("File", "file"), ("Path", "path"), ("Kind", "kind"), ("Records", "records"), ("Bytes", "bytes"), ("Status", "status")],
                state_records(state, "rp_ingest_files", "file"),
            ),
            render_record_panel(
                "Dataset Snapshots",
                [("Snapshot", "snapshot"), ("Files", "files"), ("Records", "records"), ("Transform", "transform"), ("Normalized FASTQ", "normalized_fastq"), ("Status", "status")],
                state_records(state, "rp_dataset_snapshot", "snapshot"),
            ),
            render_record_panel(
                "Data Preview Records",
                [("Preview", "preview"), ("Rows", "rows"), ("Columns", "columns"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_data_preview", "preview"),
            ),
            render_record_panel(
                "Derived Data Preview",
                [("Preview", "derived_preview"), ("Rows", "rows"), ("Columns", "columns"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_data_preview", "derived_preview"),
            ),
            render_record_panel(
                "Data Quality State",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_data_quality", "rp_api_data"),
                    (
                        "dataset=",
                        "rules=",
                        "passed=",
                        "failed=",
                        "min_reads=",
                        "derived_variants=",
                        "metrics_section=",
                        "sample_sheet_valid=",
                        "decision=",
                        "host_file_verify",
                        "host_action_file_verify",
                        "host_action_file_verified=",
                        "host_action_file_missing=",
                    ),
                ),
            ),
            render_record_panel(
                "Data Transform Records",
                [("Transform", "transform"), ("Input", "input"), ("Output", "output"), ("Status", "status")],
                state_records(state, "rp_data_transform", "transform"),
            ),
            render_record_panel(
                "Derived Data Products",
                [("Derived", "derived"), ("Input", "input"), ("Output", "output"), ("Status", "status")],
                state_records(state, "rp_data_transform", "derived"),
            ),
            render_record_panel(
                "Dataset Collection",
                [("Item", "item"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_dataset_collection", "item"),
            ),
            render_record_panel(
                "Data Manifest Verification",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_ingest_files", "rp_data_quality", "rp_dataset_collection", "rp_api_data"),
                    (
                        "host_file_manifest",
                        "host_file_verify",
                        "host_action_file_manifest",
                        "host_action_file_sha_records=",
                        "host_action_file_verified=",
                        "host_action_file_missing=",
                    ),
                ),
            ),
        ]
    if file_name == "project.html":
        project_source_rows = [
            row for row in operations_source_files(state)
            if row.get("operation_section") in {"project_followup", "execution_plan", "operations_report"}
        ]
        return [
            render_record_panel(
                "Project Handoff",
                [("Source", "project_handoff"), ("Project", "project"), ("Space", "space"), ("Note", "note"), ("Action Item", "action_item"), ("Answer", "answer"), ("Repair", "repair"), ("Search", "search"), ("Status", "status")],
                state_records(state, "rp_review_pack", "project_handoff"),
            ),
            render_record_panel(
                "Project Evidence Package",
                [("Evidence", "evidence"), ("Bridge", "bridge"), ("Source", "source"), ("Delivery", "delivery"), ("Operations", "operations"), ("Project", "project"), ("Status", "status")],
                state_records(state, "rp_review_pack", "evidence") + state_records(state, "rp_review_pack", "bridge"),
            ),
            render_record_panel(
                "Project Package Records",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_runner", "rp_package", "rp_actionio", "rp_web_bundle"),
                    (
                        "host_action_project_",
                        "host_action_research_search",
                        "host_action_search_",
                        "host_action_operations_",
                        "host_action_delivery_",
                        "host_action_plan_queue",
                    ),
                ),
            ),
            render_record_panel(
                "Project Quality And Repair",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_runner", "rp_package", "rp_review_pack"),
                    (
                        "host_action_quality_gate",
                        "host_action_quality_repair",
                        "operations_handoff=",
                        "project_handoff=",
                    ),
                ),
            ),
            render_record_panel(
                "Project Search And Notes",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_runner", "rp_package", "rp_actionio", "rp_web_bundle", "rp_uresrun"),
                    (
                        "host_action_project_note",
                        "host_action_project_action_item",
                        "host_action_project_answer",
                        "host_action_project_repair",
                        "host_action_research_search",
                        "host_action_search_",
                    ),
                ),
            ),
            render_record_panel(
                "Project Source Files",
                [("Section", "operation_section"), ("State File", "state_file"), ("Record", "record"), ("Rendered Page", "rendered_page"), ("Status", "status")],
                project_source_rows,
            ),
        ]
    if file_name == "project-review.html":
        return [
            render_record_panel(
                "Project Release Gate",
                [("Gate", "release_gate"), ("Project", "project"), ("Decision", "decision"), ("Checks", "checks"), ("Required Actions", "required_actions"), ("Suggested Actions", "suggested_actions"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "release_gate"),
            ),
            render_record_panel(
                "Project Snapshots",
                [("Snapshot", "project_snapshot"), ("Project", "project"), ("Files", "files"), ("Present", "present"), ("Missing", "missing"), ("Hash Records", "hash_records"), ("Changes", "changes"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "project_snapshot"),
            ),
            render_record_panel(
                "Snapshot Comparison",
                [("Comparison", "snapshot_comparison"), ("Project", "project"), ("Left", "left"), ("Right", "right"), ("Changed Files", "changed_files"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "snapshot_comparison"),
            ),
            render_record_panel(
                "Project Reproducibility Audit",
                [("Audit", "reproducibility_audit"), ("Project", "project"), ("Inputs", "inputs"), ("Outputs", "outputs"), ("Notebooks", "notebooks"), ("Claim Audits", "claim_audits"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "reproducibility_audit"),
            ),
            render_record_panel(
                "Project Provenance Graph",
                [("Record", "provenance_graph"), ("Project", "project"), ("Nodes", "nodes"), ("Edges", "edges"), ("Dot", "dot"), ("From", "from"), ("To", "to"), ("Relation", "relation"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "provenance_graph") + state_records(state, "rp_web_bundle", "provenance_edge"),
            ),
            render_record_panel(
                "Project Delivery Report",
                [("Delivery", "project_delivery"), ("Project", "project"), ("Decision", "decision"), ("Bundle", "bundle"), ("Release Gate", "release_gate"), ("Handoff", "handoff"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "project_delivery"),
            ),
            render_record_panel(
                "Project Package Index",
                [("Index", "package_index"), ("Handoff", "handoff"), ("Release Gate", "release_gate"), ("Snapshot", "snapshot"), ("Reproducibility", "reproducibility"), ("Provenance", "provenance"), ("Status", "status")],
                state_records(state, "rp_web_bundle", "package_index") + state_records(state, "rp_web_bundle", "package_intake"),
            ),
            render_record_panel(
                "Project Review Host Actions",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_web_bundle", "rp_actionio", "rp_package", "rp_review_pack"),
                    (
                        "host_action_project_review_",
                        "host_action_project_release_gate",
                        "host_action_project_snapshot",
                        "host_action_project_reproducibility",
                        "host_action_project_provenance",
                        "host_action_project_delivery",
                        "host_action_project_package_intake",
                        "release_gate=",
                        "project_snapshot=",
                        "snapshot_comparison=",
                        "reproducibility_audit=",
                        "provenance_graph=",
                        "project_delivery=",
                        "package_index=",
                    ),
                ),
            ),
        ]
    if file_name == "studio.html":
        return [
            render_record_panel(
                "Studio Sessions",
                [("Session", "studio_session"), ("Title", "title"), ("Goal", "goal"), ("Direction", "direction"), ("Workbench", "workbench"), ("Run", "run"), ("Answer", "answer"), ("Decision", "decision"), ("Status", "status")],
                state_records(state, "rp_studio", "studio_session"),
            ),
            render_record_panel(
                "Studio Materials",
                [("Material", "studio_material"), ("Notes", "notes"), ("Rows", "csv_rows"), ("References", "references"), ("Workspace", "workspace"), ("Status", "status")],
                state_records(state, "rp_studio", "studio_material"),
            ),
            render_record_panel(
                "Studio Links",
                [("Links", "studio_links"), ("Studio", "studio"), ("Workbench", "workbench"), ("Project", "project"), ("Download", "download"), ("Status", "status")],
                state_records(state, "rp_studio", "studio_links"),
            ),
            render_record_panel(
                "Studio Host Actions",
                [("State File", "state_file"), ("Key", "key"), ("Value", "value")],
                key_value_rows(
                    state,
                    ("rp_studio", "rp_runner", "rp_package", "rp_actionio", "rp_web_bundle"),
                    ("host_action_studio_", "studio_session=", "studio_material=", "studio_links="),
                ),
            ),
        ]
    if file_name == "compare.html":
        compare_rows = [
            ("File Scans", metric_value(state, [("rp_api_compare", "file_scans"), ("rp_ui_compare", "pain_file_scans")])),
            ("State Convention", metric_value(state, [("rp_api_compare", "state_convention"), ("rp_ui_compare", "pain_state_convention")])),
            ("User Permission", metric_value(state, [("rp_api_compare", "user_permission_only"), ("rp_ui_compare", "pain_user_permissions")])),
            ("Context Trusted", metric_value(state, [("rp_api_compare", "context_trusted")])),
            ("Rebuild Steps", metric_value(state, [("rp_api_compare", "rebuild_steps"), ("rp_ui_compare", "pain_rebuild_steps")])),
            ("Data Pipeline Files", metric_value(state, [("rp_api_compare", "data_pipeline_files")])),
            ("Workflow Runner Files", metric_value(state, [("rp_api_compare", "workflow_runner_files")])),
            ("Portability Checks", metric_value(state, [("rp_agentcmp", "workflow_portability_checks")])),
            ("Backend Checks", metric_value(state, [("rp_agentcmp", "portability_backend_checks")])),
            ("Backend Runner", metric_value(state, [("rp_agentcmp", "backend_runner_checks")])),
            ("Backend Evidence", metric_value(state, [("rp_agentcmp", "backend_runner_report_checks")])),
            ("Integrity Plane", metric_value(state, [("rp_agentcmp", "integrity_plane_checks"), ("rp_integrity", "integrity_checks")])),
            ("Coherence Plane", metric_value(state, [("rp_agentcmp", "coherence_plane_checks"), ("rp_coherence", "coherence_checks")])),
            ("Publication Workflow", metric_value(state, [("rp_agentcmp", "publication_checks"), ("rp_publication", "publication_checks")])),
            ("Experiment Schedule", metric_value(state, [("rp_agentcmp", "experiment_scheduling_checks"), ("rp_expsched", "experiment_scheduling_checks")])),
            ("Training Compliance", metric_value(state, [("rp_agentcmp", "training_compliance_checks"), ("rp_traincomp", "training_compliance_checks")])),
            ("Reader Contract", metric_value(state, [("rp_agentcmp", "reader_contract")])),
        ]
        check_rows = [
            ("Coherence Checks", metric_value(state, [("rp_api_compare", "coherence_checks"), ("rp_ui_compare", "coherence_checks")])),
            ("Namespace Checks", metric_value(state, [("rp_api_compare", "namespace_checks"), ("rp_ui_compare", "namespace_checks")])),
            ("Surface Checks", metric_value(state, [("rp_api_compare", "surface_checks"), ("rp_ui_compare", "surface_checks")])),
            ("Status Semantics", metric_value(state, [("rp_api_compare", "status_semantics"), ("rp_ui_compare", "status_semantics")])),
            ("Reference Checks", metric_value(state, [("rp_api_compare", "reference_checks"), ("rp_ui_compare", "reference_checks")])),
            ("Evidence Trace Checks", metric_value(state, [("rp_api_compare", "evidence_trace_checks"), ("rp_ui_compare", "evidence_trace_checks")])),
            ("Integrity Evidence", metric_value(state, [("rp_integrity", "evidence_checks")])),
            ("Integrity References", metric_value(state, [("rp_integrity", "reference_checks")])),
            ("Coherence Delivery", metric_value(state, [("rp_coherence", "delivery_checks")])),
            ("Coherence Run State", metric_value(state, [("rp_coherence", "run_state_checks")])),
            ("Coherence Lifecycle", metric_value(state, [("rp_coherence", "lifecycle_checks")])),
        ]
        host_alignment_rows = [
            ("Host Modules", metric_value(state, [("host_platform_alignment", "host_modules")])),
            ("Tracked Modules", metric_value(state, [("host_platform_alignment", "tracked_host_modules")])),
            ("Plain Sources", metric_value(state, [("host_platform_alignment", "plain_sources")])),
            ("AgentOS Sources", metric_value(state, [("host_platform_alignment", "agentos_sources")])),
            ("Runtime State Checked", metric_value(state, [("host_platform_alignment", "runtime_state_checked")])),
            ("Capability Groups", "{}/{}".format(metric_value(state, [("host_platform_alignment", "groups_ok")]), metric_value(state, [("host_platform_alignment", "groups_total")]))),
            ("Untracked Host Modules", metric_value(state, [("host_platform_alignment", "untracked_host_modules")])),
            ("Status", metric_value(state, [("host_platform_alignment", "status")])),
        ]
        host_test_rows = [
            ("Host Test Methods", metric_value(state, [("host_test_alignment", "host_tests")])),
            ("Test Themes", "{}/{}".format(metric_value(state, [("host_test_alignment", "themes_ok")]), metric_value(state, [("host_test_alignment", "themes_total")]))),
            ("Unclassified Tests", metric_value(state, [("host_test_alignment", "unclassified_tests")])),
            ("Status", metric_value(state, [("host_test_alignment", "status")])),
        ]
        host_surface_rows = [
            ("宿主机 API 路由数", metric_value(state, [("host_surface_alignment", "host_api_routes")])),
            ("宿主机 action 路由数", metric_value(state, [("host_surface_alignment", "host_action_routes")])),
            ("宿主机下载引用数", metric_value(state, [("host_surface_alignment", "host_download_refs")])),
            ("运行状态检查", metric_value(state, [("host_surface_alignment", "runtime_state_checked")])),
            ("plain 源码 API 路由数", metric_value(state, [("host_surface_alignment", "plain_source_api_routes")])),
            ("AgentOS 源码 API 路由数", metric_value(state, [("host_surface_alignment", "agentos_source_api_routes")])),
            ("plain 运行 API 路由数", metric_value(state, [("host_surface_alignment", "plain_runtime_api_routes")])),
            ("AgentOS 运行 API 路由数", metric_value(state, [("host_surface_alignment", "agentos_runtime_api_routes")])),
            ("plain 源码 action 路由数", metric_value(state, [("host_surface_alignment", "plain_source_action_routes")])),
            ("AgentOS 源码 action 路由数", metric_value(state, [("host_surface_alignment", "agentos_source_action_routes")])),
            ("plain 运行 action 路由数", metric_value(state, [("host_surface_alignment", "plain_runtime_action_routes")])),
            ("AgentOS 运行 action 路由数", metric_value(state, [("host_surface_alignment", "agentos_runtime_action_routes")])),
            ("状态", metric_value(state, [("host_surface_alignment", "status")])),
        ]
        host_seeded_rows = [
            ("action", metric_value(state, [("host_seeded_action", "action")])),
            ("action 数量", metric_value(state, [("host_seeded_action", "action_count")])),
            ("action 类型", metric_value(state, [("host_seeded_action", "action_kinds")])),
            ("plain 状态", metric_value(state, [("host_seeded_action", "plain_status")])),
            ("AgentOS 状态", metric_value(state, [("host_seeded_action", "agentos_status")])),
            ("整体状态", metric_value(state, [("host_seeded_action", "status")])),
        ]
        sections = [
            render_line_panel("Plain Kernel Signals", compare_rows),
            render_line_panel("Consistency Signals", check_rows),
        ]
        if "host_platform_alignment" in state:
            sections.extend(
                [
                    render_line_panel("Host Platform Alignment Summary", host_alignment_rows),
                    render_record_panel(
                        "Host Platform Capability Groups",
                        [
                            ("Group", "capability_group"),
                            ("Status", "status"),
                            ("Host Modules", "host_modules"),
                            ("Plain Sources", "plain_sources"),
                            ("AgentOS Sources", "agentos_sources"),
                            ("Reader Keywords", "reader_keywords"),
                            ("Plain Runtime", "plain_runtime_hits"),
                            ("AgentOS Runtime", "agentos_runtime_hits"),
                        ],
                        state_records(state, "host_platform_alignment", "capability_group"),
                    ),
                ]
            )
        if "host_test_alignment" in state:
            sections.extend(
                [
                    render_line_panel("Host Platform Test Summary", host_test_rows),
                    render_record_panel(
                        "Host Platform Test Themes",
                        [
                            ("Theme", "test_theme"),
                            ("Status", "status"),
                            ("Host Tests", "host_tests"),
                            ("Evidence Tokens", "evidence_tokens"),
                            ("Missing Plain", "missing_plain"),
                            ("Missing AgentOS", "missing_agentos"),
                        ],
                        state_records(state, "host_test_alignment", "test_theme"),
                    ),
                ]
            )
        if "host_surface_alignment" in state:
            sections.extend(
                [
                    render_line_panel("宿主机 Web/API/action 概览", host_surface_rows),
                    render_record_panel(
                        "宿主机路由前缀",
                        [("API 前缀", "api_prefix"), ("action 前缀", "action_prefix"), ("状态", "status")],
                        state_records(state, "host_surface_alignment", "api_prefix")
                        + state_records(state, "host_surface_alignment", "action_prefix"),
                    ),
                ]
            )
        if "host_seeded_action" in state:
            sections.extend(
                [
                    render_line_panel("宿主机 action 运行实测", host_seeded_rows),
                    render_record_panel(
                        "预置 action 双目标结果",
                        [
                            ("目标", "seeded_action_target"),
                            ("状态", "status"),
                            ("准备动作数", "prepare_actions"),
                            ("接收动作数", "prepare_accepted"),
                            ("运行状态", "run_status"),
                            ("运行通过", "run_passed"),
                            ("嵌入记录数", "embedded_action_records"),
                            ("抽取状态文件数", "extracted_state_files"),
                            ("失败项", "failures"),
                        ],
                        state_records(state, "host_seeded_action", "seeded_action_target"),
                    ),
                ]
            )
        sections.extend(
            [
            render_record_panel(
                "AgentOS Main Flow Kernel Stages",
                [
                    ("Stage", "stage"),
                    ("Context", "context_trusted"),
                    ("Metadata Query", "metadata_query"),
                    ("Event Notify", "agent_event_notify"),
                    ("Recovery", "failure_recovery"),
                    ("Audit", "provenance_audit"),
                    ("Permission", "permission_control"),
                    ("Timeline", "timeline_observe"),
                    ("Workbench Verify", "workbench_file_verify"),
                    ("Package Provenance", "package_provenance"),
                    ("Real Task Context", "real_task_context"),
                    ("Status", "status"),
                ],
                state_records(state, "rp_agentos_mainflow", "stage"),
            ),
            render_record_panel(
                "AgentOS Kernel Output Files",
                [
                    ("Kernel Stage", "kernel_stage"),
                    ("State File", "state_file"),
                    ("Kernel Services", "kernel_services"),
                    ("Details", "details"),
                    ("Status", "status"),
                ],
                agentos_kernel_outputs(state),
            ),
            render_record_panel(
                "Backend Runner Cases",
                [
                    ("Case", "runner_case"),
                    ("Input", "input"),
                    ("Artifact", "artifact"),
                    ("Result", "result"),
                    ("Input Check", "input_check"),
                    ("Artifact Check", "artifact_check"),
                    ("Attempts", "att"),
                    ("Retry", "retry"),
                    ("Ticks", "ticks"),
                    ("Reason", "reason"),
                ],
                state_records(state, "rp_backend_exec", "runner_case"),
            ),
            render_record_panel(
                "Backend Case Details",
                [
                    ("Case", "runner_detail"),
                    ("Source", "src"),
                    ("Required", "req"),
                    ("Observed", "obs"),
                    ("Action", "act"),
                    ("Review", "review"),
                ],
                state_records(state, "rp_backend_exec", "runner_detail"),
            ),
            render_record_panel(
                "Backend Evidence Report",
                [
                    ("Case", "runner_report"),
                    ("Plain Cost", "plain_cost"),
                    ("AgentOS Replace", "agentos_replace"),
                    ("Risk", "risk"),
                    ("Status", "status"),
                ],
                state_records(state, "rp_backend_exec", "runner_report"),
            ),
            render_record_panel(
                "Backend Study Metrics",
                [
                    ("Arm", "study_metric"),
                    ("File Scans", "file_scans"),
                    ("Context Trusted", "context_trusted"),
                    ("Rebuild Steps", "rebuild_steps"),
                    ("Batch Tools", "batch_tools"),
                    ("Metadata Index", "metadata_index"),
                    ("Detail Checks", "detail_checks"),
                    ("Result", "result"),
                ],
                state_records(state, "rp_study", "study_metric"),
            ),
            render_record_panel(
                "Backend Scenario Handoff",
                [("Handoff", "study_handoff"), ("Status", "status")],
                state_records(state, "rp_study", "study_handoff"),
            ),
            ]
        )
        return sections
    if file_name == "artifacts.html":
        host_action_rows = []
        for name, prefixes in (
            ("rp_artifact", ("host_artifact_",)),
            ("rp_artifact_manifest", ("host_artifact_manifest_", "host_workflow_artifact_action=")),
            ("rp_stage_log", ("host_artifact_log=",)),
            ("rp_chart_data", ("host_artifact_chart=",)),
        ):
            for line in state_prefixed_lines(state, name, prefixes):
                host_action_rows.append((name, line))
        review_signal_rows = []
        for name, prefixes in (
            ("rp_review_dashboard", ("section=artifacts", "gate=artifact_manifest", "host_relay_quality=")),
            ("rp_review_pack", ("evidence=artifact_manifest", "evidence=llm_quality")),
            ("rp_llmeval", ("host_relay_eval_batch=",)),
        ):
            for line in state_prefixed_lines(state, name, prefixes):
                review_signal_rows.append((name, line))
        return [
            render_record_panel(
                "Artifact Manifest Records",
                [("Record", "record"), ("Kind", "kind"), ("Path", "path"), ("Section", "section"), ("Status", "status")],
                state_records(state, "rp_artifact_manifest", "record"),
            ),
            render_record_panel(
                "Artifact Dossier",
                [("Dossier", "dossier"), ("Source", "source"), ("Stage Log", "stage_log"), ("Chart", "chart"), ("Review Pack", "review_pack"), ("Status", "status")],
                state_records(state, "rp_artifact_manifest", "dossier"),
            ),
            render_record_panel(
                "Derived Artifact Sections",
                [("Section", "section"), ("Reads", "reads"), ("Bases", "bases"), ("Reference", "reference"), ("Variants", "variant_count"), ("Status", "status")],
                state_records(state, "rp_artifact", "section"),
            ),
            render_record_panel(
                "Artifact Provenance",
                [("Provenance", "provenance"), ("Stage", "stage"), ("Event", "event"), ("Retry", "retry"), ("Cache", "cache"), ("Review Gate", "review_gate"), ("LLM Quality", "llm_quality"), ("Status", "status")],
                state_records(state, "rp_artifact", "provenance"),
            ),
            render_record_panel(
                "Artifact Review Path",
                [
                    ("Path", "artifact_review_path"),
                    ("Input", "input"),
                    ("Prepared", "prepared"),
                    ("Artifact", "artifact"),
                    ("Metrics", "metrics"),
                    ("Chart", "chart"),
                    ("Failure", "failure"),
                    ("Retry", "retry"),
                    ("Event", "event"),
                    ("Report", "report"),
                    ("Review", "review"),
                    ("Review Pack", "review_pack"),
                    ("Delivery", "delivery"),
                    ("Status", "status"),
                ],
                state_records(state, "rp_artifact_manifest", "artifact_review_path"),
            ),
            render_record_panel(
                "Artifact Source Map",
                [
                    ("Path", "artifact_path"),
                    ("Field", "field"),
                    ("Reference", "reference"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Status", "status"),
                ],
                artifact_source_map(state),
            ),
            render_record_panel(
                "Delivery Source Map",
                [
                    ("Delivery Record", "delivery_record"),
                    ("Field", "field"),
                    ("Reference", "reference"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Source File", "source_file"),
                    ("Status", "status"),
                ],
                delivery_source_map(state),
            ),
            render_record_panel(
                "Dossier Checks",
                [("Check", "dossier_check"), ("Source", "source"), ("Stage", "stage"), ("Event", "event"), ("Gate", "gate"), ("Status", "status")],
                state_records(state, "rp_artifact_manifest", "dossier_check"),
            ),
            render_record_panel(
                "Archive Files",
                [("Archive File", "archive_file"), ("Kind", "kind"), ("Status", "status")],
                state_records(state, "rp_artifact", "archive_file"),
            ),
            render_record_panel(
                "Stage Logs",
                [("Log", "log"), ("Status", "status"), ("Reason", "reason"), ("Artifact", "artifact")],
                state_records(state, "rp_stage_log", "log"),
            ),
            render_line_panel("Review And LLM Signals", review_signal_rows),
            render_line_panel("Host Artifact Actions", host_action_rows),
            render_record_panel(
                "Operations Source Files",
                [("Section", "operation_section"), ("State File", "state_file"), ("Record", "record"), ("Rendered Page", "rendered_page"), ("Status", "status")],
                operations_source_files(state),
            ),
        ]
    if file_name == "review.html":
        review_evidence_rows = (
            state_records(state, "rp_review_pack", "evidence")
            + state_records(state, "rp_review_pack", "backend_evidence_review")
        )
        return [
            render_record_panel(
                "Review Sections",
                [("Section", "section"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_review_dashboard", "section"),
            ),
            render_record_panel(
                "Review Gates",
                [("Gate", "gate"), ("Status", "status"), ("Source", "source")],
                state_records(state, "rp_review_dashboard", "gate"),
            ),
            render_record_panel(
                "Review Handoffs",
                [("Handoff", "handoff"), ("Artifact", "artifact"), ("Status", "status")],
                state_records(state, "rp_review_dashboard", "handoff"),
            ),
            render_record_panel(
                "Review Decisions",
                [("Decision", "decision"), ("Basis", "basis")],
                state_records(state, "rp_review_dashboard", "decision"),
            ),
            render_record_panel(
                "Review Evidence Pack",
                [("Evidence", "evidence"), ("Backend Evidence", "backend_evidence_review"), ("Source", "source"), ("Plain Costs", "plain_costs"), ("AgentOS Replacements", "agentos_replacements"), ("Risks", "risks"), ("Status", "status")],
                review_evidence_rows,
            ),
            render_record_panel(
                "Review Source Map",
                [
                    ("Kind", "source_kind"),
                    ("Record", "record"),
                    ("Field", "field"),
                    ("Reference", "reference"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Status", "status"),
                ],
                review_source_map(state),
            ),
            render_record_panel(
                "Delivery Source Map",
                [
                    ("Delivery Record", "delivery_record"),
                    ("Field", "field"),
                    ("Reference", "reference"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Source File", "source_file"),
                    ("Status", "status"),
                ],
                delivery_source_map(state),
            ),
            render_record_panel(
                "Review Backend Evidence",
                [("Backend Evidence", "backend_review_evidence"), ("Plain Costs", "plain_costs"), ("AgentOS Replacements", "agentos_replacements"), ("Risks", "risks"), ("Review Pack", "review_pack"), ("Status", "status")],
                state_records(state, "rp_review_dashboard", "backend_review_evidence"),
            ),
            render_record_panel(
                "Review Backend Actions",
                [("Case", "backend_action_review"), ("Action", "action"), ("Review", "review"), ("Plain Cost", "plain_cost"), ("AgentOS Replace", "agentos_replace"), ("Status", "status")],
                state_records(state, "rp_review_pack", "backend_action_review"),
            ),
            render_record_panel(
                "Review Pack Actions",
                [("Action", "action"), ("Owner", "owner"), ("Status", "status")],
                state_records(state, "rp_review_pack", "action"),
            ),
            render_record_panel(
                "Review Pack Bridges",
                [("Bridge", "bridge"), ("Delivery", "delivery"), ("Operations", "operations"), ("Project", "project"), ("Status", "status")],
                state_records(state, "rp_review_pack", "bridge"),
            ),
            render_record_panel(
                "Review Operations Summary",
                [("Source", "operations_handoff"), ("Tasks", "tasks"), ("Next", "next"), ("Report", "report"), ("Plan", "plan"), ("Quality", "quality"), ("Repair", "repair"), ("Backend", "backend"), ("Status", "status")],
                state_records(state, "rp_review_pack", "operations_handoff"),
            ),
            render_record_panel(
                "Review Workbench Summary",
                [("Source", "workbench_handoff"), ("Workbench", "workbench"), ("Task", "task"), ("Task Status", "task_status"), ("Manifest", "manifest"), ("Verified", "verified"), ("Missing", "missing"), ("Bundle", "bundle"), ("Status", "status")],
                state_records(state, "rp_review_pack", "workbench_handoff"),
            ),
            render_record_panel(
                "Review Project Summary",
                [("Source", "project_handoff"), ("Project", "project"), ("Space", "space"), ("Note", "note"), ("Action Item", "action_item"), ("Answer", "answer"), ("Repair", "repair"), ("Search", "search"), ("Status", "status")],
                state_records(state, "rp_review_pack", "project_handoff"),
            ),
            render_record_panel(
                "Report Source Map",
                [
                    ("Report Section", "report_section"),
                    ("State File", "state_file"),
                    ("Source Line", "source_line"),
                    ("Linked Sources", "linked_sources"),
                    ("Review Page", "review_page"),
                    ("Status", "status"),
                ],
                report_source_map(state),
            ),
            render_record_panel(
                "Operations Report Narrative",
                [("Section", "operation_section"), ("Source", "source"), ("Detail", "detail"), ("Status", "status")],
                operations_report_narrative(state),
            ),
            render_record_panel(
                "Operations Source Files",
                [("Section", "operation_section"), ("State File", "state_file"), ("Record", "record"), ("Rendered Page", "rendered_page"), ("Status", "status")],
                operations_source_files(state),
            ),
        ]
    if file_name == "llm.html":
        return [
            render_record_panel(
                "LLM Relay Flow",
                [
                    ("Request", "flow"),
                    ("Route", "route"),
                    ("Provider", "provider"),
                    ("Response", "response"),
                    ("Summary", "summary"),
                    ("Quality", "quality"),
                    ("Checks", "checks"),
                    ("Guard", "guard"),
                    ("Secret In Packet", "secret"),
                    ("Replay", "replay"),
                    ("Prompt Hash", "prompt_hash"),
                    ("Outputs", "outputs"),
                    ("Status", "status"),
                ],
                llm_relay_flow(state),
            ),
            render_record_panel(
                "Relay Requests",
                [("Request", "host_relay_request"), ("Route", "route"), ("Provider", "provider"), ("Prompt Hash", "prompt_hash"), ("Source", "source")],
                state_records(state, "rp_llm_req", "host_relay_request"),
            ),
            render_record_panel(
                "Relay Responses",
                [("Response", "host_relay_response"), ("Request", "request"), ("Summary", "summary"), ("Citations", "citations"), ("Status", "status")],
                state_records(state, "rp_llm_resp", "host_relay_response"),
            ),
            render_record_panel(
                "Quality Checks",
                [("Request", "host_relay_eval"), ("Response", "response"), ("Checks", "checks"), ("Passed", "passed"), ("Status", "status")],
                state_records(state, "rp_llmeval", "host_relay_eval"),
            ),
            render_record_panel(
                "Packet Guard",
                [("Request", "host_relay_guard"), ("Prompt Hash", "prompt_hash"), ("Secret Ref", "secret_ref"), ("Secret In Packet", "secret_in_packet"), ("Status", "status")],
                state_records(state, "rp_llm_guard", "host_relay_guard"),
            ),
            render_record_panel(
                "Replay Records",
                [("Request", "host_relay_replay"), ("Response", "response"), ("Prompt Hash", "prompt_hash"), ("Mode", "mode"), ("Status", "status")],
                state_records(state, "rp_relay", "host_relay_replay"),
            ),
        ]
    return []


def render_table(title: str, rows: Iterable[str]) -> str:
    body = []
    for row in rows:
        if "=" in row:
            key, value = row.split("=", 1)
        else:
            key, value = "record", row
        body.append(
            "<tr><th>{}</th><td>{}</td></tr>".format(
                html.escape(key.strip()),
                html.escape(value.strip()),
            )
        )
    if not body:
        body.append("<tr><td colspan='2'>No source rows</td></tr>")
    return "<section class='panel'><h2>{}</h2><table>{}</table></section>".format(html.escape(title), "".join(body))


def render_action_log(actions: list[dict[str, object]]) -> str:
    rows = []
    for record in actions[-12:]:
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(record.get("sequence", ""))),
                html.escape(str(record.get("path", ""))),
                html.escape(str(record.get("status", ""))),
            )
        )
    if not rows:
        rows.append("<tr><td colspan='3'>No host actions</td></tr>")
    return (
        "<section class='panel'><h2>Host Actions</h2>"
        "<table><tr><th>Sequence</th><th>Path</th><th>Status</th></tr>{}</table></section>"
    ).format("".join(rows))


def action_trace_group(path: str) -> str:
    if "/research/studio-launch" in path:
        return "studio"
    if "/workflow-portability/" in path:
        return "portability"
    if "/host-workflow/" in path:
        return "workflow"
    if "/artifact-" in path:
        return "artifact"
    if "/operations-" in path:
        return "operations"
    if "/llm-relay" in path:
        return "llm"
    if "/workbench" in path or "/export-workbench" in path:
        return "workbench"
    if (
        "/project-handoff-audit" in path
        or "/project-release-gate" in path
        or "/project-snapshot" in path
        or "/project-reproducibility-audit" in path
        or "/project-provenance-graph" in path
        or "/project-delivery" in path
        or "/package-intake" in path
    ):
        return "project"
    if "/project-space" in path or "/research-search/" in path:
        return "project"
    if "/agentcompare/" in path:
        return "compare"
    if "/review" in path or "/revision" in path:
        return "review"
    if "/export-bundle" in path or "/delivery" in path:
        return "delivery"
    if path.endswith("/research/run") or path.endswith("/research/rerun"):
        return "run"
    if (
        "/research/dataset" in path
        or "/research/library-source" in path
        or "/research/template" in path
        or "/research/inspect-workspace" in path
        or "/research/import-workspace" in path
        or "/research/import-and-run" in path
        or "/research/literature-search" in path
        or "/research/evidence-" in path
    ):
        return "inputs"
    return ""


def payload_summary(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    preferred = [
        "run_id",
        "workbench",
        "workbench_id",
        "project_id",
        "query",
        "task",
        "status",
        "decision",
        "manifest",
        "verified",
        "missing",
        "bundle",
        "request_id",
        "response_id",
        "profile",
        "title",
        "goal",
        "direction",
    ]
    parts: list[str] = []
    seen: set[str] = set()
    for key in preferred:
        if key in payload:
            parts.append(f"{key}={payload[key]}")
            seen.add(key)
    for key in sorted(payload):
        if key in seen:
            continue
        parts.append(f"{key}={payload[key]}")
        if len(parts) >= 6:
            break
    return ";".join(str(part).replace("\n", " ").replace(";", ",") for part in parts)


def action_trace_panel(title: str, actions: list[dict[str, object]], groups: set[str]) -> str:
    rows: list[dict[str, str]] = []
    for record in actions:
        path = str(record.get("path", ""))
        group = action_trace_group(path)
        if not group or group not in groups:
            continue
        rows.append(
            {
                "action_trace": str(record.get("sequence", "")),
                "group": group,
                "path": path,
                "payload": payload_summary(record.get("payload", {})),
                "status": str(record.get("status", "")),
            }
        )
    return render_record_panel(
        title,
        [("Sequence", "action_trace"), ("Group", "group"), ("Path", "path"), ("Payload", "payload"), ("Status", "status")],
        rows,
        "No related host actions",
    )


def action_output_spec(path: str, group: str) -> tuple[str, str]:
    if group == "studio":
        return "rp_studio,rp_runner,rp_package,rp_actionio,rp_web_bundle", "studio.html,workbench.html,project.html"
    if group == "workflow":
        return "rp_stage_dag,rp_stage_state,rp_run_events,rp_cache_index,rp_retry_plan,rp_worker,rp_execobs,rp_artifact_manifest,rp_package", "run.html,artifacts.html"
    if group == "artifact":
        return "rp_artifact,rp_artifact_manifest,rp_stage_log,rp_chart_data,rp_package", "run.html,artifacts.html"
    if group == "llm":
        return "rp_llm_req,rp_llmq,rp_llm_resp,rp_llm_packets,rp_llm_hostreq,rp_llm_fallback,rp_api_runtime", "llm.html,run.html,review.html"
    if group == "workbench":
        return "rp_runner,rp_revision,rp_package,rp_nbexec,rp_uresrun", "run.html,review.html,project.html"
    if group == "review":
        return "rp_review2,rp_revision,rp_report_text,rp_review_dashboard,rp_review_pack", "review.html,run.html"
    if group == "delivery":
        return "rp_package,rp_artifact_manifest,rp_nbexec,rp_uresrun", "review.html,artifacts.html,run.html"
    if group == "operations":
        return "rp_runner,rp_package,rp_actionio,rp_web_bundle", "run.html,review.html,project.html,actions.html"
    if group == "project":
        return "rp_web_bundle,rp_package,rp_review_pack,rp_runner,rp_actionio", "project.html,project-review.html,review.html,run.html"
    if group == "compare":
        return "rp_agentcmp,rp_api_compare,rp_backend_exec,rp_study", "compare.html,review.html"
    if group == "portability":
        return "rp_wfio,rp_package,rp_agentcmp", "compare.html,actions.html"
    if group == "inputs":
        return "rp_input,rp_lit,rp_knowledge,rp_api_evidence,rp_uresrun", "run.html,evidence.html,data.html"
    if group == "run" or path.endswith("/research/run"):
        return "rp_input,rp_runner,rp_report_text,rp_api_run,rp_uresrun", "run.html"
    return "rp_actionio,rp_web_bundle", "actions.html"


def action_evidence_prefixes(path: str, group: str) -> tuple[str, ...]:
    if group == "studio":
        return ("host_action_studio_", "studio_session=", "studio_material=", "studio_links=")
    if path.endswith("/host-workflow/stage-attempt"):
        return ("host_workflow_stage_action=",)
    if path.endswith("/host-workflow/cache-decision"):
        return ("host_workflow_cache_action=",)
    if path.endswith("/host-workflow/retry-decision"):
        return ("host_workflow_retry_action=",)
    if path.endswith("/host-workflow/artifact-manifest"):
        return ("host_workflow_artifact_action=",)
    if path.endswith("/host-workflow/report-export"):
        return ("host_workflow_report_action=",)
    if path.endswith("/research/artifact-input"):
        return ("host_artifact_manifest_input=", "host_artifact_input=")
    if path.endswith("/research/artifact-derive"):
        return ("host_artifact_manifest_derive=", "host_artifact_derive=")
    if path.endswith("/research/artifact-log"):
        return ("host_artifact_log=", "host_artifact_manifest_log=")
    if path.endswith("/research/artifact-chart"):
        return ("host_artifact_chart=", "host_artifact_manifest_chart=")
    if path.endswith("/research/artifact-package"):
        return ("host_artifact_package=", "host_artifact_manifest_package=")
    if group == "llm":
        return ("host_relay_", "host_action_llm_", "host_llm_")
    if group == "workbench":
        return ("host_action_workbench_", "host_report_workbench_", "host_manifest_workbench_")
    if group == "review":
        return ("host_action_review_", "host_action_revision_", "host_report_review_", "host_report_revision_")
    if group == "delivery":
        return ("host_action_export_", "host_action_bundle_", "host_manifest_bundle=", "host_action_workbench_package=")
    if group == "operations":
        return ("host_action_operations_", "operations_handoff=")
    if group == "project":
        return (
            "host_action_project_",
            "project_handoff=",
            "release_gate=",
            "project_snapshot=",
            "snapshot_comparison=",
            "reproducibility_audit=",
            "provenance_graph=",
            "project_delivery=",
            "package_index=",
        )
    if group == "compare":
        return ("host_action_compare", "backend_runner", "study_metric=", "runner_case=")
    if group == "portability":
        return ("host_portability_", "host_action_portability_")
    if group == "inputs":
        return ("host_action_", "literature_search_id=", "evidence_protocol=")
    if group == "run":
        return ("host_action_run_id=", "host_report_run_id=", "host_action_status=")
    return ("host_action_",)


def first_matching_state_line(state: dict[str, dict[str, object]], outputs: str, prefixes: tuple[str, ...]) -> str:
    for name in split_list(outputs):
        for line in state_lines(state, name):
            stripped = line.strip()
            if any(stripped.startswith(prefix) for prefix in prefixes):
                return "{}:{}".format(name, stripped)
    for name in split_list(outputs):
        lines = state_lines(state, name)
        if lines:
            return "{}:{}".format(name, lines[0].strip())
    return ""


def matching_state_lines(
    state: dict[str, dict[str, object]],
    outputs: str,
    prefixes: tuple[str, ...],
    max_rows: int = 4,
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for name in split_list(outputs):
        for line in state_lines(state, name):
            stripped = line.strip()
            if any(stripped.startswith(prefix) for prefix in prefixes):
                rows.append((name, stripped))
                if len(rows) >= max_rows:
                    return rows
    if rows:
        return rows
    for name in split_list(outputs):
        lines = state_lines(state, name)
        if lines:
            rows.append((name, lines[0].strip()))
            if len(rows) >= max_rows:
                break
    return rows


def detail_kind_from_line(line: str) -> str:
    if "=" in line:
        return line.split("=", 1)[0]
    return "record"


def action_output_links(state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in actions:
        path = str(record.get("path", ""))
        group = action_trace_group(path)
        if not group or group not in groups:
            continue
        outputs, pages = action_output_spec(path, group)
        existing_outputs = [name for name in split_list(outputs) if name in state]
        evidence = first_matching_state_line(state, outputs, action_evidence_prefixes(path, group))
        rows.append(
            {
                "action_output": str(record.get("sequence", "")),
                "group": group,
                "path": path,
                "payload": payload_summary(record.get("payload", {})),
                "ucore_outputs": ",".join(existing_outputs),
                "rendered_pages": pages,
                "evidence": evidence,
                "status": "ready" if existing_outputs else str(record.get("status", "")),
            }
        )
    return rows


def action_output_detail_links(state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in actions:
        path = str(record.get("path", ""))
        group = action_trace_group(path)
        if not group or group not in groups:
            continue
        outputs, pages = action_output_spec(path, group)
        prefixes = action_evidence_prefixes(path, group)
        for source_file, detail in matching_state_lines(state, outputs, prefixes):
            rows.append(
                {
                    "action_detail": str(record.get("sequence", "")),
                    "group": group,
                    "path": path,
                    "source_file": source_file,
                    "detail_kind": detail_kind_from_line(detail),
                    "detail": detail,
                    "rendered_pages": pages,
                    "status": "ready",
                }
            )
    return rows


def action_impact_specs(path: str, group: str) -> list[tuple[str, str, tuple[str, ...], str]]:
    specs: list[tuple[str, str, tuple[str, ...], str]] = []
    if group == "studio":
        specs.append(("studio_session", "rp_studio", ("studio_session=", "host_action_studio_"), "studio.html,workbench.html"))
    if group in {"run", "inputs", "workbench", "review", "delivery", "operations", "project", "compare", "studio"}:
        pages = "run.html,review.html,project.html" if group == "project" else "run.html,review.html"
        specs.append(("report_section", "rp_report_text", ("host_report_", "host_relay_report_summary=", "backend_evidence_report="), pages))
    if group in {"workflow", "artifact", "delivery", "operations"}:
        specs.append(("artifact_path", "rp_artifact_manifest", ("artifact_review_path=", "host_artifact_manifest_", "host_workflow_artifact_action="), "artifacts.html,run.html"))
    if group in {"review", "delivery", "operations", "project", "llm", "workflow", "artifact"}:
        pages = "review.html,project.html" if group == "project" else "review.html"
        specs.append(("review_gate", "rp_review_dashboard", ("gate=", "section=", "decision=", "host_relay_quality=", "backend_review_evidence="), pages))
    if group == "llm":
        specs.append(("llm_packet", "rp_llm_packets", ("host_llm_packet_", "host_relay_packet=", "secret_in_packet="), "llm.html,run.html,review.html"))
        specs.append(("llm_quality", "rp_llmeval", ("host_relay_eval=", "host_relay_eval_batch="), "llm.html,review.html"))
    if path.endswith("/research/artifact-package"):
        specs.append(("delivery_package", "rp_package", ("host_action_export_", "host_action_bundle_", "delivery_files="), "review.html,artifacts.html"))
    if path.endswith("/workflow-portability/package"):
        specs.append(("portability_package", "rp_wfio", ("host_portability_package_action=", "host_portability_package="), "compare.html,actions.html"))
    return specs


def action_impact_links(state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in actions:
        path = str(record.get("path", ""))
        group = action_trace_group(path)
        if not group or group not in groups:
            continue
        for target, source_file, prefixes, pages in action_impact_specs(path, group):
            for detail in state_prefixed_lines(state, source_file, prefixes)[:3]:
                key = (str(record.get("sequence", "")), target, source_file, detail)
                if key in seen:
                    continue
                seen.add(key)
                parsed = parse_kv_record(detail)
                rows.append(
                    {
                        "action_impact": str(record.get("sequence", "")),
                        "group": group,
                        "path": path,
                        "target": target,
                        "state_file": source_file,
                        "detail_kind": detail_kind_from_line(detail),
                        "detail": detail,
                        "rendered_pages": pages,
                        "status": parsed.get("status", "present"),
                    }
                )
    return rows


def action_output_panel(title: str, state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> str:
    return render_record_panel(
        title,
        [
            ("Sequence", "action_output"),
            ("Group", "group"),
            ("Path", "path"),
            ("Payload", "payload"),
            ("uCore Outputs", "ucore_outputs"),
            ("Rendered Pages", "rendered_pages"),
            ("Evidence", "evidence"),
            ("Status", "status"),
        ],
        action_output_links(state, actions, groups),
        "No related host action outputs",
    )


def action_output_detail_panel(title: str, state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> str:
    return render_record_panel(
        title,
        [
            ("Sequence", "action_detail"),
            ("Group", "group"),
            ("Path", "path"),
            ("State File", "source_file"),
            ("Detail Kind", "detail_kind"),
            ("Detail", "detail"),
            ("Rendered Pages", "rendered_pages"),
            ("Status", "status"),
        ],
        action_output_detail_links(state, actions, groups),
        "No related host action detail rows",
    )


def action_impact_panel(title: str, state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> str:
    return render_record_panel(
        title,
        [
            ("Sequence", "action_impact"),
            ("Group", "group"),
            ("Path", "path"),
            ("Target", "target"),
            ("State File", "state_file"),
            ("Detail Kind", "detail_kind"),
            ("Detail", "detail"),
            ("Rendered Pages", "rendered_pages"),
            ("Status", "status"),
        ],
        action_impact_links(state, actions, groups),
        "No related host action impact rows",
    )


def action_delta_specs(path: str) -> list[tuple[str, str, str, str, str]]:
    specs: list[tuple[str, str, str, str, str]] = []
    if path.endswith("/research/run"):
        return [
            ("run_id", "run_id", "rp_report_text", "host_report_run_id", "host_report_run_id"),
            ("title", "title", "rp_report_text", "host_report_title", "host_report_title"),
            ("question", "question", "rp_report_text", "host_report_question", "host_report_question"),
            ("provider", "provider", "rp_report_text", "host_report_provider", "host_report_provider"),
            ("dataset_rows", "dataset_rows", "rp_report_text", "host_report_dataset_rows", "host_report_dataset_rows"),
            ("csv_file", "csv_file", "rp_input", "host_action_csv_file", "host_action_csv_file"),
            ("reference_file", "reference_file", "rp_input", "host_action_reference_file", "host_action_reference_file"),
        ]
    if path.endswith("/research/dataset"):
        return [
            ("dataset_title", "title", "rp_input", "host_action_dataset_title", "host_action_dataset_title"),
            ("dataset_rows", "dataset_rows", "rp_input", "host_action_dataset_rows", "host_action_dataset_rows"),
            ("dataset_columns", "columns", "rp_input", "host_action_dataset_columns", "host_action_dataset_columns"),
        ]
    if path.endswith("/research/library-source"):
        return [("citation_key", "citation_key", "rp_input", "host_action_library_citation", "host_action_library_citation")]
    if path.endswith("/research/template"):
        return [("template_name", "name", "rp_input", "host_action_template_name", "host_action_template_name")]
    if path.endswith("/research/inspect-workspace") or path.endswith("/research/import-workspace"):
        return [
            ("workspace_root", "root", "rp_input", "host_action_workspace_root", "host_action_workspace_root"),
            ("workspace_manifest", "manifest", "rp_input", "host_action_workspace_manifest", "host_action_workspace_manifest"),
        ]
    if path.endswith("/research/literature-search"):
        return [
            ("literature_query", "query", "rp_lit", "host_action_literature_query", "host_action_literature_query"),
            ("max_results", "max_results", "rp_lit", "host_action_literature_max_results", "host_action_literature_max_results"),
        ]
    if path.endswith("/research/evidence-review"):
        return [("included", "included", "rp_lit", "host_action_evidence_included", "host_action_evidence_included")]
    if path.endswith("/research/evidence-protocol"):
        return [("protocol_title", "title", "rp_lit", "host_action_protocol_title", "host_action_protocol_title")]
    if "/host-workflow/" in path:
        specs.extend(
            [
                ("workflow_id", "workflow_id", "rp_stage_dag", "host_workflow_id", "host_workflow_id"),
                ("run_id", "run_id", "rp_stage_state", "host_workflow_run_id", "host_workflow_run_id"),
            ]
        )
        if path.endswith("/host-workflow/run"):
            specs.extend(
                [
                    ("engine", "engine", "rp_stage_dag", "host_workflow_engine", "host_workflow_engine"),
                    ("retry_stage", "retry_stage", "rp_stage_state", "host_workflow_retry_stage", "host_workflow_retry_stage"),
                    ("cache_hit_stage", "cache_hit_stage", "rp_stage_state", "host_workflow_cache_hit_stage", "host_workflow_cache_hit_stage"),
                    ("worker_slots", "worker_slots", "rp_stage_state", "host_workflow_worker_slots", "host_workflow_worker_slots"),
                    ("queue_depth", "queue_depth", "rp_stage_state", "host_workflow_queue_depth", "host_workflow_queue_depth"),
                ]
            )
        if path.endswith("/host-workflow/export"):
            specs.extend(
                [
                    ("workflow_export", "bundle", "rp_runner", "host_action_workflow_export", "host_action_workflow_export"),
                    ("workflow_export_format", "format", "rp_runner", "host_action_workflow_export_format", "host_action_workflow_export_format"),
                ]
            )
        if path.endswith("/host-workflow/stage-attempt"):
            specs.extend(
                [
                    ("stage", "stage", "rp_stage_state", "host_workflow_stage_action", "host_workflow_stage_action"),
                    ("attempt", "attempt", "rp_stage_state", "host_workflow_stage_action", "attempt"),
                    ("status", "status", "rp_stage_state", "host_workflow_stage_action", "status"),
                ]
            )
        if path.endswith("/host-workflow/cache-decision"):
            specs.extend(
                [
                    ("cache_key", "cache_key", "rp_cache_index", "host_workflow_cache_action", "key"),
                    ("cache_result", "cache_result", "rp_cache_index", "host_workflow_cache_action", "result"),
                ]
            )
        if path.endswith("/host-workflow/retry-decision"):
            specs.extend(
                [
                    ("retry_stage", "stage", "rp_retry_plan", "host_workflow_retry_action", "host_workflow_retry_action"),
                    ("retry_reason", "retry_reason", "rp_retry_plan", "host_workflow_retry_action", "reason"),
                    ("decision", "decision", "rp_retry_plan", "host_workflow_retry_action", "decision"),
                ]
            )
        if path.endswith("/host-workflow/artifact-manifest"):
            specs.extend(
                [
                    ("artifact", "artifact", "rp_artifact_manifest", "host_workflow_artifact_action", "host_workflow_artifact_action"),
                    ("sha256", "sha256", "rp_artifact_manifest", "host_workflow_artifact_action", "sha256"),
                ]
            )
        if path.endswith("/host-workflow/report-export"):
            specs.extend(
                [
                    ("report", "report", "rp_report_text", "host_workflow_report_action", "host_workflow_report_action"),
                    ("sections", "sections", "rp_report_text", "host_workflow_report_action", "sections"),
                ]
            )
        return specs
    if path.endswith("/research/artifact-input"):
        return [
            ("artifact_input", "file", "rp_artifact_manifest", "host_artifact_manifest_input", "host_artifact_manifest_input"),
            ("sha256", "sha256", "rp_artifact_manifest", "host_artifact_manifest_input", "sha256"),
        ]
    if path.endswith("/research/artifact-derive"):
        return [
            ("artifact_source", "input", "rp_artifact_manifest", "host_artifact_manifest_derive", "host_artifact_manifest_derive"),
            ("artifact_output", "output", "rp_artifact_manifest", "host_artifact_manifest_derive", "output"),
            ("operation", "operation", "rp_artifact_manifest", "host_artifact_manifest_derive", "operation"),
        ]
    if path.endswith("/research/artifact-log"):
        return [
            ("log", "log", "rp_stage_log", "host_artifact_log", "host_artifact_log"),
            ("stage", "stage", "rp_stage_log", "host_artifact_log", "stage"),
        ]
    if path.endswith("/research/artifact-chart"):
        return [
            ("chart", "chart", "rp_chart_data", "host_artifact_chart", "host_artifact_chart"),
            ("points", "points", "rp_chart_data", "host_artifact_chart", "points"),
        ]
    if path.endswith("/research/artifact-package"):
        return [
            ("artifact_package", "package", "rp_artifact_manifest", "host_artifact_manifest_package", "host_artifact_manifest_package"),
            ("manifest", "manifest", "rp_artifact_manifest", "host_artifact_manifest_package", "manifest"),
            ("files", "files", "rp_artifact_manifest", "host_artifact_manifest_package", "files"),
        ]
    if "/workflow-portability/" in path:
        if path.endswith("/workflow-portability/package"):
            return [
                ("import_id", "import_id", "rp_wfio", "host_portability_package_action", "import"),
                ("package", "package", "rp_wfio", "host_portability_package_action", "host_portability_package_action"),
                ("export_format", "export_format", "rp_wfio", "host_portability_package_action", "format"),
            ]
        return [
            ("import_id", "import_id", "rp_wfio", "host_portability_import", "host_portability_import"),
            ("target_runtime", "target_runtime", "rp_wfio", "host_portability_target", "host_portability_target"),
            ("compare_profile", "compare_profile", "rp_wfio", "host_portability_compare_profile", "host_portability_compare_profile"),
            ("package", "package", "rp_wfio", "host_portability_package", "host_portability_package"),
        ]
    if path.endswith("/research/llm-relay-request"):
        return [
            ("request_id", "request_id", "rp_llm_packets", "host_llm_packet_request", "host_llm_packet_request"),
            ("route", "route", "rp_llm_packets", "host_llm_packet_route", "host_llm_packet_route"),
            ("provider", "provider", "rp_llm_req", "host_llm_provider", "host_llm_provider"),
        ]
    if path.endswith("/research/llm-relay-response"):
        return [
            ("request_id", "request_id", "rp_llm_packets", "host_llm_packet_request", "host_llm_packet_request"),
            ("response_id", "response_id", "rp_llm_packets", "host_llm_packet_response", "host_llm_packet_response"),
            ("summary", "summary", "rp_llm_resp", "host_llm_response_summary", "host_llm_response_summary"),
        ]
    if path.endswith("/research/llm-relay-fallback"):
        return [("fallback_case", "case", "rp_llm_fallback", "host_llm_fallback_case", "host_llm_fallback_case")]
    if path.endswith("/research/review"):
        return [
            ("reviewer", "reviewer", "rp_report_text", "host_report_reviewer", "host_report_reviewer"),
            ("decision", "decision", "rp_report_text", "host_report_review_decision", "host_report_review_decision"),
        ]
    if path.endswith("/research/revision-task"):
        return [("targets", "targets", "rp_report_text", "host_report_revision_targets", "host_report_revision_targets")]
    if path.endswith("/research/run-revision-task"):
        return [("task_id", "task_id", "rp_revision", "host_action_revision_task_id", "host_action_revision_task_id")]
    if path.endswith("/research/export-bundle"):
        return [("bundle", "bundle", "rp_report_text", "host_report_bundle", "host_report_bundle")]
    if path.endswith("/agentcompare/run"):
        return [("profile", "profile", "rp_report_text", "host_report_compare_profile", "host_report_compare_profile")]
    if path.endswith("/research/project-release-gate"):
        return [("decision", "decision", "rp_web_bundle", "host_action_project_release_gate", "host_action_project_release_gate")]
    if path.endswith("/research/project-delivery"):
        return [("bundle", "bundle", "rp_web_bundle", "host_action_project_delivery", "host_action_project_delivery")]
    if path.endswith("/research/package-intake"):
        return [("label", "label", "rp_web_bundle", "host_action_project_package_intake", "host_action_project_package_intake")]
    if "/workbench" in path or path.endswith("/research/export-workbench"):
        specs.append(("workbench", "workbench", "rp_report_text", "host_report_workbench", "host_report_workbench"))
        specs.append(("workbench_id", "workbench_id", "rp_report_text", "host_report_workbench", "host_report_workbench"))
        specs.append(("question", "question", "rp_report_text", "host_report_workbench_question", "host_report_workbench_question"))
        specs.append(("task", "task", "rp_report_text", "host_report_workbench_task", "host_report_workbench_task"))
        specs.append(("note_title", "title", "rp_report_text", "host_report_workbench_note_title", "host_report_workbench_note_title"))
        specs.append(("manifest", "manifest", "rp_report_text", "host_report_workbench_manifest", "host_report_workbench_manifest"))
        specs.append(("bundle", "bundle", "rp_report_text", "host_report_workbench_bundle", "host_report_workbench_bundle"))
        return specs
    return specs


def state_key_value(state: dict[str, dict[str, object]], source_file: str, line_key: str, value_key: str) -> tuple[str, str]:
    prefix = line_key + "="
    for line in state_lines(state, source_file):
        stripped = line.strip()
        parsed = parse_kv_record(stripped)
        if stripped.startswith(prefix):
            if value_key in parsed:
                return parsed[value_key], stripped
            return stripped[len(prefix) :], stripped
        if line_key in parsed:
            if value_key in parsed:
                return parsed[value_key], stripped
            return parsed[line_key], stripped
    return "", ""


def action_delta_status(requested: str, observed: str) -> str:
    if not requested:
        return "not_requested"
    if not observed:
        return "missing"
    if requested == observed or requested in observed:
        return "matched"
    return "different"


def action_delta_rows(state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in actions:
        path = str(record.get("path", ""))
        group = action_trace_group(path)
        payload = record.get("payload", {})
        if not group or group not in groups or not isinstance(payload, dict):
            continue
        for field, payload_key, source_file, line_key, value_key in action_delta_specs(path):
            requested = str(payload.get(payload_key, ""))
            if not requested:
                continue
            observed, source_line = state_key_value(state, source_file, line_key, value_key)
            rows.append(
                {
                    "action_delta": str(record.get("sequence", "")),
                    "group": group,
                    "path": path,
                    "field": field,
                    "requested": requested,
                    "observed": observed,
                    "state_file": source_file,
                    "source_key": value_key if value_key == line_key else line_key + "." + value_key,
                    "source_line": source_line,
                    "status": action_delta_status(requested, observed),
                }
            )
    return rows


def action_delta_panel(title: str, state: dict[str, dict[str, object]], actions: list[dict[str, object]], groups: set[str]) -> str:
    return render_record_panel(
        title,
        [
            ("Sequence", "action_delta"),
            ("Group", "group"),
            ("Path", "path"),
            ("Field", "field"),
            ("Requested", "requested"),
            ("Observed", "observed"),
            ("State File", "state_file"),
            ("Source Key", "source_key"),
            ("Status", "status"),
        ],
        action_delta_rows(state, actions, groups),
        "No related host action delta rows",
    )


def default_batch_payload() -> str:
    actions = {
        "actions": [
            {"path": "/actions/research/run", "payload": {"run_id": "RUN-WEB", "source": "reader-ui"}},
            {"path": "/actions/research/studio-launch", "payload": {"title": "Studio evidence review", "goal": "Turn pasted materials into a workbench answer", "direction": "evidence review", "material_notes": "Browser supplied notes and table rows.", "provider_id": "template", "workbench_id": "W1", "latest_run_id": "RUN-WEB", "latest_answer_id": "answer-web"}},
            {"path": "/actions/research/dataset-preview", "payload": {"dataset_id": "usable-dataset:response-table", "rows": "7", "quality": "pass"}},
            {"path": "/actions/research/dataset-visualization", "payload": {"dataset_id": "usable-dataset:response-table", "chart": "response-chart.svg", "x_field": "sample", "y_field": "value", "group_field": "group", "points": "7"}},
            {"path": "/actions/research/dataset-card", "payload": {"dataset_id": "usable-dataset:response-table", "readiness": "ready", "warnings": "0"}},
            {"path": "/actions/research/dataset-answer", "payload": {"dataset_id": "usable-dataset:response-table", "question": "Which group is stronger?", "answer": "treatment"}},
            {"path": "/actions/research/dataset-run", "payload": {"dataset_id": "usable-dataset:response-table", "run_id": "usable-run:dataset:web", "provider_id": "template", "question": "Which group is stronger?", "artifacts": "5"}},
            {"path": "/actions/research/dataset-run-comparison", "payload": {"dataset_id": "usable-dataset:response-table", "left_run": "usable-run:dataset:base", "right_run": "usable-run:dataset:web", "decision": "stable"}},
            {"path": "/actions/research/dataset-portfolio", "payload": {"dataset_id": "usable-dataset:response-table", "filter": "ready", "datasets": "3", "ready": "3"}},
            {"path": "/actions/research/source-portfolio", "payload": {"source_id": "usable-source:library2026:1", "query": "agent provenance", "sources": "42", "reviewed": "8"}},
            {"path": "/actions/research/sample-workbench", "payload": {"workbench_id": "usable-workbench:sample-web", "template_id": "usable-template:workspace-900", "dataset_id": "usable-dataset:response-table", "question": "What is ready for review?"}},
            {"path": "/actions/research/study-protocol", "payload": {"protocol_id": "usable-study-protocol:web", "title": "Web protocol", "question": "Which group is stronger?", "hypothesis": "treatment is stronger", "dataset_tags": "response", "source_tags": "agent"}},
            {"path": "/actions/research/run-study-protocol", "payload": {"protocol_id": "usable-study-protocol:web", "run_id": "usable-study-protocol-run:web", "provider_id": "template"}},
            {"path": "/actions/research/study-protocol-compliance", "payload": {"run_id": "usable-study-protocol-run:web", "decision": "pass", "findings": "0"}},
            {"path": "/actions/research/study-protocol-bundle", "payload": {"run_id": "usable-study-protocol-run:web", "bundle": "study-protocol-web.zip", "files": "8"}},
            {"path": "/actions/research/study-protocol-launch", "payload": {"launch_id": "study-protocol-launch:web", "protocol_id": "usable-study-protocol:web", "run_id": "usable-study-protocol-run:web", "provider_id": "template"}},
            {"path": "/actions/research/study-protocol-launch-rerun", "payload": {"launch_id": "study-protocol-launch:web", "rerun_id": "study-protocol-rerun:web", "provider_id": "template"}},
            {"path": "/actions/research/study-protocol-launch-comparison", "payload": {"launch_id": "study-protocol-launch:web", "left": "launch:web:base", "right": "launch:web:rerun", "changed_metrics": "0"}},
            {"path": "/actions/research/study-protocol-reproduction-package", "payload": {"launch_id": "study-protocol-launch:web", "package_id": "study-protocol-reproduction-package:web", "files": "8", "notebooks": "2", "datasets": "2"}},
            {"path": "/actions/research/study-protocol-reproduction-package-review", "payload": {"package_id": "study-protocol-reproduction-package:web", "decision": "approved", "reviewer": "Wang"}},
            {"path": "/actions/research/study-protocol-reproduction-package-action-plan", "payload": {"package_id": "study-protocol-reproduction-package:web", "steps": "5", "owner": "recovery"}},
            {"path": "/actions/research/study-protocol-reproduction-package-action-execute", "payload": {"package_id": "study-protocol-reproduction-package:web", "steps_done": "5", "result": "passed", "provider_id": "template"}},
            {"path": "/actions/research/review", "payload": {"run_id": "RUN-WEB", "reviewer": "Wang", "decision": "needs_revision"}},
            {"path": "/actions/research/revision-task", "payload": {"review_id": "usable-review:Wang:1", "targets": "methods,chart_caption,statistics"}},
            {"path": "/actions/research/run-revision-task", "payload": {"run_id": "RUN-WEB", "task_id": "usable-revision-task:RUN-WEB:1"}},
            {"path": "/actions/research/workbench", "payload": {"workbench": "usable-workbench:RUN-WEB", "workbench_title": "RUN-WEB workbench", "literature_query": "agent workflow provenance"}},
            {"path": "/actions/research/workbench-answer", "payload": {"workbench": "usable-workbench:RUN-WEB", "question": "What is ready for review?"}},
            {"path": "/actions/research/workbench-evidence-search", "payload": {"workbench": "usable-workbench:RUN-WEB", "query": "recovery evidence"}},
            {"path": "/actions/research/workbench-task", "payload": {"workbench": "usable-workbench:RUN-WEB", "task": "human_review", "status": "waiting"}},
            {"path": "/actions/research/workbench-note", "payload": {"workbench": "usable-workbench:RUN-WEB", "note_kind": "decision", "title": "Scope decision", "body": "Use recovered evidence first."}},
            {"path": "/actions/research/workbench-brief", "payload": {"workbench": "usable-workbench:RUN-WEB", "brief_format": "html"}},
            {"path": "/actions/research/workbench-evidence-dossier", "payload": {"workbench": "usable-workbench:RUN-WEB", "dossier_format": "markdown"}},
            {"path": "/actions/research/workbench-evidence-graph", "payload": {"workbench": "usable-workbench:RUN-WEB", "graph_format": "dot"}},
            {"path": "/actions/research/workbench-citations", "payload": {"workbench": "usable-workbench:RUN-WEB", "citation_format": "bibtex"}},
            {"path": "/actions/research/workbench-manuscript", "payload": {"workbench": "usable-workbench:RUN-WEB", "manuscript_format": "markdown"}},
            {"path": "/actions/research/workbench-manuscript-audit", "payload": {"workbench": "usable-workbench:RUN-WEB", "audit_scope": "citations"}},
            {"path": "/actions/research/workbench-manuscript-revision-plan", "payload": {"workbench": "usable-workbench:RUN-WEB", "revision_area": "methods"}},
            {"path": "/actions/research/workbench-manuscript-revision-task", "payload": {"workbench": "usable-workbench:RUN-WEB", "revision_task": "1", "revision_status": "done"}},
            {"path": "/actions/research/workbench-task-board", "payload": {"workbench": "usable-workbench:RUN-WEB", "board_filter": "open"}},
            {"path": "/actions/research/workbench-task-board-row", "payload": {"workbench": "usable-workbench:RUN-WEB", "row_id": "usable-workbench:RUN-WEB:board:task:human_review", "row_status": "done"}},
            {"path": "/actions/research/workbench-notes", "payload": {"workbench": "usable-workbench:RUN-WEB", "notes_filter": "decision"}},
            {"path": "/actions/research/workbench-runbook", "payload": {"workbench": "usable-workbench:RUN-WEB", "runbook_format": "markdown"}},
            {"path": "/actions/research/workbench-timeline", "payload": {"workbench": "usable-workbench:RUN-WEB", "timeline_format": "html"}},
            {"path": "/actions/research/workbench-file-manifest", "payload": {"workbench": "usable-workbench:RUN-WEB", "manifest": "delivery-manifest.json", "files": "9", "sha_records": "9"}},
            {"path": "/actions/research/workbench-file-verify", "payload": {"workbench": "usable-workbench:RUN-WEB", "manifest": "delivery-manifest.json", "files": "9", "sha_records": "9", "verified": "9", "missing": "0"}},
            {"path": "/actions/research/workbench-handoff-package", "payload": {"workbench": "usable-workbench:RUN-WEB", "handoff_scope": "full"}},
            {"path": "/actions/research/workbench-complete", "payload": {"workbench": "usable-workbench:RUN-WEB", "review_decision": "approved"}},
            {"path": "/actions/research/export-workbench", "payload": {"workbench": "usable-workbench:RUN-WEB", "bundle": "workbench-bundle.zip"}},
            {"path": "/actions/research/project-scaffold", "payload": {"template_id": "scaffold-template:dataset-review", "project_id": "reader-project", "title": "Reader project", "dataset_id": "dataset-reader", "library_source_id": "library-reader", "files": "9", "workspace": "workspace/reader-project"}},
            {"path": "/actions/research/project-launch", "payload": {"project_id": "reader-project", "scaffold_id": "scaffold:reader-project:dataset-review", "workbench_id": "usable-workbench:reader-project", "run_id": "usable-run:reader-project", "provider_id": "template", "question": "Is the reader project ready?"}},
            {"path": "/actions/research/project-action-execute", "payload": {"project_id": "reader-project", "action_id": "usable-project-action:reader-project:1", "action_key": "build_reproduction_package", "provider_id": "template", "max_steps": "5", "result": "completed"}},
            {"path": "/actions/research/project-handoff-audit", "payload": {"project_id": "lab-gene-x", "scope": "full", "decision": "ready"}},
            {"path": "/actions/research/project-release-gate", "payload": {"project_id": "lab-gene-x", "decision": "release", "checks": "6", "required_actions": "0", "suggested_actions": "2"}},
            {"path": "/actions/research/project-snapshot", "payload": {"project_id": "lab-gene-x", "snapshot_id": "project-snapshot:lab-gene-x:1", "files": "11", "hash_records": "11", "changes": "0"}},
            {"path": "/actions/research/project-snapshot-comparison", "payload": {"project_id": "lab-gene-x", "left": "snapshot0", "right": "snapshot1", "changed_files": "0", "decision": "stable"}},
            {"path": "/actions/research/project-reproducibility-audit", "payload": {"project_id": "lab-gene-x", "inputs": "2", "outputs": "8", "notebooks": "2", "claim_audits": "1", "decision": "passed"}},
            {"path": "/actions/research/project-provenance-graph", "payload": {"project_id": "lab-gene-x", "nodes": "9", "edges": "12", "dot": "project-provenance.dot"}},
            {"path": "/actions/research/project-delivery", "payload": {"project_id": "lab-gene-x", "bundle": "project-bundle.zip", "decision": "ready", "release_gate": "release", "handoff": "ready"}},
            {"path": "/actions/research/package-intake", "payload": {"package_id": "external-review", "label": "External review package", "files": "5", "sha256": "checked", "decision": "accepted"}},
            {"path": "/actions/research/export-notebook", "payload": {"run_id": "RUN-WEB", "format": "ipynb"}},
            {"path": "/actions/research/export-bundle", "payload": {"run_id": "RUN-WEB", "bundle": "reviewer-evidence"}},
            {"path": "/actions/research/llm-relay-request", "payload": {"request_id": "llm-web-q1", "run_id": "RUN-WEB", "route": "review_summary", "provider": "host-relay", "prompt": "summarize_recovery_evidence", "budget": "2048", "secret_ref": "host_env"}},
            {"path": "/actions/research/llm-relay-response", "payload": {"request_id": "llm-web-q1", "response_id": "llm-web-r1", "provider": "host-relay", "mode": "template", "summary": "Recovered_evidence_ready", "citations": "5"}},
            {"path": "/actions/research/llm-relay-fallback", "payload": {"case": "missing_cloud_key", "action": "template_response", "reason": "host_env_absent", "fallback_status": "ready"}},
            {"path": "/actions/host-workflow/stage-attempt", "payload": {"workflow_id": "WF1", "run_id": "RUN-WEB", "stage": "clean", "attempt": "2", "status": "failed", "command": "clean_reads", "duration_ms": "1200"}},
            {"path": "/actions/host-workflow/cache-decision", "payload": {"workflow_id": "WF1", "run_id": "RUN-WEB", "stage": "analyze", "cache_key": "cache:WF1:analyze", "cache_result": "hit", "cache_policy": "content"}},
            {"path": "/actions/host-workflow/retry-decision", "payload": {"workflow_id": "WF1", "run_id": "RUN-WEB", "stage": "clean", "retry_reason": "checksum_mismatch", "next_attempt": "3", "decision": "rerun_stage"}},
            {"path": "/actions/host-workflow/artifact-manifest", "payload": {"workflow_id": "WF1", "run_id": "RUN-WEB", "artifact": "clean.metrics.json", "artifact_kind": "metrics", "sha256": "sha-web-wf1", "bytes": "4096"}},
            {"path": "/actions/host-workflow/report-export", "payload": {"workflow_id": "WF1", "run_id": "RUN-WEB", "report": "workflow-report.md", "format": "markdown", "sections": "5", "status": "ready"}},
            {"path": "/actions/research/artifact-input", "payload": {"run_id": "RUN-WEB", "file": "reads_R1.fastq", "artifact_kind": "fastq", "sha256": "sha-web-input", "bytes": "2048", "source": "upload"}},
            {"path": "/actions/research/artifact-derive", "payload": {"run_id": "RUN-WEB", "input": "reads_R1.fastq", "output": "clean_reads.fastq", "operation": "trim", "stage": "clean", "sha256": "sha-web-derived"}},
            {"path": "/actions/research/artifact-log", "payload": {"run_id": "RUN-WEB", "stage": "clean", "log": "clean.log", "level": "warn", "message": "adapter_trimmed"}},
            {"path": "/actions/research/artifact-chart", "payload": {"run_id": "RUN-WEB", "chart": "qc-chart.json", "chart_type": "line", "data_file": "clean.metrics.json", "points": "12"}},
            {"path": "/actions/research/artifact-package", "payload": {"run_id": "RUN-WEB", "package": "artifact-bundle.zip", "manifest": "artifact-manifest.json", "files": "5", "status": "ready"}},
            {"path": "/actions/workflow-portability/run", "payload": {"import_id": "workflow-import:web-nextflow", "source_format": "nextflow", "source": "main.web.nf", "target_runtime": "agentos-ucore", "execution_plan": "workflow-migration-execution-plan:web-nextflow:agentcompare", "compare_profile": "compare-profile:web-nextflow:migration", "scenario_id": "backend-scenario:web-nextflow", "rehearsal_status": "passed", "readiness_decision": "ready_for_agentos", "package": "workflow-portability-web.zip"}},
            {"path": "/actions/workflow-portability/import", "payload": {"import_id": "workflow-import:web-nextflow", "source_format": "nextflow", "source": "main.web.nf", "normalized_steps": "15", "adapter_id": "adapter:web-nextflow"}},
            {"path": "/actions/workflow-portability/plan", "payload": {"import_id": "workflow-import:web-nextflow", "migration_plan": "workflow-migration-plan:web-nextflow", "target_runtime": "agentos-ucore", "migration_steps": "9", "risk_items": "4"}},
            {"path": "/actions/workflow-portability/bind", "payload": {"execution_plan": "workflow-migration-execution-plan:web-nextflow:agentcompare", "compare_profile": "compare-profile:web-nextflow:migration", "scenario_id": "backend-scenario:web-nextflow", "backend_cases": "4"}},
            {"path": "/actions/workflow-portability/rehearse", "payload": {"rehearsal_id": "workflow-rehearsal:web-nextflow", "binding_id": "workflow-migration-binding:web-nextflow", "rehearsal_status": "passed", "observed_ready": "3", "skipped": "1"}},
            {"path": "/actions/workflow-portability/review", "payload": {"review_id": "workflow-migration-readiness:web-nextflow", "readiness_decision": "ready_for_agentos", "blocking_items": "0", "work_items": "6"}},
            {"path": "/actions/workflow-portability/package", "payload": {"import_id": "workflow-import:web-nextflow", "package": "workflow-portability-web.zip", "export_format": "zip", "bundle": "workflow-portability-web.zip"}},
            {"path": "/actions/agentcompare/run", "payload": {"profile": "plain_ucore_batch"}},
        ]
    }
    return json.dumps(actions, indent=2)


def render_action_panel() -> str:
    return """<section class='panel action-panel'>
  <h2>Batch Actions</h2>
  <textarea id='batch-payload' spellcheck='false'>{payload}</textarea>
  <div class='action-row'>
    <button type='button' onclick='sendBatch()'>Run Batch</button>
    <output id='batch-status'>idle</output>
  </div>
</section>""".format(payload=html.escape(default_batch_payload()))


def render_overview(
    file_name: str,
    state: dict[str, dict[str, object]],
    contract: dict[str, object],
    action_count: int,
    last_run: dict[str, object],
) -> str:
    last_status = str(last_run.get("status", "none"))
    common = [
        ("State Files", len(state), "plain uCore output"),
        ("Host Actions", action_count, "reader log"),
        ("Last Run", last_status, "QEMU path"),
    ]
    page_metrics: dict[str, list[tuple[str, object, str]]] = {
        "index.html": [
            ("Contract", contract.get("contract", ""), "rp_web_bundle"),
            ("Views", contract.get("views", ""), "reader contract"),
            ("Dynamic Inputs", metric_value(state, [("rp_web_bundle", "dynamic_inputs"), ("rp_input", "dynamic_submissions")]), "rp_input"),
        ],
        "run.html": [
            ("Run Status", metric_value(state, [("rp_runner", "status"), ("rp_api_run", "status")]), "rp_runner"),
            ("Workbench Tasks", metric_value(state, [("rp_runner", "workbench_tasks")]), "rp_runner"),
            ("Artifacts", metric_value(state, [("rp_runner", "host_action_artifacts"), ("rp_package", "artifacts")]), "rp_package"),
        ],
        "workflow.html": [
            ("Workflow", metric_value(state, [("rp_stage_state", "host_workflow_id"), ("rp_plan", "workflow")]), "rp_stage_state"),
            ("Run", metric_value(state, [("rp_stage_state", "host_workflow_run_id")]), "rp_stage_state"),
            ("Retry", metric_value(state, [("rp_retry_plan", "host_workflow_retry_stage"), ("rp_retry_plan", "retry_stage")]), "rp_retry_plan"),
            ("Events", metric_value(state, [("rp_execobs", "host_workflow_observer_events"), ("rp_run_events", "events")]), "rp_execobs"),
        ],
        "workbench.html": [
            ("Workbench", metric_value(state, [("rp_runner", "host_action_workbench_id"), ("rp_report_text", "host_report_workbench"), ("rp_uresrun", "host_action_workbench")]), "rp_runner"),
            ("Task", metric_value(state, [("rp_runner", "host_action_workbench_task"), ("rp_report_text", "host_report_workbench_task")]), "rp_runner"),
            ("Manifest", metric_value(state, [("rp_package", "host_action_workbench_manifest"), ("rp_report_text", "host_report_workbench_manifest")]), "rp_package"),
            ("Bundle", metric_value(state, [("rp_package", "host_action_workbench_bundle"), ("rp_report_text", "host_report_workbench_bundle")]), "rp_package"),
        ],
        "studio.html": [
            ("Sessions", metric_value(state, [("rp_studio", "sessions")]), "rp_studio"),
            ("Latest Session", metric_value(state, [("rp_studio", "latest_session"), ("rp_studio", "host_action_studio_session")]), "rp_studio"),
            ("Title", metric_value(state, [("rp_studio", "host_action_studio_title")]), "rp_studio"),
            ("Goal", metric_value(state, [("rp_studio", "host_action_studio_goal")]), "rp_studio"),
        ],
        "agents.html": [
            ("Agents", metric_value(state, [("rp_agents", "agents"), ("rp_api_agents", "agents")]), "rp_agents"),
            ("Decisions", metric_value(state, [("rp_decisions", "decisions")]), "rp_decisions"),
            ("Messages", metric_value(state, [("rp_handoff", "handoffs"), ("rp_mail", "messages")]), "role files"),
        ],
        "evidence.html": [
            ("Claims", metric_value(state, [("rp_evidence", "claims"), ("rp_api_evidence", "claims")]), "rp_evidence"),
            ("Delivery Files", metric_value(state, [("rp_package", "delivery_files")]), "rp_package"),
            ("Review Comments", metric_value(state, [("rp_review2", "comments")]), "rp_review2"),
        ],
        "compare.html": [
            ("Plain Kernel", metric_value(state, [("rp_agentcmp", "plain_kernel"), ("rp_api_compare", "plain_kernel")]), "rp_agentcmp"),
            ("Checks", metric_value(state, [("rp_consistency", "checks")]), "rp_consistency"),
            ("Portability Checks", metric_value(state, [("rp_agentcmp", "workflow_portability_checks")]), "rp_agentcmp"),
            ("Backend Checks", metric_value(state, [("rp_agentcmp", "portability_backend_checks")]), "rp_agentcmp"),
            ("Backend Runner", metric_value(state, [("rp_agentcmp", "backend_runner_checks")]), "rp_agentcmp"),
            ("Coherence Plane", metric_value(state, [("rp_agentcmp", "coherence_plane_checks"), ("rp_coherence", "coherence_checks")]), "rp_coherence"),
            ("Publication", metric_value(state, [("rp_agentcmp", "publication_checks"), ("rp_publication", "publication_checks")]), "rp_publication"),
            ("Analysis Results", metric_value(state, [("rp_agentcmp", "analysis_results_checks"), ("rp_analysisres", "analysis_results_checks")]), "rp_analysisres"),
            ("Decision Support", metric_value(state, [("rp_agentcmp", "decision_support_checks"), ("rp_decsupport", "decision_support_checks")]), "rp_decsupport"),
            ("Usable Research", metric_value(state, [("rp_agentcmp", "usable_research_checks"), ("rp_usable", "usable_research_checks")]), "rp_usable"),
            ("Mature Capability", metric_value(state, [("rp_agentcmp", "mature_capability_checks"), ("rp_mature", "capability_checks")]), "rp_mature"),
            ("QEMU", metric_value(state, [("rp_host_run_result", "qemu_orch_passed")]), "rp_host_run_result"),
        ],
        "artifacts.html": [
            ("Manifest Records", metric_value(state, [("rp_artifact_manifest", "manifest_records"), ("rp_api_artifacts", "manifest_records")]), "rp_artifact_manifest"),
            ("Real Items", metric_value(state, [("rp_artifact_manifest", "real_artifact_items"), ("rp_package", "real_artifact_items")]), "rp_package"),
            ("Provenance", metric_value(state, [("rp_consistency", "artifact_provenance")]), "rp_consistency"),
            ("Path Steps", metric_value(state, [("rp_consistency", "artifact_path_rebuild_steps")]), "rp_consistency"),
            ("Package", metric_value(state, [("rp_package", "status")]), "rp_package"),
        ],
        "delivery.html": [
            ("Files", metric_value(state, [("rp_package", "delivery_files")]), "rp_package"),
            ("Checks", metric_value(state, [("rp_web_bundle", "delivery_checks")]), "rp_web_bundle"),
            ("Evidence", metric_value(state, [("rp_package", "evidence_bundle_entries")]), "rp_package"),
            ("Downloads", metric_value(state, [("rp_web_bundle", "downloadable_units")]), "rp_web_bundle"),
        ],
        "data.html": [
            ("Submissions", metric_value(state, [("rp_input", "dynamic_submissions")]), "rp_input"),
            ("Snapshots", metric_value(state, [("rp_dataset_snapshot", "snapshots"), ("rp_api_data", "dataset_snapshots")]), "rp_dataset_snapshot"),
            ("Quality", metric_value(state, [("rp_data_quality", "passed")]), "rp_data_quality"),
        ],
        "project.html": [
            ("Project", metric_value(state, [("rp_package", "host_action_project_id"), ("rp_runner", "host_action_project_id")]), "rp_package"),
            ("Space", metric_value(state, [("rp_package", "host_action_project_space"), ("rp_runner", "host_action_project_space")]), "rp_package"),
            ("Action Item", metric_value(state, [("rp_package", "host_action_project_action_item"), ("rp_runner", "host_action_project_action_item")]), "rp_package"),
            ("Search", metric_value(state, [("rp_package", "host_action_research_search"), ("rp_runner", "host_action_research_search")]), "rp_package"),
        ],
        "project-review.html": [
            ("Project", metric_value(state, [("rp_web_bundle", "project"), ("rp_package", "host_action_project_id")]), "rp_web_bundle"),
            ("Release Gate", metric_value(state, [("rp_web_bundle", "host_action_project_release_gate"), ("rp_web_bundle", "release_gate")]), "rp_web_bundle"),
            ("Snapshot", metric_value(state, [("rp_web_bundle", "host_action_project_snapshot"), ("rp_web_bundle", "project_snapshot")]), "rp_web_bundle"),
            ("Reproducibility", metric_value(state, [("rp_web_bundle", "host_action_project_reproducibility"), ("rp_web_bundle", "reproducibility_audit")]), "rp_web_bundle"),
            ("Provenance", metric_value(state, [("rp_web_bundle", "host_action_project_provenance_graph"), ("rp_web_bundle", "provenance_graph")]), "rp_web_bundle"),
            ("Delivery", metric_value(state, [("rp_web_bundle", "host_action_project_delivery"), ("rp_web_bundle", "project_delivery")]), "rp_web_bundle"),
        ],
        "usable-research.html": [
            ("Checks", metric_value(state, [("rp_usable", "usable_research_checks"), ("rp_agentcmp", "usable_research_checks")]), "rp_usable"),
            ("Templates", metric_value(state, [("rp_usable", "templates"), ("rp_usabletpl", "templates")]), "rp_usabletpl"),
            ("Datasets", metric_value(state, [("rp_usable", "datasets"), ("rp_usableds", "datasets")]), "rp_usableds"),
            ("Library Sources", metric_value(state, [("rp_usable", "library_sources"), ("rp_usablelib", "library_sources")]), "rp_usablelib"),
            ("DAG Stages", metric_value(state, [("rp_usable", "dag_stages")]), "rp_usabledag"),
        ],
        "services.html": [
            ("Bio Ops", metric_value(state, [("rp_bioop", "ops")]), "rp_bioop"),
            ("Lab Ops", metric_value(state, [("rp_labresop", "ops")]), "rp_labresop"),
            ("Publication Ops", metric_value(state, [("rp_pubop", "ops")]), "rp_pubop"),
            ("Knowledge Ops", metric_value(state, [("rp_knowop", "ops")]), "rp_knowop"),
            ("Runtime Ops", metric_value(state, [("rp_runop", "ops")]), "rp_runop"),
        ],
        "publication.html": [
            ("Checks", metric_value(state, [("rp_publication", "publication_checks")]), "rp_publication"),
            ("Submissions", metric_value(state, [("rp_publication", "submissions")]), "rp_publication"),
            ("Reviews", metric_value(state, [("rp_publication", "review_rounds")]), "rp_publication"),
            ("Responses", metric_value(state, [("rp_peerresp", "packages"), ("rp_publication", "response_packages")]), "rp_peerresp"),
            ("Decisions", metric_value(state, [("rp_publication", "decisions")]), "rp_publication"),
        ],
        "model-registry.html": [
            ("Models", metric_value(state, [("rp_modelreg", "registered_models")]), "rp_modelreg"),
            ("Versions", metric_value(state, [("rp_modelver", "version")]), "rp_modelver"),
            ("Evaluation", metric_value(state, [("rp_modeleval", "status")]), "rp_modeleval"),
            ("Deployment", metric_value(state, [("rp_modeldep", "status")]), "rp_modeldep"),
            ("Serving", metric_value(state, [("rp_modelserve", "status")]), "rp_modelserve"),
        ],
        "systematic-review.html": [
            ("Checks", metric_value(state, [("rp_sysreview", "systematic_review_checks")]), "rp_sysreview"),
            ("Protocol", metric_value(state, [("rp_sysreview", "protocol")]), "rp_sysreview"),
            ("Search Results", metric_value(state, [("rp_syssearch", "results")]), "rp_syssearch"),
            ("Screening", metric_value(state, [("rp_sysscreen", "screening_decisions")]), "rp_sysscreen"),
            ("Included", metric_value(state, [("rp_sysprisma", "included")]), "rp_sysprisma"),
        ],
        "experiment-schedule.html": [
            ("Checks", metric_value(state, [("rp_expsched", "experiment_scheduling_checks")]), "rp_expsched"),
            ("Schedule", metric_value(state, [("rp_expsched", "schedule")]), "rp_expsched"),
            ("Tasks", metric_value(state, [("rp_schedtask", "tasks")]), "rp_schedtask"),
            ("Bookings", metric_value(state, [("rp_schedbook", "bookings")]), "rp_schedbook"),
            ("Conflicts", metric_value(state, [("rp_schedconf", "conflicts")]), "rp_schedconf"),
        ],
        "training-compliance.html": [
            ("Checks", metric_value(state, [("rp_traincomp", "training_compliance_checks")]), "rp_traincomp"),
            ("Requirements", metric_value(state, [("rp_traincomp", "requirements"), ("rp_trainreq", "requirements")]), "rp_trainreq"),
            ("Records", metric_value(state, [("rp_traincomp", "training_records"), ("rp_trainrec", "records")]), "rp_trainrec"),
            ("Competency", metric_value(state, [("rp_traincomp", "competency_assessments"), ("rp_trainassess", "assessments")]), "rp_trainassess"),
            ("Authorizations", metric_value(state, [("rp_traincomp", "active_authorizations"), ("rp_trainauth", "active_authorizations")]), "rp_trainauth"),
            ("Open Gaps", metric_value(state, [("rp_traincomp", "open_gaps"), ("rp_traingap", "open")]), "rp_traingap"),
        ],
        "analysis-results.html": [
            ("Checks", metric_value(state, [("rp_analysisres", "analysis_results_checks")]), "rp_analysisres"),
            ("Plans", metric_value(state, [("rp_analysisres", "analysis_plans"), ("rp_anplan", "plans")]), "rp_anplan"),
            ("Runs", metric_value(state, [("rp_analysisres", "analysis_runs"), ("rp_anrun", "runs")]), "rp_anrun"),
            ("Tables", metric_value(state, [("rp_analysisres", "result_tables"), ("rp_resulttbl", "tables")]), "rp_resulttbl"),
            ("Statistics", metric_value(state, [("rp_analysisres", "statistical_results"), ("rp_statres", "statistics")]), "rp_statres"),
            ("Figures", metric_value(state, [("rp_analysisres", "figures"), ("rp_anfig", "figures")]), "rp_anfig"),
        ],
        "decision-support.html": [
            ("Checks", metric_value(state, [("rp_decsupport", "decision_support_checks")]), "rp_decsupport"),
            ("Options", metric_value(state, [("rp_decsupport", "options"), ("rp_decopt", "options")]), "rp_decopt"),
            ("Criteria", metric_value(state, [("rp_decsupport", "criteria"), ("rp_deccrit", "criteria")]), "rp_deccrit"),
            ("Scores", metric_value(state, [("rp_decsupport", "scores"), ("rp_decscore", "scores")]), "rp_decscore"),
            ("Selected", metric_value(state, [("rp_decsupport", "recommended_option")]), "rp_decsupport"),
            ("Hybrid Score", metric_value(state, [("rp_decsupport", "weighted_score_agentos_ucore_hybrid")]), "rp_decsupport"),
        ],
        "mature.html": [
            ("Profiles", metric_value(state, [("rp_mature", "reference_platforms"), ("rp_mature_refs", "profiles")]), "rp_mature"),
            ("Mappings", metric_value(state, [("rp_mature", "capability_mappings"), ("rp_mature_map", "mappings")]), "rp_mature_map"),
            ("Checks", metric_value(state, [("rp_mature", "capability_checks"), ("rp_mature_checks", "checks")]), "rp_mature_checks"),
            ("Errors", metric_value(state, [("rp_mature", "errors"), ("rp_mature_checks", "errors")]), "rp_mature_checks"),
        ],
        "review.html": [
            ("Sections", metric_value(state, [("rp_review_dashboard", "sections")]), "rp_review_dashboard"),
            ("Decision", metric_value(state, [("rp_review_dashboard", "decision")]), "rp_review_dashboard"),
            ("Pack", metric_value(state, [("rp_review_pack", "pack")]), "rp_review_pack"),
            ("Handoff Checks", metric_value(state, [("rp_agentcmp", "review_handoff_checks")]), "rp_agentcmp"),
            ("Delivery", metric_value(state, [("rp_package", "latest_delivery_status"), ("rp_package", "status")]), "rp_package"),
        ],
        "llm.html": [
            ("Requests", metric_value(state, [("rp_llm_resp", "requests"), ("rp_llmq", "queued")]), "rp_llm_resp"),
            ("Quality", metric_value(state, [("rp_llmeval", "host_relay_eval_batch"), ("rp_llmeval", "passed")]), "rp_llmeval"),
            ("Guard", metric_value(state, [("rp_llm_guard", "host_relay_guard_batch"), ("rp_llm_guard", "secret_scan")]), "rp_llm_guard"),
            ("Delivery Checks", metric_value(state, [("rp_agentcmp", "llm_delivery_checks")]), "rp_agentcmp"),
        ],
        "coherence.html": [
            ("Checks", metric_value(state, [("rp_coherence", "coherence_checks")]), "rp_coherence"),
            ("Delivery", metric_value(state, [("rp_coherence", "delivery_checks")]), "rp_coherence"),
            ("Run State", metric_value(state, [("rp_coherence", "run_state_checks")]), "rp_coherence"),
            ("Tool Protocol", metric_value(state, [("rp_coherence", "tool_protocol_checks")]), "rp_coherence"),
        ],
        "actions.html": [
            ("Configured Actions", metric_value(state, [("rp_api_action", "actions"), ("rp_web_bundle", "reader_actions")]), "rp_api_action"),
            ("Action Source", metric_value(state, [("rp_actionio", "host_action_source"), ("rp_web_bundle", "host_action_source")]), "rp_actionio"),
            ("uCore Result", metric_value(state, [("rp_host_run_result", "status")]), "rp_host_run_result"),
        ],
    }
    return render_metric_cards(page_metrics.get(file_name, []) + common)


def page_html(title: str, nav: str, sections: list[str]) -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; color: #1d2733; background: #f5f7fa; }}
    .app {{ min-height: 100vh; display: grid; grid-template-columns: 220px minmax(0, 1fr); }}
    .sidebar {{ background: #12343b; color: white; padding: 20px 14px; }}
    .brand {{ font-size: 18px; font-weight: 700; margin: 0 0 18px; }}
    nav {{ display: grid; gap: 6px; }}
    nav a {{ color: #d6f5ef; text-decoration: none; font-weight: 700; padding: 9px 10px; border-radius: 6px; }}
    nav a.active {{ background: #2b7a78; color: white; }}
    main {{ padding: 22px 28px 40px; max-width: 1320px; width: 100%; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 0 18px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    header p {{ margin: 4px 0 0; color: #5c6f82; }}
    .badge {{ background: #def7ec; color: #0f5132; border: 1px solid #a7e3c1; border-radius: 999px; padding: 6px 10px; font-weight: 700; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 0 0 16px; }}
    .metric {{ background: white; border: 1px solid #d9e2ec; border-radius: 6px; padding: 13px; min-height: 96px; }}
    .metric span {{ color: #52616f; font-size: 13px; }}
    .metric strong {{ display: block; font-size: 24px; margin: 8px 0 6px; overflow-wrap: anywhere; }}
    .metric small {{ color: #6b7c8f; }}
    .panel {{ background: white; border: 1px solid #d9e2ec; border-radius: 6px; padding: 16px; margin: 0 0 16px; overflow-x: auto; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }}
    .summary-item {{ border: 1px solid #d9e2ec; border-radius: 6px; padding: 11px; background: #fbfdff; }}
    .summary-item span {{ color: #52616f; font-size: 13px; }}
    .summary-item strong {{ display: block; margin: 7px 0 5px; font-size: 17px; overflow-wrap: anywhere; }}
    .summary-item small {{ color: #6b7c8f; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th {{ width: 240px; text-align: left; color: #334e68; background: #f0f4f8; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 7px 9px; vertical-align: top; }}
    code {{ background: #f0f4f8; padding: 2px 4px; border-radius: 3px; }}
    textarea {{ width: 100%; min-height: 220px; resize: vertical; font: 13px Consolas, monospace; border: 1px solid #bcccdc; border-radius: 6px; padding: 10px; }}
    .action-row {{ display: flex; align-items: center; gap: 12px; margin-top: 10px; }}
    button {{ border: 0; background: #2b7a78; color: white; border-radius: 6px; padding: 9px 14px; font-weight: 700; cursor: pointer; }}
    output {{ color: #334e68; font-weight: 700; }}
    @media (max-width: 760px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: static; }}
      main {{ padding: 18px; }}
      header {{ align-items: flex-start; flex-direction: column; }}
    }}
  </style>
  <script>
    async function sendBatch() {{
      const status = document.getElementById('batch-status');
      status.value = 'running';
      try {{
        const response = await fetch('/actions/batch', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: document.getElementById('batch-payload').value
        }});
        const data = await response.json();
        status.value = response.ok ? 'ready: ' + (data.actions || []).length + ' actions' : 'failed';
        if (response.ok) window.location.reload();
      }} catch (error) {{
        status.value = 'failed';
      }}
    }}
  </script>
</head>
<body>
  <div class="app">
    <aside class="sidebar"><p class="brand">Plain uCore Research</p>{nav}</aside>
    <main>
      <header><div><h1>{title}</h1><p>Rendered from plain uCore state files.</p></div><span class="badge">live files</span></header>
      {sections}
    </main>
  </div>
</body>
</html>
""".format(title=html.escape(title), nav=nav, sections="\n".join(sections))


def render_site(
    state_dir: Path,
    out_dir: Path,
    host_platform_alignment_path: Path | None = None,
    host_test_alignment_path: Path | None = None,
    host_surface_alignment_path: Path | None = None,
    seeded_action_state_path: Path | None = None,
) -> dict[str, object]:
    state = load_state(state_dir)
    host_platform_alignment = load_optional_json(host_platform_alignment_path)
    host_test_alignment = load_optional_json(host_test_alignment_path)
    host_surface_alignment = load_optional_json(host_surface_alignment_path)
    seeded_action_state = load_optional_json(seeded_action_state_path)
    render_state = dict(state)
    if host_platform_alignment:
        render_state["host_platform_alignment"] = alignment_state_item("platform", host_platform_alignment)
    if host_test_alignment:
        render_state["host_test_alignment"] = alignment_state_item("tests", host_test_alignment)
    if host_surface_alignment:
        render_state["host_surface_alignment"] = alignment_state_item("surface", host_surface_alignment)
    if seeded_action_state:
        render_state["host_seeded_action"] = seeded_action_state_item(seeded_action_state)
    contract = reader_contract(state)
    problems = validate_contract(contract)
    out_dir.mkdir(parents=True, exist_ok=True)
    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    for old_api in api_dir.glob("*.json"):
        old_api.unlink()
    action_log = out_dir / "host-actions.jsonl"
    actions = read_jsonl_file(action_log)
    last_run = read_json_file(out_dir / "last-run.json")

    for name, item in render_state.items():
        write_json(api_dir / f"{name}.json", {"name": name, "values": item["values"], "lines": item["lines"]})
    extra_api_files = 0
    extra_api_files += write_optional_api(api_dir, "host_platform_alignment_raw", host_platform_alignment)
    extra_api_files += write_optional_api(api_dir, "host_test_alignment_raw", host_test_alignment)
    extra_api_files += write_optional_api(api_dir, "host_surface_alignment_raw", host_surface_alignment)

    for file_name, title, primary, extras in PAGE_SPECS:
        nav = "<nav>{}</nav>".format(
            "".join(
                "<a class='{cls}' href='{href}'>{label}</a>".format(
                    cls="active" if file == file_name else "",
                    href=html.escape(file),
                    label=html.escape(nav_title),
                )
                for file, nav_title, _, _ in PAGE_SPECS
            )
        )
        sections = [render_overview(file_name, render_state, contract, len(actions), last_run)]
        summary_panel = render_page_summary(file_name, render_state)
        if summary_panel:
            sections.append(summary_panel)
        detail_panel = render_detail_panel(file_name, render_state)
        if detail_panel:
            sections.append(detail_panel)
        sections.extend(render_grouped_details(file_name, render_state))
        if file_name == "run.html":
            sections.append(action_trace_panel("Run Action Trace", actions, {"run", "inputs", "workflow", "artifact", "llm", "workbench", "review", "delivery"}))
            sections.append(action_output_panel("Run Action Output Links", render_state, actions, {"run", "inputs", "workflow", "artifact", "llm", "workbench", "review", "delivery"}))
            sections.append(action_output_detail_panel("Run Action Output Details", render_state, actions, {"run", "inputs", "workflow", "artifact", "llm", "workbench", "review", "delivery"}))
            sections.append(action_impact_panel("Run Action Impact", render_state, actions, {"run", "inputs", "workflow", "artifact", "llm", "workbench", "review", "delivery"}))
            sections.append(action_delta_panel("Run Action Delta", render_state, actions, {"run", "inputs", "workflow", "artifact", "llm", "workbench", "review", "delivery"}))
        if file_name == "workflow.html":
            sections.append(action_trace_panel("Workflow Action Trace", actions, {"workflow", "artifact"}))
            sections.append(action_output_panel("Workflow Action Output Links", state, actions, {"workflow", "artifact"}))
            sections.append(action_output_detail_panel("Workflow Action Output Details", state, actions, {"workflow", "artifact"}))
            sections.append(action_impact_panel("Workflow Action Impact", state, actions, {"workflow", "artifact"}))
            sections.append(action_delta_panel("Workflow Action Delta", state, actions, {"workflow", "artifact"}))
        if file_name == "workbench.html":
            sections.append(action_trace_panel("Workbench Action Trace", actions, {"studio", "workbench", "review", "delivery", "operations", "project"}))
            sections.append(action_output_panel("Workbench Action Output Links", state, actions, {"studio", "workbench", "review", "delivery", "operations", "project"}))
            sections.append(action_output_detail_panel("Workbench Action Output Details", state, actions, {"studio", "workbench", "review", "delivery", "operations", "project"}))
            sections.append(action_impact_panel("Workbench Action Impact", state, actions, {"studio", "workbench", "review", "delivery", "operations", "project"}))
            sections.append(action_delta_panel("Workbench Action Delta", state, actions, {"studio", "workbench", "review", "delivery", "operations", "project"}))
        if file_name == "studio.html":
            sections.append(action_trace_panel("Studio Action Trace", actions, {"studio", "workbench", "run", "inputs"}))
            sections.append(action_output_panel("Studio Action Output Links", state, actions, {"studio", "workbench", "run", "inputs"}))
            sections.append(action_output_detail_panel("Studio Action Output Details", state, actions, {"studio", "workbench", "run", "inputs"}))
            sections.append(action_impact_panel("Studio Action Impact", state, actions, {"studio", "workbench", "run", "inputs"}))
            sections.append(action_delta_panel("Studio Action Delta", state, actions, {"studio", "workbench", "run", "inputs"}))
        if file_name == "compare.html":
            sections.append(action_trace_panel("Compare Action Trace", actions, {"compare", "portability", "workflow", "artifact"}))
            sections.append(action_output_panel("Compare Action Output Links", state, actions, {"compare", "portability", "workflow", "artifact"}))
            sections.append(action_output_detail_panel("Compare Action Output Details", state, actions, {"compare", "portability", "workflow", "artifact"}))
            sections.append(action_impact_panel("Compare Action Impact", state, actions, {"compare", "portability", "workflow", "artifact"}))
            sections.append(action_delta_panel("Compare Action Delta", state, actions, {"compare", "portability", "workflow", "artifact"}))
        if file_name == "review.html":
            sections.append(action_trace_panel("Review Action Trace", actions, {"review", "delivery", "operations", "workbench", "project", "llm", "compare"}))
            sections.append(action_output_panel("Review Action Output Links", state, actions, {"review", "delivery", "operations", "workbench", "project", "llm", "compare"}))
            sections.append(action_output_detail_panel("Review Action Output Details", state, actions, {"review", "delivery", "operations", "workbench", "project", "llm", "compare"}))
            sections.append(action_impact_panel("Review Action Impact", state, actions, {"review", "delivery", "operations", "workbench", "project", "llm", "compare"}))
            sections.append(action_delta_panel("Review Action Delta", state, actions, {"review", "delivery", "operations", "workbench", "project", "llm", "compare"}))
        if file_name == "artifacts.html":
            sections.append(action_output_panel("Artifact Action Output Links", state, actions, {"artifact", "workflow"}))
            sections.append(action_output_detail_panel("Artifact Action Output Details", state, actions, {"artifact", "workflow"}))
            sections.append(action_impact_panel("Artifact Action Impact", state, actions, {"artifact", "workflow"}))
            sections.append(action_delta_panel("Artifact Action Delta", state, actions, {"artifact", "workflow"}))
        if file_name == "delivery.html":
            sections.append(action_trace_panel("Delivery Action Trace", actions, {"delivery", "workbench", "operations", "project", "artifact"}))
            sections.append(action_output_panel("Delivery Action Output Links", state, actions, {"delivery", "workbench", "operations", "project", "artifact"}))
            sections.append(action_output_detail_panel("Delivery Action Output Details", state, actions, {"delivery", "workbench", "operations", "project", "artifact"}))
            sections.append(action_impact_panel("Delivery Action Impact", state, actions, {"delivery", "workbench", "operations", "project", "artifact"}))
            sections.append(action_delta_panel("Delivery Action Delta", state, actions, {"delivery", "workbench", "operations", "project", "artifact"}))
        if file_name == "data.html":
            sections.append(action_trace_panel("Data Action Trace", actions, {"run", "inputs", "workbench", "artifact"}))
            sections.append(action_output_panel("Data Action Output Links", state, actions, {"run", "inputs", "workbench", "artifact"}))
            sections.append(action_output_detail_panel("Data Action Output Details", state, actions, {"run", "inputs", "workbench", "artifact"}))
            sections.append(action_impact_panel("Data Action Impact", state, actions, {"run", "inputs", "workbench", "artifact"}))
            sections.append(action_delta_panel("Data Action Delta", state, actions, {"run", "inputs", "workbench", "artifact"}))
        if file_name == "project.html":
            sections.append(action_trace_panel("Project Action Trace", actions, {"studio", "project", "operations", "workbench", "review", "delivery"}))
            sections.append(action_output_panel("Project Action Output Links", state, actions, {"studio", "project", "operations", "workbench", "review", "delivery"}))
            sections.append(action_output_detail_panel("Project Action Output Details", state, actions, {"studio", "project", "operations", "workbench", "review", "delivery"}))
            sections.append(action_impact_panel("Project Action Impact", state, actions, {"studio", "project", "operations", "workbench", "review", "delivery"}))
            sections.append(action_delta_panel("Project Action Delta", state, actions, {"studio", "project", "operations", "workbench", "review", "delivery"}))
        if file_name == "project-review.html":
            sections.append(action_trace_panel("Project Review Action Trace", actions, {"project", "delivery", "operations", "workbench"}))
            sections.append(action_output_panel("Project Review Action Output Links", state, actions, {"project", "delivery", "operations", "workbench"}))
            sections.append(action_output_detail_panel("Project Review Action Output Details", state, actions, {"project", "delivery", "operations", "workbench"}))
            sections.append(action_impact_panel("Project Review Action Impact", state, actions, {"project", "delivery", "operations", "workbench"}))
            sections.append(action_delta_panel("Project Review Action Delta", state, actions, {"project", "delivery", "operations", "workbench"}))
        if file_name == "llm.html":
            sections.append(action_trace_panel("LLM Action Trace", actions, {"llm"}))
            sections.append(action_output_panel("LLM Action Output Links", state, actions, {"llm"}))
            sections.append(action_output_detail_panel("LLM Action Output Details", state, actions, {"llm"}))
            sections.append(action_impact_panel("LLM Action Impact", state, actions, {"llm"}))
            sections.append(action_delta_panel("LLM Action Delta", state, actions, {"llm"}))
        if file_name == "actions.html":
            sections.append(render_action_panel())
            sections.append(action_output_panel("Action Output Links", state, actions, {"run", "inputs", "workflow", "artifact", "llm", "studio", "workbench", "review", "delivery", "operations", "project", "compare", "portability"}))
            sections.append(action_output_detail_panel("Action Output Details", state, actions, {"run", "inputs", "workflow", "artifact", "llm", "studio", "workbench", "review", "delivery", "operations", "project", "compare", "portability"}))
            sections.append(action_impact_panel("Action Impact", state, actions, {"run", "inputs", "workflow", "artifact", "llm", "studio", "workbench", "review", "delivery", "operations", "project", "compare", "portability"}))
            sections.append(action_delta_panel("Action Delta", state, actions, {"run", "inputs", "workflow", "artifact", "llm", "studio", "workbench", "review", "delivery", "operations", "project", "compare", "portability"}))
            sections.append(render_action_log(actions))
        sections.append(render_table(primary, state_lines(state, primary)))
        for extra in extras:
            sections.append(render_table(extra, state_lines(state, extra)))
        (out_dir / file_name).write_text(page_html(title, nav, sections), encoding="utf-8")

    summary = {
        "state_dir": str(state_dir),
        "state_files": len(state),
        "pages": len(PAGE_SPECS),
        "api_json_files": len(render_state) + extra_api_files,
        "alignment_api_files": extra_api_files + len(render_state) - len(state),
        "action_count": len(actions),
        "last_run_status": last_run.get("status", ""),
        "contract": contract,
        "problems": problems,
        "status": "ready" if not problems else "invalid",
    }
    write_json(out_dir / "reader-summary.json", summary)
    return summary


def count_action_records(action_log: Path) -> int:
    if not action_log.exists():
        return 0
    return sum(1 for line in action_log.read_text(encoding="utf-8").splitlines() if line.strip())


def append_action_records(
    out_dir: Path,
    state_dir: Path,
    actions: list[dict[str, object]],
    write_state: bool,
) -> list[dict[str, object]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    action_log = out_dir / "host-actions.jsonl"
    sequence = count_action_records(action_log) + 1
    records: list[dict[str, object]] = []
    for action in actions:
        action_path = str(action.get("path", ""))
        payload = action.get("payload", {})
        if not isinstance(payload, dict):
            payload = {"value": payload}
        records.append(
            {
                "sequence": sequence,
                "path": action_path,
                "payload": payload,
                "status": "accepted",
            }
        )
        sequence += 1
    with action_log.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    if write_state:
        inbox = state_dir / "rp_host_action_inbox"
        with inbox.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(f"action={record['sequence']};path={record['path']};status=accepted\n")
    if records:
        write_json(out_dir / "last-action.json", records[-1])
        write_json(out_dir / "last-actions.json", records)
    return records


def append_action_record(out_dir: Path, state_dir: Path, action_path: str, payload: dict[str, object], write_state: bool) -> dict[str, object]:
    records = append_action_records(out_dir, state_dir, [{"path": action_path, "payload": payload}], write_state)
    record = records[0]
    return record


def parse_batch_actions(payload: dict[str, object]) -> tuple[list[dict[str, object]], str]:
    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        return [], "actions must be a non-empty list"
    actions: list[dict[str, object]] = []
    for index, raw in enumerate(raw_actions, start=1):
        if not isinstance(raw, dict):
            return [], f"actions[{index}] must be an object"
        path = raw.get("path")
        if not isinstance(path, str) or not path.startswith("/actions/") or path == "/actions/batch":
            return [], f"actions[{index}].path must be an action path"
        item_payload = raw.get("payload", {})
        if item_payload is None:
            item_payload = {}
        if not isinstance(item_payload, dict):
            return [], f"actions[{index}].payload must be an object"
        actions.append({"path": path, "payload": item_payload})
    return actions, ""


def run_action_package(
    state_dir: Path,
    out_dir: Path,
    repo_dir: Path,
    run_root: Path,
    timeout_seconds: int,
    wsl_distro: str,
    runner_module: object | None = None,
) -> dict[str, object]:
    runner = runner_module or importlib.import_module("plain_ucore_action_runner")
    action_log = out_dir / "host-actions.jsonl"
    actions = runner.read_jsonl(action_log)  # type: ignore[attr-defined]
    run_root.mkdir(parents=True, exist_ok=True)
    run_dir = run_root / f"run-{len(actions):04d}"
    package_summary = runner.prepare_action_state(actions, state_dir, run_dir)  # type: ignore[attr-defined]
    run_summary = runner.run_plain_ucore(repo_dir, run_dir, timeout_seconds, wsl_distro)  # type: ignore[attr-defined]
    runner.publish_next_state(run_dir / "state-next", state_dir)  # type: ignore[attr-defined]
    result = {
        "package": package_summary,
        "run": run_summary,
        "run_dir": str(run_dir),
        "status": "ready" if run_summary.get("passed") else "failed",
    }
    write_json(out_dir / "last-run.json", result)
    return result


def run_llm_relay_package(
    state_dir: Path,
    out_dir: Path,
    mode: str,
    relay_module: object | None = None,
) -> dict[str, object]:
    relay = relay_module or importlib.import_module("plain_ucore_llm_relay")
    return relay.run_relay(state_dir, state_dir, mode, out_dir / "llm-relay-summary.json")  # type: ignore[attr-defined]


def parse_request_body(headers: object, body: bytes) -> dict[str, object]:
    content_type = ""
    if hasattr(headers, "get"):
        content_type = headers.get("Content-Type", "")  # type: ignore[assignment]
    text = body.decode("utf-8", errors="replace")
    if "application/json" in content_type:
        try:
            data = json.loads(text or "{}")
            if isinstance(data, dict):
                return data
            return {"value": data}
        except json.JSONDecodeError:
            return {"raw": text, "parse_error": "json"}
    if "application/x-www-form-urlencoded" in content_type:
        parsed = parse_qs(text, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in parsed.items()}
    return {"raw": text}


def make_service_handler(
    state_dir: Path,
    out_dir: Path,
    write_state: bool,
    auto_run_ucore: bool = False,
    repo_dir: Path | None = None,
    run_root: Path | None = None,
    runner_timeout: int = 80,
    wsl_distro: str = "Ubuntu",
    runner_module: object | None = None,
    auto_llm_relay: bool = False,
    llm_relay_mode: str = "auto",
    llm_relay_module: object | None = None,
) -> type[BaseHTTPRequestHandler]:
    action_lock = Lock()
    actual_repo_dir = repo_dir or Path(".")
    actual_run_root = run_root or (out_dir / "auto-runs")

    class PlainUCoreReaderHandler(BaseHTTPRequestHandler):
        server_version = "PlainUCoreReader/0.3"

        def log_message(self, format: str, *args: object) -> None:
            return

        def send_json(self, status: int, data: object) -> None:
            payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def send_text_file(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/reader-summary":
                self.send_json(200, render_site(state_dir, out_dir))
                return
            if path == "/api/contract":
                state = load_state(state_dir)
                contract = reader_contract(state)
                self.send_json(200, {"contract": contract, "problems": validate_contract(contract)})
                return
            if path == "/api/live":
                summary = render_site(state_dir, out_dir)
                action_log = out_dir / "host-actions.jsonl"
                action_count = 0
                if action_log.exists():
                    action_count = sum(1 for line in action_log.read_text(encoding="utf-8").splitlines() if line.strip())
                last_run_path = out_dir / "last-run.json"
                last_run = json.loads(last_run_path.read_text(encoding="utf-8")) if last_run_path.exists() else {}
                self.send_json(200, {"summary": summary, "action_count": action_count, "last_run": last_run})
                return
            if path.startswith("/api/state/"):
                name = unquote(path[len("/api/state/") :])
                state = load_state(state_dir)
                item = state.get(name)
                if not item:
                    self.send_json(404, {"error": "state_not_found", "name": name})
                    return
                self.send_json(200, {"name": name, "values": item["values"], "lines": item["lines"]})
                return

            render_site(state_dir, out_dir)
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            if "/" in rel or "\\" in rel or ".." in rel:
                self.send_json(404, {"error": "not_found"})
                return
            file_path = out_dir / rel
            if not file_path.exists() or not file_path.is_file():
                self.send_json(404, {"error": "not_found"})
                return
            content_type = "text/html; charset=utf-8" if file_path.suffix == ".html" else "application/octet-stream"
            if file_path.suffix == ".json":
                content_type = "application/json; charset=utf-8"
            self.send_text_file(file_path, content_type)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/actions/"):
                self.send_json(404, {"error": "action_not_found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            payload = parse_request_body(self.headers, body)
            with action_lock:
                batch_records: list[dict[str, object]] = []
                record: dict[str, object] = {}
                if parsed.path == "/actions/batch":
                    batch_actions, error = parse_batch_actions(payload)
                    if error:
                        self.send_json(400, {"error": "invalid_batch", "detail": error})
                        return
                    batch_records = append_action_records(out_dir, state_dir, batch_actions, write_state)
                else:
                    record = append_action_record(out_dir, state_dir, parsed.path, payload, write_state)
                run_result = {}
                if auto_run_ucore:
                    run_result = run_action_package(
                        state_dir,
                        out_dir,
                        actual_repo_dir,
                        actual_run_root,
                        runner_timeout,
                        wsl_distro,
                        runner_module,
                    )
                relay_result = {}
                if auto_llm_relay:
                    relay_result = run_llm_relay_package(
                        state_dir,
                        out_dir,
                        llm_relay_mode,
                        llm_relay_module,
                    )
                render_site(state_dir, out_dir)
            status = 202
            if run_result and run_result.get("status") != "ready":
                status = 500
            if batch_records:
                self.send_json(status, {"actions": batch_records, "run": run_result, "relay": relay_result})
            else:
                self.send_json(status, {"action": record, "run": run_result, "relay": relay_result})

    return PlainUCoreReaderHandler


def serve_dir(
    state_dir: Path,
    out_dir: Path,
    port: int,
    write_state: bool,
    auto_run_ucore: bool,
    repo_dir: Path,
    run_root: Path,
    runner_timeout: int,
    wsl_distro: str,
    auto_llm_relay: bool,
    llm_relay_mode: str,
) -> None:
    handler = make_service_handler(
        state_dir,
        out_dir,
        write_state,
        auto_run_ucore,
        repo_dir,
        run_root,
        runner_timeout,
        wsl_distro,
        auto_llm_relay=auto_llm_relay,
        llm_relay_mode=llm_relay_mode,
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"plain_ucore_reader: serving http://127.0.0.1:{port}/")
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Render plain uCore research platform state for host viewing.")
    parser.add_argument("--state-dir", type=Path, required=True, help="Directory containing rp_* state files.")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for static HTML and JSON.")
    parser.add_argument("--serve", action="store_true", help="Serve dynamic host pages, APIs, and POST action capture.")
    parser.add_argument("--port", type=int, default=8767, help="Local port for --serve.")
    parser.add_argument("--write-state-actions", action="store_true", help="Also append POST action records to the state directory.")
    parser.add_argument("--auto-run-ucore", action="store_true", help="After each POST action, prepare action state, run rp_orch, and refresh the served state directory.")
    parser.add_argument("--auto-run-llm-relay", action="store_true", help="After each POST action, run the host LLM relay over refreshed rp_* state files.")
    parser.add_argument("--llm-relay-mode", default="auto", choices=["auto", "template", "mock", "openai-compatible", "cloud"], help="Execution mode for --auto-run-llm-relay.")
    parser.add_argument("--repo-dir", type=Path, default=Path("."), help="Repository root used by --auto-run-ucore.")
    parser.add_argument("--run-root", type=Path, default=Path("runtime/plain_ucore_auto_runs"), help="Directory for automatic action packages and QEMU logs.")
    parser.add_argument("--timeout", type=int, default=80, help="QEMU run timeout in seconds for --auto-run-ucore.")
    parser.add_argument("--wsl-distro", default="Ubuntu", help="WSL distribution name on Windows.")
    parser.add_argument("--host-platform-alignment", type=Path, default=None, help="Optional host platform capability alignment JSON.")
    parser.add_argument("--host-test-alignment", type=Path, default=None, help="Optional host platform test theme alignment JSON.")
    parser.add_argument("--host-surface-alignment", type=Path, default=None, help="Optional host Web/API/action surface alignment JSON.")
    parser.add_argument("--seeded-action-state", type=Path, default=None, help="Optional seeded action runtime state JSON.")
    args = parser.parse_args()

    summary = render_site(
        args.state_dir,
        args.out_dir,
        args.host_platform_alignment,
        args.host_test_alignment,
        args.host_surface_alignment,
        args.seeded_action_state,
    )
    print(
        "plain_ucore_reader: pages={pages} api_json={api_json_files} state_files={state_files} status={status}".format(
            **summary
        )
    )
    if summary["status"] != "ready":
        for problem in summary["problems"]:  # type: ignore[index]
            print(f"plain_ucore_reader: problem={problem}")
        return 1
    if args.serve:
        serve_dir(
            args.state_dir,
            args.out_dir,
            args.port,
            args.write_state_actions,
            args.auto_run_ucore,
            args.repo_dir,
            args.run_root,
            args.timeout,
            args.wsl_distro,
            args.auto_run_llm_relay,
            args.llm_relay_mode,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
