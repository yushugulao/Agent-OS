#!/usr/bin/env python3
"""Run one AgentOS QEMU case with fail-closed output monitoring."""

import argparse
import codecs
import math
import os
import re
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
MARKER_SUBSTRING = "substring"
MARKER_EXACT_LINE = "exact-line"
CONTROL_SIGNALS = tuple(
    signum
    for signum in (
        getattr(signal, "SIGHUP", None),
        signal.SIGINT,
        signal.SIGTERM,
    )
    if signum is not None
)


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
        )

    @property
    def checkpoint_succeeded(self):
        return (
            self.marker_seen
            and not self.failure_seen
            and not self.timed_out
            and self.reason == "expected_checkpoint"
            and self.signals_sent == (signal.SIGTERM,)
            and self.checkpoint_leader_signaled
            and self.returncode in (0, -signal.SIGTERM)
            and self.output_eof
            and self.expected_faults_satisfied
        )


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
        if marker_mode not in (MARKER_SUBSTRING, MARKER_EXACT_LINE):
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

    @property
    def expected_faults_satisfied(self):
        expected = len(self.expected_bad_addr_markers)
        return (
            len(self.expected_fault_markers_seen) == expected
            and self.expected_faults_consumed == expected
            and self.expected_fault_pending == 0
        )

    def _emit(self, text):
        if not text:
            return
        if self.output_stream is not None:
            self.output_stream.write(text)
            self.output_stream.flush()
        if self.log_stream is not None:
            self.log_stream.write(text)
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
        self._emit(tail)
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


def _linux_process_group_has_live_member(pgid):
    """Return None off procfs, otherwise ignore exited group zombies."""
    try:
        entries = os.scandir("/proc")
    except OSError:
        return None
    with entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                with open(
                    f"/proc/{entry.name}/stat",
                    "r",
                    encoding="ascii",
                    errors="replace",
                ) as stat_file:
                    stat = stat_file.read()
                close_paren = stat.rfind(")")
                if close_paren < 0:
                    continue
                fields = stat[close_paren + 2:].split()
                state = fields[0]
                process_group = int(fields[2])
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError:
                return None
            except (OSError, ValueError, IndexError):
                continue
            if process_group == pgid and state not in ("X", "Z"):
                return True
    return False


def _process_tree_alive(proc):
    if not _leader_exited(proc):
        return True
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
    return leader_sent or group_delivery, leader_confirmed


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
        if leader_exited and not _process_tree_alive(proc) and eof:
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
    eof = False
    if natural_exit_deadline is not None:
        eof = _wait_for_tree_exit_and_eof(
            proc,
            pidfd,
            fd,
            scanner,
            natural_exit_deadline,
        )
    sent, confirmed = _signal_process_tree(proc, pidfd, first_signal)
    if sent:
        signals_sent.append(first_signal)
    first_signal_confirmed_for_leader = confirmed
    eof = _wait_for_tree_exit_and_eof(
        proc,
        pidfd,
        fd,
        scanner,
        time.monotonic() + TERMINATE_SECONDS,
        initial_eof=eof,
    )
    sent, _ = _signal_process_tree(proc, pidfd, signal.SIGKILL)
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
        sent, _ = _signal_process_tree(proc, pidfd, signal.SIGKILL)
        if sent:
            signals_sent.append(signal.SIGKILL)
        proc.wait(timeout=TERMINATE_SECONDS)
    if (
        not first_signal_confirmed_for_leader
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
    return tuple(signals_sent), eof, first_signal_confirmed_for_leader


def _force_stop_process_tree(proc, pidfd):
    """Exception-path teardown that cannot depend on output decoding."""
    if proc is None:
        return
    try:
        _signal_process_tree(proc, pidfd, signal.SIGTERM)
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
    try:
        _signal_process_tree(proc, pidfd, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.wait(timeout=TERMINATE_SECONDS)
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


class _ProcessLifecycle:
    """Own a spawned QEMU process until its full output tree is drained."""

    def __init__(self):
        self.proc = None
        self.fd = None
        self.pidfd = None
        self.scanner = None
        self.released = False

    def start(self, command, scanner):
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
            proc = subprocess.Popen(command, **popen_options)
            self.proc = proc
            self.pidfd = _open_pidfd(proc)
            self.scanner = scanner
            assert proc.stdout is not None
            self.fd = proc.stdout.fileno()
            os.set_blocking(self.fd, False)
            return proc, self.fd
        finally:
            if blocked is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def release(self):
        self._close_pidfd()
        self.released = True

    def _close_pidfd(self):
        if self.pidfd is None:
            return
        try:
            os.close(self.pidfd)
        except OSError:
            pass
        self.pidfd = None

    def abort(self):
        if self.released or self.proc is None:
            return
        try:
            if self.fd is not None and self.scanner is not None:
                _stop_and_drain(
                    self.proc,
                    self.pidfd,
                    self.fd,
                    self.scanner,
                )
            else:
                _force_stop_process_tree(self.proc, self.pidfd)
        except BaseException:
            _force_stop_process_tree(self.proc, self.pidfd)
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
        self._close_pidfd()
        self.released = True


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

    if completion_mode not in (COMPLETION_NATURAL, COMPLETION_CHECKPOINT):
        raise ValueError(f"unsupported completion mode: {completion_mode!r}")
    if marker_mode not in (MARKER_SUBSTRING, MARKER_EXACT_LINE):
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
        proc, fd = _lifecycle.start(command, scanner)

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
                completion_mode == COMPLETION_CHECKPOINT
                and scanner.marker_seen
            ):
                reason = "expected_checkpoint"
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
                    if log is not None:
                        print(notice, file=log, flush=True)
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
                    completion_mode == COMPLETION_CHECKPOINT
                    and scanner.marker_seen
                ):
                    reason = "expected_checkpoint"
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
            checkpoint_leader_signaled,
        ) = _stop_and_drain(
            proc,
            _lifecycle.pidfd,
            fd,
            scanner,
            natural_exit_deadline=natural_exit_deadline,
        )
        scanner.finish()
        proc.stdout.close()
        _lifecycle.release()

        if scanner.marker_seen and marker_deadline is None:
            marker_deadline = scanner.marker_seen_at + marker_grace
        if scanner.marker_seen and not marker_reported:
            marker_reported = True
            notice = (
                f"[agent-tests] {init_proc}: marker observed, "
                "checking teardown"
            )
            _write_notice(output_stream, notice)
            if log is not None:
                print(notice, file=log, flush=True)
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
            reason in ("process_exit", "expected_checkpoint")
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
            checkpoint_leader_signaled=checkpoint_leader_signaled,
            lines=tuple(scanner.lines),
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
        choices=(MARKER_SUBSTRING, MARKER_EXACT_LINE),
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
        choices=(COMPLETION_NATURAL, COMPLETION_CHECKPOINT),
        default=COMPLETION_NATURAL,
    )
    parser.add_argument("--expected-bad-addr-after", action="append", default=[])
    parser.add_argument(
        "--expected-bad-addr-marker-mode",
        choices=(MARKER_SUBSTRING, MARKER_EXACT_LINE),
        default=MARKER_EXACT_LINE,
    )
    parser.add_argument("--timing-file")
    return parser.parse_args()


def main():
    args = parse_args()
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

    result = monitor_command(
        build_qemu_command(args.qemu, args.kernel, args.image),
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
    succeeded = (
        result.checkpoint_succeeded
        if args.completion_mode == COMPLETION_CHECKPOINT
        else result.succeeded
    )
    if not succeeded:
        print(
            f"[agent-tests] {args.init_proc}: failed "
            f"reason={result.reason} rc={result.returncode} "
            f"signals={result.signals_sent} eof={result.output_eof}; "
            "last log lines:",
            file=sys.stderr,
        )
        for line in result.lines[-40:]:
            print(line, file=sys.stderr)
        return 1

    if args.timing_file is not None:
        with open(args.timing_file, "a", encoding="utf-8") as timing:
            timing.write(f"{args.init_proc} {result.elapsed:.9f}\n")
    print(f"[agent-tests] {args.init_proc}: elapsed={result.elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
