#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FS_BLOCKS="${FS_ENOSPC_BLOCKS:-192}"
FS_INODES="${FS_ENOSPC_INODES:-16}"
FS_CACHE_INODES="${FS_ENOSPC_CACHE_INODES:-8}"
TMPDIR_FS="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_FS}"' EXIT

build_user() {
	local tree="$1"
	local tag="$2"
	local user_dir="${tree:+${tree}/}user"

	make -C "${user_dir}" \
		TOOLPREFIX="${TOOLPREFIX}" \
		CHAPTER=fs_enospc \
		build_dir="${TMPDIR_FS}/${tag}-user-build" \
		out_dir="${TMPDIR_FS}/${tag}-user-target" \
		asm_dir="${TMPDIR_FS}/${tag}-user-asm"
}

build_image() {
	local tree="$1"
	local tag="$2"
	local prefix="${tree:+${tree}/}"

	cc -DNINODE="${FS_INODES}" -DFSSIZE="${FS_BLOCKS}" \
		"${prefix}nfs/fs.c" -o "${TMPDIR_FS}/${tag}-mkfs"
	"${TMPDIR_FS}/${tag}-mkfs" "${TMPDIR_FS}/${tag}.img" \
		"${TMPDIR_FS}/${tag}-user-target/bin/fsenospc_ucore"
}

run_case() {
	local tag="$1"
	local kernel="$2"
	local image="$3"

	"${PYTHON_BIN}" - "${tag}" "${kernel}" "${image}" "${QEMU}" <<'PY'
import os
import re
import select
import signal
import subprocess
import sys
import time

tag, kernel, image, qemu = sys.argv[1:5]
marker = "fsenospc_ucore: parent passed"
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
start = time.monotonic()
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    preexec_fn=os.setsid,
)
chunks = []
marker_at = -1
failure_match = None
timed_out = False
assert proc.stdout is not None
while time.monotonic() - start < 120:
    ready, _, _ = select.select([proc.stdout], [], [], 0.2)
    if ready:
        chunk = os.read(proc.stdout.fileno(), 4096)
        if not chunk:
            break
        chunks.append(chunk)
        output = b"".join(chunks).decode("utf-8", errors="replace")
        marker_at = output.find(marker)
        failure_match = failure.search(output)
        if marker_at >= 0 or failure_match is not None:
            break
    elif proc.poll() is not None:
        break
else:
    timed_out = True

if proc.poll() is None:
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=2)

output = b"".join(chunks).decode("utf-8", errors="replace")
print(output, end="")
lines = output.splitlines()
failed_before_marker = (
    failure_match is not None
    and (marker_at < 0 or failure_match.start() < marker_at)
)
if timed_out:
    print(f"[fs-enospc] {tag} timed out", file=sys.stderr)
if timed_out or marker_at < 0 or failed_before_marker:
    print(f"[fs-enospc] {tag} failed", file=sys.stderr)
    for line in lines[-40:]:
        print(line, file=sys.stderr)
    raise SystemExit(1)
print(f"[fs-enospc] {tag} passed in {time.monotonic() - start:.1f}s")
PY
}

build_user "" agent
build_image "" agent
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	INIT_PROC=fsenospc_ucore FS_ICACHE_SIZE="${FS_CACHE_INODES}"
cp build/kernel "${TMPDIR_FS}/agent-kernel"

build_user baseline_ucore baseline
build_image baseline_ucore baseline
make -C baseline_ucore -B build \
	TOOLPREFIX="${TOOLPREFIX}" LOG=error INIT_PROC=fsenospc_ucore \
	FS_ICACHE_SIZE="${FS_CACHE_INODES}"
cp baseline_ucore/build/kernel "${TMPDIR_FS}/baseline-kernel"

run_case agent "${TMPDIR_FS}/agent-kernel" "${TMPDIR_FS}/agent.img"
run_case baseline "${TMPDIR_FS}/baseline-kernel" "${TMPDIR_FS}/baseline.img"

echo "[fs-enospc] both targets passed"
