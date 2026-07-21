#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
TMPDIR_FAIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_FAIR}"' EXIT

build_case() {
	local tree="$1"
	local tag="$2"
	local prefix="${tree:+${tree}/}"
	local user_dir="${prefix}user"

	make -C "${user_dir}" \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER=safety \
		build_dir="${TMPDIR_FAIR}/${tag}-user-build" \
		out_dir="${TMPDIR_FAIR}/${tag}-user-target" \
		asm_dir="${TMPDIR_FAIR}/${tag}-user-asm"
	cc "${prefix}nfs/fs.c" -o "${TMPDIR_FAIR}/${tag}-mkfs"
	"${TMPDIR_FAIR}/${tag}-mkfs" "${TMPDIR_FAIR}/${tag}.img" \
		"${TMPDIR_FAIR}/${tag}-user-target/bin/syscallfair_ucore"
	if [[ -n "${tree}" ]]; then
		make -C "${tree}" -B build TOOLPREFIX="${TOOLPREFIX}" \
			LOG=error INIT_PROC=syscallfair_ucore
		cp "${tree}/build/kernel" "${TMPDIR_FAIR}/${tag}-kernel"
	else
		make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
			INIT_PROC=syscallfair_ucore
		cp build/kernel "${TMPDIR_FAIR}/${tag}-kernel"
	fi
}

run_case() {
	local tag="$1"
	local kernel="$2"
	local image="$3"
	local run_image="${TMPDIR_FAIR}/${tag}-run.img"

	cp "${image}" "${run_image}"
	"${PYTHON_BIN}" - "${tag}" "${kernel}" "${run_image}" \
		"${QEMU}" "${CASE_TIMEOUT}" <<'PY'
import os
import re
import select
import signal
import subprocess
import sys
import time

tag, kernel, image, qemu, timeout_text = sys.argv[1:6]
phase_markers = {
    "console": (
        b"SYSCALLFAIR_CONSOLE_BEGIN",
        b"SYSCALLFAIR_CONSOLE_PEER",
        b"SYSCALLFAIR_CONSOLE_END",
    ),
    "inode": (
        b"SYSCALLFAIR_INODE_BEGIN",
        b"SYSCALLFAIR_INODE_PEER",
        b"SYSCALLFAIR_INODE_END",
    ),
    "trunc": (
        b"SYSCALLFAIR_TRUNC_BEGIN",
        b"SYSCALLFAIR_TRUNC_PEER",
        b"SYSCALLFAIR_TRUNC_END",
    ),
}
inode_short = b"SYSCALLFAIR_INODE_SHORT"
passed = b"syscallfair_ucore: parent passed"
failure = re.compile(
    rb"check failed|panic|unknown syscall|bad addr|IllegalInstruction",
    re.IGNORECASE,
)


def parse_timeout(text):
    unit = text[-1:]
    number = text[:-1] if unit.isalpha() else text
    value = float(number)
    if unit in ("m", "M"):
        return value * 60
    if unit in ("h", "H"):
        return value * 3600
    if unit in ("", "s", "S"):
        return value
    raise SystemExit(f"[syscall-fairness] unsupported timeout: {text}")


cmd = [
    qemu, "-nographic", "-machine", "virt", "-bios", "default",
    "-kernel", kernel,
    "-drive", f"file={image},if=none,format=raw,id=x0",
    "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
]
proc = None
output = bytearray()
timed_out = False
stopped_by_runner = False

try:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        start_new_session=True,
    )
    assert proc.stdout is not None
    passed_time = None
    start = time.monotonic()
    timeout = parse_timeout(timeout_text)

    while time.monotonic() - start < timeout:
        if passed_time is not None and time.monotonic() - passed_time >= 5.0:
            break
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if ready:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if passed in output and passed_time is None:
                passed_time = time.monotonic()
        elif proc.poll() is not None:
            break

    timed_out = time.monotonic() - start >= timeout
    stopped_by_runner = proc.poll() is None
    data = bytes(output)
    positions = {}
    ordered = True
    unique = True
    previous_end = -1
    for name, (begin, peer, end) in phase_markers.items():
        begin_pos = data.find(begin)
        peer_pos = data.find(peer)
        end_pos = data.find(end)
        positions[name] = {
            "begin": begin_pos,
            "peer": peer_pos,
            "end": end_pos,
        }
        ordered = ordered and begin_pos >= 0 and begin_pos < peer_pos < end_pos
        ordered = ordered and previous_end < begin_pos
        unique = unique and all(data.count(marker) == 1 for marker in (begin, peer, end))
        previous_end = end_pos

    short_pos = data.find(inode_short)
    positions["inode"]["short"] = short_pos
    ordered = (
        ordered
        and positions["inode"]["peer"] < short_pos
        and short_pos < positions["inode"]["end"]
    )
    unique = unique and data.count(inode_short) == 1
    passed_pos = data.find(passed)
    ordered = ordered and previous_end < passed_pos
    failure_match = failure.search(data)
    bad_exit = stopped_by_runner or proc.returncode != 0
    if timed_out or not ordered or not unique or failure_match or bad_exit:
        print(
            f"[syscall-fairness] {tag} failed "
            f"positions={positions} passed={passed_pos} unique={unique}",
            file=sys.stderr,
        )
        tail = data.decode("utf-8", errors="replace").splitlines()[-40:]
        for line in tail:
            print(line[-240:], file=sys.stderr)
        raise SystemExit(1)

    summary = " ".join(
        f"{name}={value['begin']}/{value['peer']}/{value['end']}"
        for name, value in positions.items()
    )
    print(f"[syscall-fairness] {tag} passed {summary}")
finally:
    if proc is not None:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
PY
}

build_case "" agent
build_case baseline_ucore baseline
run_case agent "${TMPDIR_FAIR}/agent-kernel" \
	"${TMPDIR_FAIR}/agent.img"
run_case baseline "${TMPDIR_FAIR}/baseline-kernel" \
	"${TMPDIR_FAIR}/baseline.img"

echo "[syscall-fairness] both targets passed"
