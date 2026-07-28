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
	local mkfs_sources=("${prefix}nfs/fs.c")
	if [[ -z "${tree}" ]]; then
		mkfs_sources+=(nfs/host_image_snapshot.c)
	fi

	make -C "${user_dir}" \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER=file_resource \
		build_dir="${TMPDIR_FILE_RESOURCE}/${tag}-user-build" \
		out_dir="${TMPDIR_FILE_RESOURCE}/${tag}-user-target" \
		asm_dir="${TMPDIR_FILE_RESOURCE}/${tag}-user-asm"
	mkdir -p "${TMPDIR_FILE_RESOURCE}/fixture/bin"
	printf 'file-resource-fixture\n' \
		>"${TMPDIR_FILE_RESOURCE}/fixture/bin/frsource"
	cc "${mkfs_sources[@]}" -o "${TMPDIR_FILE_RESOURCE}/${tag}-mkfs"
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
	local runner_status

	cp "${image}" "${run_image}"
	local log_file="${TMPDIR_FILE_RESOURCE}/${tag}.log"

	if "${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "${tag}" \
		--marker "fileresource_ucore: parent passed" \
		--marker-mode exact-line \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" \
		--kernel "${kernel}" \
		--image "${run_image}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	evidence_append_guest_log "file-resource:${tag}" "${log_file}"
	if [[ ${runner_status} -ne 0 ]]; then
		return "${runner_status}"
	fi
	"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
		--log-file "${log_file}" \
		--tag "file-resource:${tag}" \
		--profile file-resource
	echo "[file-resource] ${tag} passed"
}

build_case "" agent
build_case baseline_ucore baseline
run_case agent "${TMPDIR_FILE_RESOURCE}/agent-kernel" \
	"${TMPDIR_FILE_RESOURCE}/agent.img"
run_case baseline "${TMPDIR_FILE_RESOURCE}/baseline-kernel" \
	"${TMPDIR_FILE_RESOURCE}/baseline.img"

echo "[file-resource] both targets passed"
