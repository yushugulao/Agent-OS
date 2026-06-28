#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DUAL_LOG_DIR="${DUAL_LOG_DIR:-/tmp/agentos-dual-platform}"

mkdir -p "${DUAL_LOG_DIR}"

echo "[dual-platform] checking target structure"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"

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

platform_program_count() {
	local makefile="$1"
	local tests
	local count=0
	local app

	tests="$(grep '^PLATFORM_TESTS :=' "${makefile}" | sed 's/^PLATFORM_TESTS :=//')"
	for app in ${tests}; do
		case "${app}" in
		rp_orch) ;;
		rp_*) count=$((count + 1)) ;;
		esac
	done
	echo "${count}"
}

expected_programs="$(platform_program_count "${ROOT_DIR}/user/Makefile")"

seeded_work_dir="${DUAL_LOG_DIR}/seeded-action-state"
seeded_summary="${DUAL_LOG_DIR}/seeded-action-state.json"

echo "[dual-platform] running seeded dual-target research platform"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_seeded_action_state.py" \
	--work-dir "${seeded_work_dir}" \
	--timeout "${SEEDED_ACTION_TIMEOUT:-300}" \
	--json-out "${seeded_summary}"

plain_log="${seeded_work_dir}/plain/ucore-run.log"
agentos_log="${seeded_work_dir}/agentos/ucore-run.log"
plain_state_src="${seeded_work_dir}/plain/state-extracted"
agentos_state_src="${seeded_work_dir}/agentos/state-extracted"

echo "[dual-platform] plain uCore research platform log: ${plain_log}"
require_log "${plain_log}" "rp_orch: passed" "plain rp_orch passed marker"
require_log "${plain_log}" "rp_orch: programs_ok=${expected_programs} programs_total=${expected_programs}" "plain complete program count"
require_log "${plain_log}" "rp_compare_plain: plain_kernel=passed .*programs=${expected_programs} .*status=ready" "plain compare summary"
require_log "${plain_log}" "rp_backend: cases=7 executable=7 userland_equivalent=ready" "plain 7-case user-space backend marker"
reject_log "${plain_log}" "child_failed|IllegalInstruction|unknown syscall|bad addr|rp_orch: failed|status=failed" "plain platform failure marker"

echo "[dual-platform] AgentOS-uCore research platform log: ${agentos_log}"
require_log "${agentos_log}" "rp_agentos_orch: passed" "AgentOS rp_agentos_orch passed marker"
require_log "${agentos_log}" "rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready" "AgentOS kernel-backed orchestrator marker"
require_log "${agentos_log}" "rp_orch: programs_ok=${expected_programs} programs_total=${expected_programs}" "AgentOS complete program count"
require_log "${agentos_log}" "rp_compare_plain: plain_kernel=passed .*programs=${expected_programs} .*status=ready" "AgentOS compare summary"
require_log "${agentos_log}" "rp_backend: cases=8 executable=8 agentos=mainflow_bound" "AgentOS 8-case kernel-bound backend marker"
reject_log "${agentos_log}" "child_failed|IllegalInstruction|unknown syscall|bad addr|rp_orch: failed|status=failed" "AgentOS platform failure marker"

rm -rf "${DUAL_LOG_DIR}/plain-state" "${DUAL_LOG_DIR}/agentos-state"
cp -a "${plain_state_src}" "${DUAL_LOG_DIR}/plain-state"
cp -a "${agentos_state_src}" "${DUAL_LOG_DIR}/agentos-state"

plain_count="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["extracted_state_files"])' "${DUAL_LOG_DIR}/plain-state/extract-summary.json")"
agentos_count="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["extracted_state_files"])' "${DUAL_LOG_DIR}/agentos-state/extract-summary.json")"
if [ "${plain_count}" -lt 240 ]; then
	echo "[dual-platform] plain extracted too few state files: ${plain_count} < 240" >&2
	exit 1
fi
if [ "${agentos_count}" -lt 240 ]; then
	echo "[dual-platform] AgentOS extracted too few state files: ${agentos_count} < 240" >&2
	exit 1
fi
for file in rp_backend rp_agentcmp rp_web_bundle rp_api_compare rp_package rp_review_dashboard; do
	if [ ! -f "${DUAL_LOG_DIR}/plain-state/${file}" ]; then
		echo "[dual-platform] plain extracted state is missing ${file}" >&2
		exit 1
	fi
done
for file in rp_backend rp_agentcmp rp_agentos_kernel rp_agentos_mainflow rp_agentos_conflict rp_web_bundle rp_api_compare; do
	if [ ! -f "${DUAL_LOG_DIR}/agentos-state/${file}" ]; then
		echo "[dual-platform] AgentOS extracted state is missing ${file}" >&2
		exit 1
	fi
done
echo "[dual-platform] plain extracted state files: ${plain_count}"
echo "[dual-platform] AgentOS extracted state files: ${agentos_count}"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_platform_alignment.py" \
	--plain-state-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-state-dir "${DUAL_LOG_DIR}/agentos-state" \
	--json-out "${DUAL_LOG_DIR}/host-platform-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_action_kind_alignment.py" \
	--json-out "${DUAL_LOG_DIR}/host-action-kind-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_test_alignment.py" \
	--json-out "${DUAL_LOG_DIR}/host-test-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_surface_alignment.py" \
	--plain-state-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-state-dir "${DUAL_LOG_DIR}/agentos-state" \
	--json-out "${DUAL_LOG_DIR}/host-surface-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/compare_dual_platform_state.py" \
	--plain-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-dir "${DUAL_LOG_DIR}/agentos-state" \
	--min-common-files 240 \
	--json-out "${DUAL_LOG_DIR}/state-compare-summary.json"

rm -rf "${DUAL_LOG_DIR}/plain-reader" "${DUAL_LOG_DIR}/agentos-reader"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/plain_ucore_reader.py" \
	--state-dir "${DUAL_LOG_DIR}/plain-state" \
	--out-dir "${DUAL_LOG_DIR}/plain-reader" \
	--host-platform-alignment "${DUAL_LOG_DIR}/host-platform-alignment.json" \
	--host-test-alignment "${DUAL_LOG_DIR}/host-test-alignment.json" \
	--host-surface-alignment "${DUAL_LOG_DIR}/host-surface-alignment.json" \
	--seeded-action-state "${DUAL_LOG_DIR}/seeded-action-state.json"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/plain_ucore_reader.py" \
	--state-dir "${DUAL_LOG_DIR}/agentos-state" \
	--out-dir "${DUAL_LOG_DIR}/agentos-reader" \
	--host-platform-alignment "${DUAL_LOG_DIR}/host-platform-alignment.json" \
	--host-test-alignment "${DUAL_LOG_DIR}/host-test-alignment.json" \
	--host-surface-alignment "${DUAL_LOG_DIR}/host-surface-alignment.json" \
	--seeded-action-state "${DUAL_LOG_DIR}/seeded-action-state.json"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_reader_output.py" \
	--reader-dir "${DUAL_LOG_DIR}/plain-reader" \
	--json-out "${DUAL_LOG_DIR}/plain-reader-check.json"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_reader_output.py" \
	--reader-dir "${DUAL_LOG_DIR}/agentos-reader" \
	--json-out "${DUAL_LOG_DIR}/agentos-reader-check.json"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/compare_dual_platform_reader.py" \
	--plain-summary "${DUAL_LOG_DIR}/plain-reader/reader-summary.json" \
	--agentos-summary "${DUAL_LOG_DIR}/agentos-reader/reader-summary.json" \
	--json-out "${DUAL_LOG_DIR}/reader-compare-summary.json"

echo "[dual-platform] plain and AgentOS platforms both passed"
echo "[dual-platform] research platform programs: ${expected_programs}"
echo "[dual-platform] plain backend: userland_equivalent=ready"
echo "[dual-platform] AgentOS backend: cases=8 agentos=mainflow_bound"
