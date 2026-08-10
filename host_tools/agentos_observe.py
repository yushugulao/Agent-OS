#!/usr/bin/env python3
"""Read-only high-signal view of the active AgentOS kernel timeline."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_local_protocol as local  # noqa: E402


def _field(event: Mapping[str, object], *names: str) -> str:
    for name in names:
        value = event.get(name)
        if value not in (None, "", 0):
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            return str(value)
    return "-"


def _state(event: Mapping[str, object]) -> str:
    if event.get("event") in ("llm_request", "waiting_llm"):
        return "WAITING_LLM"
    value = event.get("state", event.get("loop_state"))
    if isinstance(value, int) and not isinstance(value, bool):
        return {0: "NONE", 1: "IDLE", 2: "RUNNING", 3: "WAITING"}.get(value, str(value))
    return str(value) if value not in (None, "") else "-"


def _status(event: Mapping[str, object]) -> str:
    value = event.get("status", event.get("code"))
    if value == 0:
        return "OK"
    return str(value) if value not in (None, "") else "-"


def _provenance(event: Mapping[str, object]) -> str:
    value = event.get("provenance", event.get("labels"))
    if not isinstance(value, int) or isinstance(value, bool):
        return str(value) if value not in (None, "") else "-"
    names = []
    for bit, name in (
        (1 << 1, "TRUSTED_USER_CONTROL"),
        (1 << 3, "UNTRUSTED_FILE_DATA"),
        (1 << 4, "UNTRUSTED_TOOL_OUTPUT"),
        (1 << 5, "CROSS_AGENT_DATA"),
    ):
        if value & bit:
            names.append(name)
    return "|".join(names) if names else ("NONE" if value == 0 else hex(value))


def render_header(output: TextIO) -> None:
    print(
        f"{'tick':>8} {'pid':>5} {'source':<7} {'state':<13} {'event':<24} "
        f"{'tool':<16} {'corr':>6} {'status':<14} {'ctx':>6} provenance",
        file=output,
    )
    print("-" * 120, file=output)
    output.flush()


def render_event(event: Mapping[str, object], output: TextIO, *, json_events: bool) -> None:
    if json_events:
        print(
            json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            file=output,
            flush=True,
        )
        return
    print(
        f"{_field(event, 'tick'):>8} "
        f"{_field(event, 'pid', 'agent_pid', 'control_id'):>5} "
        f"{_field(event, 'source'):<7.7} "
        f"{_state(event):<13.13} "
        f"{_field(event, 'event', 'kind'):<24.24} "
        f"{_field(event, 'tool'):<16.16} "
        f"{_field(event, 'corr_id'):>6} "
        f"{_status(event):<14.14} "
        f"{_field(event, 'context_seq', 'sequence'):>6} "
        f"{_provenance(event)}",
        file=output,
        flush=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe the active AgentOS kernel timeline.")
    parser.add_argument("--attach", choices=("latest",), default="latest")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--count", type=int, default=0, help="Exit after N telemetry events.")
    parser.add_argument("--until-event", default="", help="Exit after this exact event name.")
    parser.add_argument("--json-events", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    connection: socket.socket | None = None
    stream = None
    try:
        args = _parser().parse_args(argv)
        if args.count < 0:
            raise ValueError("--count must not be negative")
        state = local.load_state(args.state_file)
        connection = local.connect_from_state(state, role="observer")
        stream = connection.makefile("rb")
        welcome = local.recv_one(stream)
        if welcome.get("type") == "error":
            raise local.LocalProtocolError(f"observer rejected: {welcome.get('code')}")
        if welcome.get("type") != "welcome" or welcome.get("role") != "observer":
            raise local.LocalProtocolError("observer handshake is malformed")
        if not args.json_events:
            render_header(sys.stdout)
        seen = 0
        while True:
            event = local.recv_one(stream)
            if event.get("type") != "telemetry":
                continue
            render_event(event, sys.stdout, json_events=args.json_events)
            seen += 1
            if args.count and seen >= args.count:
                return 0
            if args.until_event and event.get("event") == args.until_event:
                return 0
    except KeyboardInterrupt:
        return 0
    except (local.LocalProtocolError, OSError, ValueError) as error:
        print(f"agentos-observe: {error}", file=sys.stderr)
        return 1
    finally:
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
