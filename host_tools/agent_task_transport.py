#!/usr/bin/env python3
"""Transport-neutral Agent Task Channel model and deterministic test adapter.

The protocol deliberately carries only values that a future binary SQ/CQ adapter
can validate.  JSON, remote identities, OAuth credentials, and protocol-specific
objects belong in the user-space gateway, not in this interface.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping, Protocol, runtime_checkable


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROVENANCE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class TaskTransportError(RuntimeError):
    """Base class for Task Channel transport failures."""


class InvalidIssuerError(TaskTransportError):
    """The caller is not the channel's bound single issuer."""


class UnknownRequestError(TaskTransportError):
    """A binding does not identify a live request in this channel generation."""


class DuplicateRequestError(TaskTransportError):
    """A submission key was reused for different request bytes."""


class InvalidTaskTransitionError(TaskTransportError):
    """A requested state transition is not legal."""


class TaskStatus(str, Enum):
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskEventKind(str, Enum):
    STATUS = "status"
    ARTIFACT = "artifact"


TERMINAL_TASK_STATUSES = frozenset(
    (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
)


@dataclass(frozen=True)
class TaskChannelIssuer:
    """Complete single-issuer and lifecycle binding for one channel."""

    issuer_id: str
    lifecycle_id: int
    lifecycle_generation: int
    channel_generation: int

    def __post_init__(self) -> None:
        if not self.issuer_id or len(self.issuer_id) > 256:
            raise ValueError("issuer_id must be a non-empty bounded string")
        if (
            type(self.lifecycle_id) is not int
            or self.lifecycle_id <= 0
            or self.lifecycle_id > (1 << 32) - 1
        ):
            raise ValueError("lifecycle_id must be a positive uint32")
        for name in ("lifecycle_generation", "channel_generation"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0 or value > (1 << 64) - 1:
                raise ValueError(f"{name} must be a positive uint64")


@dataclass(frozen=True)
class TaskResourceHandle:
    """Generation-checked resource handle, independent of its future ABI layout."""

    slot: int
    generation: int
    owned: bool = True

    def __post_init__(self) -> None:
        if type(self.slot) is not int or self.slot < 0 or self.slot > (1 << 32) - 1:
            raise ValueError("slot must be a uint32")
        if (
            type(self.generation) is not int
            or self.generation <= 0
            or self.generation > (1 << 64) - 1
        ):
            raise ValueError("generation must be a positive uint64")
        if type(self.owned) is not bool:
            raise ValueError("owned must be a bool")


@dataclass(frozen=True)
class TaskChannelRequest:
    """Copied-and-validated logical SQE used by the gateway transport."""

    tool_id: int
    schema_digest: str
    contract_node_id: int
    contract_generation: int
    attempt_id: int
    payload: object
    submission_key: str
    input_handle: TaskResourceHandle | None = None
    deadline_ns: int | None = None
    link_request_id: int | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.tool_id) is not int or self.tool_id <= 0 or self.tool_id > (1 << 16) - 1:
            raise ValueError("tool_id must fit the Task Channel uint16 field")
        if _SHA256_RE.fullmatch(self.schema_digest) is None:
            raise ValueError("schema_digest must be lowercase SHA-256")
        if (
            type(self.contract_node_id) is not int
            or self.contract_node_id < 0
            or self.contract_node_id > (1 << 32) - 1
        ):
            raise ValueError("contract_node_id must be a uint32")
        if (
            type(self.contract_generation) is not int
            or self.contract_generation <= 0
            or self.contract_generation > (1 << 64) - 1
        ):
            raise ValueError("contract_generation must be a positive uint64")
        if type(self.attempt_id) is not int or self.attempt_id <= 0 or self.attempt_id > 0xFFFF:
            raise ValueError("attempt_id must fit the Task Channel uint16 field")
        if not self.submission_key or len(self.submission_key) > 256:
            raise ValueError("submission_key must be a non-empty bounded string")
        if self.deadline_ns is not None and (
            type(self.deadline_ns) is not int
            or self.deadline_ns <= 0
            or self.deadline_ns > (1 << 64) - 1
        ):
            raise ValueError("deadline_ns must be a positive uint64 when present")
        if self.link_request_id is not None and (
            type(self.link_request_id) is not int
            or self.link_request_id <= 0
            or self.link_request_id > (1 << 64) - 1
        ):
            raise ValueError("link_request_id must be a positive uint64 when present")
        if type(self.provenance) is not tuple:
            raise ValueError("provenance must be a tuple")
        if len(self.provenance) > 16 or len(set(self.provenance)) != len(self.provenance):
            raise ValueError("provenance labels must be unique and bounded")
        if any(_PROVENANCE_RE.fullmatch(label) is None for label in self.provenance):
            raise ValueError("provenance contains an invalid fixed label")


@dataclass(frozen=True)
class TaskBinding:
    """Durable mapping target stored behind an opaque protocol task ID."""

    lifecycle_id: int
    lifecycle_generation: int
    contract_generation: int
    channel_generation: int
    request_id: int

    def __post_init__(self) -> None:
        if (
            type(self.lifecycle_id) is not int
            or self.lifecycle_id <= 0
            or self.lifecycle_id > (1 << 32) - 1
        ):
            raise ValueError("lifecycle_id must be a positive uint32")
        for name in (
            "lifecycle_generation",
            "contract_generation",
            "channel_generation",
            "request_id",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0 or value > (1 << 64) - 1:
                raise ValueError(f"{name} must be a positive uint64")


@dataclass(frozen=True)
class TaskChannelEvent:
    sequence: int
    kind: TaskEventKind
    status: TaskStatus
    context_sequence: int
    evidence_ticket: int
    status_message: str | None = None
    result: object | None = None
    result_handle: TaskResourceHandle | None = None
    error: Mapping[str, object] | None = None
    input_requests: Mapping[str, object] | None = None
    artifact: Mapping[str, object] | None = None
    artifact_index: int | None = None
    append: bool = False
    last_chunk: bool = False
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskChannelSnapshot:
    binding: TaskBinding
    status: TaskStatus
    latest_sequence: int
    context_sequence: int
    evidence_ticket: int
    status_message: str | None = None
    result: object | None = None
    result_handle: TaskResourceHandle | None = None
    error: Mapping[str, object] | None = None
    input_requests: Mapping[str, object] | None = None
    provenance: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskChannelSubmission:
    binding: TaskBinding
    snapshot: TaskChannelSnapshot
    duplicate: bool = False


@dataclass(frozen=True)
class TaskChannelOutcome:
    """A deterministic handler result used by the in-memory adapter."""

    status: TaskStatus = TaskStatus.WORKING
    status_message: str | None = None
    result: object | None = None
    error: Mapping[str, object] | None = None
    input_requests: Mapping[str, object] | None = None
    provenance: tuple[str, ...] = ()


@runtime_checkable
class TaskChannelTransport(Protocol):
    """Narrow interface implemented later by the binary SQ/CQ adapter."""

    def submit(
        self, issuer: TaskChannelIssuer, request: TaskChannelRequest
    ) -> TaskChannelSubmission:
        ...

    def snapshot(
        self, issuer: TaskChannelIssuer, binding: TaskBinding
    ) -> TaskChannelSnapshot:
        ...

    def update(
        self,
        issuer: TaskChannelIssuer,
        binding: TaskBinding,
        input_responses: Mapping[str, object],
    ) -> TaskChannelSnapshot:
        ...

    def cancel(
        self, issuer: TaskChannelIssuer, binding: TaskBinding
    ) -> TaskChannelSnapshot:
        ...

    def events(
        self,
        issuer: TaskChannelIssuer,
        binding: TaskBinding,
        *,
        after_sequence: int = 0,
    ) -> tuple[TaskChannelEvent, ...]:
        ...


TaskHandler = Callable[[TaskChannelRequest], TaskChannelOutcome | object]


@dataclass
class _RequestState:
    request: TaskChannelRequest
    binding: TaskBinding
    status: TaskStatus
    events: list[TaskChannelEvent] = field(default_factory=list)
    status_message: str | None = None
    result: object | None = None
    result_handle: TaskResourceHandle | None = None
    error: Mapping[str, object] | None = None
    input_requests: Mapping[str, object] | None = None
    provenance: tuple[str, ...] = ()


class InMemoryTaskChannelTransport:
    """Deterministic, thread-safe single-issuer Task Channel adapter.

    It snapshots submissions with ``deepcopy`` before dispatch to model the
    kernel's copy-before-validation TOCTOU boundary.  ``publish`` and
    ``publish_artifact`` are deterministic driver hooks for unit tests and
    user-space protocol simulations; they are not part of the transport
    Protocol implemented by a real SQ/CQ backend.
    """

    def __init__(
        self,
        issuer: TaskChannelIssuer,
        *,
        handlers: Mapping[int, TaskHandler] | None = None,
        first_request_id: int = 1,
    ) -> None:
        if type(first_request_id) is not int or first_request_id <= 0:
            raise ValueError("first_request_id must be positive")
        self._issuer = issuer
        self._handlers = dict(handlers or {})
        self._next_request_id = first_request_id
        self._next_event_sequence = 1
        self._next_context_sequence = 1
        self._next_evidence_ticket = 1
        self._next_handle_slot = 1
        self._states: dict[int, _RequestState] = {}
        self._submission_keys: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()
        self.submitted_requests = 0
        self.handler_invocations = 0
        self.cancel_transitions = 0

    @staticmethod
    def _request_fingerprint(request: TaskChannelRequest) -> str:
        try:
            payload = json.dumps(
                request.payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("request payload must be deterministic JSON data") from exc
        framed = {
            "contract_node_id": request.contract_node_id,
            "contract_generation": request.contract_generation,
            "attempt_id": request.attempt_id,
            "deadline_ns": request.deadline_ns,
            "input_handle": None
            if request.input_handle is None
            else {
                "generation": request.input_handle.generation,
                "owned": request.input_handle.owned,
                "slot": request.input_handle.slot,
            },
            "link_request_id": request.link_request_id,
            "payload": payload,
            "provenance": list(request.provenance),
            "schema_digest": request.schema_digest,
            "tool_id": request.tool_id,
        }
        raw = json.dumps(
            framed,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(raw).hexdigest()

    def _require_issuer(self, issuer: TaskChannelIssuer) -> None:
        if issuer != self._issuer:
            raise InvalidIssuerError("Task Channel operation came from the wrong issuer")

    def _state(self, binding: TaskBinding) -> _RequestState:
        if (
            binding.lifecycle_id != self._issuer.lifecycle_id
            or binding.lifecycle_generation != self._issuer.lifecycle_generation
            or binding.channel_generation != self._issuer.channel_generation
        ):
            raise UnknownRequestError("stale or foreign lifecycle/channel binding")
        state = self._states.get(binding.request_id)
        if state is None or state.binding != binding:
            raise UnknownRequestError("unknown request binding")
        return state

    @staticmethod
    def _copy_mapping(value: Mapping[str, object] | None) -> Mapping[str, object] | None:
        if value is None:
            return None
        return copy.deepcopy(dict(value))

    def _append_status(
        self,
        state: _RequestState,
        status: TaskStatus,
        *,
        status_message: str | None = None,
        result: object | None = None,
        error: Mapping[str, object] | None = None,
        input_requests: Mapping[str, object] | None = None,
        provenance: tuple[str, ...] = (),
    ) -> TaskChannelEvent:
        current = state.status
        if current in TERMINAL_TASK_STATUSES:
            raise InvalidTaskTransitionError("terminal Task state cannot change")
        allowed = {
            TaskStatus.WORKING: {
                TaskStatus.WORKING,
                TaskStatus.INPUT_REQUIRED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.INPUT_REQUIRED: {
                TaskStatus.WORKING,
                TaskStatus.INPUT_REQUIRED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            },
        }
        if status not in allowed[current]:
            raise InvalidTaskTransitionError(f"illegal transition {current.value} -> {status.value}")
        if status is TaskStatus.COMPLETED and error is not None:
            raise InvalidTaskTransitionError("completed Task cannot carry an error")
        if status is TaskStatus.FAILED and error is None:
            raise InvalidTaskTransitionError("failed Task requires an error")
        if status is TaskStatus.INPUT_REQUIRED and not input_requests:
            raise InvalidTaskTransitionError("input_required Task requires input requests")
        if status is not TaskStatus.INPUT_REQUIRED and input_requests is not None:
            raise InvalidTaskTransitionError("only input_required may carry input requests")

        result_copy = copy.deepcopy(result)
        error_copy = self._copy_mapping(error)
        inputs_copy = self._copy_mapping(input_requests)
        result_handle = None
        if status is TaskStatus.COMPLETED and result is not None:
            result_handle = TaskResourceHandle(self._next_handle_slot, 1, True)
            self._next_handle_slot += 1
        event = TaskChannelEvent(
            sequence=self._next_event_sequence,
            kind=TaskEventKind.STATUS,
            status=status,
            context_sequence=self._next_context_sequence,
            evidence_ticket=self._next_evidence_ticket,
            status_message=status_message,
            result=result_copy,
            result_handle=result_handle,
            error=error_copy,
            input_requests=inputs_copy,
            provenance=tuple(provenance),
        )
        self._next_event_sequence += 1
        self._next_context_sequence += 1
        self._next_evidence_ticket += 1
        state.status = status
        state.status_message = status_message
        state.result = result_copy
        state.result_handle = result_handle
        state.error = error_copy
        state.input_requests = inputs_copy
        state.provenance = tuple(provenance)
        state.events.append(event)
        return event

    def _snapshot(self, state: _RequestState) -> TaskChannelSnapshot:
        latest = state.events[-1]
        return TaskChannelSnapshot(
            binding=state.binding,
            status=state.status,
            latest_sequence=latest.sequence,
            context_sequence=latest.context_sequence,
            evidence_ticket=latest.evidence_ticket,
            status_message=state.status_message,
            result=copy.deepcopy(state.result),
            result_handle=state.result_handle,
            error=self._copy_mapping(state.error),
            input_requests=self._copy_mapping(state.input_requests),
            provenance=state.provenance,
        )

    def submit(
        self, issuer: TaskChannelIssuer, request: TaskChannelRequest
    ) -> TaskChannelSubmission:
        self._require_issuer(issuer)
        request_copy = copy.deepcopy(request)
        fingerprint = self._request_fingerprint(request_copy)
        with self._lock:
            old = self._submission_keys.get(request_copy.submission_key)
            if old is not None:
                old_fingerprint, request_id = old
                if old_fingerprint != fingerprint:
                    raise DuplicateRequestError(
                        "submission_key was reused with different request content"
                    )
                state = self._states[request_id]
                return TaskChannelSubmission(state.binding, self._snapshot(state), True)

            request_id = self._next_request_id
            self._next_request_id += 1
            binding = TaskBinding(
                lifecycle_id=issuer.lifecycle_id,
                lifecycle_generation=issuer.lifecycle_generation,
                contract_generation=request_copy.contract_generation,
                channel_generation=issuer.channel_generation,
                request_id=request_id,
            )
            state = _RequestState(request_copy, binding, TaskStatus.WORKING)
            self._states[request_id] = state
            self._submission_keys[request_copy.submission_key] = (fingerprint, request_id)
            self.submitted_requests += 1
            self._append_status(
                state,
                TaskStatus.WORKING,
                status_message="accepted",
                provenance=request_copy.provenance,
            )

            handler = self._handlers.get(request_copy.tool_id)
            if handler is not None:
                self.handler_invocations += 1
                outcome = handler(copy.deepcopy(request_copy))
                if not isinstance(outcome, TaskChannelOutcome):
                    outcome = TaskChannelOutcome(
                        status=TaskStatus.COMPLETED,
                        result=outcome,
                        provenance=request_copy.provenance,
                    )
                if outcome.status is not TaskStatus.WORKING:
                    self._append_status(
                        state,
                        outcome.status,
                        status_message=outcome.status_message,
                        result=outcome.result,
                        error=outcome.error,
                        input_requests=outcome.input_requests,
                        provenance=outcome.provenance or request_copy.provenance,
                    )
            return TaskChannelSubmission(binding, self._snapshot(state), False)

    def snapshot(
        self, issuer: TaskChannelIssuer, binding: TaskBinding
    ) -> TaskChannelSnapshot:
        self._require_issuer(issuer)
        with self._lock:
            return self._snapshot(self._state(binding))

    def update(
        self,
        issuer: TaskChannelIssuer,
        binding: TaskBinding,
        input_responses: Mapping[str, object],
    ) -> TaskChannelSnapshot:
        self._require_issuer(issuer)
        if not isinstance(input_responses, Mapping):
            raise ValueError("input_responses must be a mapping")
        with self._lock:
            state = self._state(binding)
            if state.status is not TaskStatus.INPUT_REQUIRED:
                raise InvalidTaskTransitionError("Task is not waiting for input")
            outstanding = set((state.input_requests or {}).keys())
            accepted = outstanding.intersection(input_responses.keys())
            if not accepted:
                return self._snapshot(state)
            self._append_status(
                state,
                TaskStatus.WORKING,
                status_message="input accepted",
                provenance=state.provenance,
            )
            return self._snapshot(state)

    def cancel(
        self, issuer: TaskChannelIssuer, binding: TaskBinding
    ) -> TaskChannelSnapshot:
        self._require_issuer(issuer)
        with self._lock:
            state = self._state(binding)
            if state.status in TERMINAL_TASK_STATUSES:
                return self._snapshot(state)
            self._append_status(
                state,
                TaskStatus.CANCELLED,
                status_message="cancelled",
                provenance=state.provenance,
            )
            self.cancel_transitions += 1
            return self._snapshot(state)

    def events(
        self,
        issuer: TaskChannelIssuer,
        binding: TaskBinding,
        *,
        after_sequence: int = 0,
    ) -> tuple[TaskChannelEvent, ...]:
        self._require_issuer(issuer)
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        with self._lock:
            state = self._state(binding)
            return tuple(
                copy.deepcopy(event)
                for event in state.events
                if event.sequence > after_sequence
            )

    def publish(
        self,
        binding: TaskBinding,
        status: TaskStatus,
        *,
        status_message: str | None = None,
        result: object | None = None,
        error: Mapping[str, object] | None = None,
        input_requests: Mapping[str, object] | None = None,
        provenance: tuple[str, ...] = (),
    ) -> TaskChannelSnapshot:
        with self._lock:
            state = self._state(binding)
            self._append_status(
                state,
                status,
                status_message=status_message,
                result=result,
                error=error,
                input_requests=input_requests,
                provenance=provenance or state.provenance,
            )
            return self._snapshot(state)

    def accepted_request(self, binding: TaskBinding) -> TaskChannelRequest:
        """Return a defensive copy for deterministic adapter assertions."""

        with self._lock:
            return copy.deepcopy(self._state(binding).request)

    def publish_artifact(
        self,
        binding: TaskBinding,
        artifact: Mapping[str, object],
        *,
        artifact_index: int,
        append: bool = False,
        last_chunk: bool = False,
        provenance: tuple[str, ...] = (),
    ) -> TaskChannelEvent:
        if type(artifact_index) is not int or artifact_index < 0:
            raise ValueError("artifact_index must be a non-negative integer")
        if not isinstance(artifact, Mapping) or not artifact:
            raise ValueError("artifact must be a non-empty mapping")
        with self._lock:
            state = self._state(binding)
            if state.status in TERMINAL_TASK_STATUSES:
                raise InvalidTaskTransitionError("terminal Task cannot publish artifacts")
            event = TaskChannelEvent(
                sequence=self._next_event_sequence,
                kind=TaskEventKind.ARTIFACT,
                status=state.status,
                context_sequence=self._next_context_sequence,
                evidence_ticket=self._next_evidence_ticket,
                artifact=copy.deepcopy(dict(artifact)),
                artifact_index=artifact_index,
                append=bool(append),
                last_chunk=bool(last_chunk),
                provenance=provenance or state.provenance,
            )
            self._next_event_sequence += 1
            self._next_context_sequence += 1
            self._next_evidence_ticket += 1
            state.events.append(event)
            return copy.deepcopy(event)


__all__ = [
    "DuplicateRequestError",
    "InMemoryTaskChannelTransport",
    "InvalidIssuerError",
    "InvalidTaskTransitionError",
    "TaskBinding",
    "TaskChannelEvent",
    "TaskChannelIssuer",
    "TaskChannelOutcome",
    "TaskChannelRequest",
    "TaskChannelSnapshot",
    "TaskChannelSubmission",
    "TaskChannelTransport",
    "TaskEventKind",
    "TaskResourceHandle",
    "TaskStatus",
    "TaskTransportError",
    "TERMINAL_TASK_STATUSES",
    "UnknownRequestError",
]
