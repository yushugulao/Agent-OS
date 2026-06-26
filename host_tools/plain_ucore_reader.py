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
    ("agents.html", "Agents", "rp_api_agents", ["rp_ui_agent", "rp_agents", "rp_decisions"]),
    ("evidence.html", "Evidence", "rp_api_evidence", ["rp_ui_evidence", "rp_evidence", "rp_package"]),
    ("review.html", "Review", "rp_review_dashboard", ["rp_review_pack", "rp_review2", "rp_revision", "rp_package", "rp_report_text"]),
    ("compare.html", "Compare", "rp_api_compare", ["rp_ui_compare", "rp_agentcmp", "rp_consistency"]),
    ("artifacts.html", "Artifacts", "rp_api_artifacts", ["rp_artifact", "rp_artifact_manifest", "rp_package"]),
    ("data.html", "Data", "rp_api_data", ["rp_input", "rp_dataset_snapshot", "rp_data_quality"]),
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


def state_prefixed_lines(state: dict[str, dict[str, object]], name: str, prefixes: tuple[str, ...]) -> list[str]:
    rows: list[str] = []
    for line in state_lines(state, name):
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in prefixes):
            rows.append(stripped)
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
    report_items = [
        ("Run", metric_value(state, [("rp_report_text", "host_report_run_id"), ("rp_input", "host_action_run_id")]), "rp_report_text"),
        ("Reviewer", metric_value(state, [("rp_report_text", "host_report_reviewer"), ("rp_review2", "host_action_reviewer")]), "rp_review2"),
        ("Decision", metric_value(state, [("rp_report_text", "host_report_review_decision"), ("rp_review2", "host_action_review_decision")]), "rp_review2"),
        ("Revision Targets", metric_value(state, [("rp_report_text", "host_report_revision_targets"), ("rp_revision", "host_action_revision_targets")]), "rp_revision"),
        ("Bundle", metric_value(state, [("rp_report_text", "host_report_bundle"), ("rp_package", "host_action_export_bundle_name")]), "rp_package"),
        ("Compare Profile", metric_value(state, [("rp_report_text", "host_report_compare_profile"), ("rp_agentcmp", "host_action_compare_profile")]), "rp_agentcmp"),
    ]
    evidence_items = [
        ("Manifest Run", metric_value(state, [("rp_artifact_manifest", "host_manifest_run_id"), ("rp_report_text", "host_report_run_id")]), "rp_artifact_manifest"),
        ("Notebook Format", metric_value(state, [("rp_artifact_manifest", "host_manifest_notebook_format"), ("rp_nbexec", "host_action_notebook_format")]), "rp_nbexec"),
        ("Bundle", metric_value(state, [("rp_artifact_manifest", "host_manifest_bundle"), ("rp_package", "host_action_export_bundle_name")]), "rp_package"),
        ("Contents", metric_value(state, [("rp_package", "host_action_bundle_contents")]), "rp_package"),
        ("Evidence Entries", metric_value(state, [("rp_package", "evidence_bundle_entries")]), "rp_package"),
        ("Manifest Records", metric_value(state, [("rp_artifact_manifest", "manifest_records")]), "rp_artifact_manifest"),
    ]
    compare_items = [
        ("Payload Applied", metric_value(state, [("rp_api_compare", "host_action_payload_applied")]), "rp_api_compare"),
        ("Run", metric_value(state, [("rp_api_compare", "host_action_run_id")]), "rp_api_compare"),
        ("Reviewer", metric_value(state, [("rp_api_compare", "host_action_reviewer")]), "rp_api_compare"),
        ("Revision Targets", metric_value(state, [("rp_api_compare", "host_action_revision_targets")]), "rp_api_compare"),
        ("Bundle", metric_value(state, [("rp_api_compare", "host_action_bundle")]), "rp_api_compare"),
        ("Compare Profile", metric_value(state, [("rp_api_compare", "host_action_compare_profile")]), "rp_api_compare"),
    ]
    llm_items = [
        ("Relay", metric_value(state, [("rp_llm_resp", "host_relay_process"), ("rp_relay", "mode")]), "rp_llm_resp"),
        ("Quality", metric_value(state, [("rp_llmeval", "host_relay_eval_batch")]), "rp_llmeval"),
        ("Guard", metric_value(state, [("rp_llm_guard", "host_relay_guard_batch")]), "rp_llm_guard"),
        ("Replay", metric_value(state, [("rp_relay", "host_relay_replay_batch")]), "rp_relay"),
        ("Routes", metric_value(state, [("rp_prompt", "host_relay_prompt_batch"), ("rp_prompt", "routes")]), "rp_prompt"),
        ("Runtime", metric_value(state, [("rp_api_runtime", "host_llm_relay_quality")]), "rp_api_runtime"),
    ]
    review_items = [
        ("Run", metric_value(state, [("rp_review_dashboard", "run"), ("rp_report_text", "host_report_run_id")]), "rp_review_dashboard"),
        ("Sections", metric_value(state, [("rp_review_dashboard", "sections")]), "rp_review_dashboard"),
        ("Decision", metric_value(state, [("rp_review_dashboard", "decision")]), "rp_review_dashboard"),
        ("Evidence Pack", metric_value(state, [("rp_review_pack", "pack")]), "rp_review_pack"),
        ("Human Review", metric_value(state, [("rp_review2", "decision"), ("rp_report_text", "host_report_review_decision")]), "rp_review2"),
        ("Delivery", metric_value(state, [("rp_package", "latest_delivery_status"), ("rp_package", "status")]), "rp_package"),
        ("Bridge", metric_value(state, [("rp_review_pack", "bridge"), ("rp_package", "review_pack_bridge")]), "rp_review_pack"),
        ("Host Relay Quality", metric_value(state, [("rp_review_pack", "host_relay_quality"), ("rp_review_dashboard", "host_relay_quality")]), "rp_review_pack"),
    ]
    if file_name == "run.html":
        return render_summary_panel("Research Output", report_items)
    if file_name in ("evidence.html", "artifacts.html"):
        return render_summary_panel("Evidence Package", evidence_items)
    if file_name == "review.html":
        return render_summary_panel("Review Dashboard", review_items)
    if file_name == "compare.html":
        return render_summary_panel("Compare Summary", compare_items)
    if file_name == "llm.html":
        return render_summary_panel("Relay Quality", llm_items)
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
    ]
    llm_items = [
        ("Requests", metric_value(state, [("rp_llm_resp", "requests"), ("rp_llmq", "queued")]), "rp_llm_resp"),
        ("Responses", metric_value(state, [("rp_llm_resp", "responses")]), "rp_llm_resp"),
        ("Mode", metric_value(state, [("rp_llm_resp", "mode")]), "rp_llm_resp"),
        ("Secret Material", metric_value(state, [("rp_llm_hostreq", "secret_material")]), "rp_llm_hostreq"),
        ("Packet Secret", metric_value(state, [("rp_llm_packets", "secret_in_packet")]), "rp_llm_packets"),
        ("Agent Decision", metric_value(state, [("rp_agent_run", "host_relay_agent_decision")]), "rp_agent_run"),
    ]
    review_items = [
        ("Workflow", metric_value(state, [("rp_review_dashboard", "section")]), "rp_review_dashboard"),
        ("Required Files", metric_value(state, [("rp_review_dashboard", "gate")]), "rp_review_dashboard"),
        ("Pack Action", metric_value(state, [("rp_review_pack", "action")]), "rp_review_pack"),
        ("Pack Bridge", metric_value(state, [("rp_review_pack", "bridge"), ("rp_package", "review_pack_bridge")]), "rp_review_pack"),
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
    if file_name == "llm.html":
        return render_summary_panel("Relay State", llm_items)
    return ""


def render_grouped_details(file_name: str, state: dict[str, dict[str, object]]) -> list[str]:
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
            ("Reader Contract", metric_value(state, [("rp_agentcmp", "reader_contract")])),
        ]
        check_rows = [
            ("Coherence Checks", metric_value(state, [("rp_api_compare", "coherence_checks"), ("rp_ui_compare", "coherence_checks")])),
            ("Namespace Checks", metric_value(state, [("rp_api_compare", "namespace_checks"), ("rp_ui_compare", "namespace_checks")])),
            ("Surface Checks", metric_value(state, [("rp_api_compare", "surface_checks"), ("rp_ui_compare", "surface_checks")])),
            ("Status Semantics", metric_value(state, [("rp_api_compare", "status_semantics"), ("rp_ui_compare", "status_semantics")])),
            ("Reference Checks", metric_value(state, [("rp_api_compare", "reference_checks"), ("rp_ui_compare", "reference_checks")])),
            ("Evidence Trace Checks", metric_value(state, [("rp_api_compare", "evidence_trace_checks"), ("rp_ui_compare", "evidence_trace_checks")])),
        ]
        return [
            render_line_panel("Plain Kernel Signals", compare_rows),
            render_line_panel("Consistency Signals", check_rows),
        ]
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
                "Archive Files",
                [("Archive File", "archive_file"), ("Kind", "kind"), ("Status", "status")],
                state_records(state, "rp_artifact", "archive_file"),
            ),
            render_record_panel(
                "Stage Logs",
                [("Log", "log"), ("Status", "status"), ("Reason", "reason"), ("Artifact", "artifact")],
                state_records(state, "rp_stage_log", "log"),
            ),
            render_line_panel("Host Artifact Actions", host_action_rows),
        ]
    if file_name == "review.html":
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
                [("Evidence", "evidence"), ("Source", "source"), ("Status", "status")],
                state_records(state, "rp_review_pack", "evidence"),
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
        ]
    if file_name == "llm.html":
        return [
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


def default_batch_payload() -> str:
    actions = {
        "actions": [
            {"path": "/actions/research/run", "payload": {"run_id": "RUN-WEB", "source": "reader-ui"}},
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
            ("QEMU", metric_value(state, [("rp_host_run_result", "qemu_orch_passed")]), "rp_host_run_result"),
        ],
        "artifacts.html": [
            ("Manifest Records", metric_value(state, [("rp_artifact_manifest", "manifest_records"), ("rp_api_artifacts", "manifest_records")]), "rp_artifact_manifest"),
            ("Real Items", metric_value(state, [("rp_artifact_manifest", "real_artifact_items"), ("rp_package", "real_artifact_items")]), "rp_package"),
            ("Package", metric_value(state, [("rp_package", "status")]), "rp_package"),
        ],
        "data.html": [
            ("Submissions", metric_value(state, [("rp_input", "dynamic_submissions")]), "rp_input"),
            ("Snapshots", metric_value(state, [("rp_dataset_snapshot", "snapshots"), ("rp_api_data", "dataset_snapshots")]), "rp_dataset_snapshot"),
            ("Quality", metric_value(state, [("rp_data_quality", "passed")]), "rp_data_quality"),
        ],
        "review.html": [
            ("Sections", metric_value(state, [("rp_review_dashboard", "sections")]), "rp_review_dashboard"),
            ("Decision", metric_value(state, [("rp_review_dashboard", "decision")]), "rp_review_dashboard"),
            ("Pack", metric_value(state, [("rp_review_pack", "pack")]), "rp_review_pack"),
            ("Delivery", metric_value(state, [("rp_package", "latest_delivery_status"), ("rp_package", "status")]), "rp_package"),
        ],
        "llm.html": [
            ("Requests", metric_value(state, [("rp_llm_resp", "requests"), ("rp_llmq", "queued")]), "rp_llm_resp"),
            ("Quality", metric_value(state, [("rp_llmeval", "host_relay_eval_batch"), ("rp_llmeval", "passed")]), "rp_llmeval"),
            ("Guard", metric_value(state, [("rp_llm_guard", "host_relay_guard_batch"), ("rp_llm_guard", "secret_scan")]), "rp_llm_guard"),
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


def render_site(state_dir: Path, out_dir: Path) -> dict[str, object]:
    state = load_state(state_dir)
    contract = reader_contract(state)
    problems = validate_contract(contract)
    out_dir.mkdir(parents=True, exist_ok=True)
    api_dir = out_dir / "api"
    action_log = out_dir / "host-actions.jsonl"
    actions = read_jsonl_file(action_log)
    last_run = read_json_file(out_dir / "last-run.json")

    for name, item in state.items():
        write_json(api_dir / f"{name}.json", {"name": name, "values": item["values"], "lines": item["lines"]})

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
        sections = [render_overview(file_name, state, contract, len(actions), last_run)]
        summary_panel = render_page_summary(file_name, state)
        if summary_panel:
            sections.append(summary_panel)
        detail_panel = render_detail_panel(file_name, state)
        if detail_panel:
            sections.append(detail_panel)
        sections.extend(render_grouped_details(file_name, state))
        if file_name == "actions.html":
            sections.append(render_action_panel())
            sections.append(render_action_log(actions))
        sections.append(render_table(primary, state_lines(state, primary)))
        for extra in extras:
            sections.append(render_table(extra, state_lines(state, extra)))
        (out_dir / file_name).write_text(page_html(title, nav, sections), encoding="utf-8")

    summary = {
        "state_dir": str(state_dir),
        "state_files": len(state),
        "pages": len(PAGE_SPECS),
        "api_json_files": len(state),
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
    args = parser.parse_args()

    summary = render_site(args.state_dir, args.out_dir)
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
