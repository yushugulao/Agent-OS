#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
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
	local log_file="${TMPDIR_FAIR}/${tag}.log"

	"${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "${tag}" \
		--marker "syscallfair_ucore: parent passed" \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" \
		--kernel "${kernel}" \
		--image "${run_image}"
	"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
		--log-file "${log_file}" \
		--tag "syscall-fairness:${tag}" \
		--profile syscall-fairness
	echo "[syscall-fairness] ${tag} passed"
}

build_case "" agent
build_case baseline_ucore baseline
run_case agent "${TMPDIR_FAIR}/agent-kernel" \
	"${TMPDIR_FAIR}/agent.img"
run_case baseline "${TMPDIR_FAIR}/baseline-kernel" \
	"${TMPDIR_FAIR}/baseline.img"

echo "[syscall-fairness] both targets passed"
