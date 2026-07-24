#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
LOG="${LOG:-error}"
CHAPTER="${CHAPTER:-agent}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-2}"
REQUIRE_FULL_SUITE="${REQUIRE_FULL_SUITE:-0}"
AGENT_TEST_CALIBRATE="${AGENT_TEST_CALIBRATE:-0}"
timing_file_owned=0
if [[ -z "${AGENT_TEST_TIMING_FILE:-}" ]]; then
	AGENT_TEST_TIMING_FILE="${TMPDIR:-/tmp}/agentos-agent-timings.$$"
	timing_file_owned=1
fi

cleanup() {
	if [[ "${timing_file_owned}" == "1" ]]; then
		rm -f "${AGENT_TEST_TIMING_FILE}"
	fi
}
trap cleanup EXIT

if [[ "${REQUIRE_FULL_SUITE}" != "0" && "${REQUIRE_FULL_SUITE}" != "1" ]]; then
	echo "[agent-tests] REQUIRE_FULL_SUITE must be 0 or 1" >&2
	exit 1
fi
if [[ "${REQUIRE_FULL_SUITE}" == "1" && -n "${AGENT_TEST_CASE:-}" ]]; then
	echo "[agent-tests] AGENT_TEST_CASE is forbidden for a required full suite" >&2
	exit 1
fi
if [[ "${AGENT_TEST_CALIBRATE}" != "0" && "${AGENT_TEST_CALIBRATE}" != "1" ]]; then
	echo "[agent-tests] AGENT_TEST_CALIBRATE must be 0 or 1" >&2
	exit 1
fi
if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
	if [[ "${REQUIRE_FULL_SUITE}" != "1" ]]; then
		echo "[agent-tests] calibration requires REQUIRE_FULL_SUITE=1" >&2
		exit 1
	fi
	if [[ "${timing_file_owned}" == "1" ]]; then
		echo "[agent-tests] calibration requires a persistent AGENT_TEST_TIMING_FILE" >&2
		exit 1
	fi
fi
: >"${AGENT_TEST_TIMING_FILE}"

check_suite_budget() {
	local calibration_args=()
	if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
		calibration_args+=(--agent-test-calibration)
	fi
	"${PYTHON_BIN}" scripts/check-kernel-budgets.py \
		--check agent-tests \
		--config ci/kernel-budgets.json \
		--agent-test-timing-file "${AGENT_TEST_TIMING_FILE}" \
		"${calibration_args[@]}"
}

run_case() {
	local init_proc="$1"
	local marker="$2"
	local expected_bad_addr_marker="${3:-}"
	local log_file="/tmp/agentos-${init_proc}.log"
	local expected_fault_args=()

	if [[ -n "${expected_bad_addr_marker}" ]]; then
		expected_fault_args+=(
			--expected-bad-addr-after "${expected_bad_addr_marker}"
		)
	fi

	echo "[agent-tests] running ${init_proc}"
	rm -f nfs/fs-copy.img os/initproc.S build/os/initproc.o
	make build \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}"
	cp nfs/fs.img nfs/fs-copy.img
	"${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "${init_proc}" \
		--marker "${marker}" \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" \
		--timing-file "${AGENT_TEST_TIMING_FILE}" \
		"${expected_fault_args[@]}"
	echo "[agent-tests] ${init_proc} passed"
}

make -C user clean
make clean
make user TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}"
make nfs/fs.img TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}"
make build TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore

if [[ -n "${AGENT_TEST_CASE:-}" ]]; then
	expected_bad_addr_marker=""
	if [[ "${AGENT_TEST_CASE}" == "iobudget_ucore" ]]; then
		expected_bad_addr_marker="iobudget_ucore: fault_exit_armed=1"
	fi
	run_case "${AGENT_TEST_CASE}" "${AGENT_TEST_CASE}: parent passed" \
		"${expected_bad_addr_marker}"
	echo "[agent-tests] full-suite duration budget skipped for targeted run"
	exit 0
fi

run_case agentfinal_ucore "agentfinal_ucore: parent passed"
run_case agentfs_ucore "agentfs_ucore: parent passed"
run_case agentscan_ucore "agentscan_ucore: parent passed"
run_case agentloop_ucore "agentloop_ucore: parent passed"
run_case agentsched_ucore "agentsched_ucore: parent passed"
run_case agentconflict_ucore "agentconflict_ucore: parent passed"
run_case agentllm_ucore "agentllm_ucore: parent passed"
run_case agentbench_ucore "agentbench_ucore: parent passed"
run_case labbench_ucore "labbench_ucore: parent passed"
run_case labdemo_ucore "labdemo_ucore: parent passed"
run_case agentsecurity_ucore "agentsecurity_ucore: parent passed"
run_case agentscope_ucore "agentscope_ucore: parent passed"
run_case agenttrust_ucore "agenttrust_ucore: parent passed"
run_case agentvfs_ucore "agentvfs_ucore: parent passed"
run_case iobudget_ucore "iobudget_ucore: parent passed" \
	"iobudget_ucore: fault_exit_armed=1"
run_case usersafety_ucore "usersafety_ucore: parent passed"

check_suite_budget
echo "[agent-tests] all Agent-OS uCore checks passed"
