#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
TMPDIR_REAP="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_REAP}"' EXIT

build_case() {
	local tree="$1"
	local tag="$2"
	local prefix="${tree:+${tree}/}"
	local user_dir="${prefix}user"
	local apps=("${TMPDIR_REAP}/${tag}-user-target/bin/procreap_ucore")
	local mkfs_sources=("${prefix}nfs/fs.c")
	if [[ -z "${tree}" ]]; then
		mkfs_sources+=(nfs/host_image_snapshot.c)
	fi

	make -C "${user_dir}" \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER=proc_reap \
		build_dir="${TMPDIR_REAP}/${tag}-user-build" \
		out_dir="${TMPDIR_REAP}/${tag}-user-target" \
		asm_dir="${TMPDIR_REAP}/${tag}-user-asm"
	if [[ -z "${tree}" ]]; then
		apps+=("${TMPDIR_REAP}/${tag}-user-target/bin/procreap_agent_ucore")
	fi
	cc "${mkfs_sources[@]}" -o "${TMPDIR_REAP}/${tag}-mkfs"
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
	local runner_status

	cp "${image}" "${run_image}"
	local log_file="${TMPDIR_REAP}/${tag}.log"

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
		--image "${run_image}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	evidence_append_guest_log "proc-reap:${tag}" "${log_file}"
	if [[ ${runner_status} -ne 0 ]]; then
		return "${runner_status}"
	fi
	"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
		--log-file "${log_file}" \
		--tag "proc-reap:${tag}" \
		--profile proc-reap
	echo "[proc-reap] ${tag} passed"
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
