#!/usr/bin/env python3
"""Validate the structured one-boot AgentOS console replay transcript."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence


MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class ValidationError(ValueError):
    """A replay artifact does not satisfy the acceptance contract."""


def _reject_constant(value: str) -> object:
    raise ValidationError(f"non-finite JSON value is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_jsonl(path: Path, label: str) -> list[dict[str, object]]:
    try:
        size = path.stat().st_size
        if size > MAX_TRANSCRIPT_BYTES:
            raise ValidationError(f"{label} exceeds {MAX_TRANSCRIPT_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"cannot read {label}: {error}") from error

    records: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, ValidationError) as error:
            raise ValidationError(f"{label}:{line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValidationError(f"{label}:{line_number}: top-level JSON must be an object")
        records.append(value)
    if not records:
        raise ValidationError(f"{label} is empty")
    return records


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int(value: object) -> bool:
    return _is_int(value) and int(value) > 0


def _status_is(record: dict[str, object], expected: int) -> bool:
    value = record.get("status")
    return _is_int(value) and int(value) == expected


def _select(
    records: Sequence[dict[str, object]],
    key: str,
    value: object,
) -> list[dict[str, object]]:
    return [record for record in records if record.get(key) == value]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _fixture_digests(records: Sequence[dict[str, object]]) -> list[str]:
    _require(len(records) == 7, f"fixture must contain exactly 7 responses, got {len(records)}")
    digests: list[str] = []
    for index, record in enumerate(records, 1):
        _require(
            set(record) == {"request_sha256", "response"},
            f"fixture response {index} has unexpected fields",
        )
        digest = record.get("request_sha256")
        _require(
            isinstance(digest, str) and DIGEST_RE.fullmatch(digest) is not None,
            f"fixture response {index} has no valid request_sha256",
        )
        _require(isinstance(record.get("response"), dict), f"fixture response {index} is malformed")
        digests.append(digest)
    _require(len(set(digests)) == 7, "fixture request_sha256 values must be unique")
    return digests


def _validate_tool_events(controller: Sequence[dict[str, object]]) -> None:
    tools = _select(controller, "type", "tool_event")
    for tool_name in ("query_file", "echo"):
        events = _select(tools, "tool", tool_name)
        _require(len(events) == 1, f"expected exactly one {tool_name} tool_event")
        _require(_status_is(events[0], 0), f"{tool_name} did not complete with status=0")

    sends = _select(tools, "tool", "send_message")
    _require(len(sends) == 2, "expected one denied and one approved send_message result")
    denied = [event for event in sends if _status_is(event, -8)]
    succeeded = [event for event in sends if _status_is(event, 0)]
    _require(len(denied) == 1, "denied send_message must be reported exactly once with status=-8")
    _require(
        denied[0].get("result") == "not_approved",
        "denied send_message must remain a non-executed not_approved result",
    )
    _require(len(succeeded) == 1, "approved send_message must execute exactly once with status=0")


def _validate_controller(
    records: Sequence[dict[str, object]], fixture_digests: Sequence[str]
) -> str:
    errors = _select(records, "type", "error") + _select(records, "type", "model_error")
    _require(not errors, "controller transcript contains an error event")

    requests = _select(records, "type", "model_request")
    _require(len(requests) == 7, f"expected 7 model_request events, got {len(requests)}")
    observed_digests = [record.get("request_sha256") for record in requests]
    _require(
        observed_digests == list(fixture_digests),
        "model_request digests do not exactly match the replay fixture order",
    )
    correlations = [record.get("corr_id") for record in requests]
    _require(all(_positive_int(value) for value in correlations), "model_request corr_id is malformed")
    _require(
        correlations == sorted(correlations) and len(set(correlations)) == 7,
        "model_request corr_id values are not strictly increasing",
    )

    completions = _select(records, "type", "turn_complete")
    _require(len(completions) == 3, f"expected 3 turn_complete events, got {len(completions)}")
    _require(
        [record.get("turn_id") for record in completions] == [1, 2, 3],
        "turn_complete events are not the three ordered user turns",
    )
    _require(
        all(record.get("status") == "completed" for record in completions),
        "a replay turn did not complete successfully",
    )

    requests_for_approval = _select(records, "type", "approval_request")
    _require(
        len(requests_for_approval) == 2
        and all(record.get("tool") == "send_message" for record in requests_for_approval),
        "expected two send_message approval requests",
    )
    decisions = _select(records, "type", "approval_decision")
    _require(
        [record.get("decision") for record in decisions] == ["deny", "once"],
        "approval decisions must be exactly deny then once",
    )
    _require(
        [record.get("corr_id") for record in decisions]
        == [record.get("corr_id") for record in requests_for_approval],
        "approval decisions are not bound to their requests",
    )

    _validate_tool_events(records)

    controls = _select(records, "type", "control_result")
    for command in ("tools", "status", "context"):
        matches = _select(controls, "command", command)
        _require(len(matches) == 1, f"expected exactly one /{command} control result")
        _require(matches[0].get("status") == "ok", f"/{command} did not return status=ok")

    ready = _select(records, "type", "session_ready")
    _require(len(ready) == 1, "controller transcript lacks a unique session_ready event")
    session_id = ready[0].get("session_id")
    _require(isinstance(session_id, str) and bool(session_id), "session_ready has no session_id")
    closed = _select(records, "type", "session_closed")
    _require(len(closed) == 1, "controller transcript lacks a unique session_closed event")
    return session_id


def _validate_observer(records: Sequence[dict[str, object]], session_id: str) -> None:
    _require(
        all(record.get("type") == "telemetry" for record in records),
        "observer transcript contains a non-telemetry record",
    )
    attached = _select(records, "event", "observer_attached")
    _require(len(attached) == 1, "observer did not record its attach handshake")
    _require(
        attached[0].get("session_id") == session_id,
        "controller and observer attached to different sessions",
    )

    waiting = _select(records, "event", "waiting_llm")
    _require(waiting, "observer did not receive a waiting_llm snapshot")

    fresh_timeline = [
        record
        for record in records
        if record.get("event") == "kernel_timeline"
        and record.get("source") == "context_timeline"
        and record.get("fresh") is True
        and _positive_int(record.get("record_sequence"))
    ]
    _require(
        fresh_timeline,
        "observer did not receive a fresh context_timeline/kernel_timeline record",
    )

    completed_turns = {
        int(record["turn_id"])
        for record in _select(records, "event", "turn_complete")
        if _positive_int(record.get("turn_id"))
    }
    _require(
        completed_turns.issuperset({1, 2, 3}),
        "observer did not receive turn_complete snapshots for all three turns",
    )
    _require(
        len(_select(records, "event", "session_closed")) == 1,
        "observer did not receive a unique session_closed snapshot",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate controller and observer NDJSON from agentos-console-replay."
    )
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        fixture = _load_jsonl(args.fixture, "replay fixture")
        controller = _load_jsonl(args.controller, "controller transcript")
        observer = _load_jsonl(args.observer, "observer transcript")
        digests = _fixture_digests(fixture)
        session_id = _validate_controller(controller, digests)
        _validate_observer(observer, session_id)
    except ValidationError as error:
        print(f"agentos-console-replay: FAIL: {error}", file=sys.stderr)
        return 1

    print(
        "agentos-console-replay: PASS "
        "(7 digests, 3 turns, governed tools, fresh kernel timeline, clean close)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
