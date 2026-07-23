#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
THREAD_RESOURCE_POOL_SIZE=19
THREAD_RESOURCE_ORDINARY_LIMIT=12
THREAD_RESOURCE_RESERVED_LIMIT=6
THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT=6
THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT=4
TMPDIR_THREAD_RESOURCE="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_THREAD_RESOURCE}"' EXIT

make -C user \
	TOOLPREFIX="${TOOLPREFIX}" CHAPTER=thread_resource \
	build_dir="${TMPDIR_THREAD_RESOURCE}/user-build" \
	out_dir="${TMPDIR_THREAD_RESOURCE}/user-target" \
	asm_dir="${TMPDIR_THREAD_RESOURCE}/user-asm"
cc nfs/fs.c -o "${TMPDIR_THREAD_RESOURCE}/mkfs"
"${TMPDIR_THREAD_RESOURCE}/mkfs" \
	"${TMPDIR_THREAD_RESOURCE}/thread-resource.img" \
	"${TMPDIR_THREAD_RESOURCE}/user-target/bin/threadresource_ucore"
make -B build TOOLPREFIX="${TOOLPREFIX}" \
	LOG=error INIT_PROC=threadresource_ucore CHAPTER=thread_resource \
	THREAD_RESOURCE_POOL_SIZE="${THREAD_RESOURCE_POOL_SIZE}" \
	THREAD_RESOURCE_ORDINARY_LIMIT="${THREAD_RESOURCE_ORDINARY_LIMIT}" \
	THREAD_RESOURCE_RESERVED_LIMIT="${THREAD_RESOURCE_RESERVED_LIMIT}" \
	THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT="${THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT}" \
	THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT="${THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT}"
cp build/kernel "${TMPDIR_THREAD_RESOURCE}/kernel"
cp "${TMPDIR_THREAD_RESOURCE}/thread-resource.img" \
	"${TMPDIR_THREAD_RESOURCE}/run.img"

"${PYTHON_BIN}" - "${TMPDIR_THREAD_RESOURCE}/kernel" \
	"${TMPDIR_THREAD_RESOURCE}/run.img" "${QEMU}" "${CASE_TIMEOUT}" <<'PY'
import os
import re
import select
import signal
import subprocess
import sys
import time

kernel, image, qemu, timeout_text = sys.argv[1:5]
markers = [
    b"threadresource_ucore: domain_limit=1",
    b"threadresource_ucore: capacity_reject_stable=1",
    b"threadresource_ucore: reserved_domain_limit=1",
    b"threadresource_ucore: reserved_domain_reuse=1",
    b"threadresource_ucore: exit_reuse=1",
    b"threadresource_ucore: ordinary_waterline=1",
    b"threadresource_ucore: global_thread_limit=1",
    b"threadresource_ucore: reserved_global_limit=1",
    b"threadresource_ucore: reserved_progress=1",
    b"threadresource_ucore: reserved_global_reuse=1",
    b"threadresource_ucore: global_reuse=1",
    b"threadresource_ucore: domain_fairness=1",
    b"threadresource_ucore: parent passed",
]
failure = re.compile(
    rb"check failed|panic|unknown syscall|bad addr|IllegalInstruction",
    re.IGNORECASE,
)
fairness = re.compile(
    rb"threadresource_ucore: domain_fairness=1 "
    rb"hog=(\d+) victim=(\d+) bound=(\d+)"
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
    raise SystemExit(f"[thread-resource] unsupported timeout: {text}")


cmd = [
    qemu, "-nographic", "-machine", "virt", "-bios", "default",
    "-kernel", kernel,
    "-drive", f"file={image},if=none,format=raw,id=x0",
    "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
]
proc = None
output = bytearray()

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
    start = time.monotonic()
    timeout = parse_timeout(timeout_text)
    marker_time = None

    while time.monotonic() - start < timeout:
        if marker_time is not None and time.monotonic() - marker_time >= 5:
            break
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if ready:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if markers[-1] in output and marker_time is None:
                marker_time = time.monotonic()
        elif proc.poll() is not None:
            break

    data = bytes(output)
    positions = [data.find(marker) for marker in markers]
    ordered = all(position >= 0 for position in positions)
    ordered = ordered and positions == sorted(positions)
    unique = all(data.count(marker) == 1 for marker in markers)
    failure_match = failure.search(data)
    fairness_match = fairness.search(data)
    fairness_ok = False
    if fairness_match:
        hog, victim, bound = map(int, fairness_match.groups())
        fairness_ok = victim == 512 and bound == 576 and hog <= bound
    timed_out = time.monotonic() - start >= timeout
    exited = proc.poll() is not None
    bad_exit = exited and proc.returncode != 0
    teardown_stuck = marker_time is not None and not exited
    if (
        timed_out
        or not ordered
        or not unique
        or failure_match
        or not fairness_ok
        or bad_exit
        or teardown_stuck
    ):
        print(
            "[thread-resource] failed "
            f"positions={positions} unique={unique} fairness={fairness_ok} "
            f"timeout={timed_out} teardown_stuck={teardown_stuck}",
            file=sys.stderr,
        )
        for line in data.decode("utf-8", errors="replace").splitlines()[-80:]:
            print(line[-240:], file=sys.stderr)
        raise SystemExit(1)
    print(
        "[thread-resource] passed "
        "pool=19 ordinary=12 reserved=6 domain=6/4"
    )
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

echo "[thread-resource] all checks passed"
