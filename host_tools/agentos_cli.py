#!/usr/bin/env python3
"""Codex-style interactive controller for a running AgentOS relay daemon."""

from __future__ import annotations

import argparse
import io
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence, TextIO

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import agentos_local_protocol as local  # noqa: E402


APPROVAL_BINDING_FIELDS = (
    "turn_id",
    "request_id",
    "corr_id",
    "tool",
    "arguments_sha256",
    "nonce",
)
NEXUS_APPROVAL_BINDING_FIELDS = APPROVAL_BINDING_FIELDS + (
    "tool_id",
    "issued_tick",
    "expires_tick",
)


class ConsoleConnection:
    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.stream = connection.makefile("rb")
        self.events: list[dict[str, object]] = []
        self.condition = threading.Condition()
        self.closed = False
        self.guest_profile = "agentlive"
        self.approval_binding: dict[str, object] | None = None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def send(self, message: Mapping[str, object]) -> None:
        try:
            self.connection.sendall(local.encode_message(message))
        except OSError as error:
            raise local.LocalProtocolError("AgentOS controller connection closed") from error

    def next(self, timeout: float | None = None) -> dict[str, object]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.condition:
            while not self.events:
                if self.closed:
                    raise local.LocalProtocolError("AgentOS controller connection closed")
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise local.LocalProtocolError("timed out waiting for AgentOS")
                self.condition.wait(remaining)
            return self.events.pop(0)

    def close(self) -> None:
        try:
            self.connection.close()
        except OSError:
            pass

    def _read(self) -> None:
        try:
            while True:
                message = local.recv_one(self.stream)
                with self.condition:
                    self.events.append(message)
                    self.condition.notify_all()
        except (OSError, local.LocalProtocolError):
            pass
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _terminal_text(value: object) -> str:
    text = str(value)
    rendered: list[str] = []
    for char in text:
        code = ord(char)
        if char in ("\n", "\t") or (code >= 0x20 and not 0x7F <= code <= 0x9F):
            rendered.append(char)
        elif code <= 0xFF:
            rendered.append(f"\\x{code:02x}")
        else:
            rendered.append(f"\\u{code:04x}")
    return "".join(rendered)


def _display_value(value: object) -> str:
    if isinstance(value, dict):
        return _terminal_text(_json(value))
    if isinstance(value, list):
        return _terminal_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        )
    return _terminal_text(value)


def _guest_profile(value: Mapping[str, object]) -> str:
    profile = value.get("guest_profile", "agentlive")
    if not isinstance(profile, str) or profile not in local.GUEST_PROFILES:
        raise local.LocalProtocolError("AgentOS Guest profile is malformed")
    return profile


def _task_identity(event: Mapping[str, object]) -> str:
    role = event.get("agent_role", event.get("role", "agent"))
    task_id = event.get("task_id", "?")
    agent_id = event.get("agent_id", event.get("agent_pid", "?"))
    return f"{role} agent={agent_id} task={task_id}"


def render_event(event: Mapping[str, object], output: TextIO, *, json_events: bool) -> None:
    if json_events:
        print(_json(event), file=output, flush=True)
        return
    kind = event.get("type")
    if kind == "welcome":
        return
    if kind == "session_ready":
        profile = event.get("guest_profile", "agentlive")
        suffix = f" profile={profile}" if profile == "nexus" else ""
        print(f"AgentOS session {event.get('session_id', '')[:12]} ready{suffix}", file=output)
    elif kind == "turn_started":
        print(
            f"thinking...  [turn {event.get('turn_id')} request {event.get('request_id')}]",
            file=output,
        )
    elif kind == "model_request":
        identity = (
            f" {_task_identity(event)}" if "task_id" in event else ""
        )
        print(
            f"  model request corr={event.get('corr_id')} round={event.get('round')}{identity}",
            file=output,
        )
    elif kind == "model_response":
        if event.get("response_type") == "tool_use":
            print(
                f"-> {_terminal_text(event.get('tool'))} "
                f"{_display_value(event.get('arguments', {}))}",
                file=output,
            )
        # Final text is rendered once from TURN_COMPLETE, after the Guest has
        # committed its Context and returned to IDLE.
        else:
            pass
    elif kind == "tool_event":
        direction = "<-" if event.get("status") is not None else "->"
        detail = event.get("result", event.get("message", ""))
        identity = f" [{_task_identity(event)}]" if "task_id" in event else ""
        print(
            f"{direction} {_terminal_text(event.get('tool', event.get('event', 'tool')))}"
            f" status={_terminal_text(event.get('status', ''))}{identity} "
            f"{_display_value(detail)}",
            file=output,
        )
    elif kind == "task_event":
        event_name = str(event.get("event", "progress"))
        verbs = {
            "assigned": "Coordinator delegated",
            "accepted": "accepted",
            "progress": "progress",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "artifact_published": "published artifact",
        }
        detail = event.get("summary", "")
        artifact = event.get("artifact_handle", "")
        resource = event.get("resource_used", "")
        extras = " ".join(
            value
            for value in (
                f"artifact={artifact}" if artifact not in ("", None, 0) else "",
                f"resource={resource}" if resource not in ("", None, 0) else "",
                _terminal_text(detail) if detail else "",
            )
            if value
        )
        print(
            f"  {verbs.get(event_name, event_name)} {_task_identity(event)}"
            f" state={event.get('task_state')} status={event.get('status')}"
            f"{(' ' + extras) if extras else ''}",
            file=output,
        )
    elif kind == "approval_request":
        display = event.get("display", event.get("arguments", ""))
        print("The model wants to call:", file=output)
        identity = f" [{_task_identity(event)}]" if "task_id" in event else ""
        print(
            f"  {_terminal_text(event.get('tool'))}{identity} {_display_value(display)}",
            file=output,
        )
        print("Approve with /approve, /approve session, or /deny", file=output)
    elif kind == "approval_decision":
        print(
            f"approval {event.get('decision')} for {event.get('tool')}"
            f" corr={event.get('corr_id')}",
            file=output,
        )
    elif kind == "control_result":
        result = event.get("result", event.get("message", ""))
        print(f"/{event.get('command')}: {_display_value(result)}", file=output)
    elif kind == "daemon_status":
        profile = event.get("guest_profile", "agentlive")
        profile_text = f"profile={profile} " if profile == "nexus" else ""
        print(
            f"session={event.get('session_id', '')[:12]} {profile_text}ready={event.get('ready')} "
            f"turn={event.get('active_turn')} round={event.get('round')} "
            f"model_wait={event.get('waiting_model')} approval_wait={event.get('waiting_approval')}",
            file=output,
        )
    elif kind == "turn_cancelling":
        print("cancelling current turn...", file=output)
    elif kind == "turn_complete":
        if event.get("answer"):
            print(_terminal_text(event["answer"]), file=output)
        print(
            f"[turn {event.get('turn_id')} {event.get('status')}]",
            file=output,
        )
    elif kind == "session_closing":
        print("closing AgentOS session...", file=output)
    elif kind == "session_closed":
        print("AgentOS session closed", file=output)
    elif kind == "error":
        print(
            f"error: {_terminal_text(event.get('code'))}: "
            f"{_terminal_text(event.get('message', ''))}",
            file=output,
        )
    elif kind not in ("pong", "idle"):
        print(_json(event), file=output)
    output.flush()


def _send_approval(connection: ConsoleConnection, decision: str) -> None:
    binding = connection.approval_binding
    binding_fields = (
        NEXUS_APPROVAL_BINDING_FIELDS
        if getattr(connection, "guest_profile", "agentlive") == "nexus"
        else APPROVAL_BINDING_FIELDS
    )
    if binding is None or set(binding) != set(binding_fields):
        raise local.LocalProtocolError("approval prompt has no valid binding")
    connection.send({"type": "approval", "decision": decision, **binding})


def _capture_approval_binding(
    connection: ConsoleConnection, event: Mapping[str, object]
) -> None:
    nexus = getattr(connection, "guest_profile", "agentlive") == "nexus"
    binding_fields = (
        NEXUS_APPROVAL_BINDING_FIELDS if nexus else APPROVAL_BINDING_FIELDS
    )
    binding = {key: event.get(key) for key in binding_fields}
    for key in ("turn_id", "request_id", "corr_id"):
        value = binding[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise local.LocalProtocolError("approval request binding is malformed")
    for key in ("tool", "arguments_sha256", "nonce"):
        if not isinstance(binding[key], str) or not binding[key]:
            raise local.LocalProtocolError("approval request binding is malformed")
    if nexus:
        tool_id = binding["tool_id"]
        issued_tick = binding["issued_tick"]
        expires_tick = binding["expires_tick"]
        if (
            not isinstance(tool_id, int)
            or isinstance(tool_id, bool)
            or tool_id <= 0
            or not isinstance(issued_tick, int)
            or isinstance(issued_tick, bool)
            or issued_tick < 0
            or not isinstance(expires_tick, int)
            or isinstance(expires_tick, bool)
            or expires_tick <= issued_tick
        ):
            raise local.LocalProtocolError("approval request binding is malformed")
    connection.approval_binding = binding


def _send_command(
    connection: ConsoleConnection, line: str, *, approval_pending: bool = False
) -> str:
    approval_text = line.strip()
    if approval_pending and approval_text.lower() in ("", "n", "no"):
        _send_approval(connection, "deny")
        return "approval"
    if approval_pending and approval_text.lower() in ("y", "yes"):
        _send_approval(connection, "once")
        return "approval"
    if approval_pending and approval_text.lower() in ("a", "always"):
        _send_approval(connection, "session")
        return "approval"
    if line == "":
        return "idle"
    command = line.lower()
    if approval_pending and command not in (
        "/approve",
        "/approve session",
        "/deny",
        "/quit",
    ):
        raise local.LocalProtocolError("answer approval with y, n, a, /approve or /deny")
    if command in (
        "/tools",
        "/context",
        "/status",
        "/reset",
        "/agents",
        "/tasks",
        "/artifacts",
    ):
        connection.send({"type": "command", "command": command[1:]})
        return "control"
    if command in ("/approve", "/approve session"):
        _send_approval(connection, "session" if command.endswith(" session") else "once")
        return "approval"
    if command == "/deny":
        _send_approval(connection, "deny")
        return "approval"
    if command == "/quit":
        connection.send({"type": "session_close"})
        return "quit"
    # Only an exact built-in command is control syntax.  Everything else is
    # user content, including leading/trailing spaces and goals beginning '/'.
    connection.send({"type": "user_message", "content": line})
    return "turn"


def _wait_gate(
    connection: ConsoleConnection,
    action: str,
    output: TextIO,
    *,
    json_events: bool,
    timeout: float | None,
) -> str:
    terminal: dict[str, set[str]] = {
        "turn": {"approval_request", "turn_complete", "error", "session_closed"},
        "approval": {"approval_request", "turn_complete", "error", "session_closed"},
        "control": {"control_result", "error", "session_closed"},
        "quit": {"session_closed", "error"},
    }
    if action not in terminal:
        return action
    while True:
        try:
            event = connection.next(timeout)
        except KeyboardInterrupt:
            if action in ("turn", "approval"):
                connection.send({"type": "cancel"})
                print("", file=output)
                action = "turn"
                continue
            raise
        render_event(event, output, json_events=json_events)
        kind = str(event.get("type", ""))
        if kind == "approval_request":
            _capture_approval_binding(connection, event)
        elif kind in ("turn_complete", "session_closed"):
            connection.approval_binding = None
        if kind in terminal[action]:
            return kind


def _lines(args: argparse.Namespace) -> tuple[TextIO, bool]:
    if args.script is not None:
        return args.script.open("r", encoding="utf-8"), True
    return sys.stdin, not sys.stdin.isatty()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Attach to the active AgentOS console.")
    parser.add_argument("--attach", choices=("latest",), default="latest")
    parser.add_argument("--state-file", type=Path)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--script", type=Path)
    input_group.add_argument(
        "--task-file",
        type=Path,
        help="Submit the complete UTF-8 file as one exact task.",
    )
    parser.add_argument("--json-events", action="store_true")
    parser.add_argument("--event-timeout", type=float, default=0.0)
    parser.add_argument(
        "--expect-guest-profile",
        choices=tuple(sorted(local.GUEST_PROFILES)),
        default="",
    )
    parser.add_argument(
        "--no-close-on-eof",
        action="store_true",
        help="Leave the daemon running after non-interactive input reaches EOF.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    connection: ConsoleConnection | None = None
    source: TextIO | None = None
    try:
        args = _parser().parse_args(argv)
        state = local.load_state(args.state_file)
        state_profile = _guest_profile(state)
        if args.expect_guest_profile and state_profile != args.expect_guest_profile:
            raise local.LocalProtocolError(
                "active AgentOS console has an unexpected Guest profile"
            )
        connection = ConsoleConnection(local.connect_from_state(state, role="controller"))
        connection.guest_profile = state_profile
        task_file_content: str | None = None
        if args.task_file is not None:
            with args.task_file.open("r", encoding="utf-8", newline="") as task_file:
                task_file_content = task_file.read()
            source, scripted = io.StringIO(""), True
        else:
            source, scripted = _lines(args)
        timeout = args.event_timeout or None
        first = connection.next(5.0)
        render_event(first, sys.stdout, json_events=args.json_events)
        if first.get("type") != "welcome" or first.get("role") != "controller":
            raise local.LocalProtocolError("AgentOS controller handshake is malformed")
        while True:
            ready = connection.next(5.0)
            render_event(ready, sys.stdout, json_events=args.json_events)
            if ready.get("type") == "session_ready":
                ready_profile = _guest_profile(ready)
                if ready_profile != state_profile or (
                    args.expect_guest_profile
                    and ready_profile != args.expect_guest_profile
                ):
                    raise local.LocalProtocolError(
                        "AgentOS session_ready Guest profile does not match"
                    )
                break
            if ready.get("type") == "error":
                raise local.LocalProtocolError(
                    f"AgentOS session failed before ready: {ready.get('code', 'UNKNOWN')}"
                )
        if not scripted:
            print(
                f"AgentOS CLI  Model: {state.get('model', '') or state.get('provider', '')}\n",
                flush=True,
            )
        approval_pending = False
        while True:
            try:
                if scripted:
                    exact_task = task_file_content is not None
                    if exact_task:
                        line = task_file_content
                        task_file_content = None
                        raw = "<task-file>"
                    else:
                        raw = source.readline()
                    if not raw:
                        if approval_pending:
                            action = _send_command(
                                connection, "", approval_pending=True
                            )
                            gate = _wait_gate(
                                connection,
                                action,
                                sys.stdout,
                                json_events=args.json_events,
                                timeout=timeout,
                            )
                            approval_pending = gate == "approval_request"
                            if approval_pending:
                                continue
                        break
                    if not exact_task:
                        line = raw.rstrip("\r\n")
                        if not line and not approval_pending:
                            continue
                    if not args.json_events:
                        shown = "<task-file>" if exact_task else line
                        print(f"agentos> {shown}", flush=True)
                else:
                    prompt = "Approve? [y/N/a] " if approval_pending else "agentos> "
                    line = input(prompt)
            except EOFError:
                break
            except KeyboardInterrupt:
                print("")
                if approval_pending:
                    connection.send({"type": "cancel"})
                    gate = _wait_gate(
                        connection,
                        "turn",
                        sys.stdout,
                        json_events=args.json_events,
                        timeout=timeout,
                    )
                    approval_pending = gate == "approval_request"
                continue
            try:
                if scripted and exact_task:
                    connection.send({"type": "user_message", "content": line})
                    action = "turn"
                else:
                    action = _send_command(
                        connection, line, approval_pending=approval_pending
                    )
                gate = _wait_gate(
                    connection,
                    action,
                    sys.stdout,
                    json_events=args.json_events,
                    timeout=timeout,
                )
                approval_pending = gate == "approval_request"
                if gate == "error" and action == "quit":
                    return 1
                if gate == "session_closed":
                    return 0
            except local.LocalProtocolError as error:
                print(f"agentos-cli: {error}", file=sys.stderr)
                if scripted:
                    return 1
        if not args.no_close_on_eof:
            connection.send({"type": "session_close"})
            gate = _wait_gate(
                connection,
                "quit",
                sys.stdout,
                json_events=args.json_events,
                timeout=timeout,
            )
            if gate == "error":
                return 1
        return 0
    except (local.LocalProtocolError, OSError, ValueError) as error:
        print(f"agentos-cli: {error}", file=sys.stderr)
        return 1
    finally:
        if source is not None and source is not sys.stdin:
            source.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
