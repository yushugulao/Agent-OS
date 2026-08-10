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


def _guest_profile(value: Mapping[str, object]) -> str:
    profile = value.get("guest_profile", "agentlive")
    if not isinstance(profile, str) or profile not in local.GUEST_PROFILES:
        raise local.LocalProtocolError("AgentOS Guest profile is malformed")
    return profile


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
    value = event.get("state", event.get("loop_state", event.get("task_state")))
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


def _lifecycle(event: Mapping[str, object]) -> str:
    lifecycle_id = event.get("workflow_lifecycle_id")
    generation = event.get("workflow_lifecycle_generation")
    if lifecycle_id in (None, "", 0) or generation in (None, "", 0):
        return "-"
    return f"{lifecycle_id}:{generation}"


def _waits(event: Mapping[str, object]) -> str:
    sleep = event.get("wait_sleep_delta", event.get("wait_sleep_count"))
    wake = event.get("wait_wakeup_delta", event.get("wait_wakeup_count"))
    if sleep in (None, "") and wake in (None, ""):
        return "-"
    return f"{sleep or 0}/{wake or 0}"


def _route(event: Mapping[str, object]) -> str:
    source = event.get("source_pid")
    target = event.get("target_pid")
    if source in (None, "", 0) and target in (None, "", 0):
        return "-"
    return f"{source or 0}->{target or 0}"


def _capabilities(event: Mapping[str, object]) -> str:
    value = event.get("capability_mask")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return "-"
    return f"caps=0x{value:x}"


def render_header(output: TextIO) -> None:
    print(
        f"{'tick':>8} {'pid':>5} {'agent':>5} {'control':>9} {'role':<11} "
        f"{'lifecycle':>13} {'task':>6} {'corr':>6} "
        f"{'state':<13} {'event':<20} {'tool':<14} {'status':<10} "
        f"{'ctx':>6} {'record':>7} {'route':>11} {'wait s/w':>9} "
        f"{'budget':>8} {'vruntime':>9} {'capabilities':>23} {'resource':>8} "
        f"{'reason':<18} {'source':<16} provenance",
        file=output,
    )
    print("-" * 296, file=output)
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
        f"{_field(event, 'pid', 'agent_pid'):>5} "
        f"{_field(event, 'agent_id'):>5} "
        f"{_field(event, 'actor_control_id', 'agent_control_id', 'control_id'):>9} "
        f"{_field(event, 'agent_role', 'role'):<11.11} "
        f"{_lifecycle(event):>13.13} "
        f"{_field(event, 'task_id'):>6} "
        f"{_field(event, 'corr_id'):>6} "
        f"{_state(event):<13.13} "
        f"{_field(event, 'event', 'kind'):<20.20} "
        f"{_field(event, 'tool', 'tool_id'):<14.14} "
        f"{_status(event):<10.10} "
        f"{_field(event, 'context_seq'):>6} "
        f"{_field(event, 'record_sequence', 'sequence'):>7} "
        f"{_route(event):>11.11} "
        f"{_waits(event):>9.9} "
        f"{_field(event, 'sched_budget_used'):>8} "
        f"{_field(event, 'sched_vruntime'):>9} "
        f"{_capabilities(event):>23.23} "
        f"{_field(event, 'resource_used'):>8} "
        f"{_field(event, 'reason'):<18.18} "
        f"{_field(event, 'source'):<16.16} "
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
    parser.add_argument(
        "--expect-guest-profile",
        choices=tuple(sorted(local.GUEST_PROFILES)),
        default="",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    connection: socket.socket | None = None
    stream = None
    try:
        args = _parser().parse_args(argv)
        if args.count < 0:
            raise ValueError("--count must not be negative")
        state = local.load_state(args.state_file)
        state_profile = _guest_profile(state)
        if args.expect_guest_profile and state_profile != args.expect_guest_profile:
            raise local.LocalProtocolError(
                "active AgentOS console has an unexpected Guest profile"
            )
        connection = local.connect_from_state(state, role="observer")
        stream = connection.makefile("rb")
        welcome = local.recv_one(stream)
        if welcome.get("type") == "error":
            raise local.LocalProtocolError(f"observer rejected: {welcome.get('code')}")
        if welcome.get("type") != "welcome" or welcome.get("role") != "observer":
            raise local.LocalProtocolError("observer handshake is malformed")
        if not args.json_events:
            render_header(sys.stdout)
        attached = local.recv_one(stream)
        if (
            attached.get("type") != "telemetry"
            or attached.get("event") != "observer_attached"
        ):
            raise local.LocalProtocolError("observer attach snapshot is malformed")
        attached_profile = _guest_profile(attached)
        if attached_profile != state_profile or (
            args.expect_guest_profile
            and attached_profile != args.expect_guest_profile
        ):
            raise local.LocalProtocolError(
                "observer Guest profile does not match active state"
            )
        render_event(attached, sys.stdout, json_events=args.json_events)
        seen = 1
        if args.count and seen >= args.count:
            return 0
        if args.until_event and attached.get("event") == args.until_event:
            return 0
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
