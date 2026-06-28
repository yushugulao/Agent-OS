#!/usr/bin/env python3
"""Compare Host Reader render summaries for plain uCore and AgentOS-uCore."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_summary(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"missing reader summary: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def as_int(summary: dict[str, object], key: str) -> int:
    value = summary.get(key)
    if not isinstance(value, int):
        raise ValueError(f"reader summary field is not an integer: {key}")
    return value


def as_status(summary: dict[str, object]) -> str:
    value = summary.get("status")
    if not isinstance(value, str):
        raise ValueError("reader summary field is not a string: status")
    return value


def html_files(root: Path) -> set[str]:
    return {path.name for path in root.glob("*.html") if path.is_file()}


def api_json_files(root: Path) -> set[str]:
    api_dir = root / "api"
    if not api_dir.is_dir():
        raise ValueError(f"missing reader API directory: {api_dir}")
    return {path.name for path in api_dir.glob("*.json") if path.is_file()}


def compare_reader(plain_summary_path: Path, agentos_summary_path: Path) -> dict[str, object]:
    plain = read_summary(plain_summary_path)
    agentos = read_summary(agentos_summary_path)
    plain_root = plain_summary_path.parent
    agentos_root = agentos_summary_path.parent

    plain_status = as_status(plain)
    agentos_status = as_status(agentos)
    if plain_status != "ready":
        raise ValueError(f"plain reader is not ready: {plain_status}")
    if agentos_status != "ready":
        raise ValueError(f"AgentOS reader is not ready: {agentos_status}")

    plain_pages = as_int(plain, "pages")
    agentos_pages = as_int(agentos, "pages")
    if agentos_pages != plain_pages:
        raise ValueError(f"reader page count differs: plain={plain_pages} agentos={agentos_pages}")

    plain_state_files = as_int(plain, "state_files")
    agentos_state_files = as_int(agentos, "state_files")
    if agentos_state_files < plain_state_files:
        raise ValueError(f"AgentOS reader has fewer state files: {agentos_state_files} < {plain_state_files}")

    plain_api_json = as_int(plain, "api_json_files")
    agentos_api_json = as_int(agentos, "api_json_files")
    if agentos_api_json < plain_api_json:
        raise ValueError(f"AgentOS reader has fewer API JSON files: {agentos_api_json} < {plain_api_json}")

    plain_pages_set = html_files(plain_root)
    agentos_pages_set = html_files(agentos_root)
    if plain_pages_set != agentos_pages_set:
        missing_pages = sorted(plain_pages_set - agentos_pages_set)
        extra_pages = sorted(agentos_pages_set - plain_pages_set)
        raise ValueError(
            "reader page set differs: missing={} extra={}".format(
                ",".join(missing_pages[:20]),
                ",".join(extra_pages[:20]),
            )
        )

    plain_api_set = api_json_files(plain_root)
    agentos_api_set = api_json_files(agentos_root)
    missing_api = sorted(plain_api_set - agentos_api_set)
    if missing_api:
        raise ValueError("AgentOS reader API is missing plain files: " + ",".join(missing_api[:20]))

    return {
        "plain_pages": plain_pages,
        "agentos_pages": agentos_pages,
        "plain_state_files": plain_state_files,
        "agentos_state_files": agentos_state_files,
        "plain_api_json": plain_api_json,
        "agentos_api_json": agentos_api_json,
        "checked_pages": len(plain_pages_set),
        "checked_api_json": len(plain_api_set),
        "status": "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Host Reader summaries for both uCore targets.")
    parser.add_argument("--plain-summary", type=Path, required=True)
    parser.add_argument("--agentos-summary", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    summary = compare_reader(args.plain_summary, args.agentos_summary)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        "dual_platform_reader_compare: plain_pages={plain_pages} agentos_pages={agentos_pages} plain_state_files={plain_state_files} agentos_state_files={agentos_state_files} plain_api_json={plain_api_json} agentos_api_json={agentos_api_json} checked_pages={checked_pages} checked_api_json={checked_api_json} status={status}".format(
            **summary
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
