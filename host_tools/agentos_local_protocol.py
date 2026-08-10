#!/usr/bin/env python3
"""Strict local IPC primitives for the interactive AgentOS console.

The public console uses two AF_UNIX sockets: one controller socket and one
fan-out telemetry socket.  This module deliberately contains no model or
AgentOS policy; it only provides bounded, authenticated local transport.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping


LOCAL_PROTOCOL = 1
MAX_LOCAL_LINE_BYTES = 16 * 1024
MAX_LOCAL_DEPTH = 32
STATE_FILE_NAME = "latest.json"
GUEST_PROFILES = frozenset(("agentlive", "nexus"))


class LocalProtocolError(RuntimeError):
    """A bounded, non-secret local protocol failure."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _depth(value: object, current: int = 0) -> int:
    if current > MAX_LOCAL_DEPTH:
        return current
    if isinstance(value, dict):
        return max((_depth(item, current + 1) for item in value.values()), default=current)
    if isinstance(value, list):
        return max((_depth(item, current + 1) for item in value), default=current)
    return current


def encode_message(value: Mapping[str, object]) -> bytes:
    if not isinstance(value, Mapping):
        raise LocalProtocolError("local message must be an object")
    try:
        raw = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise LocalProtocolError("local message is not encodable JSON") from error
    if len(raw) > MAX_LOCAL_LINE_BYTES:
        raise LocalProtocolError("local message exceeds the size limit")
    return raw


def decode_message(raw: bytes) -> dict[str, object]:
    if not raw.endswith(b"\n") or raw.endswith(b"\r\n"):
        raise LocalProtocolError("local message must end with one LF")
    if len(raw) > MAX_LOCAL_LINE_BYTES:
        raise LocalProtocolError("local message exceeds the size limit")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise LocalProtocolError("local message is not strict JSON") from error
    if not isinstance(value, dict) or _depth(value) > MAX_LOCAL_DEPTH:
        raise LocalProtocolError("local message must be a bounded object")
    return value


class NdjsonReader:
    """Incremental strict NDJSON reader with a hard line bound."""

    def __init__(self, *, max_line_bytes: int = MAX_LOCAL_LINE_BYTES) -> None:
        if not 128 <= max_line_bytes <= MAX_LOCAL_LINE_BYTES:
            raise ValueError("invalid local line limit")
        self.max_line_bytes = max_line_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, object]]:
        if not isinstance(chunk, bytes):
            raise TypeError("local transport chunks must be bytes")
        self._buffer.extend(chunk)
        messages: list[dict[str, object]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) >= self.max_line_bytes:
                    raise LocalProtocolError("unterminated local message exceeds the limit")
                return messages
            if newline + 1 > self.max_line_bytes:
                raise LocalProtocolError("local message exceeds the size limit")
            raw = bytes(self._buffer[: newline + 1])
            del self._buffer[: newline + 1]
            messages.append(decode_message(raw))

    def finish(self) -> None:
        if self._buffer:
            raise LocalProtocolError("local transport ended with a partial message")


@dataclass(frozen=True)
class RuntimePaths:
    directory: Path
    control_socket: Path
    telemetry_socket: Path
    state_file: Path


class RuntimeLock:
    """Process-lifetime owner lock preventing two daemons from owning `latest`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    @classmethod
    def acquire(cls, directory: Path) -> "RuntimeLock":
        lock = cls(directory / "daemon.lock")
        descriptor = os.open(lock.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        try:
            os.chmod(lock.path, 0o600)
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise LocalProtocolError("another AgentOS relay daemon is active") from error
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise LocalProtocolError("another AgentOS relay daemon is active") from error
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n".encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
            lock._handle = handle
            return lock
        except BaseException:
            handle.close()
            raise

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()

    def __enter__(self) -> "RuntimeLock":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()


def _uid() -> int:
    getter = getattr(os, "getuid", None)
    return int(getter()) if getter is not None else 0


def _owned_directory(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    if hasattr(info, "st_uid") and info.st_uid != _uid():
        return False
    return not bool(info.st_mode & 0o022)


def runtime_base() -> Path:
    """Select an owner-only base below /run/user or /tmp.

    AF_UNIX is intentionally required: the API key never crosses this socket,
    but the bearer token and approval decisions still deserve OS-local access
    control rather than an unauthenticated TCP listener.
    """

    candidates: list[Path] = []
    configured = os.environ.get("XDG_RUNTIME_DIR", "")
    if configured:
        candidates.append(Path(configured))
    if os.name != "nt":
        candidates.append(Path("/run/user") / str(_uid()))
    for candidate in candidates:
        if candidate.is_absolute() and _owned_directory(candidate):
            return candidate
    return Path(tempfile.gettempdir())


def prepare_runtime_paths(session: str, *, base: Path | None = None) -> RuntimePaths:
    if not session or len(session) > 32 or not session.isalnum():
        raise ValueError("local session name is malformed")
    root = (base or runtime_base()).resolve()
    directory = root / f"agentos-{_uid()}"
    directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as error:
        raise LocalProtocolError("cannot protect AgentOS runtime directory") from error
    if not _owned_directory(directory):
        raise LocalProtocolError("AgentOS runtime directory is not owner-only")
    suffix = session[:12]
    paths = RuntimePaths(
        directory=directory,
        control_socket=directory / f"control-{suffix}.sock",
        telemetry_socket=directory / f"telemetry-{suffix}.sock",
        state_file=directory / STATE_FILE_NAME,
    )
    # Linux sockaddr_un.sun_path is normally 108 bytes including NUL.
    for path in (paths.control_socket, paths.telemetry_socket):
        if len(os.fsencode(path)) >= 104:
            raise LocalProtocolError("AgentOS runtime socket path is too long")
    return paths


def bind_owner_socket(path: Path, *, backlog: int = 8) -> socket.socket:
    if not hasattr(socket, "AF_UNIX"):
        raise LocalProtocolError("AF_UNIX is required for the AgentOS console")
    try:
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(backlog)
        server.settimeout(0.25)
        return server
    except OSError as error:
        try:
            server.close()  # type: ignore[possibly-undefined]
        except (NameError, OSError):
            pass
        raise LocalProtocolError("cannot create protected AgentOS local socket") from error


def publish_state(
    paths: RuntimePaths,
    *,
    session_id: str,
    token: str,
    pid: int,
    provider: str,
    model: str,
    guest_profile: str = "agentlive",
) -> None:
    if not isinstance(guest_profile, str) or guest_profile not in GUEST_PROFILES:
        raise ValueError("Guest profile is unsupported")
    state = {
        "protocol": LOCAL_PROTOCOL,
        "session_id": session_id,
        "token": token,
        "pid": pid,
        "provider": provider,
        "model": model,
        "guest_profile": guest_profile,
        "control_socket": str(paths.control_socket),
        "telemetry_socket": str(paths.telemetry_socket),
    }
    raw = encode_message(state)
    temporary = paths.state_file.with_suffix(f".{secrets.token_hex(4)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, paths.state_file)
        os.chmod(paths.state_file, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(path: Path | None = None) -> dict[str, object]:
    if path is None:
        path = runtime_base() / f"agentos-{_uid()}" / STATE_FILE_NAME
    try:
        info = path.stat()
        if info.st_mode & 0o077:
            raise LocalProtocolError("AgentOS state file permissions are unsafe")
        with path.open("rb") as handle:
            raw = handle.read(MAX_LOCAL_LINE_BYTES + 1)
    except OSError as error:
        raise LocalProtocolError("no active AgentOS console was found") from error
    if len(raw) > MAX_LOCAL_LINE_BYTES:
        raise LocalProtocolError("AgentOS state file is oversized")
    state = decode_message(raw)
    required = {
        "protocol": int,
        "session_id": str,
        "token": str,
        "pid": int,
        "control_socket": str,
        "telemetry_socket": str,
    }
    for key, expected in required.items():
        if not isinstance(state.get(key), expected) or isinstance(state.get(key), bool):
            raise LocalProtocolError("AgentOS state file is malformed")
    if state["protocol"] != LOCAL_PROTOCOL:
        raise LocalProtocolError("AgentOS local protocol version is unsupported")
    guest_profile = state.get("guest_profile")
    if guest_profile is not None and (
        not isinstance(guest_profile, str) or guest_profile not in GUEST_PROFILES
    ):
        raise LocalProtocolError("AgentOS state file has an unsupported Guest profile")
    return state


def connect_from_state(state: Mapping[str, object], *, role: str) -> socket.socket:
    if role not in ("controller", "observer"):
        raise ValueError("invalid AgentOS local role")
    key = "control_socket" if role == "controller" else "telemetry_socket"
    path = state.get(key)
    token = state.get("token")
    if not isinstance(path, str) or not isinstance(token, str):
        raise LocalProtocolError("AgentOS state lacks local connection data")
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(path)
        client.sendall(
            encode_message(
                {"type": "hello", "protocol": LOCAL_PROTOCOL, "role": role, "token": token}
            )
        )
        return client
    except OSError as error:
        try:
            client.close()  # type: ignore[possibly-undefined]
        except (NameError, OSError):
            pass
        raise LocalProtocolError("cannot connect to the AgentOS console") from error


def recv_one(stream: BinaryIO, *, limit: int = MAX_LOCAL_LINE_BYTES) -> dict[str, object]:
    raw = stream.readline(limit + 1)
    if not raw:
        raise LocalProtocolError("AgentOS local connection closed")
    if len(raw) > limit:
        raise LocalProtocolError("AgentOS local response is oversized")
    return decode_message(raw)
