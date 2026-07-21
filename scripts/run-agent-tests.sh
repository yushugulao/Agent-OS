#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
LOG="${LOG:-error}"
CHAPTER="${CHAPTER:-agent}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-2}"

run_case() {
	local init_proc="$1"
	local marker="$2"
	local log_file="/tmp/agentos-${init_proc}.log"

	echo "[agent-tests] running ${init_proc}"
	rm -f nfs/fs-copy.img os/initproc.S build/os/initproc.o
	make build \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}"
	cp nfs/fs.img nfs/fs-copy.img
	"${PYTHON_BIN}" - "$init_proc" "$marker" "$log_file" "$CASE_TIMEOUT" "$IDLE_NOTICE_SECONDS" "$MARKER_GRACE_SECONDS" "$QEMU" <<'PY'
import os
import re
import select
import signal
import subprocess
import sys
import time

init_proc, marker, log_file, timeout_text, idle_text, grace_text, qemu = sys.argv[1:8]


def parse_duration(text):
    unit = text[-1:]
    number = text[:-1] if unit.isalpha() else text
    try:
        value = float(number)
    except ValueError:
        raise SystemExit(f"[agent-tests] {init_proc}: bad CASE_TIMEOUT={text!r}")
    if unit in ("s", "S") or not unit.isalpha():
        return value
    if unit in ("m", "M"):
        return value * 60
    if unit in ("h", "H"):
        return value * 3600
    raise SystemExit(f"[agent-tests] {init_proc}: unsupported CASE_TIMEOUT={text!r}")


case_timeout = parse_duration(timeout_text)
idle_notice = parse_duration(idle_text)
marker_grace = parse_duration(grace_text)
failure_re = re.compile(r"check failed|panic|unknown syscall|bad addr|IllegalInstruction|child_failed")
cmd = [
    qemu,
    "-nographic",
    "-machine",
    "virt",
    "-bios",
    "default",
    "-kernel",
    "build/kernel",
    "-drive",
    "file=nfs/fs-copy.img,if=none,format=raw,id=x0",
    "-device",
    "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
]

start = time.monotonic()
last_output = start
last_notice = start
marker_seen = False
marker_time = None
failure_seen = False
lines = []

with open(log_file, "w", encoding="utf-8", errors="replace") as log:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
    )
    assert proc.stdout is not None
    while True:
        now = time.monotonic()
        if marker_time is not None and now - marker_time >= marker_grace:
            print(f"[agent-tests] {init_proc}: marker grace elapsed, stopping QEMU")
            break
        if now - start > case_timeout:
            print(f"[agent-tests] {init_proc}: exceeded {timeout_text}", file=sys.stderr)
            break
        if now - last_output >= idle_notice and now - last_notice >= idle_notice:
            print(
                f"[agent-tests] {init_proc}: no output for {int(now - last_output)}s",
                file=sys.stderr,
            )
            last_notice = now
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if ready:
            line = proc.stdout.readline()
            if line == "":
                if proc.poll() is not None:
                    break
                continue
            last_output = time.monotonic()
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
            lines.append(line.rstrip("\n"))
            if len(lines) > 80:
                lines = lines[-80:]
            if failure_re.search(line):
                failure_seen = True
                print(f"[agent-tests] {init_proc}: failure text detected", file=sys.stderr)
                break
            if marker in line and marker_time is None:
                marker_seen = True
                marker_time = time.monotonic()
                print(f"[agent-tests] {init_proc}: marker observed, checking teardown")
                log.write(f"[agent-tests] {init_proc}: marker observed, checking teardown\n")
                log.flush()
        elif proc.poll() is not None:
            break

    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)

if failure_seen or not marker_seen:
    print(f"[agent-tests] {init_proc}: last log lines:", file=sys.stderr)
    for line in lines[-40:]:
        print(line, file=sys.stderr)
    sys.exit(1)

elapsed = time.monotonic() - start
print(f"[agent-tests] {init_proc}: elapsed={elapsed:.1f}s")
sys.exit(0)
PY
	echo "[agent-tests] ${init_proc} passed"
}

make -C user clean
make clean
make user TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}"
make nfs/fs.img TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}"
make build TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore

run_case agentfinal_ucore "agentfinal_ucore: parent passed"
run_case agentfs_ucore "agentfs_ucore: parent passed"
run_case agentscan_ucore "agentscan_ucore: parent passed"
run_case agentloop_ucore "agentloop_ucore: parent passed"
run_case agentsched_ucore "agentsched_ucore: parent passed"
run_case agentconflict_ucore "agentconflict_ucore: parent passed"
run_case agentllm_ucore "agentllm_ucore: parent passed"
run_case agentbench_ucore "agentbench_ucore: parent passed"
run_case labbench_ucore "labbench_ucore: parent passed"
run_case labdemo_ucore "labdemo_ucore: parent passed"
run_case agentsecurity_ucore "agentsecurity_ucore: parent passed"
run_case agentscope_ucore "agentscope_ucore: parent passed"
run_case agenttrust_ucore "agenttrust_ucore: parent passed"
run_case agentvfs_ucore "agentvfs_ucore: parent passed"
run_case usersafety_ucore "usersafety_ucore: parent passed"

echo "[agent-tests] all Agent-OS uCore checks passed"
