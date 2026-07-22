#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
FILE_RESOURCE_POOL_SIZE=64
FILE_RESOURCE_ORDINARY_LIMIT=48
FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT=16
FILE_RESOURCE_DOMAIN_RESERVED_LIMIT=16
TMPDIR_FILE_RESOURCE="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_FILE_RESOURCE}"' EXIT

build_case() {
	local tree="$1"
	local tag="$2"
	local prefix="${tree:+${tree}/}"
	local user_dir="${prefix}user"

	make -C "${user_dir}" \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER=file_resource \
		build_dir="${TMPDIR_FILE_RESOURCE}/${tag}-user-build" \
		out_dir="${TMPDIR_FILE_RESOURCE}/${tag}-user-target" \
		asm_dir="${TMPDIR_FILE_RESOURCE}/${tag}-user-asm"
	mkdir -p "${TMPDIR_FILE_RESOURCE}/fixture/bin"
	printf 'file-resource-fixture\n' \
		>"${TMPDIR_FILE_RESOURCE}/fixture/bin/frsource"
	cc "${prefix}nfs/fs.c" -o "${TMPDIR_FILE_RESOURCE}/${tag}-mkfs"
	"${TMPDIR_FILE_RESOURCE}/${tag}-mkfs" \
		"${TMPDIR_FILE_RESOURCE}/${tag}.img" \
		"${TMPDIR_FILE_RESOURCE}/${tag}-user-target/bin/fileresource_ucore" \
		"${TMPDIR_FILE_RESOURCE}/fixture/bin/frsource"
	if [[ -n "${tree}" ]]; then
		make -C "${tree}" -B build TOOLPREFIX="${TOOLPREFIX}" \
			LOG=error INIT_PROC=fileresource_ucore \
			FILE_RESOURCE_POOL_SIZE="${FILE_RESOURCE_POOL_SIZE}" \
			FILE_RESOURCE_ORDINARY_LIMIT="${FILE_RESOURCE_ORDINARY_LIMIT}" \
			FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT="${FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT}" \
			FILE_RESOURCE_DOMAIN_RESERVED_LIMIT="${FILE_RESOURCE_DOMAIN_RESERVED_LIMIT}"
		cp "${tree}/build/kernel" \
			"${TMPDIR_FILE_RESOURCE}/${tag}-kernel"
	else
		make -B build TOOLPREFIX="${TOOLPREFIX}" \
			LOG=error INIT_PROC=fileresource_ucore \
			FILE_RESOURCE_POOL_SIZE="${FILE_RESOURCE_POOL_SIZE}" \
			FILE_RESOURCE_ORDINARY_LIMIT="${FILE_RESOURCE_ORDINARY_LIMIT}" \
			FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT="${FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT}" \
			FILE_RESOURCE_DOMAIN_RESERVED_LIMIT="${FILE_RESOURCE_DOMAIN_RESERVED_LIMIT}"
		cp build/kernel "${TMPDIR_FILE_RESOURCE}/${tag}-kernel"
	fi
}

run_case() {
	local tag="$1"
	local kernel="$2"
	local image="$3"
	local run_image="${TMPDIR_FILE_RESOURCE}/${tag}-run.img"

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
markers = [
    b"fileresource_ucore: blocking_pin_bounded=1",
    b"fileresource_ucore: exit_reuse=1",
    b"fileresource_ucore: pipe_rollback=1",
    b"fileresource_ucore: domain_limit=1",
    b"fileresource_ucore: ordinary_waterline=1",
    b"fileresource_ucore: reserved_progress=1",
    b"fileresource_ucore: parent passed",
]
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
    raise SystemExit(f"[file-resource] unsupported timeout: {text}")


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
    passed_time = None

    while time.monotonic() - start < timeout:
        if passed_time is not None and time.monotonic() - passed_time >= 2.0:
            break
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if ready:
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if markers[-1] in output and passed_time is None:
                passed_time = time.monotonic()
        elif proc.poll() is not None:
            break

    data = bytes(output)
    positions = [data.find(marker) for marker in markers]
    ordered = all(position >= 0 for position in positions)
    ordered = ordered and positions == sorted(positions)
    unique = all(data.count(marker) == 1 for marker in markers)
    failure_match = failure.search(data)
    timed_out = time.monotonic() - start >= timeout
    stopped_by_runner = proc.poll() is None
    bad_exit = not stopped_by_runner and proc.returncode != 0
    if timed_out or not ordered or not unique or failure_match or bad_exit:
        print(
            f"[file-resource] {tag} failed positions={positions} "
            f"unique={unique} timeout={timed_out}",
            file=sys.stderr,
        )
        for line in data.decode("utf-8", errors="replace").splitlines()[-50:]:
            print(line[-240:], file=sys.stderr)
        raise SystemExit(1)
    print(
        f"[file-resource] {tag} passed "
        f"pool=64 ordinary=48 domain=16 reserve=16"
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
}

build_case "" agent
build_case baseline_ucore baseline
run_case agent "${TMPDIR_FILE_RESOURCE}/agent-kernel" \
	"${TMPDIR_FILE_RESOURCE}/agent.img"
run_case baseline "${TMPDIR_FILE_RESOURCE}/baseline-kernel" \
	"${TMPDIR_FILE_RESOURCE}/baseline.img"

echo "[file-resource] both targets passed"
