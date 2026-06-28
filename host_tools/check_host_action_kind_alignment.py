#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from plain_ucore_action_runner import action_kind


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


def read_user_sources(root: Path, relative_dir: str) -> str:
    source_dir = root / relative_dir
    if not source_dir.exists():
        raise FileNotFoundError(f"user source directory is missing: {relative_dir}")
    chunks: list[str] = []
    for path in sorted(source_dir.glob("*.c")):
        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def missing_kinds(source_text: str, kinds: list[str]) -> list[str]:
    missing: list[str] = []
    for kind in kinds:
        if f"kind={kind}" not in source_text:
            missing.append(kind)
    return missing


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

    plain_sources = read_user_sources(root, "user/src")
    agentos_sources = read_user_sources(root, "agentos_ucore/user/src")
    plain_missing = missing_kinds(plain_sources, kinds)
    agentos_missing = missing_kinds(agentos_sources, kinds)

    failures: list[str] = []
    if generic_routes:
        failures.append("generic action mappings: " + ",".join(generic_routes[:12]))
    if plain_missing:
        failures.append("plain missing action kinds: " + ",".join(plain_missing[:24]))
    if agentos_missing:
        failures.append("AgentOS missing action kinds: " + ",".join(agentos_missing[:24]))

    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(host_dir),
        "host_action_routes": len(action_routes),
        "host_action_kinds": len(kinds),
        "generic_routes": generic_routes,
        "plain_missing_kinds": plain_missing,
        "agentos_missing_kinds": agentos_missing,
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
        f"status={summary['status']}"
    )
    if summary["status"] == "failed":
        for failure in summary["failures"]:
            print(f"host_action_kind_alignment: failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
