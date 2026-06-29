#!/usr/bin/env python3
"""Validate rendered 本地结果阅读器 files."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from plain_ucore_reader import PAGE_SPECS


REQUIRED_PAGES = {
    "index.html",
    "run.html",
    "agents.html",
    "evidence.html",
    "compare.html",
    "actions.html",
}

HTML_MARKERS = (
    "<main>",
    "<aside class=\"sidebar\">",
    "<h1>",
    "<section",
    "Plain uCore Research",
    "Rendered from plain uCore state files.",
)

EXPECTED_PAGE_TITLES = {file_name: title for file_name, title, _primary, _extras in PAGE_SPECS}

AGENTOS_COMPARE_API = {
    "rp_agentos_mainflow.json",
    "rp_agentos_query.json",
    "rp_agentos_recovery.json",
    "rp_agentos_timeline.json",
    "rp_agentos_collab_ack.json",
    "rp_agentos_audit.json",
    "rp_agentos_workbench.json",
    "rp_agentos_package.json",
    "rp_agentos_real_task.json",
    "rp_agentos_conflict.json",
}

AGENTOS_COMPARE_MARKERS = (
    "AgentOS Main Flow Kernel Stages",
    "AgentOS Kernel Output Files",
    "rp_agentos_mainflow",
    "rp_agentos_query",
    "rp_agentos_recovery",
    "rp_agentos_timeline",
    "rp_agentos_collab_ack",
    "kernel_shadow",
    "kernel_metadata_index",
    "kernel_event_queue",
    "kernel_ledger",
    "kernel_context_record",
    "kernel_exclusive",
)


def read_summary(reader_dir: Path) -> dict[str, object]:
    summary_path = reader_dir / "reader-summary.json"
    if not summary_path.is_file():
        raise ValueError(f"missing reader summary: {summary_path}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def require_int(summary: dict[str, object], key: str) -> int:
    value = summary.get(key)
    if not isinstance(value, int):
        raise ValueError(f"summary field is not an integer: {key}")
    return value


def validate_html(reader_dir: Path, expected_count: int) -> set[str]:
    page_names = {path.name for path in reader_dir.glob("*.html") if path.is_file()}
    if len(page_names) != expected_count:
        raise ValueError(f"HTML page count mismatch: files={len(page_names)} summary={expected_count}")
    expected_page_names = set(EXPECTED_PAGE_TITLES)
    if page_names != expected_page_names:
        missing_pages = sorted(expected_page_names - page_names)
        extra_pages = sorted(page_names - expected_page_names)
        raise ValueError(
            "HTML page set does not match reader spec: missing={} extra={}".format(
                ",".join(missing_pages[:20]),
                ",".join(extra_pages[:20]),
            )
        )
    missing_required = sorted(REQUIRED_PAGES - page_names)
    if missing_required:
        raise ValueError("missing required HTML pages: " + ",".join(missing_required))
    for page_name in sorted(page_names):
        text = (reader_dir / page_name).read_text(encoding="utf-8", errors="replace")
        if len(text) < 1000:
            raise ValueError(f"HTML page is unexpectedly small: {page_name}")
        for marker in HTML_MARKERS:
            if marker not in text:
                raise ValueError(f"HTML page {page_name} is missing marker: {marker}")
        expected_title = "<h1>{}</h1>".format(html.escape(EXPECTED_PAGE_TITLES[page_name]))
        if expected_title not in text:
            raise ValueError(f"HTML page {page_name} is missing expected title: {EXPECTED_PAGE_TITLES[page_name]}")
    return page_names


def validate_api(reader_dir: Path, expected_count: int) -> set[str]:
    api_dir = reader_dir / "api"
    if not api_dir.is_dir():
        raise ValueError(f"missing API directory: {api_dir}")
    api_names = {path.name for path in api_dir.glob("*.json") if path.is_file()}
    if len(api_names) != expected_count:
        raise ValueError(f"API JSON count mismatch: files={len(api_names)} summary={expected_count}")
    for api_name in sorted(api_names):
        path = api_dir / api_name
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") != api_name[:-5]:
            raise ValueError(f"API JSON name does not match file name: {api_name}")
        if not isinstance(data.get("values"), dict):
            raise ValueError(f"API JSON values is not an object: {api_name}")
        if not isinstance(data.get("lines"), list):
            raise ValueError(f"API JSON lines is not an array: {api_name}")
    return api_names


def validate_agentos_compare(reader_dir: Path, api_names: set[str]) -> int:
    present = AGENTOS_COMPARE_API & api_names
    if not present:
        return 0
    missing_api = sorted(AGENTOS_COMPARE_API - api_names)
    if missing_api:
        raise ValueError("AgentOS reader API is missing kernel evidence files: " + ",".join(missing_api))
    compare_html = (reader_dir / "compare.html").read_text(encoding="utf-8", errors="replace")
    for marker in AGENTOS_COMPARE_MARKERS:
        if marker not in compare_html:
            raise ValueError(f"AgentOS Compare page is missing kernel evidence marker: {marker}")
    return len(AGENTOS_COMPARE_MARKERS)


def check_reader_output(reader_dir: Path) -> dict[str, object]:
    summary = read_summary(reader_dir)
    if summary.get("status") != "ready":
        raise ValueError(f"reader summary is not ready: {summary.get('status')}")
    expected_pages = require_int(summary, "pages")
    expected_api = require_int(summary, "api_json_files")
    expected_state = require_int(summary, "state_files")
    page_names = validate_html(reader_dir, expected_pages)
    api_names = validate_api(reader_dir, expected_api)
    agentos_compare_markers = validate_agentos_compare(reader_dir, api_names)
    if expected_api < expected_state:
        raise ValueError(f"API count is smaller than state count in summary: state={expected_state} api={expected_api}")
    return {
        "pages": len(page_names),
        "api_json": len(api_names),
        "state_files": expected_state,
        "required_pages": len(REQUIRED_PAGES),
        "spec_pages": len(EXPECTED_PAGE_TITLES),
        "agentos_compare_markers": agentos_compare_markers,
        "status": "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate 本地结果阅读器 rendered HTML and API JSON.")
    parser.add_argument("--reader-dir", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    summary = check_reader_output(args.reader_dir)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        "reader_output_check: pages={pages} api_json={api_json} state_files={state_files} required_pages={required_pages} spec_pages={spec_pages} agentos_compare_markers={agentos_compare_markers} status={status}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
