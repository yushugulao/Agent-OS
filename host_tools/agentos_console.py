#!/usr/bin/env python3
"""Single entry point for AgentOS daemon, CLI and observer processes."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_cli  # noqa: E402
import agentos_local_protocol as local  # noqa: E402
import agentos_observe  # noqa: E402
import agentos_relayd  # noqa: E402


def _split_run_arguments(values: Sequence[str]) -> tuple[list[str], list[str], Path | None]:
    daemon: list[str] = []
    client: list[str] = []
    runtime_dir: Path | None = None
    index = 0
    client_value_options = {"--script", "--event-timeout"}
    client_flags = {"--json-events", "--no-close-on-eof"}
    while index < len(values):
        value = values[index]
        if value == "--":
            client.extend(values[index + 1 :])
            break
        if value in client_flags:
            client.append(value)
            index += 1
            continue
        if value in client_value_options:
            if index + 1 >= len(values):
                raise ValueError(f"{value} requires a value")
            client.extend(values[index : index + 2])
            index += 2
            continue
        daemon.append(value)
        if value == "--runtime-dir":
            if index + 1 >= len(values):
                raise ValueError("--runtime-dir requires a value")
            runtime_dir = Path(values[index + 1])
            daemon.append(values[index + 1])
            index += 2
            continue
        index += 1
    return daemon, client, runtime_dir


def _state_path(runtime_dir: Path | None) -> Path:
    base = runtime_dir.resolve() if runtime_dir else local.runtime_base()
    uid = int(os.getuid()) if hasattr(os, "getuid") else 0
    return base / f"agentos-{uid}" / local.STATE_FILE_NAME


def _terminate_child_group(
    process: subprocess.Popen[object], *, grace_seconds: float = 3.0
) -> None:
    """Bounded TERM -> KILL -> wait for the daemon process group."""

    if process.poll() is not None:
        try:
            process.wait(timeout=0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=grace_seconds)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=grace_seconds)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_combined(values: Sequence[str]) -> int:
    daemon_args, client_args, runtime_dir = _split_run_arguments(values)
    state_file = _state_path(runtime_dir)
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        str(_HERE / "agentos_relayd.py"),
        *daemon_args,
        "--quiet",
    ]
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    try:
        deadline = time.monotonic() + 150.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return int(process.returncode) if process.returncode is not None else 1
            try:
                state = local.load_state(state_file)
                if state.get("pid") == process.pid:
                    break
            except local.LocalProtocolError:
                pass
            time.sleep(0.05)
        else:
            raise local.LocalProtocolError("AgentOS relay daemon did not publish its state")
        result = agentos_cli.main(["--state-file", str(state_file), *client_args])
        if result != 0:
            _terminate_child_group(process)
            return result
        if "--no-close-on-eof" in client_args and process.poll() is None:
            return 0
        try:
            daemon_result = process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            _terminate_child_group(process)
            return 1
        return 0 if daemon_result == 0 else int(daemon_result or 1)
    except KeyboardInterrupt:
        _terminate_child_group(process)
        return 130
    except (OSError, local.LocalProtocolError, ValueError) as error:
        print(f"agentos-console: {error}", file=sys.stderr)
        _terminate_child_group(process)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or attach to the Codex-style AgentOS console."
    )
    parser.add_argument("command", choices=("run", "daemon", "cli", "observe"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    values = list(args.arguments)
    if values[:1] == ["--"]:
        values = values[1:]
    if args.command == "run":
        return run_combined(values)
    if args.command == "daemon":
        return agentos_relayd.main(values)
    if args.command == "cli":
        return agentos_cli.main(values)
    return agentos_observe.main(values)


if __name__ == "__main__":
    raise SystemExit(main())
