#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_host_dir(root: Path) -> Path:
    override = os.environ.get("HOST_PLATFORM_DIR")
    if override:
        return Path(override)
    return root.parent / "research-agent-platform-userland"


def collect_routes(host_dir: Path) -> dict[str, object]:
    source = host_dir / "agent_platform" / "api_server.py"
    if not source.exists():
        raise FileNotFoundError(f"host API server is missing: {source}")
    text = source.read_text(encoding="utf-8")
    api_routes = sorted(set(re.findall(r'path == "(/api/[^"]+)"', text)))
    action_routes = sorted(set(re.findall(r'path == "(/actions/[^"]+)"', text)))
    download_refs = sorted(set(re.findall(r'"(/download/[^"]+)"', text)))
    action_prefixes = sorted({"/".join(route.split("/")[:3]) for route in action_routes})
    api_prefixes = sorted({"/".join(route.split("/")[:3]) for route in api_routes})
    return {
        "api_routes": api_routes,
        "action_routes": action_routes,
        "download_refs": download_refs,
        "api_prefixes": api_prefixes,
        "action_prefixes": action_prefixes,
    }


def read_text(root: Path, relative: str) -> str:
    path = root / relative
    if not path.exists():
        raise FileNotFoundError(f"source file is missing: {relative}")
    return path.read_text(encoding="utf-8")


def source_value(text: str, key: str) -> int | None:
    matches = re.findall(rf"{re.escape(key)}=(\d+)", text)
    if not matches:
        return None
    return max(int(value) for value in matches)


def parse_state_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for part in raw.strip().split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def int_value(values: dict[str, str], key: str) -> int | None:
    raw = values.get(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def check_source_counts(label: str, text: str, expected_api: int, expected_actions: int, failures: list[str]) -> dict[str, object]:
    api_count = source_value(text, "host_api_routes")
    action_count = source_value(text, "host_action_routes")
    reader_actions = source_value(text, "reader_actions")
    if api_count != expected_api:
        failures.append(f"{label}: host_api_routes={api_count} expected={expected_api}")
    if action_count != expected_actions:
        failures.append(f"{label}: host_action_routes={action_count} expected={expected_actions}")
    if reader_actions is None or reader_actions < expected_actions:
        failures.append(f"{label}: reader_actions={reader_actions} expected_at_least={expected_actions}")
    return {
        "host_api_routes": api_count,
        "host_action_routes": action_count,
        "reader_actions": reader_actions,
    }


def check_runtime_counts(
    label: str,
    state_dir: Path | None,
    expected_api: int,
    expected_actions: int,
    failures: list[str],
) -> dict[str, object]:
    if state_dir is None:
        return {}
    catalog = parse_state_values(state_dir / "rp_api_catalog")
    bundle = parse_state_values(state_dir / "rp_web_bundle")
    api_count = int_value(catalog, "host_api_routes")
    action_count = int_value(catalog, "host_action_routes")
    reader_actions = int_value(bundle, "reader_actions")
    if api_count != expected_api:
        failures.append(f"{label}: runtime host_api_routes={api_count} expected={expected_api}")
    if action_count != expected_actions:
        failures.append(f"{label}: runtime host_action_routes={action_count} expected={expected_actions}")
    if reader_actions is None or reader_actions < expected_actions:
        failures.append(f"{label}: runtime reader_actions={reader_actions} expected_at_least={expected_actions}")
    return {
        "host_api_routes": api_count,
        "host_action_routes": action_count,
        "reader_actions": reader_actions,
    }


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

    routes = collect_routes(host_dir)
    api_routes = routes["api_routes"]
    action_routes = routes["action_routes"]
    expected_api = len(api_routes)  # type: ignore[arg-type]
    expected_actions = len(action_routes)  # type: ignore[arg-type]
    failures: list[str] = []

    plain_source = read_text(root, "user/src/rp_web_export.c")
    agentos_source = read_text(root, "agentos_ucore/user/src/rp_web_export.c")
    plain_counts = check_source_counts("plain source", plain_source, expected_api, expected_actions, failures)
    agentos_counts = check_source_counts("AgentOS source", agentos_source, expected_api, expected_actions, failures)

    check_runtime_state = plain_state_dir is not None or agentos_state_dir is not None
    if check_runtime_state and (plain_state_dir is None or agentos_state_dir is None):
        raise ValueError("plain and AgentOS state directories must be supplied together")
    plain_runtime = check_runtime_counts("plain runtime", plain_state_dir, expected_api, expected_actions, failures)
    agentos_runtime = check_runtime_counts("AgentOS runtime", agentos_state_dir, expected_api, expected_actions, failures)

    return {
        "status": "failed" if failures else "ready",
        "host_dir": str(host_dir),
        "host_api_routes": expected_api,
        "host_action_routes": expected_actions,
        "host_download_refs": len(routes["download_refs"]),  # type: ignore[arg-type]
        "api_prefixes": routes["api_prefixes"],
        "action_prefixes": routes["action_prefixes"],
        "runtime_state_checked": check_runtime_state,
        "plain_source": plain_counts,
        "agentos_source": agentos_counts,
        "plain_runtime": plain_runtime,
        "agentos_runtime": agentos_runtime,
        "api_route_sample": api_routes[:12],  # type: ignore[index]
        "action_route_sample": action_routes[:12],  # type: ignore[index]
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check host Web/API/action surface alignment with uCore targets.")
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
        print(f"host_surface_alignment: status=skipped reason={summary['reason']} host_dir={summary['host_dir']}")
        return 0

    print(
        "host_surface_alignment: "
        f"api_routes={summary['host_api_routes']} "
        f"action_routes={summary['host_action_routes']} "
        f"download_refs={summary['host_download_refs']} "
        f"runtime_state_checked={int(bool(summary['runtime_state_checked']))} "
        f"status={summary['status']}"
    )
    if summary["status"] == "failed":
        for failure in summary["failures"]:
            print(f"host_surface_alignment: failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
