#!/usr/bin/env python3
"""Bounded HTTPS/model relay for the AgentOS QEMU guest.

The guest owns the prompt, conversation, tool loop, and tool execution.  This
host process owns only the QEMU serial transport and provider protocol
translation.  In particular, it never executes a tool or selects one on the
model's behalf.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Protocol, Sequence


WIRE_PREFIX = b"@AGENTOS/1"
WIRE_KINDS = frozenset(("HELLO", "REQUEST", "RESPONSE", "ERROR", "GOODBYE"))
WIRE_SESSION_RE = re.compile(r"[0-9a-f]{32}\Z")
WIRE_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
WIRE_BASE64_RE = re.compile(rb"[A-Za-z0-9_-]*\Z")
TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")

# This is also the Guest's compile-time protocol limit.  The 4 KiB decoded
# budget accommodates rich tool schemas plus the bounded six-turn history;
# the current SBI console input path remains polled, so frames stay compact.
PROTOCOL_MAX_PAYLOAD_BYTES = 4096
PROTOCOL_MAX_WIRE_LINE_BYTES = 6144
# Matches the compact Guest parser's fixed goal buffer.  The limit is in UTF-8
# bytes, not characters, and the relay never truncates it.
MAX_GOAL_BYTES = 240
MAX_FINAL_BYTES = 512
MAX_APPROVED_TOOLS = 16
MAX_REQUIRED_GUEST_MARKERS = 16
MAX_GUEST_MARKER_BYTES = 256
GUEST_RELAY_READY_LINE = b"agentlive_ucore: relay_ready=1 live=1"
DEFAULT_MAX_ROUNDS = 8
DEFAULT_MAX_OUTPUT_TOKENS = 1024
MAX_OUTPUT_TOKENS = 2048
DEFAULT_HTTP_TIMEOUT_SECONDS = 45.0
MAX_HTTP_TIMEOUT_SECONDS = 80.0
DEFAULT_SESSION_TIMEOUT_SECONDS = 600.0
DEFAULT_BOOT_TIMEOUT_SECONDS = 120.0
DEFAULT_IDLE_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_HTTP_RESPONSE_BYTES = 1024 * 1024
MAX_MESSAGES = 64
MAX_TOOLS = 32
MAX_CORRELATION_ID = (1 << 63) - 1
MAX_SEQUENCE = (1 << 63) - 1


class RelayError(RuntimeError):
    """Fail-closed error with a stable, non-secret public classification."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


class WireProtocolError(RelayError):
    pass


class ProviderError(RelayError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_json_object(raw: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise WireProtocolError("BAD_JSON", f"{label} is not valid bounded JSON") from error
    if not isinstance(value, dict):
        raise WireProtocolError("BAD_JSON", f"{label} must be a JSON object")
    return value


def canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise WireProtocolError("BAD_JSON", "object cannot be encoded as canonical JSON") from error


@dataclass(frozen=True)
class WireFrame:
    session: str
    seq: int
    kind: str
    payload: bytes

    def json_object(self) -> dict[str, object]:
        return _parse_json_object(self.payload, label=f"{self.kind} payload")


class FrameCodec:
    """Encode and integrity-check compact, newline-delimited serial frames."""

    def __init__(self, max_payload_bytes: int = PROTOCOL_MAX_PAYLOAD_BYTES) -> None:
        if not 1 <= max_payload_bytes <= PROTOCOL_MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"max_payload_bytes must be in 1..{PROTOCOL_MAX_PAYLOAD_BYTES}"
            )
        self.max_payload_bytes = max_payload_bytes

    def encode(self, frame: WireFrame) -> bytes:
        self._validate_header(frame.session, frame.seq, frame.kind)
        if len(frame.payload) > self.max_payload_bytes:
            raise WireProtocolError("FRAME_TOO_LARGE", "frame payload exceeds protocol limit")
        digest = hashlib.sha256(frame.payload).hexdigest()
        payload = base64.urlsafe_b64encode(frame.payload).rstrip(b"=")
        line = b" ".join(
            (
                WIRE_PREFIX,
                frame.session.encode("ascii"),
                str(frame.seq).encode("ascii"),
                frame.kind.encode("ascii"),
                str(len(frame.payload)).encode("ascii"),
                digest.encode("ascii"),
                payload,
            )
        ) + b"\n"
        if len(line) > PROTOCOL_MAX_WIRE_LINE_BYTES:
            raise WireProtocolError("FRAME_TOO_LARGE", "encoded frame exceeds wire limit")
        return line

    def encode_json(
        self, session: str, seq: int, kind: str, payload: Mapping[str, object]
    ) -> bytes:
        return self.encode(WireFrame(session, seq, kind, canonical_json_bytes(payload)))

    def decode(self, line: bytes) -> WireFrame:
        if len(line) > PROTOCOL_MAX_WIRE_LINE_BYTES:
            raise WireProtocolError("FRAME_TOO_LARGE", "serial frame exceeds wire limit")
        if not line.endswith(b"\n") or line.endswith(b"\r\n"):
            raise WireProtocolError("BAD_FRAME", "serial frame must end with LF")
        fields = line[:-1].split(b" ")
        if len(fields) != 7 or fields[0] != WIRE_PREFIX:
            raise WireProtocolError("BAD_FRAME", "serial frame header is malformed")
        try:
            session = fields[1].decode("ascii")
            seq_text = fields[2].decode("ascii")
            kind = fields[3].decode("ascii")
            length_text = fields[4].decode("ascii")
            digest = fields[5].decode("ascii")
        except UnicodeDecodeError as error:
            raise WireProtocolError("BAD_FRAME", "serial frame header is not ASCII") from error
        if not seq_text.isdigit() or (len(seq_text) > 1 and seq_text[0] == "0"):
            raise WireProtocolError("BAD_SEQUENCE", "frame sequence is not canonical")
        if not length_text.isdigit() or (
            len(length_text) > 1 and length_text[0] == "0"
        ):
            raise WireProtocolError("BAD_FRAME", "frame length is not canonical")
        seq = int(seq_text)
        length = int(length_text)
        self._validate_header(session, seq, kind)
        if length > self.max_payload_bytes:
            raise WireProtocolError("FRAME_TOO_LARGE", "frame payload exceeds protocol limit")
        if WIRE_DIGEST_RE.fullmatch(digest) is None:
            raise WireProtocolError("BAD_HASH", "frame digest is malformed")
        encoded = fields[6]
        if WIRE_BASE64_RE.fullmatch(encoded) is None:
            raise WireProtocolError("BAD_FRAME", "frame payload encoding is malformed")
        try:
            payload = base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        except (ValueError, base64.binascii.Error) as error:
            raise WireProtocolError("BAD_FRAME", "frame payload encoding is invalid") from error
        if base64.urlsafe_b64encode(payload).rstrip(b"=") != encoded:
            raise WireProtocolError("BAD_FRAME", "frame payload encoding is not canonical")
        if len(payload) != length:
            raise WireProtocolError("BAD_FRAME", "frame payload length does not match header")
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, digest):
            raise WireProtocolError("BAD_HASH", "frame payload digest does not match")
        return WireFrame(session, seq, kind, payload)

    @staticmethod
    def _validate_header(session: str, seq: int, kind: str) -> None:
        if WIRE_SESSION_RE.fullmatch(session) is None:
            raise WireProtocolError("BAD_SESSION", "frame session is malformed")
        if not isinstance(seq, int) or isinstance(seq, bool) or not 1 <= seq <= MAX_SEQUENCE:
            raise WireProtocolError("BAD_SEQUENCE", "frame sequence is out of range")
        if kind not in WIRE_KINDS:
            raise WireProtocolError("BAD_KIND", "frame kind is unsupported")


class ReceiveSequence:
    def __init__(self, session: str) -> None:
        self.session = session
        self.expected = 1

    def accept(self, frame: WireFrame) -> None:
        if frame.session != self.session:
            raise WireProtocolError("BAD_SESSION", "frame belongs to another relay session")
        if frame.seq != self.expected:
            raise WireProtocolError(
                "BAD_SEQUENCE",
                f"expected inbound sequence {self.expected}, received {frame.seq}",
            )
        self.expected += 1


def _require_string(
    value: object, label: str, *, allow_empty: bool = False, max_length: int = 1024
) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise WireProtocolError("BAD_REQUEST", f"{label} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise WireProtocolError("BAD_REQUEST", f"{label} is not Unicode scalar text") from error
    if len(encoded) > max_length:
        raise WireProtocolError("BAD_REQUEST", f"{label} is too long")
    return value


def _require_u64(value: object, label: str, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= 0xFFFFFFFFFFFFFFFF
    ):
        raise WireProtocolError("BAD_REQUEST", f"{label} must be an unsigned integer")
    return value


def _validate_tool_arguments(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderError("BAD_TOOL_ARGUMENTS", f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not TOOL_NAME_RE.fullmatch(key):
            raise ProviderError("BAD_TOOL_ARGUMENTS", f"{label} has an invalid key")
        if isinstance(item, str):
            _require_string(item, f"{label}.{key}", allow_empty=True, max_length=512)
        elif isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 0xFFFFFFFFFFFFFFFF:
            pass
        else:
            raise ProviderError(
                "BAD_TOOL_ARGUMENTS",
                f"{label}.{key} must be a string or unsigned integer",
            )
        result[key] = item
    return result


def validate_guest_request(
    value: dict[str, object], *, max_output_tokens: int
) -> dict[str, object]:
    allowed = frozenset(
        (
            "corr_id",
            "model",
            "system",
            "messages",
            "tools",
            "tool_choice",
            "max_tokens",
            "temperature",
            "stop",
        )
    )
    unknown = set(value).difference(allowed)
    if unknown:
        raise WireProtocolError("BAD_REQUEST", "request contains unsupported fields")
    corr_id = _require_u64(value.get("corr_id"), "corr_id", positive=True)
    if corr_id > MAX_CORRELATION_ID:
        raise WireProtocolError("BAD_REQUEST", "corr_id is out of range")
    messages = value.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise WireProtocolError("BAD_REQUEST", "messages must be a non-empty bounded array")
    for index, message in enumerate(messages):
        _validate_message(message, index)
    tools = value.get("tools", [])
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        raise WireProtocolError("BAD_REQUEST", "tools must be a bounded array")
    for index, tool in enumerate(tools):
        _validate_tool(tool, index)
    max_tokens = value.get("max_tokens", max_output_tokens)
    max_tokens = _require_u64(max_tokens, "max_tokens", positive=True)
    if max_tokens > max_output_tokens:
        raise WireProtocolError("TOKEN_LIMIT", "requested max_tokens exceeds host policy")
    if "model" in value:
        _require_string(value["model"], "model", max_length=128)
    if "system" in value:
        _require_string(value["system"], "system", allow_empty=True, max_length=8192)
    if "temperature" in value:
        temperature = value["temperature"]
        if (
            not isinstance(temperature, (int, float))
            or isinstance(temperature, bool)
            or not 0 <= float(temperature) <= 2
        ):
            raise WireProtocolError("BAD_REQUEST", "temperature is out of range")
    if "stop" in value:
        stop = value["stop"]
        if isinstance(stop, str):
            _require_string(stop, "stop", max_length=128)
        elif isinstance(stop, list) and 1 <= len(stop) <= 4:
            for item in stop:
                _require_string(item, "stop item", max_length=128)
        else:
            raise WireProtocolError("BAD_REQUEST", "stop must be a string or bounded array")
    if "tool_choice" in value:
        _validate_tool_choice(value["tool_choice"])
    normalized = dict(value)
    normalized["max_tokens"] = max_tokens
    normalized.setdefault("tools", [])
    return normalized


def _validate_message(value: object, index: int) -> None:
    if not isinstance(value, dict):
        raise WireProtocolError("BAD_REQUEST", f"messages[{index}] must be an object")
    role = value.get("role")
    if role == "user":
        if set(value).difference(("role", "content")):
            raise WireProtocolError("BAD_REQUEST", "user message has unsupported fields")
        _require_string(value.get("content"), "user content", allow_empty=True, max_length=8192)
        return
    if role == "assistant":
        if set(value).difference(("role", "content", "tool_use")):
            raise WireProtocolError("BAD_REQUEST", "assistant message has unsupported fields")
        if "content" in value:
            _require_string(
                value["content"], "assistant content", allow_empty=True, max_length=8192
            )
        tool_use = value.get("tool_use")
        if tool_use is None:
            if "content" not in value:
                raise WireProtocolError("BAD_REQUEST", "assistant message is empty")
            return
        if not isinstance(tool_use, dict) or set(tool_use) != {
            "corr_id",
            "tool",
            "arguments",
        }:
            raise WireProtocolError("BAD_REQUEST", "assistant tool_use is malformed")
        _require_u64(tool_use.get("corr_id"), "tool_use.corr_id", positive=True)
        tool = _require_string(tool_use.get("tool"), "tool_use.tool", max_length=64)
        if TOOL_NAME_RE.fullmatch(tool) is None:
            raise WireProtocolError("BAD_REQUEST", "tool_use.tool is invalid")
        _validate_tool_arguments(tool_use.get("arguments"), "tool_use.arguments")
        return
    if role == "tool":
        if set(value).difference(("role", "tool_corr_id", "content", "is_error")):
            raise WireProtocolError("BAD_REQUEST", "tool message has unsupported fields")
        _require_u64(value.get("tool_corr_id"), "tool_corr_id", positive=True)
        _require_string(value.get("content"), "tool content", allow_empty=True, max_length=8192)
        if "is_error" in value and not isinstance(value["is_error"], bool):
            raise WireProtocolError("BAD_REQUEST", "is_error must be boolean")
        return
    raise WireProtocolError("BAD_REQUEST", f"messages[{index}].role is unsupported")


def _validate_tool(value: object, index: int) -> None:
    if not isinstance(value, dict) or set(value).difference(
        ("name", "description", "input_schema")
    ):
        raise WireProtocolError("BAD_REQUEST", f"tools[{index}] is malformed")
    name = _require_string(value.get("name"), "tool name", max_length=64)
    if TOOL_NAME_RE.fullmatch(name) is None:
        raise WireProtocolError("BAD_REQUEST", "tool name is invalid")
    _require_string(
        value.get("description", ""), "tool description", allow_empty=True, max_length=1024
    )
    schema = value.get("input_schema")
    if not isinstance(schema, dict):
        raise WireProtocolError("BAD_REQUEST", "tool input_schema must be an object")


def _validate_tool_choice(value: object) -> None:
    if value in ("auto", "none", "required"):
        return
    if isinstance(value, dict) and set(value) == {"tool"}:
        tool = _require_string(value.get("tool"), "tool_choice.tool", max_length=64)
        if TOOL_NAME_RE.fullmatch(tool):
            return
    raise WireProtocolError("BAD_REQUEST", "tool_choice is malformed")


@dataclass(frozen=True)
class ModelReply:
    type: str
    content: str = ""
    tool: str = ""
    arguments: Mapping[str, object] | None = None
    provider_call_id: str = ""

    def wire_payload(self, corr_id: int) -> dict[str, object]:
        if self.type == "final":
            return {
                "corr_id": corr_id,
                "type": "final",
                "content": _validate_final_content(self.content),
            }
        if self.type == "tool_use":
            if self.arguments is None:
                raise ProviderError("BAD_PROVIDER_RESPONSE", "tool response has no arguments")
            return {
                "corr_id": corr_id,
                "type": "tool_use",
                "tool": self.tool,
                "arguments": dict(self.arguments),
            }
        raise ProviderError("BAD_PROVIDER_RESPONSE", "provider returned an unknown response type")


def _validate_final_content(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderError(
            "BAD_PROVIDER_RESPONSE", "model final content must be non-empty text"
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProviderError(
            "BAD_PROVIDER_RESPONSE", "model final content is not Unicode scalar text"
        ) from None
    if len(encoded) > MAX_FINAL_BYTES:
        raise ProviderError(
            "BAD_PROVIDER_RESPONSE",
            f"model final content exceeds {MAX_FINAL_BYTES} UTF-8 bytes",
        )
    return value


class ModelProvider(Protocol):
    def complete(
        self,
        request: Mapping[str, object],
        *,
        deadline_monotonic: float | None = None,
    ) -> ModelReply:
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def validate_https_endpoint(endpoint: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise ValueError("provider endpoint is malformed") from error
    if parsed.scheme.lower() != "https":
        raise ValueError("provider endpoint must use HTTPS")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("provider endpoint authority is unsafe")
    if parsed.query or parsed.fragment:
        raise ValueError("provider endpoint must not contain query or fragment data")
    if parsed.path in ("", "/"):
        raise ValueError("provider endpoint must include an API path")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("provider endpoint port is invalid")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("provider endpoint must not be localhost")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("provider endpoint address must be globally routable")
    # Recompose once so the request cannot reinterpret credentials or fragments.
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))


class JsonHttpsClient:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_HTTP_RESPONSE_BYTES,
        secrets_to_redact: Sequence[str] = (),
        opener: object | None = None,
    ) -> None:
        self.endpoint = validate_https_endpoint(endpoint)
        if not 0 < timeout_seconds <= MAX_HTTP_TIMEOUT_SECONDS:
            raise ValueError(
                f"HTTP timeout must be in (0, {MAX_HTTP_TIMEOUT_SECONDS:g}] seconds"
            )
        if not 1024 <= max_response_bytes <= 8 * 1024 * 1024:
            raise ValueError("HTTP response limit is out of range")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._secrets = tuple(secret for secret in secrets_to_redact if secret)
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def post(
        self,
        body: Mapping[str, object],
        headers: Mapping[str, str],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        data = canonical_json_bytes(body)
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "AgentOS-Guest-Relay/1",
                **dict(headers),
            },
            method="POST",
        )
        timeout = self.timeout_seconds
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise ProviderError(
                    "SESSION_TIMEOUT", "session deadline expired before provider request"
                )
            timeout = min(timeout, remaining)
        try:
            response = self._opener.open(request, timeout=timeout)  # type: ignore[attr-defined]
            with response:
                status_value = getattr(response, "status", None)
                status = int(response.getcode() if status_value is None else status_value)
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        advertised = int(content_length)
                    except ValueError as error:
                        raise ProviderError(
                            "BAD_HTTP_RESPONSE", "provider returned an invalid Content-Length"
                        ) from error
                    if advertised < 0:
                        raise ProviderError(
                            "BAD_HTTP_RESPONSE", "provider returned an invalid Content-Length"
                        )
                    if advertised > self.max_response_bytes:
                        raise ProviderError(
                            "HTTP_RESPONSE_TOO_LARGE", "provider response exceeds host limit"
                        )
                raw = response.read(self.max_response_bytes + 1)
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise ProviderError(
                    "SESSION_TIMEOUT", "provider response arrived after session deadline"
                )
        except ProviderError:
            raise
        except urllib.error.HTTPError as error:
            status = int(error.code)
            message = self._redact(str(error.reason or "HTTP error"))
            try:
                error.close()
            except OSError:
                pass
            raise ProviderError(
                "PROVIDER_HTTP_ERROR",
                f"provider returned HTTP {status}: {message}",
                retryable=status == 429 or 500 <= status < 600,
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            message = self._redact(str(getattr(error, "reason", error)))
            raise ProviderError(
                "PROVIDER_UNAVAILABLE",
                f"provider HTTPS request failed: {message}",
                retryable=True,
            ) from None
        except ValueError as error:
            message = self._redact(str(error))
            raise ProviderError(
                "BAD_HTTPS_REQUEST",
                f"provider HTTPS request was rejected: {message}",
            ) from None
        if not 200 <= status < 300:
            raise ProviderError(
                "PROVIDER_HTTP_ERROR",
                f"provider returned HTTP {status}",
                retryable=status == 429 or 500 <= status < 600,
            )
        if len(raw) > self.max_response_bytes:
            raise ProviderError("HTTP_RESPONSE_TOO_LARGE", "provider response exceeds host limit")
        try:
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
            raise ProviderError(
                "BAD_HTTP_RESPONSE", "provider returned invalid JSON"
            ) from None
        if not isinstance(value, dict):
            raise ProviderError("BAD_HTTP_RESPONSE", "provider JSON must be an object")
        return value

    def _redact(self, text: str) -> str:
        result = text
        for secret in self._secrets:
            result = result.replace(secret, "[REDACTED]")
        # Keep public diagnostics bounded even when an HTTP stack returns a body.
        return result[:256]


class _ProtocolProvider:
    def __init__(self, client: JsonHttpsClient, *, api_key: str, model: str = "") -> None:
        validate_api_key(api_key)
        self.client = client
        self._api_key = api_key
        self.model = model
        self._provider_call_ids: dict[int, str] = {}

    def _model(self, request: Mapping[str, object]) -> str:
        requested = request.get("model", "")
        if requested and not isinstance(requested, str):
            raise ProviderError("BAD_REQUEST", "model must be a string")
        if self.model and requested and requested != self.model:
            raise ProviderError("MODEL_DENIED", "requested model is outside host policy")
        chosen = self.model or str(requested)
        if not chosen:
            raise ProviderError("MODEL_REQUIRED", "no model was configured or requested")
        return chosen

    def _provider_call_id(self, corr_id: object) -> str:
        if not isinstance(corr_id, int) or isinstance(corr_id, bool):
            raise ProviderError("BAD_REQUEST", "tool correlation id is invalid")
        try:
            return self._provider_call_ids[corr_id]
        except KeyError:
            raise ProviderError(
                "UNKNOWN_TOOL_CALL", "tool result references an unknown model call"
            ) from None

    def _remember_call(self, corr_id: object, provider_call_id: str) -> None:
        if not isinstance(corr_id, int) or isinstance(corr_id, bool):
            raise ProviderError("BAD_REQUEST", "corr_id is invalid")
        if corr_id in self._provider_call_ids:
            raise ProviderError("DUPLICATE_CORRELATION", "corr_id was already completed")
        self._provider_call_ids[corr_id] = provider_call_id


class OpenAICompatibleProvider(_ProtocolProvider):
    def complete(
        self,
        request: Mapping[str, object],
        *,
        deadline_monotonic: float | None = None,
    ) -> ModelReply:
        body: dict[str, object] = {
            "model": self._model(request),
            "messages": self._messages(request),
        }
        # OpenAI's official Chat Completions endpoint uses the current token
        # field.  Third-party compatible endpoints retain the widely supported
        # legacy field rather than guessing at their feature level.
        hostname = (urllib.parse.urlsplit(self.client.endpoint).hostname or "").rstrip(
            "."
        ).lower()
        token_field = (
            "max_completion_tokens" if hostname == "api.openai.com" else "max_tokens"
        )
        body[token_field] = request["max_tokens"]
        tools = request.get("tools", [])
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools  # type: ignore[union-attr]
            ]
            body["parallel_tool_calls"] = False
        if "tool_choice" in request:
            body["tool_choice"] = self._tool_choice(request["tool_choice"])
        for key in ("temperature", "stop"):
            if key in request:
                body[key] = request[key]
        response = self.client.post(
            body,
            {"Authorization": f"Bearer {self._api_key}"},
            deadline_monotonic=deadline_monotonic,
        )
        reply = self._parse_response(response)
        if reply.type == "tool_use":
            self._remember_call(request["corr_id"], reply.provider_call_id)
        return reply

    def _messages(self, request: Mapping[str, object]) -> list[dict[str, object]]:
        translated: list[dict[str, object]] = []
        if "system" in request:
            translated.append({"role": "system", "content": request["system"]})
        for message in request["messages"]:  # type: ignore[union-attr]
            role = message["role"]
            if role == "user":
                translated.append({"role": "user", "content": message["content"]})
            elif role == "assistant":
                item: dict[str, object] = {
                    "role": "assistant",
                    "content": message.get("content", ""),
                }
                tool_use = message.get("tool_use")
                if tool_use is not None:
                    provider_id = self._provider_call_id(tool_use["corr_id"])
                    item["tool_calls"] = [
                        {
                            "id": provider_id,
                            "type": "function",
                            "function": {
                                "name": tool_use["tool"],
                                "arguments": json.dumps(
                                    tool_use["arguments"],
                                    ensure_ascii=False,
                                    allow_nan=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ]
                translated.append(item)
            else:
                translated.append(
                    {
                        "role": "tool",
                        "tool_call_id": self._provider_call_id(message["tool_corr_id"]),
                        "content": message["content"],
                    }
                )
        return translated

    @staticmethod
    def _tool_choice(value: object) -> object:
        if isinstance(value, dict):
            return {"type": "function", "function": {"name": value["tool"]}}
        return value

    @staticmethod
    def _parse_response(response: Mapping[str, object]) -> ModelReply:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI response has no choice")
        finish_reason = choices[0].get("finish_reason")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI response has no message")
        if message.get("refusal") not in (None, ""):
            raise ProviderError(
                "INCOMPLETE_MODEL_RESPONSE", "OpenAI response was refused"
            )
        tool_calls = message.get("tool_calls", [])
        if tool_calls is None:
            tool_calls = []
        if not isinstance(tool_calls, list):
            raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI tool_calls is malformed")
        if len(tool_calls) > 1:
            raise ProviderError(
                "MULTIPLE_TOOL_CALLS", "Guest protocol supports one tool call per model turn"
            )
        if tool_calls:
            if finish_reason != "tool_calls":
                raise ProviderError(
                    "INCOMPLETE_MODEL_RESPONSE",
                    "OpenAI tool response did not terminate with tool_calls",
                )
            call = tool_calls[0]
            if not isinstance(call, dict) or call.get("type", "function") != "function":
                raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI tool call is malformed")
            provider_id = call.get("id")
            function = call.get("function")
            if not isinstance(provider_id, str) or not provider_id or not isinstance(function, dict):
                raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI tool call identity is missing")
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str) or TOOL_NAME_RE.fullmatch(name) is None:
                raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI tool name is invalid")
            if not isinstance(arguments, str):
                raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI tool arguments are missing")
            try:
                raw_arguments = arguments.encode("utf-8")
            except UnicodeEncodeError:
                raise ProviderError(
                    "BAD_TOOL_ARGUMENTS", "model returned non-scalar tool arguments"
                ) from None
            parsed = _parse_provider_arguments(raw_arguments)
            return ModelReply(
                "tool_use", tool=name, arguments=parsed, provider_call_id=provider_id
            )
        if finish_reason != "stop":
            raise ProviderError(
                "INCOMPLETE_MODEL_RESPONSE",
                "OpenAI response did not terminate normally",
            )
        return ModelReply(
            "final",
            content=_validate_final_content(
                _openai_content_text(message.get("content", ""))
            ),
        )


def _openai_content_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict) or item.get("type") not in ("text", "output_text"):
                raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI content block is unsupported")
            text = item.get("text")
            if not isinstance(text, str):
                raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI text block is malformed")
            parts.append(text)
        return "".join(parts)
    raise ProviderError("BAD_PROVIDER_RESPONSE", "OpenAI message content is malformed")


def _parse_provider_arguments(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise ProviderError("BAD_TOOL_ARGUMENTS", "model returned invalid tool arguments") from None
    return _validate_tool_arguments(value, "model tool arguments")


class AnthropicMessagesProvider(_ProtocolProvider):
    def __init__(
        self,
        client: JsonHttpsClient,
        *,
        api_key: str,
        model: str = "",
        anthropic_version: str = "2023-06-01",
    ) -> None:
        super().__init__(client, api_key=api_key, model=model)
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", anthropic_version):
            raise ValueError("Anthropic version must be a date")
        self.anthropic_version = anthropic_version

    def complete(
        self,
        request: Mapping[str, object],
        *,
        deadline_monotonic: float | None = None,
    ) -> ModelReply:
        body: dict[str, object] = {
            "model": self._model(request),
            "messages": self._messages(request),
            "max_tokens": request["max_tokens"],
        }
        if "system" in request:
            body["system"] = request["system"]
        tools = request.get("tools", [])
        if tools:
            body["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["input_schema"],
                }
                for tool in tools  # type: ignore[union-attr]
            ]
            tool_choice = self._tool_choice(request.get("tool_choice", "auto"))
            tool_choice["disable_parallel_tool_use"] = True
            body["tool_choice"] = tool_choice
        elif "tool_choice" in request:
            body["tool_choice"] = self._tool_choice(request["tool_choice"])
        for key in ("temperature", "stop"):
            if key in request:
                body["stop_sequences" if key == "stop" else key] = (
                    [request[key]] if key == "stop" and isinstance(request[key], str) else request[key]
                )
        response = self.client.post(
            body,
            {
                "x-api-key": self._api_key,
                "anthropic-version": self.anthropic_version,
            },
            deadline_monotonic=deadline_monotonic,
        )
        reply = self._parse_response(response)
        if reply.type == "tool_use":
            self._remember_call(request["corr_id"], reply.provider_call_id)
        return reply

    def _messages(self, request: Mapping[str, object]) -> list[dict[str, object]]:
        translated: list[dict[str, object]] = []
        for message in request["messages"]:  # type: ignore[union-attr]
            role = message["role"]
            if role == "user":
                output_role = "user"
                content: list[dict[str, object]] = [
                    {"type": "text", "text": message["content"]}
                ]
            elif role == "assistant":
                output_role = "assistant"
                content = []
                if message.get("content", ""):
                    content.append({"type": "text", "text": message["content"]})
                tool_use = message.get("tool_use")
                if tool_use is not None:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": self._provider_call_id(tool_use["corr_id"]),
                            "name": tool_use["tool"],
                            "input": tool_use["arguments"],
                        }
                    )
            else:
                output_role = "user"
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": self._provider_call_id(message["tool_corr_id"]),
                        "content": message["content"],
                        "is_error": bool(message.get("is_error", False)),
                    }
                ]
            if translated and translated[-1]["role"] == output_role:
                translated[-1]["content"].extend(content)  # type: ignore[union-attr]
            else:
                translated.append({"role": output_role, "content": content})
        return translated

    @staticmethod
    def _tool_choice(value: object) -> dict[str, object]:
        if value == "auto":
            return {"type": "auto"}
        if value == "required":
            return {"type": "any"}
        if value == "none":
            return {"type": "none"}
        return {"type": "tool", "name": value["tool"]}  # type: ignore[index]

    @staticmethod
    def _parse_response(response: Mapping[str, object]) -> ModelReply:
        content = response.get("content")
        stop_reason = response.get("stop_reason")
        if not isinstance(content, list):
            raise ProviderError("BAD_PROVIDER_RESPONSE", "Anthropic content is missing")
        text_parts: list[str] = []
        calls: list[dict[str, object]] = []
        for block in content:
            if not isinstance(block, dict):
                raise ProviderError("BAD_PROVIDER_RESPONSE", "Anthropic content block is malformed")
            if block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise ProviderError("BAD_PROVIDER_RESPONSE", "Anthropic text block is malformed")
                text_parts.append(text)
            elif block.get("type") == "tool_use":
                calls.append(block)
            else:
                raise ProviderError(
                    "BAD_PROVIDER_RESPONSE", "Anthropic content block is unsupported"
                )
        if len(calls) > 1:
            raise ProviderError(
                "MULTIPLE_TOOL_CALLS", "Guest protocol supports one tool call per model turn"
            )
        if calls:
            if stop_reason != "tool_use":
                raise ProviderError(
                    "INCOMPLETE_MODEL_RESPONSE",
                    "Anthropic tool response did not terminate with tool_use",
                )
            call = calls[0]
            provider_id = call.get("id")
            name = call.get("name")
            if not isinstance(provider_id, str) or not provider_id:
                raise ProviderError("BAD_PROVIDER_RESPONSE", "Anthropic tool id is missing")
            if not isinstance(name, str) or TOOL_NAME_RE.fullmatch(name) is None:
                raise ProviderError("BAD_PROVIDER_RESPONSE", "Anthropic tool name is invalid")
            arguments = _validate_tool_arguments(call.get("input"), "model tool arguments")
            return ModelReply(
                "tool_use", tool=name, arguments=arguments, provider_call_id=provider_id
            )
        if stop_reason not in ("end_turn", "stop_sequence"):
            raise ProviderError(
                "INCOMPLETE_MODEL_RESPONSE",
                "Anthropic response did not terminate normally",
            )
        return ModelReply(
            "final", content=_validate_final_content("".join(text_parts))
        )


@dataclass(frozen=True)
class ReplayRecord:
    response: Mapping[str, object]
    request_sha256: str = ""


class ReplayProvider:
    """Deterministic offline provider for tests and reproducible demos."""

    def __init__(self, records: Sequence[ReplayRecord]) -> None:
        if not records:
            raise ValueError("replay provider requires at least one record")
        self._records = tuple(records)
        self._index = 0
        self._lock = threading.Lock()

    @classmethod
    def from_jsonl(cls, path: Path, *, max_bytes: int = 1024 * 1024) -> "ReplayProvider":
        info = path.stat()
        if not path.is_file() or info.st_size > max_bytes:
            raise ValueError("replay file is unavailable or oversized")
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("replay file is unavailable or oversized")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("replay file is not valid UTF-8") from error
        records: list[ReplayRecord] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
            except (ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid replay JSON on line {line_number}") from error
            if not isinstance(value, dict) or not isinstance(value.get("response"), dict):
                raise ValueError(f"invalid replay record on line {line_number}")
            digest = value.get("request_sha256", "")
            if digest and (
                not isinstance(digest, str) or WIRE_DIGEST_RE.fullmatch(digest) is None
            ):
                raise ValueError(f"invalid replay digest on line {line_number}")
            records.append(ReplayRecord(value["response"], str(digest)))
        return cls(records)

    def complete(
        self,
        request: Mapping[str, object],
        *,
        deadline_monotonic: float | None = None,
    ) -> ModelReply:
        with self._lock:
            if self._index >= len(self._records):
                raise ProviderError("REPLAY_EXHAUSTED", "deterministic replay is exhausted")
            record = self._records[self._index]
            digest = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
            if record.request_sha256 and not hmac.compare_digest(
                digest, record.request_sha256
            ):
                raise ProviderError("REPLAY_MISMATCH", "request does not match replay transcript")
            reply = _reply_from_canonical(record.response)
            self._index += 1
            return reply

    def assert_exhausted(self) -> None:
        with self._lock:
            if self._index != len(self._records):
                raise ProviderError(
                    "REPLAY_NOT_EXHAUSTED",
                    "Guest completed before deterministic replay was exhausted",
                )


def _reply_from_canonical(value: Mapping[str, object]) -> ModelReply:
    kind = value.get("type")
    if kind == "final" and set(value).issubset(("type", "content")):
        return ModelReply(
            "final", content=_validate_final_content(value.get("content", ""))
        )
    if kind == "tool_use" and set(value).issubset(("type", "tool", "arguments")):
        tool = value.get("tool")
        if not isinstance(tool, str) or TOOL_NAME_RE.fullmatch(tool) is None:
            raise ProviderError("BAD_REPLAY", "replay tool name is invalid")
        return ModelReply(
            "tool_use",
            tool=tool,
            arguments=_validate_tool_arguments(value.get("arguments"), "replay arguments"),
        )
    raise ProviderError("BAD_REPLAY", "replay response is malformed")


class RelaySession:
    """One QEMU session with independent strict RX and TX sequences."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        goal: str,
        approved_tools: Sequence[str] = (),
        session: str | None = None,
        codec: FrameCodec | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        if not 1 <= max_rounds <= DEFAULT_MAX_ROUNDS:
            raise ValueError(f"max_rounds must be in 1..{DEFAULT_MAX_ROUNDS}")
        if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError(
                f"max_output_tokens must be in 1..{MAX_OUTPUT_TOKENS}"
            )
        self.goal = validate_goal(goal)
        self.approved_tools = validate_approved_tools(approved_tools)
        self.provider = provider
        self.session = session or secrets.token_hex(16)
        if WIRE_SESSION_RE.fullmatch(self.session) is None:
            raise ValueError("session must be 32 lowercase hexadecimal characters")
        self.codec = codec or FrameCodec()
        self.max_rounds = max_rounds
        self.max_output_tokens = max_output_tokens
        self._rx = ReceiveSequence(self.session)
        self._tx_seq = 1
        self._rounds = 0
        self._last_corr_id = 0
        self._final_sent = False
        self.closed = False

    def hello_line(self) -> bytes:
        return self._send_json(
            "HELLO",
            {
                "goal": self.goal,
                "approved_tools": list(self.approved_tools),
                "max_payload": self.codec.max_payload_bytes,
                "max_rounds": self.max_rounds,
                "max_tokens": self.max_output_tokens,
            },
        )

    def handle_line(
        self, line: bytes, *, deadline_monotonic: float | None = None
    ) -> bytes | None:
        if self.closed:
            raise WireProtocolError("SESSION_CLOSED", "relay session is already closed")
        frame = self.codec.decode(line)
        self._rx.accept(frame)
        if frame.kind == "GOODBYE":
            if frame.json_object() != {"reason": "guest_complete"}:
                raise WireProtocolError(
                    "BAD_GOODBYE",
                    'Guest GOODBYE must be exactly {"reason":"guest_complete"}',
                )
            if not self._final_sent:
                raise WireProtocolError(
                    "FINAL_REQUIRED", "Guest cannot complete before a valid model final"
                )
            if isinstance(self.provider, ReplayProvider):
                self.provider.assert_exhausted()
            self.closed = True
            return self._send_json("GOODBYE", {"reason": "guest_complete"})
        if frame.kind != "REQUEST":
            raise WireProtocolError("BAD_KIND", "Guest may send only REQUEST or GOODBYE")
        if self._final_sent:
            raise WireProtocolError(
                "REQUEST_AFTER_FINAL", "Guest sent another request after model final"
            )
        raw_request = frame.json_object()
        corr_value = raw_request.get("corr_id")
        if (
            not isinstance(corr_value, int)
            or isinstance(corr_value, bool)
            or not 1 <= corr_value <= MAX_CORRELATION_ID
            or corr_value != self._last_corr_id + 1
        ):
            raise WireProtocolError(
                "BAD_CORRELATION", "corr_id must increase by exactly one"
            )
        corr_id = corr_value
        self._last_corr_id = corr_id
        try:
            request = validate_guest_request(
                raw_request, max_output_tokens=self.max_output_tokens
            )
            if self._rounds >= self.max_rounds:
                raise WireProtocolError("ROUND_LIMIT", "model round limit is exhausted")
            self._rounds += 1
            reply = self._complete_provider(request, deadline_monotonic)
            response = self._send_json("RESPONSE", reply.wire_payload(corr_id))
            if reply.type == "final":
                self._final_sent = True
            return response
        except RelayError as error:
            # Integrity failures in the frame header never reach here.  A valid,
            # correlated request gets a bounded public error so the Guest can
            # decide whether to stop or retry with the next turn.
            payload = {
                "corr_id": corr_id,
                "type": "error",
                "code": error.code,
                "message": error.public_message,
            }
            return self._send_json("ERROR", payload)

    def _complete_provider(
        self,
        request: Mapping[str, object],
        deadline_monotonic: float | None,
    ) -> ModelReply:
        if deadline_monotonic is None:
            reply = self.provider.complete(request)
            if not isinstance(reply, ModelReply):
                raise ProviderError(
                    "BAD_PROVIDER_RESPONSE", "provider returned an invalid reply"
                )
            return reply
        if time.monotonic() >= deadline_monotonic:
            raise RelayError(
                "SESSION_TIMEOUT", "session deadline expired before provider request"
            )

        result: queue.Queue[tuple[ModelReply | None, RelayError | None]] = queue.Queue(
            maxsize=1
        )

        def provider_worker() -> None:
            try:
                reply = self.provider.complete(
                    request, deadline_monotonic=deadline_monotonic
                )
                if not isinstance(reply, ModelReply):
                    raise ProviderError(
                        "BAD_PROVIDER_RESPONSE", "provider returned an invalid reply"
                    )
                item = (reply, None)
            except RelayError as error:
                item = (None, error)
            except BaseException:
                # Never let an adapter exception print request data or secrets
                # from a daemon thread traceback.
                item = (
                    None,
                    ProviderError(
                        "PROVIDER_FAILURE", "provider adapter failed unexpectedly"
                    ),
                )
            result.put_nowait(item)

        worker = threading.Thread(
            target=provider_worker, name="model-provider-request", daemon=True
        )
        try:
            worker.start()
        except RuntimeError as error:
            raise RelayError(
                "PROVIDER_WORKER", "provider request worker could not start"
            ) from error
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise RelayError(
                "SESSION_TIMEOUT", "session deadline expired during provider request"
            )
        try:
            reply, error = result.get(timeout=remaining)
        except queue.Empty:
            raise RelayError(
                "SESSION_TIMEOUT", "provider request exceeded session deadline"
            ) from None
        if time.monotonic() >= deadline_monotonic:
            raise RelayError(
                "SESSION_TIMEOUT", "provider response arrived after session deadline"
            )
        if error is not None:
            raise error
        assert reply is not None
        return reply

    def _send_json(self, kind: str, payload: Mapping[str, object]) -> bytes:
        line = self.codec.encode_json(self.session, self._tx_seq, kind, payload)
        self._tx_seq += 1
        return line


def build_qemu_command(
    qemu: str,
    *,
    kernel: str = "build/kernel",
    image: str = "nfs/fs-copy.img",
) -> list[str]:
    """Use a dedicated stdio serial chardev with no QEMU monitor multiplexing."""

    return [
        qemu,
        "-display",
        "none",
        "-monitor",
        "none",
        "-machine",
        "virt",
        "-bios",
        "default",
        "-kernel",
        kernel,
        "-drive",
        f"file={image},if=none,format=raw,id=x0",
        "-device",
        "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
        "-chardev",
        "stdio,id=agentos,signal=off,mux=off",
        "-serial",
        "chardev:agentos",
    ]


class QemuSerialProcess:
    def __init__(
        self,
        command: Sequence[str],
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("QEMU command is empty or malformed")
        self.command = tuple(command)
        self._popen_factory = popen_factory
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> subprocess.Popen[bytes]:
        if self.proc is not None:
            raise RuntimeError("QEMU process was already started")
        options: dict[str, object] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        proc = self._popen_factory(list(self.command), **options)
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            try:
                proc.kill()
            finally:
                proc.wait()
            raise RuntimeError("QEMU serial pipes were not created")
        self.proc = proc
        return proc

    def write(self, line: bytes) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("QEMU process is not running")
        try:
            remaining = memoryview(line)
            while remaining:
                written = self.proc.stdin.write(remaining)
                if not isinstance(written, int) or written <= 0:
                    raise BrokenPipeError("QEMU serial input made no progress")
                remaining = remaining[written:]
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RelayError("QEMU_PIPE_CLOSED", "QEMU serial input closed") from error

    def stop(self, grace_seconds: float = 2.0) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.terminate()
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "nt":
                        proc.kill()
                    else:
                        os.killpg(proc.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                proc.wait()
        # Terminating the reader first releases a writer blocked on a full
        # stdin pipe on both POSIX and Windows.  Closing stdin before process
        # termination can itself wait on the blocked BufferedIO lock.
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass


class SerialLineScanner:
    """Separate framed RPC from arbitrary boot/console log bytes."""

    def __init__(self, *, max_line_bytes: int = PROTOCOL_MAX_WIRE_LINE_BYTES) -> None:
        self.max_line_bytes = max_line_bytes
        self._buffer = bytearray()
        self._discarding_log = False

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        events: list[tuple[str, bytes]] = []
        self._buffer.extend(chunk)
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > self.max_line_bytes:
                    if self._buffer.startswith(WIRE_PREFIX + b" "):
                        raise WireProtocolError(
                            "FRAME_TOO_LARGE", "serial frame exceeds wire limit"
                        )
                    self._buffer.clear()
                    self._discarding_log = True
                break
            line = bytes(self._buffer[: newline + 1])
            del self._buffer[: newline + 1]
            if self._discarding_log:
                self._discarding_log = False
                continue
            if line.startswith(WIRE_PREFIX + b" "):
                if len(line) > self.max_line_bytes:
                    raise WireProtocolError(
                        "FRAME_TOO_LARGE", "serial frame exceeds wire limit"
                    )
                # SBI/UART transport may render the Guest's single LF as CRLF.
                # Normalize only this line terminator at the serial boundary;
                # FrameCodec continues to enforce the canonical LF-only wire.
                if line.endswith(b"\r\n"):
                    line = line[:-2] + b"\n"
                events.append(("frame", line))
            else:
                events.append(("log", line))
        return events


def _write_process_before_deadline(
    process: QemuSerialProcess, line: bytes, *, deadline_monotonic: float
) -> None:
    if time.monotonic() >= deadline_monotonic:
        raise RelayError(
            "QEMU_WRITE_TIMEOUT", "QEMU serial write deadline expired"
        )
    result: queue.Queue[RelayError | None] = queue.Queue(maxsize=1)

    def writer() -> None:
        try:
            process.write(line)
            error = None
        except RelayError as caught:
            error = caught
        except BaseException:
            # Suppress daemon-thread tracebacks that could expose framed data.
            error = RelayError("QEMU_WRITE_ERROR", "QEMU serial writer failed")
        result.put_nowait(error)

    worker = threading.Thread(
        target=writer, name="qemu-serial-writer", daemon=True
    )
    try:
        worker.start()
    except RuntimeError as error:
        raise RelayError(
            "QEMU_WRITE_ERROR", "QEMU serial writer could not start"
        ) from error
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise RelayError(
            "QEMU_WRITE_TIMEOUT", "QEMU serial write deadline expired"
        )
    try:
        error = result.get(timeout=remaining)
    except queue.Empty:
        raise RelayError(
            "QEMU_WRITE_TIMEOUT", "QEMU serial write exceeded its deadline"
        ) from None
    if time.monotonic() >= deadline_monotonic:
        raise RelayError(
            "QEMU_WRITE_TIMEOUT", "QEMU serial write exceeded its deadline"
        )
    if error is not None:
        raise error


def run_qemu_relay(
    process: QemuSerialProcess,
    session: RelaySession,
    *,
    session_timeout_seconds: float = DEFAULT_SESSION_TIMEOUT_SECONDS,
    boot_timeout_seconds: float = DEFAULT_BOOT_TIMEOUT_SECONDS,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    log_sink: Callable[[bytes], None] | None = None,
    required_guest_markers: Sequence[str] = (),
) -> None:
    if not 1 <= session_timeout_seconds <= 3600:
        raise ValueError("session timeout must be in 1..3600 seconds")
    if not 1 <= boot_timeout_seconds <= 3600:
        raise ValueError("boot timeout must be in 1..3600 seconds")
    if not 1 <= idle_timeout_seconds <= session_timeout_seconds:
        raise ValueError("idle timeout must not exceed session timeout")
    required_markers = frozenset(validate_required_guest_markers(required_guest_markers))
    seen_markers: set[bytes] = set()
    # Encode HELLO before spawning QEMU so aggregate goal/policy/config size
    # failures are configuration errors, not half-started Guest sessions.
    hello_line = session.hello_line()
    proc = process.start()
    assert proc.stdout is not None
    assert proc.stderr is not None
    output: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=64)

    def serial_reader() -> None:
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    output.put(("serial_eof", b""))
                    return
                output.put(("serial", chunk))
        except BaseException:
            output.put(("serial_error", b""))

    def diagnostic_reader() -> None:
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    return
                output.put(("diagnostic", chunk))
        except BaseException:
            # Diagnostics are not part of the integrity-checked Guest protocol.
            return

    serial_thread = threading.Thread(
        target=serial_reader, name="qemu-serial-reader", daemon=True
    )
    diagnostic_thread = threading.Thread(
        target=diagnostic_reader, name="qemu-diagnostic-reader", daemon=True
    )
    scanner = SerialLineScanner()
    started = time.monotonic()
    session_deadline = started + session_timeout_seconds
    ready = False
    last_frame: float | None = None
    serial_thread.start()
    diagnostic_thread.start()
    try:
        while not session.closed or not required_markers.issubset(seen_markers):
            now = time.monotonic()
            if now - started >= session_timeout_seconds:
                if session.closed:
                    raise RelayError(
                        "GUEST_MARKER_TIMEOUT",
                        "Guest acceptance markers were not observed before timeout",
                    )
                raise RelayError("SESSION_TIMEOUT", "QEMU relay session timed out")
            if not ready and now - started >= boot_timeout_seconds:
                raise RelayError(
                    "BOOT_TIMEOUT", "QEMU Guest did not announce relay readiness"
                )
            if ready and last_frame is not None and now - last_frame >= idle_timeout_seconds:
                if session.closed:
                    raise RelayError(
                        "GUEST_MARKER_TIMEOUT",
                        "Guest acceptance markers were not observed before timeout",
                    )
                raise RelayError("IDLE_TIMEOUT", "QEMU guest stopped sending protocol frames")
            deadlines = [0.5, session_timeout_seconds - (now - started)]
            if ready:
                assert last_frame is not None
                deadlines.append(idle_timeout_seconds - (now - last_frame))
            else:
                deadlines.append(boot_timeout_seconds - (now - started))
            timeout = min(deadlines)
            try:
                kind, chunk = output.get(timeout=max(0.01, timeout))
            except queue.Empty:
                continue
            if kind == "serial_eof":
                if not ready:
                    raise RelayError(
                        "QEMU_EXITED", "QEMU exited before Guest relay readiness"
                    )
                if session.closed and not required_markers.issubset(seen_markers):
                    raise RelayError(
                        "GUEST_MARKER_MISSING",
                        "Guest exited before all acceptance markers were observed",
                    )
                raise RelayError("QEMU_EXITED", "QEMU exited before Guest GOODBYE")
            if kind == "serial_error":
                raise RelayError("QEMU_OUTPUT_ERROR", "QEMU serial reader failed")
            if kind == "diagnostic":
                if log_sink is not None:
                    log_sink(chunk)
                continue
            for event_kind, line in scanner.feed(chunk):
                if event_kind == "log":
                    bare_line = line.rstrip(b"\r\n")
                    if bare_line in required_markers:
                        seen_markers.add(bare_line)
                    if not ready and bare_line == GUEST_RELAY_READY_LINE:
                        ready = True
                        write_started = time.monotonic()
                        _write_process_before_deadline(
                            process,
                            hello_line,
                            deadline_monotonic=min(
                                session_deadline,
                                write_started + idle_timeout_seconds,
                            ),
                        )
                        last_frame = time.monotonic()
                    elif session.closed:
                        last_frame = time.monotonic()
                    if log_sink is not None:
                        log_sink(line)
                    continue
                if not ready:
                    raise WireProtocolError(
                        "FRAME_BEFORE_READY",
                        "Guest sent a protocol frame before relay readiness",
                    )
                last_frame = time.monotonic()
                response = session.handle_line(
                    line, deadline_monotonic=session_deadline
                )
                if response is not None:
                    assert last_frame is not None
                    _write_process_before_deadline(
                        process,
                        response,
                        deadline_monotonic=min(
                            session_deadline,
                            last_frame + idle_timeout_seconds,
                        ),
                    )
        # The GOODBYE response was written by handle_line; shutdown is bounded.
    finally:
        process.stop()
        serial_thread.join(timeout=2)
        diagnostic_thread.join(timeout=2)


def _load_api_key(environment_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", environment_name):
        raise ValueError("API key environment variable name is invalid")
    value = os.environ.get(environment_name, "")
    if not value:
        raise ValueError(f"required API key environment variable {environment_name} is unset")
    return validate_api_key(value)


def validate_api_key(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("provider API key is malformed")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("provider API key is malformed")
    return value


def validate_goal(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Guest goal must be non-empty UTF-8 text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Guest goal is not valid UTF-8 text") from error
    if len(encoded) > MAX_GOAL_BYTES:
        raise ValueError(f"Guest goal exceeds {MAX_GOAL_BYTES} UTF-8 bytes")
    if "\x00" in value:
        raise ValueError("Guest goal contains a NUL byte")
    return value


def validate_approved_tools(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or len(values) > MAX_APPROVED_TOOLS:
        raise ValueError(f"at most {MAX_APPROVED_TOOLS} tools may be approved")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or TOOL_NAME_RE.fullmatch(value) is None:
            raise ValueError("approved tool name is invalid")
        if value in seen:
            raise ValueError("approved tool names must be unique")
        seen.add(value)
        result.append(value)
    return tuple(result)


def validate_required_guest_markers(values: Sequence[str]) -> tuple[bytes, ...]:
    if isinstance(values, (str, bytes)) or len(values) > MAX_REQUIRED_GUEST_MARKERS:
        raise ValueError(
            f"at most {MAX_REQUIRED_GUEST_MARKERS} Guest markers may be required"
        )
    result: list[bytes] = []
    seen: set[bytes] = set()
    for value in values:
        if not isinstance(value, str) or not value or "\r" in value or "\n" in value:
            raise ValueError("required Guest marker must be one non-empty line")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("required Guest marker is not valid UTF-8") from error
        if b"\x00" in encoded or len(encoded) > MAX_GUEST_MARKER_BYTES:
            raise ValueError("required Guest marker is malformed or too long")
        if encoded in seen:
            raise ValueError("required Guest markers must be unique")
        seen.add(encoded)
        result.append(encoded)
    return tuple(result)


def _load_goal(inline: str | None, path: Path | None) -> str:
    if (inline is None) == (path is None):
        raise ValueError("exactly one of --goal and --goal-file is required")
    if inline is not None:
        return validate_goal(inline)
    assert path is not None
    try:
        info = path.stat()
        if not path.is_file() or info.st_size > MAX_GOAL_BYTES:
            raise ValueError("Guest goal file is unavailable or oversized")
        with path.open("rb") as handle:
            raw = handle.read(MAX_GOAL_BYTES + 1)
    except OSError as error:
        raise ValueError("Guest goal file cannot be read") from error
    if len(raw) > MAX_GOAL_BYTES:
        raise ValueError("Guest goal file is oversized")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Guest goal file is not valid UTF-8") from error
    return validate_goal(value)


def _resolve_qemu(value: str) -> str:
    resolved = shutil.which(value)
    if resolved is None:
        candidate = Path(value)
        if not candidate.is_file():
            raise ValueError("QEMU executable was not found")
        resolved = str(candidate.resolve())
    return resolved


def _build_provider(args: argparse.Namespace) -> ModelProvider:
    if args.provider == "replay":
        if args.replay_file is None:
            raise ValueError("--replay-file is required for replay provider")
        return ReplayProvider.from_jsonl(args.replay_file)
    key_environment = args.api_key_env or (
        "OPENAI_API_KEY" if args.provider == "openai" else "ANTHROPIC_API_KEY"
    )
    api_key = _load_api_key(key_environment)
    endpoint = args.endpoint
    if not endpoint:
        endpoint = (
            "https://api.openai.com/v1/chat/completions"
            if args.provider == "openai"
            else "https://api.anthropic.com/v1/messages"
        )
    client = JsonHttpsClient(
        endpoint,
        timeout_seconds=args.http_timeout,
        max_response_bytes=args.max_http_response_bytes,
        secrets_to_redact=(api_key,),
    )
    if args.provider == "openai":
        return OpenAICompatibleProvider(client, api_key=api_key, model=args.model)
    return AnthropicMessagesProvider(
        client,
        api_key=api_key,
        model=args.model,
        anthropic_version=args.anthropic_version,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an AgentOS QEMU Guest with a bounded host-side LLM relay."
    )
    parser.add_argument("--provider", choices=("openai", "anthropic", "replay"), required=True)
    parser.add_argument("--qemu", default="qemu-system-riscv64")
    parser.add_argument("--kernel", default="build/kernel")
    parser.add_argument("--image", default="nfs/fs-copy.img")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--api-key-env",
        default="",
        help="API key environment variable (provider-specific default when omitted).",
    )
    parser.add_argument("--anthropic-version", default="2023-06-01")
    parser.add_argument("--replay-file", type=Path)
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--max-payload-bytes", type=int, default=PROTOCOL_MAX_PAYLOAD_BYTES)
    parser.add_argument("--http-timeout", type=float, default=DEFAULT_HTTP_TIMEOUT_SECONDS)
    parser.add_argument(
        "--max-http-response-bytes", type=int, default=DEFAULT_MAX_HTTP_RESPONSE_BYTES
    )
    parser.add_argument(
        "--session-timeout", type=float, default=DEFAULT_SESSION_TIMEOUT_SECONDS
    )
    parser.add_argument("--boot-timeout", type=float, default=DEFAULT_BOOT_TIMEOUT_SECONDS)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT_SECONDS)
    goal = parser.add_mutually_exclusive_group(required=True)
    goal.add_argument("--goal", default=None)
    goal.add_argument("--goal-file", type=Path, default=None)
    parser.add_argument(
        "--approve-tool",
        action="append",
        default=[],
        metavar="NAME",
        help="User-approved Guest tool name; repeat for additional tools.",
    )
    parser.add_argument(
        "--require-guest-marker",
        action="append",
        default=[],
        metavar="TEXT",
        help="Exact Guest serial log line required for functional acceptance.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        provider = _build_provider(args)
        codec = FrameCodec(args.max_payload_bytes)
        session = RelaySession(
            provider,
            goal=_load_goal(args.goal, args.goal_file),
            approved_tools=args.approve_tool,
            codec=codec,
            max_rounds=args.max_rounds,
            max_output_tokens=args.max_output_tokens,
        )
        command = build_qemu_command(
            _resolve_qemu(args.qemu), kernel=args.kernel, image=args.image
        )
        process = QemuSerialProcess(command)

        def log_sink(line: bytes) -> None:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

        run_qemu_relay(
            process,
            session,
            session_timeout_seconds=args.session_timeout,
            boot_timeout_seconds=args.boot_timeout,
            idle_timeout_seconds=args.idle_timeout,
            log_sink=None if args.quiet else log_sink,
            required_guest_markers=args.require_guest_marker,
        )
        return 0
    except (RelayError, ValueError, OSError) as error:
        code = error.code if isinstance(error, RelayError) else "CONFIGURATION"
        message = error.public_message if isinstance(error, RelayError) else str(error)
        print(f"guest_llm_relay: {code}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
