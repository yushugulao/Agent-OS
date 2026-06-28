#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from plain_ucore_action_runner import action_kind

EVIDENCE_ONLY_FILES = {"rp_compare_plain.c", "rp_test_suite.c"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_host_dir(root: Path) -> Path:
    override = os.environ.get("HOST_PLATFORM_DIR")
    if override:
        return Path(override)
    return root.parent / "research-agent-platform-userland"


def collect_action_routes(host_dir: Path) -> list[str]:
    source = host_dir / "agent_platform" / "api_server.py"
    if not source.exists():
        raise FileNotFoundError(f"host API server is missing: {source}")
    text = source.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'path == "(/actions/[^"]+)"', text)))


def read_user_source_files(root: Path, relative_dir: str) -> dict[str, str]:
    source_dir = root / relative_dir
    if not source_dir.exists():
        raise FileNotFoundError(f"user source directory is missing: {relative_dir}")
    return {path.name: path.read_text(encoding="utf-8", errors="replace") for path in sorted(source_dir.glob("*.c"))}


def kind_source_map(source_files: dict[str, str], kinds: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for kind in kinds:
        token = f"kind={kind}"
        result[kind] = [name for name, text in source_files.items() if token in text]
    return result


def missing_kinds(sources_by_kind: dict[str, list[str]]) -> list[str]:
    missing: list[str] = []
    for kind, files in sources_by_kind.items():
        if not files:
            missing.append(kind)
    return missing


def missing_runtime_handlers(sources_by_kind: dict[str, list[str]]) -> list[str]:
    missing: list[str] = []
    for kind, files in sources_by_kind.items():
        if not any(name not in EVIDENCE_ONLY_FILES for name in files):
            missing.append(kind)
    return missing


def handler_files(sources_by_kind: dict[str, list[str]]) -> list[str]:
    files: set[str] = set()
    for names in sources_by_kind.values():
        for name in names:
            if name not in EVIDENCE_ONLY_FILES:
                files.add(name)
    return sorted(files)


def kind_source_sample(sources_by_kind: dict[str, list[str]]) -> dict[str, list[str]]:
    return {kind: sources_by_kind[kind] for kind in sorted(sources_by_kind)[:16]}


def run_check(root: Path, host_dir: Path, require_host: bool) -> dict[str, object]:
    if not host_dir.exists():
        if require_host:
            raise SystemExit(f"host platform is missing: {host_dir}")
        return {
            "status": "skipped",
            "reason": "host_platform_not_found",
            "host_dir": str(host_dir),
        }

    action_routes = collect_action_routes(host_dir)
    route_kinds = {route: action_kind(route) for route in action_routes}
    kinds = sorted(set(route_kinds.values()))
    generic_routes = [route for route, kind in route_kinds.items() if kind == "generic"]

    plain_sources = kind_source_map(read_user_source_files(root, "user/src"), kinds)
    agentos_sources = kind_source_map(read_user_source_files(root, "agentos_ucore/user/src"), kinds)
    plain_missing = missing_kinds(plain_sources)
    agentos_missing = missing_kinds(agentos_sources)
    plain_handler_missing = missing_runtime_handlers(plain_sources)
    agentos_handler_missing = missing_runtime_handlers(agentos_sources)

    failures: list[str] = []
    if generic_routes:
        failures.append("generic action mappings: " + ",".join(generic_routes[:12]))
    if plain_missing:
        failures.append("plain missing action kinds: " + ",".join(plain_missing[:24]))
    if agentos_missing:
        failures.append("AgentOS missing action kinds: " + ",".join(agentos_missing[:24]))
    if plain_handler_missing:
        failures.append("plain missing runtime handlers: " + ",".join(plain_handler_missing[:24]))
    if agentos_handler_missing:
        failures.append("AgentOS missing runtime handlers: " + ",".join(agentos_handler_missing[:24]))

    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(host_dir),
        "host_action_routes": len(action_routes),
        "host_action_kinds": len(kinds),
        "generic_routes": generic_routes,
        "plain_missing_kinds": plain_missing,
        "agentos_missing_kinds": agentos_missing,
        "plain_missing_runtime_handlers": plain_handler_missing,
        "agentos_missing_runtime_handlers": agentos_handler_missing,
        "plain_handler_files": handler_files(plain_sources),
        "agentos_handler_files": handler_files(agentos_sources),
        "plain_kind_source_sample": kind_source_sample(plain_sources),
        "agentos_kind_source_sample": kind_source_sample(agentos_sources),
        "kind_sample": kinds[:16],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check host action route kind handling in both uCore targets.")
    parser.add_argument("--host-dir", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--require-host", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    host_dir = args.host_dir or default_host_dir(root)
    summary = run_check(root, host_dir, args.require_host)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if summary["status"] == "skipped":
        print(f"host_action_kind_alignment: status=skipped reason={summary['reason']} host_dir={summary['host_dir']}")
        return 0

    print(
        "host_action_kind_alignment: "
        f"action_routes={summary['host_action_routes']} "
        f"action_kinds={summary['host_action_kinds']} "
        f"generic_routes={len(summary['generic_routes'])} "
        f"plain_missing={len(summary['plain_missing_kinds'])} "
        f"agentos_missing={len(summary['agentos_missing_kinds'])} "
        f"plain_handler_missing={len(summary['plain_missing_runtime_handlers'])} "
        f"agentos_handler_missing={len(summary['agentos_missing_runtime_handlers'])} "
        f"status={summary['status']}"
    )
    if summary["status"] == "failed":
        for failure in summary["failures"]:
            print(f"host_action_kind_alignment: failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
