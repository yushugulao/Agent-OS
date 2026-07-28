#!/usr/bin/env python3
"""Run one AgentOS QEMU case with fail-closed output monitoring."""

import argparse
import codecs
import ctypes
import hashlib
import json
import math
import os
import re
import secrets
import select
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PANIC_LINE_RE = re.compile(
    r"^\[PANIC (?:"
    r"-?\d+--?\d+\]\s+\S+:\d+:"
    r"|-?\d+\]\[\S+:\d+\]:"
    r")\s+.+$",
    re.IGNORECASE,
)
ERROR_LINE_RE = re.compile(
    r"^\[ERROR -?\d+--?\d+\](?:"
    r"unknown syscall\s+-?\d+"
    r"|-?\d+ in application, bad addr = .+?, bad instruction = .+?, "
    r"core dumped\."
    r"|IllegalInstruction in application, core dumped\."
    r"|unknown trap:.+"
    r"|invalid trap from kernel:.+"
    r")$",
    re.IGNORECASE,
)
BAD_ADDR_LINE_RE = re.compile(
    r"^\[ERROR -?\d+--?\d+\]-?\d+ in application, bad addr = .+?, "
    r"bad instruction = .+?, core dumped\.$",
    re.IGNORECASE,
)
USER_FAILURE_LINE_RE = re.compile(
    r"^[A-Za-z0-9_.-]+:\s+(?:.*\s)?(?:check failed|child_failed)"
    r"(?::|\s|$).*$",
    re.IGNORECASE,
)
READ_SIZE = 64 * 1024
POLL_SECONDS = 0.1
TERMINATE_SECONDS = 2.0
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_PENDING_CHARS = 64 * 1024
MAX_DIAGNOSTIC_LINE_CHARS = 4096
COMPLETION_NATURAL = "natural"
COMPLETION_CHECKPOINT = "checkpoint"
COMPLETION_POWERCUT = "powercut"
MARKER_SUBSTRING = "substring"
MARKER_EXACT_LINE = "exact-line"
MARKER_LINE_PREFIX = "line-prefix"
CONTROL_SIGNALS = tuple(
    signum
    for signum in (
        getattr(signal, "SIGHUP", None),
        signal.SIGINT,
        signal.SIGTERM,
    )
    if signum is not None
)
_DUMPABILITY_CONDITION = threading.Condition()
_DUMPABILITY_SESSION = None
_DUMPABILITY_POISONED = False
EXECUTION_ATTESTATION_FORMAT = "agentos-qemu-execution-attestation-v1"
RUN_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
EXECUTION_ID_RE = re.compile(r"[A-Za-z0-9._:+-]{1,128}\Z")


def _regular_file_identity(path, label):
    lexical = os.lstat(path)
    if not os.path.isfile(path) or os.path.islink(path):
        raise ValueError(f"{label} must be a regular non-symlink file")
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    if size != lexical.st_size:
        raise ValueError(f"{label} changed while it was hashed")
    return {
        "path": os.path.abspath(path),
        "bytes": size,
        "sha256": digest.hexdigest(),
    }


def _write_execution_attestation(path, value):
    destination = os.path.abspath(path)
    parent = os.path.dirname(destination)
    if not parent or not os.path.isdir(parent) or os.path.islink(parent):
        raise ValueError("attestation parent must be a real directory")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


@dataclass
class _DumpabilitySession:
    original: int
    users: int = 0
    restore_succeeded: bool | None = None


@dataclass
class _DumpabilityLease:
    session: _DumpabilitySession
    released: bool = False


def parse_duration(text, name):
    unit = text[-1:]
    number = text[:-1] if unit.isalpha() else text
    try:
        value = float(number)
    except ValueError as error:
        raise ValueError(f"bad {name}={text!r}") from error
    if unit in ("s", "S") or not unit.isalpha():
        multiplier = 1
    elif unit in ("m", "M"):
        multiplier = 60
    elif unit in ("h", "H"):
        multiplier = 3600
    else:
        raise ValueError(f"unsupported {name}={text!r}")
    duration = value * multiplier
    if not math.isfinite(duration) or duration < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return duration


@dataclass(frozen=True)
class MonitorResult:
    marker_seen: bool
    failure_seen: bool
    timed_out: bool
    elapsed: float
    reason: str
    returncode: int | None
    signals_sent: tuple[int, ...]
    output_eof: bool
    expected_faults_satisfied: bool
    checkpoint_leader_signaled: bool
    lines: tuple[str, ...]
    process_tree_gone: bool = False
    process_tree_contained: bool = False
    completion_signal_attested: bool = False
    supervisor_returncode: int | None = None
    control_endpoint_restored: bool = True
    supervisor_control_healthy: bool = True

    @property
    def succeeded(self):
        return (
            self.marker_seen
            and not self.failure_seen
            and not self.timed_out
            and self.reason == "process_exit"
            and self.returncode == 0
            and not self.signals_sent
            and self.output_eof
            and self.expected_faults_satisfied
            and self.process_tree_gone
        )

    @property
    def checkpoint_succeeded(self):
        return (
            self.marker_seen
            and not self.failure_seen
            and not self.timed_out
            and self.reason == "expected_checkpoint"
            and self.signals_sent == (signal.SIGTERM,)
            and self.completion_leader_signaled
            and self.returncode in (0, -signal.SIGTERM)
            and self.output_eof
            and self.expected_faults_satisfied
            and self.process_tree_gone
        )

    @property
    def powercut_succeeded(self):
        return (
            self.marker_seen
            and not self.failure_seen
            and not self.timed_out
            and self.reason == "expected_powercut"
            and self.signals_sent == (signal.SIGKILL,)
            and self.completion_leader_signaled
            and self.supervisor_returncode == 0
            and self.control_endpoint_restored
            and self.supervisor_control_healthy
            and self.returncode == -signal.SIGKILL
            and self.output_eof
            and self.expected_faults_satisfied
            and self.process_tree_gone
            and self.process_tree_contained
            and self.completion_signal_attested
        )

    @property
    def completion_leader_signaled(self):
        """Whether the mode-specific first signal reached the leader."""
        return self.checkpoint_leader_signaled


class OutputScanner:
    """Incrementally decode one binary stream and recognize complete records."""

    def __init__(
        self,
        init_proc,
        marker,
        output_stream,
        log_stream,
        expected_bad_addr_markers=(),
        marker_mode=MARKER_EXACT_LINE,
        expected_fault_marker_mode=MARKER_EXACT_LINE,
        max_output_bytes=MAX_OUTPUT_BYTES,
        max_pending_chars=MAX_PENDING_CHARS,
    ):
        self.init_proc = init_proc
        self.marker = marker
        if marker_mode not in (
            MARKER_SUBSTRING,
            MARKER_EXACT_LINE,
            MARKER_LINE_PREFIX,
        ):
            raise ValueError(f"unsupported marker mode: {marker_mode!r}")
        if expected_fault_marker_mode not in (
            MARKER_SUBSTRING,
            MARKER_EXACT_LINE,
        ):
            raise ValueError(
                "unsupported expected fault marker mode: "
                f"{expected_fault_marker_mode!r}"
            )
        if not marker:
            raise ValueError("completion marker must not be empty")
        self.marker_mode = marker_mode
        self.output_stream = output_stream
        self.log_stream = log_stream
        self.expected_bad_addr_markers = tuple(expected_bad_addr_markers)
        if any(not marker for marker in self.expected_bad_addr_markers):
            raise ValueError("expected fault markers must not be empty")
        if len(set(self.expected_bad_addr_markers)) != len(
            self.expected_bad_addr_markers
        ):
            raise ValueError("expected fault markers must be unique")
        self.expected_fault_marker_mode = expected_fault_marker_mode
        self.max_output_bytes = max_output_bytes
        self.max_pending_chars = max_pending_chars
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pending = ""
        self.lines = deque(maxlen=80)
        self.marker_seen = False
        self.marker_seen_at = None
        self.failure_seen = False
        self.output_limit_exceeded = False
        self.total_output_bytes = 0
        self.expected_fault_markers_seen = set()
        self.expected_fault_pending = 0
        self.expected_faults_consumed = 0
        self.finished = False
        self.log_pending_cr = False

    @property
    def expected_faults_satisfied(self):
        expected = len(self.expected_bad_addr_markers)
        return (
            len(self.expected_fault_markers_seen) == expected
            and self.expected_faults_consumed == expected
            and self.expected_fault_pending == 0
        )

    def _emit(self, text, *, final=False):
        if text and self.output_stream is not None:
            self.output_stream.write(text)
            self.output_stream.flush()
        if self.log_stream is not None:
            canonical = ("\r" if self.log_pending_cr else "") + text
            self.log_pending_cr = False
            if not final and canonical.endswith("\r"):
                canonical = canonical[:-1]
                self.log_pending_cr = True
            canonical = canonical.replace("\r\n", "\n").replace("\r", "\n")
            if canonical:
                self.log_stream.write(canonical)
                self.log_stream.flush()

    def _process_line(self, line):
        line = line.rstrip("\r")
        if len(line) > self.max_pending_chars:
            self.output_limit_exceeded = True
            self.failure_seen = True
        if len(line) > MAX_DIAGNOSTIC_LINE_CHARS:
            prefix = "[...truncated...]"
            line_for_diagnostics = (
                prefix
                + line[-(MAX_DIAGNOSTIC_LINE_CHARS - len(prefix)):]
            )
        else:
            line_for_diagnostics = line
        self.lines.append(line_for_diagnostics)
        if (
            self.marker_mode == MARKER_EXACT_LINE
            and line == self.marker
        ) or (
            self.marker_mode == MARKER_LINE_PREFIX
            and line.startswith(self.marker)
        ) or (
            self.marker_mode == MARKER_SUBSTRING
            and self.marker in line
        ):
            self._note_marker()

        normalized = ANSI_ESCAPE_RE.sub("", line)
        for marker in self.expected_bad_addr_markers:
            if self.expected_fault_marker_mode == MARKER_EXACT_LINE:
                if line == marker:
                    self._arm_expected_fault(marker)
                continue
            start = line.find(marker)
            while start >= 0:
                self._arm_expected_fault(marker)
                start = line.find(marker, start + len(marker))
        if BAD_ADDR_LINE_RE.fullmatch(normalized):
            if self.expected_fault_pending:
                self.expected_fault_pending -= 1
                self.expected_faults_consumed += 1
            else:
                self.failure_seen = True
        elif (
            PANIC_LINE_RE.fullmatch(normalized)
            or ERROR_LINE_RE.fullmatch(normalized)
            or USER_FAILURE_LINE_RE.fullmatch(normalized)
        ):
            self.failure_seen = True

    def _arm_expected_fault(self, marker):
        if (
            marker in self.expected_fault_markers_seen
            or self.expected_fault_pending != 0
        ):
            self.failure_seen = True
            return
        self.expected_fault_markers_seen.add(marker)
        self.expected_fault_pending += 1

    def _note_marker(self):
        if self.marker_seen:
            return
        self.marker_seen = True
        self.marker_seen_at = time.monotonic()

    def _consume_text(self, text):
        self.pending += text
        if (
            self.marker_mode == MARKER_SUBSTRING
            and not self.marker_seen
            and self.marker in self.pending
        ):
            self._note_marker()
        records = self.pending.split("\n")
        self.pending = records.pop()
        for line in records:
            self._process_line(line)
        if len(self.pending) > self.max_pending_chars:
            prefix = "[...unterminated output truncated...]"
            self.lines.append(
                prefix
                + self.pending[
                    -(MAX_DIAGNOSTIC_LINE_CHARS - len(prefix)):
                ]
            )
            self.pending = self.pending[-MAX_DIAGNOSTIC_LINE_CHARS:]
            self.output_limit_exceeded = True
            self.failure_seen = True

    def feed(self, data):
        if self.finished:
            raise RuntimeError("cannot feed a finished output scanner")
        if self.output_limit_exceeded:
            return
        remaining = self.max_output_bytes - self.total_output_bytes
        accepted = data[:max(0, remaining)]
        self.total_output_bytes += len(data)
        text = self.decoder.decode(accepted)
        self._consume_text(text)
        self._emit(text)
        if len(accepted) != len(data):
            self.output_limit_exceeded = True
            self.failure_seen = True

    def finish(self):
        if self.finished:
            return
        tail = self.decoder.decode(b"", final=True)
        self._consume_text(tail)
        self._emit(tail, final=True)
        if self.pending:
            self._process_line(self.pending)
            self.pending = ""
        self.finished = True


def _write_notice(stream, message):
    if stream is not None:
        print(message, file=stream, flush=True)


def _read_available_chunk(fd, scanner):
    """Read at most one chunk so timeout checks cannot be starved by output."""
    try:
        data = os.read(fd, READ_SIZE)
    except (BlockingIOError, InterruptedError):
        return False, False
    if not data:
        return False, True
    scanner.feed(data)
    return True, False


def _drain_until(proc, fd, scanner, deadline):
    eof = False
    while not eof:
        now = time.monotonic()
        if now >= deadline:
            break
        received, eof = _read_available_chunk(fd, scanner)
        if eof:
            break
        now = time.monotonic()
        if now >= deadline:
            break
        wait = min(POLL_SECONDS, deadline - now)
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready and proc.poll() is not None and not received:
            # A closed pipe becomes readable as EOF; allow one short turn for it.
            continue
    return eof


def _leader_exited(proc):
    """Observe leader exit without reaping its PID on Linux/WSL."""
    if isinstance(proc, _SupervisedProcess):
        return proc.leader_exited()
    if proc.returncode is not None:
        return True
    if (
        os.name == "posix"
        and hasattr(os, "waitid")
        and hasattr(os, "WNOWAIT")
    ):
        try:
            status = os.waitid(
                os.P_PID,
                proc.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except ChildProcessError:
            return proc.poll() is not None
        return status is not None
    return proc.poll() is not None


@dataclass(frozen=True)
class _LinuxProcessInfo:
    parent: int
    process_group: int
    state: str
    start_time: int


def _read_linux_process_info(pid):
    try:
        with open(
            f"/proc/{pid}/stat",
            "r",
            encoding="ascii",
            errors="replace",
        ) as stat_file:
            stat = stat_file.read()
        close_paren = stat.rfind(")")
        if close_paren < 0:
            return None
        fields = stat[close_paren + 2:].split()
        return _LinuxProcessInfo(
            parent=int(fields[1]),
            process_group=int(fields[2]),
            state=fields[0],
            start_time=int(fields[19]),
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    except (OSError, ValueError, IndexError):
        return None


def _linux_process_snapshot():
    try:
        entries = os.scandir("/proc")
    except OSError:
        return None
    processes = {}
    with entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            info = _read_linux_process_info(pid)
            if info is not None:
                processes[pid] = info
    return processes


def _linux_process_group_has_live_member(pgid):
    """Return None off procfs, otherwise ignore exited group zombies."""
    processes = _linux_process_snapshot()
    if processes is None:
        return None
    for info in processes.values():
        if (
            info.process_group == pgid
            and info.state not in ("X", "Z")
        ):
            return True
    return False


def _enable_linux_child_subreaper():
    """Contain daemonizing descendants so they remain attributable to us."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.restype = ctypes.c_int
        return prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    except (AttributeError, OSError):
        return False


def _get_linux_dumpability():
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.restype = ctypes.c_int
        result = prctl(3, 0, 0, 0, 0)  # PR_GET_DUMPABLE
        return result if result >= 0 else None
    except (AttributeError, OSError):
        return None


def _set_linux_dumpability(value):
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.restype = ctypes.c_int
        return prctl(4, value, 0, 0, 0) == 0  # PR_SET_DUMPABLE
    except (AttributeError, OSError):
        return False


def _disable_linux_dumpability():
    """Prevent an untrusted same-UID child from reopening control FDs."""
    return _set_linux_dumpability(0)


def _acquire_linux_control_endpoint_hardening():
    """Join one process-wide hardening session for concurrent monitors."""
    global _DUMPABILITY_SESSION
    with _DUMPABILITY_CONDITION:
        if _DUMPABILITY_POISONED:
            return None
        if _DUMPABILITY_SESSION is None:
            original = _get_linux_dumpability()
            if original is None or not _set_linux_dumpability(0):
                return None
            _DUMPABILITY_SESSION = _DumpabilitySession(original=original)
        _DUMPABILITY_SESSION.users += 1
        return _DumpabilityLease(session=_DUMPABILITY_SESSION)


def _release_linux_control_endpoint_hardening(lease):
    """Return one shared restore result to every concurrent lifecycle."""
    global _DUMPABILITY_POISONED, _DUMPABILITY_SESSION
    with _DUMPABILITY_CONDITION:
        session = lease.session
        if not lease.released:
            if session.users <= 0:
                return False
            session.users -= 1
            lease.released = True
            if session.users == 0:
                try:
                    session.restore_succeeded = _set_linux_dumpability(
                        session.original
                    )
                except BaseException:
                    session.restore_succeeded = False
                if not session.restore_succeeded:
                    _DUMPABILITY_POISONED = True
                if _DUMPABILITY_SESSION is session:
                    _DUMPABILITY_SESSION = None
                _DUMPABILITY_CONDITION.notify_all()
        interrupted = None
        while session.restore_succeeded is None:
            try:
                _DUMPABILITY_CONDITION.wait()
            except BaseException as error:
                interrupted = error
        if interrupted is not None:
            raise interrupted
        return bool(session.restore_succeeded)


def _signal_stable_linux_pid(pid, start_time, sig):
    """Signal one tracked PID without crossing a PID-reuse boundary."""
    pidfd = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            pidfd = os.pidfd_open(pid, 0)
        current = _read_linux_process_info(pid)
        if (
            current is None
            or current.start_time != start_time
            or current.state in ("X", "Z")
        ):
            return False
        if pidfd is not None:
            signal.pidfd_send_signal(pidfd, sig)
        else:
            os.kill(pid, sig)
        return True
    except (OSError, ProcessLookupError):
        return False
    finally:
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass


def _linux_descendants(parent_pid, processes):
    descendants = set()
    frontier = {parent_pid}
    while frontier:
        next_frontier = {
            pid
            for pid, info in processes.items()
            if info.parent in frontier and pid not in descendants
        }
        descendants.update(next_frontier)
        frontier = next_frontier
    return descendants


def _kill_supervised_descendants(reserved_leader, timeout=TERMINATE_SECONDS):
    """Kill and reap every child in a dedicated subreaper process."""
    deadline = time.monotonic() + timeout
    while True:
        processes = _linux_process_snapshot()
        if processes is None:
            return False
        descendants = _linux_descendants(os.getpid(), processes) - {
            reserved_leader
        }
        if not descendants:
            return True
        for pid in descendants:
            info = processes.get(pid)
            if info is None:
                continue
            if info.state == "Z":
                try:
                    os.waitpid(pid, os.WNOHANG)
                except (ChildProcessError, OSError):
                    pass
            elif info.state != "X":
                _signal_stable_linux_pid(
                    pid,
                    info.start_time,
                    signal.SIGKILL,
                )
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def _wait_for_supervised_leader_exit(child, timeout=TERMINATE_SECONDS):
    """Wait for exit without reaping, preserving the leader PID and PGID."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = os.waitid(
                os.P_PID,
                child.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except (AttributeError, ChildProcessError, OSError):
            return False
        if status is not None:
            return True
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(min(POLL_SECONDS, deadline - now))


def _force_cleanup_supervised_tree(child, leader_info):
    """Fail closed while retaining the leader until descendants are gone."""
    if leader_info is None:
        try:
            child.kill()
        except ProcessLookupError:
            pass
    else:
        _supervisor_signal_leader(child, leader_info, signal.SIGKILL)
    leader_exited = _wait_for_supervised_leader_exit(child)
    descendants_gone = _kill_supervised_descendants(child.pid)
    try:
        child.wait(timeout=TERMINATE_SECONDS)
    except (subprocess.TimeoutExpired, ChildProcessError):
        return False
    return leader_exited and descendants_gone


def _supervisor_signal_leader(child, leader_info, sig):
    group_liveness = _linux_process_group_has_live_member(child.pid)
    leader_sent = _signal_stable_linux_pid(
        child.pid,
        leader_info.start_time,
        sig,
    )
    group_sent = _signal_process_group(child, sig)
    tree_sent = leader_sent or (
        group_sent and group_liveness is not False
    )
    return tree_sent, leader_sent


def _run_powercut_supervisor(control_fd, request_fd, nonce, command):
    """Own one trusted host workload and attest its Guest-facing powercut.

    Same-UID hostile host programs require an external UID/PID-namespace/
    cgroup boundary; this protocol only prevents Guest-controlled QEMU state
    from forging successful completion.
    """
    try:
        os.set_inheritable(control_fd, False)
        os.set_inheritable(request_fd, False)
    except OSError:
        os.close(control_fd)
        os.close(request_fd)
        return 125
    if (
        not _enable_linux_child_subreaper()
        or not _disable_linux_dumpability()
    ):
        os.write(control_fd, b"ERR supervisor hardening unavailable\n")
        os.close(control_fd)
        os.close(request_fd)
        return 125
    try:
        child = subprocess.Popen(command, start_new_session=True)
    except (OSError, ValueError) as error:
        message = f"ERR spawn failed: {error}\n".encode(
            "utf-8",
            errors="replace",
        )
        os.write(control_fd, message[:4096])
        os.close(control_fd)
        os.close(request_fd)
        return 125
    info = _read_linux_process_info(child.pid)
    if info is None:
        _force_cleanup_supervised_tree(child, None)
        os.write(control_fd, b"ERR missing leader identity\n")
        os.close(control_fd)
        os.close(request_fd)
        return 125
    try:
        os.write(
            control_fd,
            f"OK {nonce} {child.pid} {info.start_time}\n".encode("ascii"),
        )
    except OSError:
        _force_cleanup_supervised_tree(child, info)
        os.close(control_fd)
        os.close(request_fd)
        return 125
    os.set_blocking(request_fd, False)
    request_buffer = bytearray()
    delivered_signal = 0
    delivered_to_leader = False
    request_open = True
    try:
        while True:
            status = os.waitid(
                os.P_PID,
                child.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            if status is not None:
                break
            ready, _, _ = select.select([request_fd], [], [], POLL_SECONDS)
            if not ready:
                continue
            chunk = os.read(request_fd, 4096 - len(request_buffer))
            if not chunk:
                _supervisor_signal_leader(child, info, signal.SIGKILL)
                os.close(request_fd)
                request_open = False
                if not _wait_for_supervised_leader_exit(child):
                    _force_cleanup_supervised_tree(child, info)
                    os.close(control_fd)
                    return 125
                break
            request_buffer.extend(chunk)
            if len(request_buffer) >= 4096 and b"\n" not in request_buffer:
                _supervisor_signal_leader(child, info, signal.SIGKILL)
                continue
            while b"\n" in request_buffer:
                raw_line, remainder = bytes(request_buffer).split(b"\n", 1)
                request_buffer = bytearray(remainder)
                fields = raw_line.decode(
                    "ascii",
                    errors="replace",
                ).split()
                valid = (
                    len(fields) == 5
                    and fields[:2] == ["SIGNAL", nonce]
                )
                try:
                    request_pid = int(fields[2]) if valid else -1
                    request_start = int(fields[3]) if valid else -1
                    request_signal = int(fields[4]) if valid else -1
                except ValueError:
                    valid = False
                valid = (
                    valid
                    and request_pid == child.pid
                    and request_start == info.start_time
                    and request_signal in (signal.SIGTERM, signal.SIGKILL)
                )
                if not valid:
                    _supervisor_signal_leader(child, info, signal.SIGKILL)
                    continue
                tree_sent, leader_sent = _supervisor_signal_leader(
                    child,
                    info,
                    request_signal,
                )
                if leader_sent:
                    delivered_signal = request_signal
                    delivered_to_leader = True
                os.write(
                    control_fd,
                    (
                        f"ACK {nonce} {child.pid} {info.start_time} "
                        f"{request_signal} {1 if tree_sent else 0} "
                        f"{1 if leader_sent else 0}\n"
                    ).encode("ascii"),
                )
    except (AttributeError, ChildProcessError, OSError):
        _force_cleanup_supervised_tree(child, info)
        if request_open:
            os.close(request_fd)
        os.close(control_fd)
        return 125
    if request_open:
        os.close(request_fd)
    if not _kill_supervised_descendants(child.pid):
        child.wait()
        os.close(control_fd)
        return 125
    returncode = child.wait()
    try:
        os.write(
            control_fd,
            (
                f"DONE {nonce} {child.pid} {info.start_time} "
                f"{returncode} CLEAN {delivered_signal} "
                f"{1 if delivered_to_leader else 0}\n"
            ).encode("ascii"),
        )
    except OSError:
        os.close(control_fd)
        return 125
    os.close(control_fd)
    return 0


class _SupervisedProcess:
    """Expose a supervised workload leader through the Popen subset we use."""

    def __init__(
        self,
        owner,
        leader_pid,
        leader_start_time,
        control_fd,
        request_fd,
        control_buffer,
        nonce,
    ):
        self.owner = owner
        self.pid = leader_pid
        self.leader_start_time = leader_start_time
        self.stdout = owner.stdout
        self.control_fd = control_fd
        self.request_fd = request_fd
        self.control_buffer = bytearray(control_buffer)
        self.nonce = nonce
        self.leader_returncode = None
        self.cleanup_attested = False
        self.signal_attested = False
        self.control_healthy = True
        self.pending_completion_line = None

    @property
    def returncode(self):
        return self.leader_returncode

    @property
    def supervisor_returncode(self):
        return self.owner.returncode

    def poll(self):
        return self.owner.poll()

    def wait(self, timeout=None):
        self.owner.wait(timeout=timeout)
        self._collect_completion()
        return self.returncode

    @property
    def process_tree_contained(self):
        return self.cleanup_attested and self.control_healthy

    @property
    def process_tree_gone(self):
        return self.cleanup_attested

    def close_control(self):
        for attribute in ("control_fd", "request_fd"):
            fd = getattr(self, attribute)
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
            setattr(self, attribute, None)

    def request_signal(self, sig):
        if self.request_fd is None or self.control_fd is None:
            self.control_healthy = False
            return False, False, False
        request = (
            f"SIGNAL {self.nonce} {self.pid} {self.leader_start_time} "
            f"{int(sig)}\n"
        ).encode("ascii")
        try:
            os.write(self.request_fd, request)
        except (BrokenPipeError, OSError):
            self.control_healthy = False
            return False, False, False
        deadline = time.monotonic() + TERMINATE_SECONDS
        while True:
            line = self._read_control_line(deadline)
            if line is None:
                self.control_healthy = False
                return False, False, False
            fields = line.split()
            if fields[:2] == ["DONE", self.nonce]:
                self.pending_completion_line = line
                return False, False, False
            if len(fields) != 7 or fields[:2] != ["ACK", self.nonce]:
                self.control_healthy = False
                return False, False, False
            try:
                pid = int(fields[2])
                start_time = int(fields[3])
                ack_signal = int(fields[4])
                tree_sent = int(fields[5]) == 1
                leader_sent = int(fields[6]) == 1
            except ValueError:
                self.control_healthy = False
                return False, False, False
            valid = (
                pid == self.pid
                and start_time == self.leader_start_time
                and ack_signal == int(sig)
            )
            if not valid:
                self.control_healthy = False
                return False, False, False
            return tree_sent, leader_sent, leader_sent

    def resume_owner(self):
        if self.owner.poll() is not None or os.name != "posix":
            return False
        try:
            self.owner.send_signal(signal.SIGCONT)
            return True
        except ProcessLookupError:
            return False

    def terminate_owner(self):
        self.control_healthy = False
        self.resume_owner()
        if self.owner.poll() is not None:
            return False
        try:
            self.owner.terminate()
            return True
        except ProcessLookupError:
            return False

    def kill_owner(self):
        self.control_healthy = False
        if self.owner.poll() is not None:
            return False
        try:
            self.owner.kill()
            return True
        except ProcessLookupError:
            return False

    def _read_control_line(self, deadline):
        if self.control_fd is None:
            return None
        while b"\n" not in self.control_buffer:
            now = time.monotonic()
            if now >= deadline:
                return None
            try:
                ready, _, _ = select.select(
                    [self.control_fd],
                    [],
                    [],
                    min(POLL_SECONDS, deadline - now),
                )
            except (OSError, ValueError):
                self.control_healthy = False
                return None
            if not ready:
                continue
            try:
                chunk = os.read(
                    self.control_fd,
                    4096 - len(self.control_buffer),
                )
            except OSError:
                self.control_healthy = False
                return None
            if not chunk:
                self.control_healthy = False
                return None
            self.control_buffer.extend(chunk)
            if len(self.control_buffer) >= 4096:
                return None
        raw_line, remainder = bytes(self.control_buffer).split(b"\n", 1)
        self.control_buffer = bytearray(remainder)
        return raw_line.decode("ascii", errors="replace")

    def _collect_completion(self):
        if self.control_fd is None:
            return
        deadline = time.monotonic() + TERMINATE_SECONDS
        try:
            line = self.pending_completion_line
            if line is None:
                line = self._read_control_line(deadline)
            while line is not None:
                fields = line.split()
                if len(fields) == 8 and fields[:2] == ["DONE", self.nonce]:
                    break
                if len(fields) != 7 or fields[:2] != ["ACK", self.nonce]:
                    self.control_healthy = False
                    return
                try:
                    ack_pid = int(fields[2])
                    ack_start_time = int(fields[3])
                    ack_signal = int(fields[4])
                    ack_tree_sent = int(fields[5])
                    ack_leader_sent = int(fields[6])
                except ValueError:
                    self.control_healthy = False
                    return
                if (
                    ack_pid != self.pid
                    or ack_start_time != self.leader_start_time
                    or ack_signal not in (signal.SIGTERM, signal.SIGKILL)
                    or ack_tree_sent not in (0, 1)
                    or ack_leader_sent not in (0, 1)
                ):
                    self.control_healthy = False
                    return
                line = self._read_control_line(deadline)
            if line is None:
                return
            try:
                pid = int(fields[2])
                start_time = int(fields[3])
                leader_returncode = int(fields[4])
                delivered_signal = int(fields[6])
                delivered_to_leader = int(fields[7]) == 1
            except ValueError:
                self.control_healthy = False
                return
            if fields[5] != "CLEAN":
                self.control_healthy = False
                return
            identity_attested = (
                pid == self.pid
                and start_time == self.leader_start_time
            )
            if identity_attested:
                self.leader_returncode = leader_returncode
            else:
                self.control_healthy = False
            self.cleanup_attested = (
                identity_attested
                and self.supervisor_returncode == 0
            )
            self.signal_attested = (
                self.cleanup_attested
                and delivered_signal == signal.SIGKILL
                and delivered_to_leader
            )
        except (OSError, ValueError):
            self.control_healthy = False
        finally:
            self.close_control()

    def leader_exited(self):
        info = _read_linux_process_info(self.pid)
        return (
            info is None
            or info.start_time != self.leader_start_time
            or info.state in ("X", "Z")
        )


def _process_tree_alive(proc):
    if not _leader_exited(proc):
        return True
    if isinstance(proc, _SupervisedProcess):
        return proc.poll() is None
    if os.name != "posix":
        return False
    live_member = _linux_process_group_has_live_member(proc.pid)
    if live_member is not None:
        return live_member
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _open_pidfd(proc):
    if (
        os.name != "posix"
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
    ):
        return None
    try:
        return os.pidfd_open(proc.pid, 0)
    except OSError:
        return None


def _signal_leader(proc, pidfd, sig):
    """Return (sent, identity_confirmed) for the QEMU leader itself."""
    if _leader_exited(proc):
        return False, False
    try:
        if pidfd is not None:
            signal.pidfd_send_signal(pidfd, sig)
            return True, True
        if os.name == "nt":
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
            return True, True
        os.kill(proc.pid, sig)
    except ProcessLookupError:
        return False, False
    return True, False


def _signal_process_group(proc, sig):
    if os.name != "posix":
        return False
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return False
    return True


def _signal_process_tree(proc, pidfd, sig):
    """Signal the stable leader and its still-reserved process group."""
    if isinstance(proc, _SupervisedProcess):
        tree_sent, leader_sent, leader_confirmed = proc.request_signal(sig)
        if tree_sent or leader_sent:
            return tree_sent, leader_sent, leader_confirmed
        if not proc.control_healthy:
            # The owner may be SIGSTOPed. Resume it so a queued authenticated
            # request can still drive best-effort descendant cleanup, while
            # permanently invalidating completion attestation.
            proc.resume_owner()
        # A dead supervisor cannot attest completion, but still kill the
        # stable leader and its original group as emergency cleanup.
        group_liveness = _linux_process_group_has_live_member(proc.pid)
        leader_sent, leader_confirmed = _signal_leader(proc, pidfd, sig)
        group_sent = _signal_process_group(proc, sig)
        group_delivery = group_sent and group_liveness is not False
        return (
            leader_sent or group_delivery,
            leader_sent,
            leader_confirmed,
        )
    group_liveness = (
        _linux_process_group_has_live_member(proc.pid)
        if os.name == "posix"
        else None
    )
    leader_sent, leader_confirmed = _signal_leader(proc, pidfd, sig)
    group_sent = False
    if os.name == "posix":
        # The unreaped leader reserves its PID and PGID. Always attempt the
        # group signal so a procfs observation race cannot orphan a child.
        group_sent = _signal_process_group(proc, sig)
    elif os.name == "nt" and not leader_sent and not _leader_exited(proc):
        try:
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
            leader_sent = True
            leader_confirmed = True
        except ProcessLookupError:
            pass
    group_delivery = group_sent and group_liveness is not False
    return (
        leader_sent or group_delivery,
        leader_sent,
        leader_confirmed,
    )


def _wait_for_tree_exit_and_eof(
    proc,
    pidfd,
    fd,
    scanner,
    deadline,
    initial_eof=False,
):
    """Wait until the leader, its process group, and the log pipe are gone."""
    eof = initial_eof
    while True:
        if not eof:
            _, reached_eof = _read_available_chunk(fd, scanner)
            eof = eof or reached_eof
        leader_exited = _leader_exited(proc)
        if (
            leader_exited
            and not _process_tree_alive(proc)
            and eof
        ):
            return eof

        now = time.monotonic()
        if now >= deadline:
            return eof
        wait = min(POLL_SECONDS, deadline - now)
        readers = []
        if not eof:
            readers.append(fd)
        if not leader_exited and pidfd is not None:
            readers.append(pidfd)
        if readers:
            select.select(readers, [], [], wait)
        else:
            # Do not reap the leader until group teardown is complete. Keeping
            # its PID reserved prevents a recycled PGID from being signaled.
            time.sleep(wait)


def _stop_and_drain(
    proc,
    pidfd,
    fd,
    scanner,
    first_signal=signal.SIGTERM,
    natural_exit_deadline=None,
):
    signals_sent = []
    first_signal_confirmed_for_leader = False
    first_signal_sent_to_leader = False
    eof = False
    if natural_exit_deadline is not None:
        eof = _wait_for_tree_exit_and_eof(
            proc,
            pidfd,
            fd,
            scanner,
            natural_exit_deadline,
        )
    sent, leader_sent, confirmed = _signal_process_tree(
        proc,
        pidfd,
        first_signal,
    )
    if sent:
        signals_sent.append(first_signal)
    first_signal_sent_to_leader = leader_sent
    first_signal_confirmed_for_leader = confirmed
    eof = _wait_for_tree_exit_and_eof(
        proc,
        pidfd,
        fd,
        scanner,
        time.monotonic() + TERMINATE_SECONDS,
        initial_eof=eof,
    )
    if (
        isinstance(proc, _SupervisedProcess)
        and not proc.control_healthy
        and proc.owner.poll() is None
    ):
        proc.terminate_owner()
        eof = _wait_for_tree_exit_and_eof(
            proc,
            pidfd,
            fd,
            scanner,
            time.monotonic() + TERMINATE_SECONDS,
            initial_eof=eof,
        )
    if _process_tree_alive(proc):
        if isinstance(proc, _SupervisedProcess) and not proc.control_healthy:
            proc.kill_owner()
        else:
            sent, _, _ = _signal_process_tree(
                proc,
                pidfd,
                signal.SIGKILL,
            )
            if sent:
                signals_sent.append(signal.SIGKILL)
        eof = _wait_for_tree_exit_and_eof(
            proc,
            pidfd,
            fd,
            scanner,
            time.monotonic() + TERMINATE_SECONDS,
            initial_eof=eof,
        )
    try:
        proc.wait(timeout=TERMINATE_SECONDS)
    except subprocess.TimeoutExpired:
        if isinstance(proc, _SupervisedProcess):
            proc.kill_owner()
        else:
            sent, _, _ = _signal_process_tree(
                proc,
                pidfd,
                signal.SIGKILL,
            )
            if sent:
                signals_sent.append(signal.SIGKILL)
        proc.wait(timeout=TERMINATE_SECONDS)
    if (
        not first_signal_confirmed_for_leader
        and first_signal_sent_to_leader
        and proc.returncode == -first_signal
    ):
        first_signal_confirmed_for_leader = True
    if not eof:
        eof = _drain_until(
            proc,
            fd,
            scanner,
            time.monotonic() + TERMINATE_SECONDS,
        )
    if isinstance(proc, _SupervisedProcess):
        process_tree_gone = proc.process_tree_gone
    else:
        process_tree_gone = not _process_tree_alive(proc)
    process_tree_contained = getattr(proc, "process_tree_contained", False)
    completion_signal_attested = getattr(
        proc,
        "signal_attested",
        False,
    )
    return (
        tuple(signals_sent),
        eof,
        first_signal_confirmed_for_leader,
        process_tree_gone,
        process_tree_contained,
        completion_signal_attested,
    )


def _force_stop_process_tree(
    proc,
    pidfd,
    first_signal=signal.SIGTERM,
):
    """Exception-path teardown that cannot depend on output decoding."""
    if proc is None:
        return
    try:
        _signal_process_tree(proc, pidfd, first_signal)
    except Exception:
        pass
    deadline = time.monotonic() + TERMINATE_SECONDS
    while time.monotonic() < deadline:
        try:
            leader_alive = not _leader_exited(proc)
            tree_alive = _process_tree_alive(proc)
        except Exception:
            break
        if not leader_alive and not tree_alive:
            break
        time.sleep(POLL_SECONDS)
    if (
        isinstance(proc, _SupervisedProcess)
        and not proc.control_healthy
        and proc.owner.poll() is None
    ):
        proc.terminate_owner()
        deadline = time.monotonic() + TERMINATE_SECONDS
        while (
            proc.owner.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(POLL_SECONDS)
    try:
        if isinstance(proc, _SupervisedProcess) and not proc.control_healthy:
            proc.kill_owner()
        else:
            _signal_process_tree(proc, pidfd, signal.SIGKILL)
    except Exception:
        pass
    deadline = time.monotonic() + TERMINATE_SECONDS
    while time.monotonic() < deadline:
        try:
            if not _process_tree_alive(proc):
                break
        except Exception:
            break
        time.sleep(POLL_SECONDS)
    try:
        proc.wait(timeout=TERMINATE_SECONDS)
    except subprocess.TimeoutExpired:
        if isinstance(proc, _SupervisedProcess):
            proc.kill_owner()
            try:
                proc.wait(timeout=TERMINATE_SECONDS)
            except Exception:
                pass
    except Exception:
        pass


def _discard_output(fd):
    if fd is None:
        return
    deadline = time.monotonic() + TERMINATE_SECONDS
    while time.monotonic() < deadline:
        try:
            data = os.read(fd, READ_SIZE)
        except BlockingIOError:
            data = None
        except (InterruptedError, OSError):
            return
        if data == b"":
            return
        if data is None:
            try:
                select.select(
                    [fd],
                    [],
                    [],
                    min(POLL_SECONDS, deadline - time.monotonic()),
                )
            except (OSError, ValueError):
                return


def _unblock_child_control_signals():
    signal.pthread_sigmask(
        signal.SIG_UNBLOCK,
        set(CONTROL_SIGNALS),
    )


def _read_supervisor_handshake(fd, owner, nonce):
    deadline = time.monotonic() + TERMINATE_SECONDS
    message = bytearray()
    os.set_blocking(fd, False)
    try:
        while b"\n" not in message:
            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError("powercut supervisor handshake timed out")
            ready, _, _ = select.select(
                [fd],
                [],
                [],
                min(POLL_SECONDS, deadline - now),
            )
            if not ready:
                if owner.poll() is not None:
                    raise RuntimeError("powercut supervisor exited at startup")
                continue
            chunk = os.read(fd, 4096 - len(message))
            if not chunk:
                raise RuntimeError("powercut supervisor closed its handshake")
            message.extend(chunk)
            if len(message) >= 4096 and b"\n" not in message:
                raise RuntimeError("powercut supervisor handshake is oversized")
        raw_line, remainder = bytes(message).split(b"\n", 1)
        line = raw_line.decode("utf-8", errors="replace")
        fields = line.split()
        if len(fields) != 4 or fields[:2] != ["OK", nonce]:
            raise RuntimeError(f"powercut supervisor startup failed: {line}")
        try:
            return int(fields[2]), int(fields[3]), fd, remainder
        except ValueError as error:
            raise RuntimeError("bad powercut supervisor identity") from error
    except BaseException:
        os.close(fd)
        raise


class _ProcessLifecycle:
    """Own a spawned QEMU process until its full output tree is drained."""

    def __init__(self):
        self.proc = None
        self.fd = None
        self.pidfd = None
        self.scanner = None
        self.completion_mode = None
        self.control_endpoint_lease = None
        self.control_endpoint_restored = True
        self.released = False

    def start(self, command, scanner, completion_mode):
        blocked = None
        maskable = (
            os.name == "posix"
            and threading.current_thread() is threading.main_thread()
            and hasattr(signal, "pthread_sigmask")
        )
        if maskable:
            blocked = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                set(CONTROL_SIGNALS),
            )
        try:
            popen_options = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "bufsize": 0,
                "start_new_session": os.name == "posix",
            }
            if maskable:
                # The parent blocks cancellation across Popen so it cannot
                # lose ownership between fork and attach. Do not leak that
                # mask into QEMU.
                popen_options["preexec_fn"] = _unblock_child_control_signals
            if os.name == "nt":
                popen_options["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                )
            self.scanner = scanner
            self.completion_mode = completion_mode
            if completion_mode == COMPLETION_POWERCUT:
                if not sys.platform.startswith("linux"):
                    raise RuntimeError(
                        "powercut completion requires Linux containment"
                    )
                lease = _acquire_linux_control_endpoint_hardening()
                if lease is None:
                    raise RuntimeError(
                        "powercut control endpoint hardening unavailable"
                    )
                self.control_endpoint_lease = lease
                control_read, control_write = os.pipe()
                request_read, request_write = os.pipe()
                nonce = secrets.token_hex(32)
                supervisor_command = [
                    sys.executable,
                    os.path.abspath(__file__),
                    "--internal-powercut-supervisor",
                    str(control_write),
                    str(request_read),
                    nonce,
                    "--",
                    *command,
                ]
                popen_options["pass_fds"] = (control_write, request_read)
                try:
                    owner = subprocess.Popen(
                        supervisor_command,
                        **popen_options,
                    )
                except BaseException:
                    os.close(control_read)
                    os.close(request_read)
                    os.close(request_write)
                    raise
                finally:
                    os.close(control_write)
                os.close(request_read)
                self.proc = owner
                try:
                    (
                        leader_pid,
                        leader_start_time,
                        completion_fd,
                        completion_buffer,
                    ) = _read_supervisor_handshake(
                        control_read,
                        owner,
                        nonce,
                    )
                except BaseException:
                    os.close(request_write)
                    raise
                proc = _SupervisedProcess(
                    owner,
                    leader_pid,
                    leader_start_time,
                    completion_fd,
                    request_write,
                    completion_buffer,
                    nonce,
                )
                self.proc = proc
            else:
                proc = subprocess.Popen(command, **popen_options)
                self.proc = proc
            self.pidfd = _open_pidfd(proc)
            assert proc.stdout is not None
            self.fd = proc.stdout.fileno()
            os.set_blocking(self.fd, False)
            return proc, self.fd
        finally:
            if blocked is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def release(self):
        if isinstance(self.proc, _SupervisedProcess):
            self.proc.close_control()
        self._close_pidfd()
        restored = self._release_control_endpoint_hardening()
        self.released = True
        return restored

    def _close_pidfd(self):
        if self.pidfd is None:
            return
        try:
            os.close(self.pidfd)
        except OSError:
            pass
        self.pidfd = None

    def _release_control_endpoint_hardening(self):
        if self.control_endpoint_lease is None:
            return self.control_endpoint_restored
        self.control_endpoint_restored = (
            _release_linux_control_endpoint_hardening(
                self.control_endpoint_lease
            )
        )
        self.control_endpoint_lease = None
        return self.control_endpoint_restored

    def abort(self):
        if self.released:
            return
        if self.proc is None:
            restored = self._release_control_endpoint_hardening()
            self.released = True
            if not restored:
                raise RuntimeError(
                    "powercut control endpoint restoration failed"
                )
            return
        first_signal = (
            signal.SIGKILL
            if (
                self.completion_mode == COMPLETION_POWERCUT
                and self.scanner is not None
                and self.scanner.marker_seen
            )
            else signal.SIGTERM
        )
        try:
            if self.fd is not None and self.scanner is not None:
                _stop_and_drain(
                    self.proc,
                    self.pidfd,
                    self.fd,
                    self.scanner,
                    first_signal=first_signal,
                )
            else:
                _force_stop_process_tree(
                    self.proc,
                    self.pidfd,
                    first_signal=first_signal,
                )
        except BaseException:
            _force_stop_process_tree(
                self.proc,
                self.pidfd,
                first_signal=first_signal,
            )
            _discard_output(self.fd)
        try:
            if self.scanner is not None:
                self.scanner.finish()
        except BaseException:
            pass
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        except (OSError, ValueError):
            pass
        if isinstance(self.proc, _SupervisedProcess):
            self.proc.close_control()
        self._close_pidfd()
        restored = self._release_control_endpoint_hardening()
        self.released = True
        if not restored:
            raise RuntimeError(
                "powercut control endpoint restoration failed"
            )


class _ReceivedSignal(BaseException):
    def __init__(self, signum, frame):
        super().__init__(signum)
        self.signum = signum
        self.frame = frame


class _SignalRelay:
    """Turn cancellation into an exception until child cleanup completes."""

    def __init__(self):
        self.previous = {}
        self.received = None

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            return self
        for signum in CONTROL_SIGNALS:
            previous = signal.getsignal(signum)
            if previous == signal.SIG_IGN:
                continue
            self.previous[signum] = previous
            signal.signal(signum, self._receive)
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        for signum, previous in self.previous.items():
            signal.signal(signum, previous)

    def _receive(self, signum, frame):
        if self.received is None:
            self.received = _ReceivedSignal(signum, frame)
            raise self.received
        # A repeated cancellation must not interrupt TERM -> KILL cleanup.

    def redispatch(self, received):
        previous = self.previous.get(received.signum, signal.SIG_DFL)
        if callable(previous):
            previous(received.signum, received.frame)
            raise InterruptedError(
                f"interrupted by signal {received.signum}"
            )
        if previous == signal.SIG_IGN:
            raise InterruptedError(
                f"interrupted by signal {received.signum}"
            )
        os.kill(os.getpid(), received.signum)
        os._exit(128 + received.signum)


def _monitor_command_impl(
    command,
    *,
    init_proc,
    marker,
    case_timeout,
    idle_notice,
    marker_grace,
    completion_mode=COMPLETION_NATURAL,
    expected_bad_addr_markers=(),
    marker_mode=MARKER_EXACT_LINE,
    expected_fault_marker_mode=MARKER_EXACT_LINE,
    log_file=None,
    output_stream=sys.stdout,
    diagnostic_stream=sys.stderr,
    max_output_bytes=MAX_OUTPUT_BYTES,
    max_pending_chars=MAX_PENDING_CHARS,
    _lifecycle=None,
):
    """Monitor a command through one non-buffered binary read path."""

    if completion_mode not in (
        COMPLETION_NATURAL,
        COMPLETION_CHECKPOINT,
        COMPLETION_POWERCUT,
    ):
        raise ValueError(f"unsupported completion mode: {completion_mode!r}")
    if marker_mode not in (
        MARKER_SUBSTRING,
        MARKER_EXACT_LINE,
        MARKER_LINE_PREFIX,
    ):
        raise ValueError(f"unsupported marker mode: {marker_mode!r}")
    if expected_fault_marker_mode not in (
        MARKER_SUBSTRING,
        MARKER_EXACT_LINE,
    ):
        raise ValueError(
            "unsupported expected fault marker mode: "
            f"{expected_fault_marker_mode!r}"
        )
    if max_output_bytes <= 0 or max_pending_chars <= 0:
        raise ValueError("output limits must be positive")

    start = time.monotonic()
    case_deadline = start + case_timeout
    last_output = start
    last_notice = start
    marker_deadline = None
    marker_reported = False
    timed_out = False
    reason = "process_exit"

    log_context = (
        open(log_file, "w", encoding="utf-8", errors="replace")
        if log_file is not None
        else nullcontext(None)
    )
    with log_context as log:
        scanner = OutputScanner(
            init_proc=init_proc,
            marker=marker,
            output_stream=output_stream,
            log_stream=log,
            expected_bad_addr_markers=expected_bad_addr_markers,
            marker_mode=marker_mode,
            expected_fault_marker_mode=expected_fault_marker_mode,
            max_output_bytes=max_output_bytes,
            max_pending_chars=max_pending_chars,
        )
        if _lifecycle is None:
            raise RuntimeError("missing process lifecycle owner")
        proc, fd = _lifecycle.start(command, scanner, completion_mode)

        while True:
            now = time.monotonic()
            if scanner.failure_seen:
                reason = (
                    "output_limit"
                    if scanner.output_limit_exceeded
                    else "failure_text"
                )
                break
            if now >= case_deadline:
                timed_out = True
                reason = "timeout"
                _write_notice(
                    diagnostic_stream,
                    f"[agent-tests] {init_proc}: exceeded {case_timeout:g}s",
                )
                break
            if (
                completion_mode in (
                    COMPLETION_CHECKPOINT,
                    COMPLETION_POWERCUT,
                )
                and scanner.marker_seen
            ):
                reason = (
                    "expected_powercut"
                    if completion_mode == COMPLETION_POWERCUT
                    else "expected_checkpoint"
                )
                break
            if marker_deadline is not None and now >= marker_deadline:
                reason = "marker_grace"
                _write_notice(
                    output_stream,
                    f"[agent-tests] {init_proc}: marker grace elapsed, "
                    "stopping QEMU",
                )
                break
            if (
                idle_notice > 0
                and now - last_output >= idle_notice
                and now - last_notice >= idle_notice
            ):
                _write_notice(
                    diagnostic_stream,
                    f"[agent-tests] {init_proc}: no output for "
                    f"{int(now - last_output)}s",
                )
                last_notice = now

            deadlines = [case_deadline]
            if marker_deadline is not None:
                deadlines.append(marker_deadline)
            wait = min(POLL_SECONDS, max(0.0, min(deadlines) - now))
            ready, _, _ = select.select([fd], [], [], wait)
            if ready:
                received, eof = _read_available_chunk(fd, scanner)
                if received:
                    last_output = time.monotonic()
                if scanner.marker_seen and marker_deadline is None:
                    marker_deadline = (
                        scanner.marker_seen_at + marker_grace
                    )
                if scanner.marker_seen and not marker_reported:
                    marker_reported = True
                    notice = (
                        f"[agent-tests] {init_proc}: marker observed, "
                        "checking teardown"
                    )
                    _write_notice(output_stream, notice)
                now = time.monotonic()
                if scanner.failure_seen:
                    reason = (
                        "output_limit"
                        if scanner.output_limit_exceeded
                        else "failure_text"
                    )
                    break
                if now >= case_deadline:
                    timed_out = True
                    reason = "timeout"
                    _write_notice(
                        diagnostic_stream,
                        f"[agent-tests] {init_proc}: exceeded "
                        f"{case_timeout:g}s",
                    )
                    break
                if (
                    completion_mode in (
                        COMPLETION_CHECKPOINT,
                        COMPLETION_POWERCUT,
                    )
                    and scanner.marker_seen
                ):
                    reason = (
                        "expected_powercut"
                        if completion_mode == COMPLETION_POWERCUT
                        else "expected_checkpoint"
                    )
                    break
                if (
                    marker_deadline is not None
                    and now >= marker_deadline
                ):
                    reason = "marker_grace"
                    _write_notice(
                        output_stream,
                        f"[agent-tests] {init_proc}: marker grace elapsed, "
                        "stopping QEMU",
                    )
                    break
                if eof:
                    reason = "process_exit"
                    break
            elif _leader_exited(proc):
                reason = "process_exit"
                break

        natural_exit_deadline = None
        if reason == "process_exit":
            natural_exit_deadline = case_deadline
            if marker_deadline is not None:
                natural_exit_deadline = min(
                    natural_exit_deadline,
                    marker_deadline,
                )
        (
            signals_sent,
            output_eof,
            completion_leader_signaled,
            process_tree_gone,
            process_tree_contained,
            completion_signal_attested,
        ) = _stop_and_drain(
            proc,
            _lifecycle.pidfd,
            fd,
            scanner,
            first_signal=(
                signal.SIGKILL
                if (
                    completion_mode == COMPLETION_POWERCUT
                    and scanner.marker_seen
                )
                else signal.SIGTERM
            ),
            natural_exit_deadline=natural_exit_deadline,
        )
        scanner.finish()
        proc.stdout.close()
        control_endpoint_restored = _lifecycle.release()

        if scanner.marker_seen and marker_deadline is None:
            marker_deadline = scanner.marker_seen_at + marker_grace
        if scanner.marker_seen and not marker_reported:
            marker_reported = True
            notice = (
                f"[agent-tests] {init_proc}: marker observed, "
                "checking teardown"
            )
            _write_notice(output_stream, notice)
        finished_at = time.monotonic()
        if scanner.output_limit_exceeded:
            reason = "output_limit"
            _write_notice(
                diagnostic_stream,
                f"[agent-tests] {init_proc}: output budget exceeded",
            )
        elif scanner.failure_seen:
            reason = "failure_text"
            _write_notice(
                diagnostic_stream,
                f"[agent-tests] {init_proc}: failure text detected",
            )
        elif (
            reason in (
                "process_exit",
                "expected_checkpoint",
                "expected_powercut",
            )
            and finished_at >= case_deadline
        ):
            timed_out = True
            reason = "timeout"
            _write_notice(
                diagnostic_stream,
                f"[agent-tests] {init_proc}: exceeded {case_timeout:g}s",
            )
        elif (
            reason == "process_exit"
            and marker_deadline is not None
            and finished_at >= marker_deadline
        ):
            reason = "marker_grace"
        elif not scanner.marker_seen and reason == "process_exit":
            reason = "eof_without_marker"

        elapsed = time.monotonic() - start
        return MonitorResult(
            marker_seen=scanner.marker_seen,
            failure_seen=scanner.failure_seen,
            timed_out=timed_out,
            elapsed=elapsed,
            reason=reason,
            returncode=proc.returncode,
            signals_sent=signals_sent,
            output_eof=output_eof,
            expected_faults_satisfied=scanner.expected_faults_satisfied,
            checkpoint_leader_signaled=completion_leader_signaled,
            lines=tuple(scanner.lines),
            process_tree_gone=process_tree_gone,
            process_tree_contained=process_tree_contained,
            completion_signal_attested=completion_signal_attested,
            supervisor_returncode=getattr(
                proc,
                "supervisor_returncode",
                None,
            ),
            control_endpoint_restored=control_endpoint_restored,
            supervisor_control_healthy=getattr(
                proc,
                "control_healthy",
                True,
            ),
        )


def monitor_command(command, **options):
    """Monitor QEMU while making cancellation and exceptions teardown-safe."""
    lifecycle = _ProcessLifecycle()
    relay = _SignalRelay()
    try:
        with relay:
            try:
                return _monitor_command_impl(
                    command,
                    _lifecycle=lifecycle,
                    **options,
                )
            except BaseException:
                lifecycle.abort()
                raise
    except _ReceivedSignal as received:
        relay.redispatch(received)
        raise AssertionError("signal redispatch unexpectedly returned")


def build_qemu_command(qemu, kernel="build/kernel", image="nfs/fs-copy.img"):
    return [
        qemu,
        "-nographic",
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
    ]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-proc", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument(
        "--marker-mode",
        choices=(MARKER_SUBSTRING, MARKER_EXACT_LINE, MARKER_LINE_PREFIX),
        default=MARKER_EXACT_LINE,
    )
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--case-timeout", required=True)
    parser.add_argument("--idle-notice-seconds", required=True)
    parser.add_argument("--marker-grace-seconds", required=True)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--kernel", default="build/kernel")
    parser.add_argument("--image", default="nfs/fs-copy.img")
    parser.add_argument(
        "--completion-mode",
        choices=(
            COMPLETION_NATURAL,
            COMPLETION_CHECKPOINT,
            COMPLETION_POWERCUT,
        ),
        default=COMPLETION_NATURAL,
    )
    parser.add_argument("--expected-bad-addr-after", action="append", default=[])
    parser.add_argument(
        "--expected-bad-addr-marker-mode",
        choices=(MARKER_SUBSTRING, MARKER_EXACT_LINE),
        default=MARKER_EXACT_LINE,
    )
    parser.add_argument("--timing-file")
    parser.add_argument("--attestation-file")
    parser.add_argument("--run-id")
    parser.add_argument("--execution-id")
    return parser.parse_args()


def main():
    args = parse_args()
    attestation_values = (
        args.attestation_file,
        args.run_id,
        args.execution_id,
    )
    if any(value is not None for value in attestation_values):
        if not all(value is not None for value in attestation_values):
            print(
                "[agent-tests] execution attestation options must be supplied together",
                file=sys.stderr,
            )
            return 2
        if not RUN_ID_RE.fullmatch(args.run_id) or not EXECUTION_ID_RE.fullmatch(
            args.execution_id
        ):
            print("[agent-tests] invalid execution attestation identity", file=sys.stderr)
            return 2
    try:
        case_timeout = parse_duration(args.case_timeout, "CASE_TIMEOUT")
        idle_notice = parse_duration(
            args.idle_notice_seconds,
            "IDLE_NOTICE_SECONDS",
        )
        marker_grace = parse_duration(
            args.marker_grace_seconds,
            "MARKER_GRACE_SECONDS",
        )
    except ValueError as error:
        print(f"[agent-tests] {args.init_proc}: {error}", file=sys.stderr)
        return 2
    if case_timeout <= 0:
        print(
            f"[agent-tests] {args.init_proc}: CASE_TIMEOUT must be positive",
            file=sys.stderr,
        )
        return 2

    qemu_command = build_qemu_command(args.qemu, args.kernel, args.image)
    attestation_inputs = None
    runner_identity = None
    if args.attestation_file is not None:
        try:
            attestation_inputs = {
                "kernel": _regular_file_identity(args.kernel, "kernel input"),
                "image": _regular_file_identity(args.image, "image input"),
            }
            runner_identity = _regular_file_identity(__file__, "runner source")
        except (OSError, ValueError) as error:
            print(f"[agent-tests] attestation input rejected: {error}", file=sys.stderr)
            return 2

    result = monitor_command(
        qemu_command,
        init_proc=args.init_proc,
        marker=args.marker,
        case_timeout=case_timeout,
        idle_notice=idle_notice,
        marker_grace=marker_grace,
        completion_mode=args.completion_mode,
        expected_bad_addr_markers=args.expected_bad_addr_after,
        marker_mode=args.marker_mode,
        expected_fault_marker_mode=args.expected_bad_addr_marker_mode,
        log_file=args.log_file,
    )
    if args.completion_mode == COMPLETION_CHECKPOINT:
        succeeded = result.checkpoint_succeeded
    elif args.completion_mode == COMPLETION_POWERCUT:
        succeeded = result.powercut_succeeded
    else:
        succeeded = result.succeeded
    if not succeeded:
        print(
            f"[agent-tests] {args.init_proc}: failed "
            f"reason={result.reason} rc={result.returncode} "
            f"supervisor_rc={result.supervisor_returncode} "
            f"signals={result.signals_sent} eof={result.output_eof}; "
            f"tree_gone={result.process_tree_gone} "
            f"contained={result.process_tree_contained}; "
            f"control_healthy={result.supervisor_control_healthy} "
            f"endpoint_restored={result.control_endpoint_restored}; "
            "last log lines:",
            file=sys.stderr,
        )
        for line in result.lines[-40:]:
            print(line, file=sys.stderr)
        return 1

    if args.timing_file is not None:
        with open(args.timing_file, "a", encoding="utf-8") as timing:
            timing.write(f"{args.init_proc} {result.elapsed:.9f}\n")
    if args.attestation_file is not None:
        try:
            kernel_after = _regular_file_identity(args.kernel, "kernel output")
            if kernel_after != attestation_inputs["kernel"]:
                raise ValueError("kernel changed during QEMU execution")
            runner_after = _regular_file_identity(__file__, "runner source")
            if runner_after != runner_identity:
                raise ValueError("runner source changed during QEMU execution")
            attestation = {
                "schema_version": 1,
                "format": EXECUTION_ATTESTATION_FORMAT,
                "run_id": args.run_id,
                "execution_id": args.execution_id,
                "runner": runner_identity,
                "invocation_argv": [os.path.abspath(sys.executable), *sys.argv],
                "qemu_argv": qemu_command,
                "request": {
                    "init_proc": args.init_proc,
                    "marker": args.marker,
                    "marker_mode": args.marker_mode,
                    "expected_bad_addr_markers": args.expected_bad_addr_after,
                    "expected_fault_marker_mode": args.expected_bad_addr_marker_mode,
                    "completion_mode": args.completion_mode,
                    "case_timeout_seconds": case_timeout,
                    "idle_notice_seconds": idle_notice,
                    "marker_grace_seconds": marker_grace,
                },
                "inputs": attestation_inputs,
                "outputs": {
                    "image": _regular_file_identity(args.image, "image output"),
                    "log": _regular_file_identity(args.log_file, "Guest log output"),
                },
                "result": {
                    "succeeded": True,
                    "reason": result.reason,
                    "returncode": result.returncode,
                    "supervisor_returncode": result.supervisor_returncode,
                    "signals_sent": [int(item) for item in result.signals_sent],
                    "output_eof": result.output_eof,
                    "expected_faults_satisfied": result.expected_faults_satisfied,
                    "process_tree_gone": result.process_tree_gone,
                    "process_tree_contained": result.process_tree_contained,
                    "completion_signal_attested": result.completion_signal_attested,
                    "control_endpoint_restored": result.control_endpoint_restored,
                    "supervisor_control_healthy": result.supervisor_control_healthy,
                    "elapsed_seconds": round(result.elapsed, 9),
                },
            }
            _write_execution_attestation(args.attestation_file, attestation)
        except (OSError, ValueError) as error:
            print(f"[agent-tests] attestation publication failed: {error}", file=sys.stderr)
            return 1
    print(f"[agent-tests] {args.init_proc}: elapsed={result.elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 7 and sys.argv[1] == "--internal-powercut-supervisor":
        if sys.argv[5] != "--":
            sys.exit(125)
        sys.exit(
            _run_powercut_supervisor(
                int(sys.argv[2]),
                int(sys.argv[3]),
                sys.argv[4],
                sys.argv[6:],
            )
        )
    sys.exit(main())
