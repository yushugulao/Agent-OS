#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
TMPDIR_REAP="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_REAP}"' EXIT

build_case() {
	local tree="$1"
	local tag="$2"
	local prefix="${tree:+${tree}/}"
	local user_dir="${prefix}user"
	local apps=("${TMPDIR_REAP}/${tag}-user-target/bin/procreap_ucore")

	make -C "${user_dir}" \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER=proc_reap \
		build_dir="${TMPDIR_REAP}/${tag}-user-build" \
		out_dir="${TMPDIR_REAP}/${tag}-user-target" \
		asm_dir="${TMPDIR_REAP}/${tag}-user-asm"
	if [[ -z "${tree}" ]]; then
		apps+=("${TMPDIR_REAP}/${tag}-user-target/bin/procreap_agent_ucore")
	fi
	cc "${prefix}nfs/fs.c" -o "${TMPDIR_REAP}/${tag}-mkfs"
	"${TMPDIR_REAP}/${tag}-mkfs" "${TMPDIR_REAP}/${tag}.img" \
		"${apps[@]}"
	if [[ -n "${tree}" ]]; then
		make -C "${tree}" -B build TOOLPREFIX="${TOOLPREFIX}" \
			LOG=error INIT_PROC=procreap_ucore
		cp "${tree}/build/kernel" "${TMPDIR_REAP}/${tag}-kernel"
	else
		make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
			INIT_PROC=procreap_ucore
		cp build/kernel "${TMPDIR_REAP}/${tag}-kernel"
		make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
			INIT_PROC=procreap_agent_ucore
		cp build/kernel "${TMPDIR_REAP}/${tag}-agent-kernel"
	fi
}

run_case() {
	local tag="$1"
	local kernel="$2"
	local image="$3"
	local marker="$4"
	local run_image="${TMPDIR_REAP}/${tag}-run.img"

	cp "${image}" "${run_image}"
	"${PYTHON_BIN}" - "${tag}" "${kernel}" "${run_image}" \
		"${QEMU}" "${CASE_TIMEOUT}" "${marker}" <<'PY'
import os
import re
import select
import signal
import subprocess
import sys
import time

tag, kernel, image, qemu, timeout_text, marker = sys.argv[1:7]
failure = re.compile(
    r"check failed|panic|unknown syscall|bad addr|IllegalInstruction",
    re.IGNORECASE,
)
cmd = [
    qemu, "-nographic", "-machine", "virt", "-bios", "default",
    "-kernel", kernel,
    "-drive", f"file={image},if=none,format=raw,id=x0",
    "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
]
unit = timeout_text[-1:]
number = timeout_text[:-1] if unit.isalpha() else timeout_text
timeout = float(number)
if unit in ("m", "M"):
    timeout *= 60
elif unit in ("h", "H"):
    timeout *= 3600
elif unit not in ("", "s", "S"):
    raise SystemExit(f"[proc-reap] unsupported timeout: {timeout_text}")
start = time.monotonic()
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    preexec_fn=os.setsid,
)
chunks = []
marker_at = -1
marker_time = None
failure_match = None
failure_time = None
timed_out = False
shutdown_hung = False
assert proc.stdout is not None
while time.monotonic() - start < timeout:
    now = time.monotonic()
    if failure_time is not None and now - failure_time >= 1.0:
        break
    if (failure_time is None and marker_time is not None and
            now - marker_time >= 5.0):
        shutdown_hung = True
        break
    ready, _, _ = select.select([proc.stdout], [], [], 0.2)
    if ready:
        chunk = os.read(proc.stdout.fileno(), 4096)
        if not chunk:
            break
        chunks.append(chunk)
        output = b"".join(chunks).decode("utf-8", errors="replace")
        marker_at = output.find(marker)
        if marker_at >= 0 and marker_time is None:
            marker_time = time.monotonic()
        failure_match = failure.search(output)
        if failure_match is not None and failure_time is None:
            failure_time = time.monotonic()
    elif proc.poll() is not None:
        break
else:
    timed_out = True

stopped_by_runner = proc.poll() is None
if stopped_by_runner:
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=2)

output = b"".join(chunks).decode("utf-8", errors="replace")
print(output, end="")
bad_exit = not stopped_by_runner and proc.returncode != 0
if (timed_out or shutdown_hung or marker_at < 0 or
        failure_match is not None or bad_exit):
    if timed_out:
        reason = "timed out"
    elif shutdown_hung:
        reason = "did not shut down"
    else:
        reason = "failed"
    print(f"[proc-reap] {tag} {reason}", file=sys.stderr)
    for line in output.splitlines()[-40:]:
        print(line, file=sys.stderr)
    raise SystemExit(1)
print(f"[proc-reap] {tag} passed in {time.monotonic() - start:.1f}s")
PY
}

build_case "" agent
build_case baseline_ucore baseline
run_case agent "${TMPDIR_REAP}/agent-kernel" \
	"${TMPDIR_REAP}/agent.img" "procreap_ucore: parent passed"
run_case agent-adversarial "${TMPDIR_REAP}/agent-agent-kernel" \
	"${TMPDIR_REAP}/agent.img" "procreap_agent_ucore: parent passed"
run_case baseline "${TMPDIR_REAP}/baseline-kernel" \
	"${TMPDIR_REAP}/baseline.img" "procreap_ucore: parent passed"

echo "[proc-reap] both targets passed"
