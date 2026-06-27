#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
DUAL_LOG_DIR="${DUAL_LOG_DIR:-/tmp/agentos-dual-platform}"

mkdir -p "${DUAL_LOG_DIR}"

plain_log="${DUAL_LOG_DIR}/plain-platform.log"
agentos_log="${DUAL_LOG_DIR}/agentos-platform.log"

run_and_capture() {
	local label="$1"
	local logfile="$2"
	shift 2

	echo "[dual-platform] running ${label}"
	(
		cd "${ROOT_DIR}"
		"$@"
	) >"${logfile}" 2>&1
	echo "[dual-platform] ${label} log: ${logfile}"
}

require_log() {
	local logfile="$1"
	local pattern="$2"
	local message="$3"

	if ! grep -qE "${pattern}" "${logfile}"; then
		echo "[dual-platform] missing: ${message}" >&2
		tail -80 "${logfile}" >&2
		exit 1
	fi
}

reject_log() {
	local logfile="$1"
	local pattern="$2"
	local message="$3"

	if grep -qE "${pattern}" "${logfile}"; then
		echo "[dual-platform] unexpected: ${message}" >&2
		grep -nE "${pattern}" "${logfile}" >&2
		tail -80 "${logfile}" >&2
		exit 1
	fi
}

run_and_capture "plain uCore research platform" "${plain_log}" \
	make plain-platform-run "TOOLPREFIX=${TOOLPREFIX}"

require_log "${plain_log}" "rp_orch: passed" "plain rp_orch passed marker"
require_log "${plain_log}" "rp_backend: cases=7 executable=7 userland_equivalent=ready" "plain 7-case user-space backend marker"
reject_log "${plain_log}" "child_failed|IllegalInstruction|unknown syscall|bad addr|rp_orch: failed|status=failed" "plain platform failure marker"

run_and_capture "AgentOS-uCore research platform" "${agentos_log}" \
	make agentos-platform-run "TOOLPREFIX=${TOOLPREFIX}"

require_log "${agentos_log}" "rp_agentos_orch: passed" "AgentOS rp_agentos_orch passed marker"
require_log "${agentos_log}" "rp_backend: cases=7 executable=7 agentos=mainflow_bound" "AgentOS 7-case kernel-bound backend marker"
reject_log "${agentos_log}" "child_failed|IllegalInstruction|unknown syscall|bad addr|rp_orch: failed|status=failed" "AgentOS platform failure marker"

echo "[dual-platform] plain and AgentOS platforms both passed"
echo "[dual-platform] plain backend: userland_equivalent=ready"
echo "[dual-platform] AgentOS backend: agentos=mainflow_bound"
