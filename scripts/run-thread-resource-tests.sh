#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
THREAD_RESOURCE_POOL_SIZE=19
THREAD_RESOURCE_ORDINARY_LIMIT=12
THREAD_RESOURCE_RESERVED_LIMIT=6
THREAD_RESOURCE_DOMAIN_ORDINARY_LIMIT=6
THREAD_RESOURCE_DOMAIN_RESERVED_LIMIT=4
TMPDIR_THREAD_RESOURCE="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_THREAD_RESOURCE}"' EXIT
source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_THREAD_RESOURCE}"

make -C user \
	TOOLPREFIX="${TOOLPREFIX}" CHAPTER=thread_resource \
	build_dir="${TMPDIR_THREAD_RESOURCE}/user-build" \
	out_dir="${TMPDIR_THREAD_RESOURCE}/user-target" \
	asm_dir="${TMPDIR_THREAD_RESOURCE}/user-asm"
host_probe_compile "${TMPDIR_THREAD_RESOURCE}/mkfs" \
	nfs/fs.c nfs/host_image_snapshot.c
host_probe_run "${TMPDIR_THREAD_RESOURCE}/mkfs" \
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

log_file="${TMPDIR_THREAD_RESOURCE}/thread-resource.log"
if "${PYTHON_BIN}" scripts/agent_test_runner.py \
	--init-proc thread-resource \
	--marker "threadresource_ucore: parent passed" \
	--marker-mode exact-line \
	--log-file "${log_file}" \
	--case-timeout "${CASE_TIMEOUT}" \
	--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
	--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
	--qemu "${QEMU}" \
	--kernel "${TMPDIR_THREAD_RESOURCE}/kernel" \
	--image "${TMPDIR_THREAD_RESOURCE}/run.img"; then
	runner_status=0
else
	runner_status=$?
fi
if [[ ${runner_status} -ne 0 ]]; then
	exit "${runner_status}"
fi
"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
	--log-file "${log_file}" \
	--tag thread-resource \
	--profile thread-resource
echo "[thread-resource] all checks passed"
host_probe_report "thread-resource mkfs"
