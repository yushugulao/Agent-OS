#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FS_BLOCKS="${FS_ENOSPC_BLOCKS:-192}"
FS_INODES="${FS_ENOSPC_INODES:-24}"
FS_CACHE_INODES="${FS_ENOSPC_CACHE_INODES:-8}"
FS_QUOTA_DOMAIN_BLOCKS="${FS_QUOTA_DOMAIN_BLOCKS:-384}"
FS_QUOTA_DOMAIN_INODES="${FS_QUOTA_DOMAIN_INODES:-64}"
FS_QUOTA_RESERVE_BLOCKS="${FS_QUOTA_RESERVE_BLOCKS:-256}"
FS_QUOTA_RESERVE_INODES="${FS_QUOTA_RESERVE_INODES:-32}"
FS_QUOTA_WORKFLOW_BLOCK_RESERVE="${FS_QUOTA_WORKFLOW_BLOCK_RESERVE:-64}"
FS_QUOTA_SYSTEM_BLOCK_RESERVE="${FS_QUOTA_SYSTEM_BLOCK_RESERVE:-64}"
FS_QUOTA_WORKFLOW_INODE_RESERVE="${FS_QUOTA_WORKFLOW_INODE_RESERVE:-4}"
FS_QUOTA_SYSTEM_INODE_RESERVE="${FS_QUOTA_SYSTEM_INODE_RESERVE:-4}"
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
	local blocks="$3"
	local inodes="$4"
	local binary="$5"
	local user_tag="${6:-${tag}}"
	local workflow_blocks="${7:-1}"
	local system_blocks="${8:-1}"
	local workflow_inodes="${9:-1}"
	local system_inodes="${10:-1}"
	local prefix="${tree:+${tree}/}"

	cc -DNINODE="${inodes}" -DFSSIZE="${blocks}" \
		-DFS_WORKFLOW_BLOCK_RESERVE="${workflow_blocks}" \
		-DFS_SYSTEM_BLOCK_RESERVE="${system_blocks}" \
		-DFS_WORKFLOW_INODE_RESERVE="${workflow_inodes}" \
		-DFS_SYSTEM_INODE_RESERVE="${system_inodes}" \
		-DFS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
		-DFS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
		-DFS_SYSTEM_BLOCK_MIN_RESERVE=1 \
		-DFS_SYSTEM_INODE_MIN_RESERVE=1 \
		-DFS_STORAGE_TINY_TEST_PROFILE=1 \
		"${prefix}nfs/fs.c" -o "${TMPDIR_FS}/${tag}-mkfs"
	"${TMPDIR_FS}/${tag}-mkfs" "${TMPDIR_FS}/${tag}.img" \
		"${TMPDIR_FS}/${user_tag}-user-target/bin/${binary}"
}

check_mkfs_capacity_contract() {
	local binary="${TMPDIR_FS}/agent-user-target/bin/fsenospc_ucore"
	local log="${TMPDIR_FS}/mkfs-capacity.log"

	cc -DNINODE="${FS_INODES}" -DFSSIZE="${FS_BLOCKS}" \
		-DFS_WORKFLOW_BLOCK_RESERVE="${FS_BLOCKS}" \
		-DFS_SYSTEM_BLOCK_RESERVE="${FS_BLOCKS}" \
		-DFS_WORKFLOW_INODE_RESERVE="${FS_INODES}" \
		-DFS_SYSTEM_INODE_RESERVE="${FS_INODES}" \
		-DFS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
		-DFS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
		-DFS_SYSTEM_BLOCK_MIN_RESERVE=1 \
		-DFS_SYSTEM_INODE_MIN_RESERVE=1 \
		-DFS_STORAGE_TINY_TEST_PROFILE=1 \
		nfs/fs.c -o "${TMPDIR_FS}/unfunded-mkfs"
	if "${TMPDIR_FS}/unfunded-mkfs" \
		"${TMPDIR_FS}/unfunded.img" "${binary}" >"${log}" 2>&1; then
		echo "[fs-enospc] mkfs accepted unfunded workflow guarantees" >&2
		exit 1
	fi
	if ! grep -q "image cannot fund workflow guarantees" "${log}"; then
		echo "[fs-enospc] mkfs capacity rejection lacked diagnostic" >&2
		cat "${log}" >&2
		exit 1
	fi
	echo "[fs-enospc] mkfs capacity contract passed"
}

run_case() {
	local tag="$1"
	local kernel="$2"
	local image="$3"
	local marker="$4"
	local profile="$5"

	"${PYTHON_BIN}" - "${tag}" "${kernel}" "${image}" "${QEMU}" \
		"${marker}" "${profile}" <<'PY'
import os
import re
import select
import signal
import subprocess
import sys
import time

tag, kernel, image, qemu, marker, profile = sys.argv[1:7]
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
profile_error = None
if profile in ("domain", "reserve"):
    required = [
        "fsquota_ucore: public_version_churn=1",
        "fsquota_ucore: public_domain_limited=1",
        "fsquota_ucore: post_exit_accounting=1",
        "fsquota_ucore: workflow_reserve=1",
        "fsquota_ucore: workflow_version_reserve=1",
        "fsquota_ucore: content_version_reserve=1",
        "fsquota_ucore: kernel_metadata_reserve=1",
        "fsquota_ucore: pressure_cleanup=1",
    ]
    if profile == "domain":
        required.append("fsquota_ucore: quota_reuse=1")
    positions = [output.find(text) for text in required]
    if marker_at < 0 or any(pos < 0 or pos >= marker_at for pos in positions):
        profile_error = "missing quota marker"
    elif positions != sorted(positions):
        profile_error = "quota markers are out of order"
    pressure = re.search(
        r"fsquota_ucore: public_domain_limited=1 blocks=(\d+) inodes=(\d+)",
        output,
    )
    if pressure is None:
        profile_error = "missing quota pressure counts"
    else:
        blocks, inodes = (int(value) for value in pressure.groups())
        if profile == "domain" and not (2 <= blocks <= 16 and 4 <= inodes <= 8):
            profile_error = (
                f"domain boundary mismatch: blocks={blocks} inodes={inodes}"
            )
        if profile == "reserve" and not (blocks > 32 and inodes > 12):
            profile_error = (
                f"reserve boundary mismatch: blocks={blocks} inodes={inodes}"
            )
    churn = re.search(
        r"fsquota_ucore: public_version_churn=1 cycles=(\d+)", output
    )
    if churn is None or int(churn.group(1)) <= 512:
        profile_error = "version churn did not cross the former table capacity"
elif profile != "generic":
    profile_error = f"unknown validation profile: {profile}"
if timed_out:
    print(f"[fs-enospc] {tag} timed out", file=sys.stderr)
if timed_out or marker_at < 0 or failed_before_marker or profile_error:
    if profile_error:
        print(f"[fs-enospc] {tag} {profile_error}", file=sys.stderr)
    print(f"[fs-enospc] {tag} failed", file=sys.stderr)
    for line in lines[-40:]:
        print(line, file=sys.stderr)
    raise SystemExit(1)
print(f"[fs-enospc] {tag} passed in {time.monotonic() - start:.1f}s")
PY
}

build_user "" agent
cc host_tools/test_fs_storage_policy.c -o "${TMPDIR_FS}/storage-policy-test"
"${TMPDIR_FS}/storage-policy-test"
check_mkfs_capacity_contract
build_image "" agent "${FS_BLOCKS}" "${FS_INODES}" fsenospc_ucore agent \
	1 1 1 1
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	INIT_PROC=fsenospc_ucore FS_ICACHE_SIZE="${FS_CACHE_INODES}" \
	FS_DOMAIN_BLOCK_LIMIT=512 FS_DOMAIN_INODE_LIMIT=64 \
	FS_WORKFLOW_BLOCK_RESERVE=1 FS_SYSTEM_BLOCK_RESERVE=1 \
	FS_WORKFLOW_INODE_RESERVE=1 FS_SYSTEM_INODE_RESERVE=1 \
	FS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
	FS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
	FS_SYSTEM_BLOCK_MIN_RESERVE=1 FS_SYSTEM_INODE_MIN_RESERVE=1 \
	FS_STORAGE_TINY_TEST_PROFILE=1
cp build/kernel "${TMPDIR_FS}/agent-kernel"

build_image "" quota-domain "${FS_QUOTA_DOMAIN_BLOCKS}" \
	"${FS_QUOTA_DOMAIN_INODES}" fsquota_ucore agent \
	"${FS_QUOTA_WORKFLOW_BLOCK_RESERVE}" \
	"${FS_QUOTA_SYSTEM_BLOCK_RESERVE}" \
	"${FS_QUOTA_WORKFLOW_INODE_RESERVE}" \
	"${FS_QUOTA_SYSTEM_INODE_RESERVE}"
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	INIT_PROC=fsquota_ucore \
	FS_DOMAIN_BLOCK_LIMIT=16 FS_DOMAIN_INODE_LIMIT=8 \
	FS_WORKFLOW_BLOCK_RESERVE="${FS_QUOTA_WORKFLOW_BLOCK_RESERVE}" \
	FS_SYSTEM_BLOCK_RESERVE="${FS_QUOTA_SYSTEM_BLOCK_RESERVE}" \
	FS_WORKFLOW_INODE_RESERVE="${FS_QUOTA_WORKFLOW_INODE_RESERVE}" \
	FS_SYSTEM_INODE_RESERVE="${FS_QUOTA_SYSTEM_INODE_RESERVE}" \
	FS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
	FS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
	FS_SYSTEM_BLOCK_MIN_RESERVE=1 FS_SYSTEM_INODE_MIN_RESERVE=1 \
	FS_STORAGE_TINY_TEST_PROFILE=1
cp build/kernel "${TMPDIR_FS}/quota-domain-kernel"

build_image "" quota-reserve "${FS_QUOTA_RESERVE_BLOCKS}" \
	"${FS_QUOTA_RESERVE_INODES}" fsquota_ucore agent \
	"${FS_QUOTA_WORKFLOW_BLOCK_RESERVE}" \
	"${FS_QUOTA_SYSTEM_BLOCK_RESERVE}" \
	"${FS_QUOTA_WORKFLOW_INODE_RESERVE}" \
	"${FS_QUOTA_SYSTEM_INODE_RESERVE}"
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	INIT_PROC=fsquota_ucore \
	FS_DOMAIN_BLOCK_LIMIT=512 FS_DOMAIN_INODE_LIMIT=128 \
	FS_WORKFLOW_BLOCK_RESERVE="${FS_QUOTA_WORKFLOW_BLOCK_RESERVE}" \
	FS_SYSTEM_BLOCK_RESERVE="${FS_QUOTA_SYSTEM_BLOCK_RESERVE}" \
	FS_WORKFLOW_INODE_RESERVE="${FS_QUOTA_WORKFLOW_INODE_RESERVE}" \
	FS_SYSTEM_INODE_RESERVE="${FS_QUOTA_SYSTEM_INODE_RESERVE}" \
	FS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
	FS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
	FS_SYSTEM_BLOCK_MIN_RESERVE=1 FS_SYSTEM_INODE_MIN_RESERVE=1 \
	FS_STORAGE_TINY_TEST_PROFILE=1
cp build/kernel "${TMPDIR_FS}/quota-reserve-kernel"

build_user baseline_ucore baseline
build_image baseline_ucore baseline "${FS_BLOCKS}" "${FS_INODES}" \
	fsenospc_ucore baseline 1 1 1 1
make -C baseline_ucore -B build \
	TOOLPREFIX="${TOOLPREFIX}" LOG=error INIT_PROC=fsenospc_ucore \
	FS_ICACHE_SIZE="${FS_CACHE_INODES}"
cp baseline_ucore/build/kernel "${TMPDIR_FS}/baseline-kernel"

run_case agent "${TMPDIR_FS}/agent-kernel" "${TMPDIR_FS}/agent.img" \
	"fsenospc_ucore: parent passed" generic
run_case baseline "${TMPDIR_FS}/baseline-kernel" \
	"${TMPDIR_FS}/baseline.img" "fsenospc_ucore: parent passed" generic
run_case quota-domain "${TMPDIR_FS}/quota-domain-kernel" \
	"${TMPDIR_FS}/quota-domain.img" "fsquota_ucore: parent passed" domain
run_case quota-reserve "${TMPDIR_FS}/quota-reserve-kernel" \
	"${TMPDIR_FS}/quota-reserve.img" "fsquota_ucore: parent passed" reserve

echo "[fs-enospc] generic targets and Agent quota cases passed"
