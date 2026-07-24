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
import time
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass


FAILURE_RE = re.compile(
    r"check failed|panic|unknown syscall|bad addr|"
    r"illegalinstruction|child_failed",
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
        max_output_bytes=MAX_OUTPUT_BYTES,
        max_pending_chars=MAX_PENDING_CHARS,
    ):
        self.init_proc = init_proc
        self.marker = marker
        self.output_stream = output_stream
        self.log_stream = log_stream
        self.expected_bad_addr_markers = tuple(expected_bad_addr_markers)
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
        if self.marker in line:
            self._note_marker()

        events = [(match.start(), "failure", match) for match in FAILURE_RE.finditer(line)]
        for marker in self.expected_bad_addr_markers:
            start = line.find(marker)
            while start >= 0:
                events.append((start, "arm", marker))
                start = line.find(marker, start + len(marker))
        events.sort(key=lambda event: event[0])

        for _, kind, value in events:
            if kind == "arm":
                if (
                    value in self.expected_fault_markers_seen
                    or self.expected_fault_pending != 0
                ):
                    self.failure_seen = True
                    continue
                self.expected_fault_markers_seen.add(value)
                self.expected_fault_pending += 1
                continue
            match = value
            failure_text = match.group(0).casefold()
            if self.expected_fault_pending and failure_text == "bad addr":
                self.expected_fault_pending -= 1
                self.expected_faults_consumed += 1
                continue
            self.failure_seen = True

    def _note_marker(self):
        if self.marker_seen:
            return
        self.marker_seen = True
        self.marker_seen_at = time.monotonic()

    def _consume_text(self, text):
        self.pending += text
        if not self.marker_seen and self.marker in self.pending:
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


def _wait_for_exit_and_eof(
    proc,
    fd,
    scanner,
    deadline,
    initial_eof=False,
):
    """Let a naturally closing process publish both status and pipe EOF."""
    eof = initial_eof
    while True:
        if not eof:
            _, reached_eof = _read_available_chunk(fd, scanner)
            eof = eof or reached_eof
        if proc.poll() is not None and eof:
            return True

        now = time.monotonic()
        if now >= deadline:
            return eof
        wait = min(POLL_SECONDS, deadline - now)
        if eof:
            try:
                proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                pass
        else:
            select.select([fd], [], [], wait)


def _signal_process(proc, sig):
    if proc.poll() is not None:
        return False
    try:
        if os.name == "posix":
            os.killpg(proc.pid, sig)
        elif sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()
    except ProcessLookupError:
        return False
    return True


def _signal_process_group(proc, sig):
    if os.name != "posix":
        return False
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return False
    return True


def _stop_and_drain(
    proc,
    fd,
    scanner,
    first_signal=signal.SIGTERM,
    natural_exit_deadline=None,
):
    signals_sent = []
    eof = False
    if natural_exit_deadline is not None:
        eof = _wait_for_exit_and_eof(
            proc,
            fd,
            scanner,
            natural_exit_deadline,
        )
    if proc.poll() is None:
        if _signal_process(proc, first_signal):
            signals_sent.append(first_signal)
    eof = _wait_for_exit_and_eof(
        proc,
        fd,
        scanner,
        time.monotonic() + TERMINATE_SECONDS,
        initial_eof=eof,
    )
    if proc.poll() is None or not eof:
        if _signal_process(proc, signal.SIGKILL):
            signals_sent.append(signal.SIGKILL)
        elif not eof and _signal_process_group(proc, signal.SIGKILL):
            signals_sent.append(signal.SIGKILL)
        eof = _wait_for_exit_and_eof(
            proc,
            fd,
            scanner,
            time.monotonic() + TERMINATE_SECONDS,
            initial_eof=eof,
        )
    try:
        proc.wait(timeout=TERMINATE_SECONDS)
    except subprocess.TimeoutExpired:
        if _signal_process(proc, signal.SIGKILL):
            signals_sent.append(signal.SIGKILL)
        proc.wait(timeout=TERMINATE_SECONDS)
    if not eof:
        eof = _drain_until(
            proc,
            fd,
            scanner,
            time.monotonic() + TERMINATE_SECONDS,
        )
    return tuple(signals_sent), eof


def monitor_command(
    command,
    *,
    init_proc,
    marker,
    case_timeout,
    idle_notice,
    marker_grace,
    completion_mode=COMPLETION_NATURAL,
    expected_bad_addr_markers=(),
    log_file=None,
    output_stream=sys.stdout,
    diagnostic_stream=sys.stderr,
    max_output_bytes=MAX_OUTPUT_BYTES,
    max_pending_chars=MAX_PENDING_CHARS,
):
    """Monitor a command through one non-buffered binary read path."""

    if completion_mode not in (COMPLETION_NATURAL, COMPLETION_CHECKPOINT):
        raise ValueError(f"unsupported completion mode: {completion_mode!r}")
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
            init_proc,
            marker,
            output_stream,
            log,
            expected_bad_addr_markers,
            max_output_bytes,
            max_pending_chars,
        )
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=(os.name == "posix"),
        )
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        os.set_blocking(fd, False)

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
            elif proc.poll() is not None:
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
        signals_sent, output_eof = _stop_and_drain(
            proc,
            fd,
            scanner,
            natural_exit_deadline=natural_exit_deadline,
        )
        scanner.finish()
        proc.stdout.close()

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
            lines=tuple(scanner.lines),
        )


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
