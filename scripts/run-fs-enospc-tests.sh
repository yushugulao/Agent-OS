#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
FS_BLOCKS="${FS_ENOSPC_BLOCKS:-192}"
FS_INODES="${FS_ENOSPC_INODES:-24}"
FS_CACHE_INODES="${FS_ENOSPC_CACHE_INODES:-8}"
FS_QUOTA_DOMAIN_BLOCKS="${FS_QUOTA_DOMAIN_BLOCKS:-384}"
FS_QUOTA_DOMAIN_INODES="${FS_QUOTA_DOMAIN_INODES:-64}"
FS_QUOTA_RESERVE_BLOCKS="${FS_QUOTA_RESERVE_BLOCKS:-256}"
FS_QUOTA_RESERVE_INODES="${FS_QUOTA_RESERVE_INODES:-32}"
FS_PERSIST_BLOCKS="${FS_PERSIST_BLOCKS:-384}"
FS_PERSIST_INODES="${FS_PERSIST_INODES:-48}"
FS_PERSIST_BLOCK_LIMIT=18
FS_PERSIST_INODE_LIMIT=8
FS_QUOTA_WORKFLOW_BLOCK_RESERVE="${FS_QUOTA_WORKFLOW_BLOCK_RESERVE:-64}"
FS_QUOTA_SYSTEM_BLOCK_RESERVE="${FS_QUOTA_SYSTEM_BLOCK_RESERVE:-64}"
FS_QUOTA_WORKFLOW_INODE_RESERVE="${FS_QUOTA_WORKFLOW_INODE_RESERVE:-4}"
FS_QUOTA_SYSTEM_INODE_RESERVE="${FS_QUOTA_SYSTEM_INODE_RESERVE:-4}"
# Two fixed-capacity banks each consume 217 data blocks plus one indirect
# table. Grow only AgentOS images so the pre-genesis data arena remains the
# same size as the original pressure fixture.
AGENT_META_GENESIS_BLOCKS=436
AGENT_META_GENESIS_INODES=2

read -r calculated_genesis_blocks calculated_genesis_inodes < <(
"${PYTHON_BIN}" - <<'PY'
from host_tools.agent_metadata_disk_format import load_contract
from host_tools import plain_ucore_fs_extract as fs

layout = load_contract()
capacity = (
    layout.header_bytes
    + layout.durable_arena_bytes
    + layout.max_count * layout.record_bytes
)
data_blocks = (capacity + fs.BSIZE - 1) // fs.BSIZE
mapping_blocks = 1 if data_blocks > fs.NDIRECT else 0
print(
    len(layout.bank_names) * (data_blocks + mapping_blocks),
    len(layout.bank_names),
)
PY
)
if [[ "${calculated_genesis_blocks}" -ne ${AGENT_META_GENESIS_BLOCKS} ||
      "${calculated_genesis_inodes}" -ne ${AGENT_META_GENESIS_INODES} ]]; then
	echo "[fs-enospc] metadata genesis block contract drift: " \
		"expected ${AGENT_META_GENESIS_BLOCKS}/${AGENT_META_GENESIS_INODES}, " \
		"actual ${calculated_genesis_blocks}/${calculated_genesis_inodes}" >&2
	exit 1
fi

agent_image_blocks() {
	local original="$1" original_inodes="$2"
	local expanded_inodes=$(( original_inodes + AGENT_META_GENESIS_INODES ))
	local original_maps original_inode_blocks expanded_inode_blocks
	local original_root_data expanded_root_data root_growth target_growth
	local candidate candidate_maps next

	original_maps=$(( (original + 8191) / 8192 + (original + 255) / 256 ))
	original_inode_blocks=$(( (original_inodes + 7) / 8 ))
	expanded_inode_blocks=$(( (expanded_inodes + 7) / 8 ))
	original_root_data=$(( (original_inodes * 16 + 1023) / 1024 ))
	expanded_root_data=$(( (expanded_inodes * 16 + 1023) / 1024 ))
	root_growth=$(( expanded_root_data - original_root_data +
		(expanded_root_data > 12 ? 1 : 0) -
		(original_root_data > 12 ? 1 : 0) ))
	target_growth=$(( AGENT_META_GENESIS_BLOCKS + root_growth ))
	candidate=$(( original + target_growth +
		expanded_inode_blocks - original_inode_blocks ))
	while :; do
		candidate_maps=$(( (candidate + 8191) / 8192 +
			(candidate + 255) / 256 ))
		next=$(( original + target_growth + candidate_maps - original_maps +
			expanded_inode_blocks - original_inode_blocks ))
		[[ ${next} -eq ${candidate} ]] && break
		candidate=${next}
	done
	printf '%s\n' "${candidate}"
}

assert_genesis_geometry() {
	local original="$1" original_inodes="$2" expanded="$3" expanded_inodes="$4"
	local original_maps expanded_maps original_inode_blocks expanded_inode_blocks
	local original_root_data expanded_root_data root_growth usable_delta

	original_maps=$(( (original + 8191) / 8192 + (original + 255) / 256 ))
	expanded_maps=$(( (expanded + 8191) / 8192 + (expanded + 255) / 256 ))
	original_inode_blocks=$(( (original_inodes + 7) / 8 ))
	expanded_inode_blocks=$(( (expanded_inodes + 7) / 8 ))
	original_root_data=$(( (original_inodes * 16 + 1023) / 1024 ))
	expanded_root_data=$(( (expanded_inodes * 16 + 1023) / 1024 ))
	root_growth=$(( expanded_root_data - original_root_data +
		(expanded_root_data > 12 ? 1 : 0) -
		(original_root_data > 12 ? 1 : 0) ))
	usable_delta=$(( expanded - expanded_maps - expanded_inode_blocks -
		root_growth - original + original_maps + original_inode_blocks ))
	if [[ ${usable_delta} -ne ${AGENT_META_GENESIS_BLOCKS} ]]; then
		echo "[fs-enospc] metadata genesis geometry drift: " \
			"${original}->${expanded} adds ${usable_delta} usable blocks" >&2
		exit 1
	fi
	if [[ $((expanded_inodes - original_inodes)) -ne ${AGENT_META_GENESIS_INODES} ]]; then
		echo "[fs-enospc] metadata genesis inode compensation drift" >&2
		exit 1
	fi
}

FS_AGENT_INODES=$(( FS_INODES + AGENT_META_GENESIS_INODES ))
FS_QUOTA_DOMAIN_AGENT_INODES=$(( FS_QUOTA_DOMAIN_INODES + AGENT_META_GENESIS_INODES ))
FS_QUOTA_RESERVE_AGENT_INODES=$(( FS_QUOTA_RESERVE_INODES + AGENT_META_GENESIS_INODES ))
FS_PERSIST_AGENT_INODES=$(( FS_PERSIST_INODES + AGENT_META_GENESIS_INODES ))
FS_AGENT_BLOCKS="$(agent_image_blocks "${FS_BLOCKS}" "${FS_INODES}")"
FS_QUOTA_DOMAIN_AGENT_BLOCKS="$(agent_image_blocks \
	"${FS_QUOTA_DOMAIN_BLOCKS}" "${FS_QUOTA_DOMAIN_INODES}")"
FS_QUOTA_RESERVE_AGENT_BLOCKS="$(agent_image_blocks \
	"${FS_QUOTA_RESERVE_BLOCKS}" "${FS_QUOTA_RESERVE_INODES}")"
FS_PERSIST_AGENT_BLOCKS="$(agent_image_blocks \
	"${FS_PERSIST_BLOCKS}" "${FS_PERSIST_INODES}")"
assert_genesis_geometry "${FS_BLOCKS}" "${FS_INODES}" \
	"${FS_AGENT_BLOCKS}" "${FS_AGENT_INODES}"
assert_genesis_geometry "${FS_QUOTA_DOMAIN_BLOCKS}" \
	"${FS_QUOTA_DOMAIN_INODES}" "${FS_QUOTA_DOMAIN_AGENT_BLOCKS}" \
	"${FS_QUOTA_DOMAIN_AGENT_INODES}"
assert_genesis_geometry "${FS_QUOTA_RESERVE_BLOCKS}" \
	"${FS_QUOTA_RESERVE_INODES}" "${FS_QUOTA_RESERVE_AGENT_BLOCKS}" \
	"${FS_QUOTA_RESERVE_AGENT_INODES}"
assert_genesis_geometry "${FS_PERSIST_BLOCKS}" "${FS_PERSIST_INODES}" \
	"${FS_PERSIST_AGENT_BLOCKS}" "${FS_PERSIST_AGENT_INODES}"
TMPDIR_FS="$(mktemp -d)"
SPONSOR_HOST="${TMPDIR_FS}/fixture/bin/pqsponsor"
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
	local sponsored_file="${11:-}"
	local prefix="${tree:+${tree}/}"
	local image_files=("${TMPDIR_FS}/${user_tag}-user-target/bin/${binary}")
	local mkfs_sources=("${prefix}nfs/fs.c")
	if [[ -z "${tree}" ]]; then
		mkfs_sources+=(nfs/host_image_snapshot.c)
	fi

	if [[ -n "${sponsored_file}" ]]; then
		image_files+=("${sponsored_file}")
	fi

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
		"${mkfs_sources[@]}" -o "${TMPDIR_FS}/${tag}-mkfs"
	"${TMPDIR_FS}/${tag}-mkfs" "${TMPDIR_FS}/${tag}.img" \
		"${image_files[@]}"
	if [[ -z "${tree}" ]]; then
		"${PYTHON_BIN}" host_tools/agent_metadata_disk_format.py \
			--image "${TMPDIR_FS}/${tag}.img" --stage genesis >/dev/null
	fi
}

snapshot_image_usage() {
	local image="$1"
	local output="$2"

	"${PYTHON_BIN}" - "${image}" >"${output}" <<'PY'
import struct
import sys

BLOCK_SIZE = 1024
AGENT_MAGIC = 0x10203047
BASELINE_MAGIC = 0x10203046

path = sys.argv[1]
with open(path, "rb") as handle:
    handle.seek(BLOCK_SIZE)
    superblock = handle.read(BLOCK_SIZE)
    magic, size, _, ninodes, inodestart, bmapstart, qmapstart, datastart = \
        struct.unpack_from("<8I", superblock)
    if magic == AGENT_MAGIC:
        inode_size = 128
    elif magic == BASELINE_MAGIC:
        inode_size = 64
    else:
        raise SystemExit(f"unsupported filesystem magic: {magic:#x}")

    allocated_inodes = 0
    for inum in range(1, ninodes):
        offset = inodestart * BLOCK_SIZE + inum * inode_size
        handle.seek(offset)
        if struct.unpack("<h", handle.read(2))[0] != 0:
            allocated_inodes += 1

    allocated_blocks = 0
    owned_blocks = 0
    for block in range(datastart, size):
        handle.seek(bmapstart * BLOCK_SIZE + block // 8)
        if handle.read(1)[0] & (1 << (block % 8)):
            allocated_blocks += 1
        handle.seek(qmapstart * BLOCK_SIZE + block * 4)
        if struct.unpack("<I", handle.read(4))[0] != 0:
            owned_blocks += 1

print(allocated_blocks, allocated_inodes, owned_blocks)
PY
}

check_image_usage_delta() {
	local image="$1"
	local baseline="$2"
	local expected_blocks="$3"
	local expected_inodes="$4"
	local tag="$5"
	local current="${TMPDIR_FS}/${tag}.usage"
	local base_blocks base_inodes base_owned
	local current_blocks current_inodes current_owned

	snapshot_image_usage "${image}" "${current}"
	read -r base_blocks base_inodes base_owned <"${baseline}"
	read -r current_blocks current_inodes current_owned <"${current}"
	if [[ $((current_blocks - base_blocks)) -ne ${expected_blocks} ||
	      $((current_inodes - base_inodes)) -ne ${expected_inodes} ||
	      $((current_owned - base_owned)) -ne ${expected_blocks} ]]; then
		echo "[fs-enospc] ${tag} physical orphan reclaim mismatch: " \
			"blocks ${base_blocks}->${current_blocks}, " \
			"inodes ${base_inodes}->${current_inodes}, " \
			"owners ${base_owned}->${current_owned}" >&2
		exit 1
	fi
	echo "[fs-enospc] ${tag} physical orphan reclaim passed"
}

check_mkfs_capacity_contract() {
	local binary="${TMPDIR_FS}/agent-user-target/bin/fsenospc_ucore"
	local log="${TMPDIR_FS}/mkfs-capacity.log"

	cc -DNINODE="${FS_AGENT_INODES}" -DFSSIZE="${FS_AGENT_BLOCKS}" \
		-DFS_WORKFLOW_BLOCK_RESERVE="${FS_AGENT_BLOCKS}" \
		-DFS_SYSTEM_BLOCK_RESERVE="${FS_AGENT_BLOCKS}" \
		-DFS_WORKFLOW_INODE_RESERVE="${FS_AGENT_INODES}" \
		-DFS_SYSTEM_INODE_RESERVE="${FS_AGENT_INODES}" \
		-DFS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
		-DFS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
		-DFS_SYSTEM_BLOCK_MIN_RESERVE=1 \
		-DFS_SYSTEM_INODE_MIN_RESERVE=1 \
		-DFS_STORAGE_TINY_TEST_PROFILE=1 \
		nfs/fs.c nfs/host_image_snapshot.c \
		-o "${TMPDIR_FS}/unfunded-mkfs"
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

	cc -DNINODE="${FS_INODES}" -DFSSIZE="${AGENT_META_GENESIS_BLOCKS}" \
		-DFS_WORKFLOW_BLOCK_RESERVE=1 -DFS_SYSTEM_BLOCK_RESERVE=1 \
		-DFS_WORKFLOW_INODE_RESERVE=1 -DFS_SYSTEM_INODE_RESERVE=1 \
		-DFS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
		-DFS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
		-DFS_SYSTEM_BLOCK_MIN_RESERVE=1 \
		-DFS_SYSTEM_INODE_MIN_RESERVE=1 \
		-DFS_STORAGE_TINY_TEST_PROFILE=1 \
		nfs/fs.c nfs/host_image_snapshot.c \
		-o "${TMPDIR_FS}/undersized-genesis-mkfs"
	if "${TMPDIR_FS}/undersized-genesis-mkfs" \
		"${TMPDIR_FS}/undersized-genesis.img" >"${log}" 2>&1; then
		echo "[fs-enospc] mkfs accepted undersized metadata genesis" >&2
		exit 1
	fi
	if ! grep -q "image cannot fit canonical metadata genesis" "${log}"; then
		echo "[fs-enospc] mkfs genesis rejection lacked diagnostic" >&2
		cat "${log}" >&2
		exit 1
	fi
	echo "[fs-enospc] mkfs genesis capacity contract passed"
}

check_baseline_root_geometry() {
	local mkfs="${TMPDIR_FS}/baseline-root-mkfs"
	local inputs="${TMPDIR_FS}/baseline-root-inputs"
	local files=()
	local i path

	cc baseline_ucore/nfs/fs.c -o "${mkfs}"
	mkdir -p "${inputs}"
	for ((i = 0; i < 64; i++)); do
		path="${inputs}/r$(printf '%02d' "${i}")"
		touch "${path}"
		files+=("${path}")
	done
	"${mkfs}" "${TMPDIR_FS}/baseline-root-empty.img"
	"${mkfs}" "${TMPDIR_FS}/baseline-root-aligned.img" "${files[@]}"
	"${PYTHON_BIN}" - "${TMPDIR_FS}/baseline-root-empty.img" \
		"${TMPDIR_FS}/baseline-root-aligned.img" <<'PY'
import struct
import sys

BLOCK_SIZE = 1024
DINODE_SIZE = 64
ROOT_INODE = 1


def root_geometry(path):
    with open(path, "rb") as handle:
        handle.seek(BLOCK_SIZE)
        superblock = handle.read(BLOCK_SIZE)
        magic, _, _, _, inodestart = struct.unpack_from("<5I", superblock)
        if magic != 0x10203046:
            raise SystemExit(f"unexpected baseline magic: {magic:#x}")
        handle.seek(inodestart * BLOCK_SIZE + ROOT_INODE * DINODE_SIZE)
        dinode = handle.read(DINODE_SIZE)
    inode_type, _, _, size = struct.unpack_from("<hHII", dinode)
    addresses = struct.unpack_from("<13I", dinode, 12)
    return inode_type, size, addresses


empty_type, empty_size, empty_addrs = root_geometry(sys.argv[1])
aligned_type, aligned_size, aligned_addrs = root_geometry(sys.argv[2])
if empty_type != 1 or empty_size != 0 or any(empty_addrs):
    raise SystemExit("empty baseline root contains an unmapped size or block")
if (aligned_type != 1 or aligned_size != BLOCK_SIZE or
        aligned_addrs[0] == 0 or any(aligned_addrs[1:])):
    raise SystemExit("block-aligned baseline root contains a data hole")
PY
	echo "[fs-enospc] baseline root geometry passed"
}

run_case() {
	local tag="$1"
	local kernel="$2"
	local image="$3"
	local marker="$4"
	local profile="$5"

	local log_file="${TMPDIR_FS}/${tag}.log"
	local completion_args=()
	local runner_status

	case "${profile}" in
	orphan-crash | persistent-seed)
		completion_args+=(--completion-mode checkpoint)
		;;
	esac

	if "${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "${tag}" \
		--marker "${marker}" \
		--marker-mode exact-line \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" \
		--kernel "${kernel}" \
		--image "${image}" \
		"${completion_args[@]}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	evidence_append_guest_log "fs-enospc:${tag}" "${log_file}"
	if [[ ${runner_status} -ne 0 ]]; then
		return "${runner_status}"
	fi
	"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
		--log-file "${log_file}" \
		--tag "fs-enospc:${tag}" \
		--profile "${profile}" \
		--marker "${marker}"
	echo "[fs-enospc] ${tag} passed"
}

build_user "" agent
cc host_tools/test_fs_storage_policy.c -o "${TMPDIR_FS}/storage-policy-test"
"${TMPDIR_FS}/storage-policy-test"
check_mkfs_capacity_contract
check_baseline_root_geometry
mkdir -p "$(dirname "${SPONSOR_HOST}")"
dd if=/dev/zero of="${SPONSOR_HOST}" bs=1024 count=13 status=none
build_image "" agent "${FS_AGENT_BLOCKS}" "${FS_AGENT_INODES}" fsenospc_ucore agent \
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

build_image "" quota-domain "${FS_QUOTA_DOMAIN_AGENT_BLOCKS}" \
	"${FS_QUOTA_DOMAIN_AGENT_INODES}" fsquota_ucore agent \
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

build_image "" quota-reserve "${FS_QUOTA_RESERVE_AGENT_BLOCKS}" \
	"${FS_QUOTA_RESERVE_AGENT_INODES}" fsquota_ucore agent \
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

build_image "" principal-agent "${FS_PERSIST_AGENT_BLOCKS}" \
	"${FS_PERSIST_AGENT_INODES}" fspquota_ucore agent \
	"${FS_QUOTA_WORKFLOW_BLOCK_RESERVE}" \
	"${FS_QUOTA_SYSTEM_BLOCK_RESERVE}" \
	"${FS_QUOTA_WORKFLOW_INODE_RESERVE}" \
	"${FS_QUOTA_SYSTEM_INODE_RESERVE}" "${SPONSOR_HOST}"
snapshot_image_usage "${TMPDIR_FS}/principal-agent.img" \
	"${TMPDIR_FS}/principal-agent-baseline.usage"
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	INIT_PROC=fspquota_ucore \
	FS_DOMAIN_BLOCK_LIMIT="${FS_PERSIST_BLOCK_LIMIT}" \
	FS_DOMAIN_INODE_LIMIT="${FS_PERSIST_INODE_LIMIT}" \
	FS_WORKFLOW_BLOCK_RESERVE="${FS_QUOTA_WORKFLOW_BLOCK_RESERVE}" \
	FS_SYSTEM_BLOCK_RESERVE="${FS_QUOTA_SYSTEM_BLOCK_RESERVE}" \
	FS_WORKFLOW_INODE_RESERVE="${FS_QUOTA_WORKFLOW_INODE_RESERVE}" \
	FS_SYSTEM_INODE_RESERVE="${FS_QUOTA_SYSTEM_INODE_RESERVE}" \
	FS_WORKFLOW_BLOCK_MIN_PER_SCOPE=1 \
	FS_WORKFLOW_INODE_MIN_PER_SCOPE=1 \
	FS_SYSTEM_BLOCK_MIN_RESERVE=1 FS_SYSTEM_INODE_MIN_RESERVE=1 \
	FS_STORAGE_TINY_TEST_PROFILE=1
cp build/kernel "${TMPDIR_FS}/principal-agent-kernel"

build_user baseline_ucore baseline
build_image baseline_ucore baseline "${FS_BLOCKS}" "${FS_INODES}" \
	fsenospc_ucore baseline 1 1 1 1
make -C baseline_ucore -B build \
	TOOLPREFIX="${TOOLPREFIX}" LOG=error INIT_PROC=fsenospc_ucore \
	FS_ICACHE_SIZE="${FS_CACHE_INODES}" \
	FS_DOMAIN_BLOCK_LIMIT=512 FS_DOMAIN_INODE_LIMIT=64
cp baseline_ucore/build/kernel "${TMPDIR_FS}/baseline-kernel"

build_image baseline_ucore principal-baseline "${FS_PERSIST_BLOCKS}" \
	"${FS_PERSIST_INODES}" fspquota_ucore baseline 1 1 1 1 \
	"${SPONSOR_HOST}"
snapshot_image_usage "${TMPDIR_FS}/principal-baseline.img" \
	"${TMPDIR_FS}/principal-baseline-baseline.usage"
make -C baseline_ucore -B build \
	TOOLPREFIX="${TOOLPREFIX}" LOG=error INIT_PROC=fspquota_ucore \
	FS_DOMAIN_BLOCK_LIMIT="${FS_PERSIST_BLOCK_LIMIT}" \
	FS_DOMAIN_INODE_LIMIT="${FS_PERSIST_INODE_LIMIT}"
cp baseline_ucore/build/kernel "${TMPDIR_FS}/principal-baseline-kernel"

run_case agent "${TMPDIR_FS}/agent-kernel" "${TMPDIR_FS}/agent.img" \
	"fsenospc_ucore: parent passed" generic
run_case baseline "${TMPDIR_FS}/baseline-kernel" \
	"${TMPDIR_FS}/baseline.img" "fsenospc_ucore: parent passed" generic
run_case quota-domain "${TMPDIR_FS}/quota-domain-kernel" \
	"${TMPDIR_FS}/quota-domain.img" "fsquota_ucore: parent passed" domain
run_case quota-reserve "${TMPDIR_FS}/quota-reserve-kernel" \
	"${TMPDIR_FS}/quota-reserve.img" "fsquota_ucore: parent passed" reserve
run_case principal-agent-orphan "${TMPDIR_FS}/principal-agent-kernel" \
	"${TMPDIR_FS}/principal-agent.img" \
	"fspquota_ucore: crash_orphan_ready=1" orphan-crash
run_case principal-agent-seed "${TMPDIR_FS}/principal-agent-kernel" \
	"${TMPDIR_FS}/principal-agent.img" \
	"fspquota_ucore: durable_fixture=1 blocks=18 inodes=8 owner_exited=1" \
	persistent-seed
check_image_usage_delta "${TMPDIR_FS}/principal-agent.img" \
	"${TMPDIR_FS}/principal-agent-baseline.usage" 4 7 principal-agent
run_case principal-agent-verify "${TMPDIR_FS}/principal-agent-kernel" \
	"${TMPDIR_FS}/principal-agent.img" \
	"fspquota_ucore: parent passed" persistent-verify
run_case principal-baseline-orphan "${TMPDIR_FS}/principal-baseline-kernel" \
	"${TMPDIR_FS}/principal-baseline.img" \
	"fspquota_ucore: crash_orphan_ready=1" orphan-crash
run_case principal-baseline-seed "${TMPDIR_FS}/principal-baseline-kernel" \
	"${TMPDIR_FS}/principal-baseline.img" \
	"fspquota_ucore: durable_fixture=1 blocks=18 inodes=8 owner_exited=1" \
	persistent-seed
check_image_usage_delta "${TMPDIR_FS}/principal-baseline.img" \
	"${TMPDIR_FS}/principal-baseline-baseline.usage" 4 7 principal-baseline
run_case principal-baseline-verify "${TMPDIR_FS}/principal-baseline-kernel" \
	"${TMPDIR_FS}/principal-baseline.img" \
	"fspquota_ucore: parent passed" persistent-verify

echo "[fs-enospc] generic, persistent principal, and Agent quota cases passed"
