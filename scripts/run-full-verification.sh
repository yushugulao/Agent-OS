#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
CASE_TIMEOUT="${CASE_TIMEOUT:-240s}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BASH_BIN="${BASH_BIN:-bash}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-2s}"
MECHANISM_MARKER_GRACE_SECONDS="${MECHANISM_MARKER_GRACE_SECONDS:-5s}"
HOST_CC="${HOST_CC:-${HOSTCC:-${CC:-cc}}}"
runner_shell=("${BASH_BIN}" --noprofile --norc -p)

adaptive_jobs() {
	"${PYTHON_BIN}" -I -S -B "${ROOT_DIR}/scripts/resource-jobs.py" --kind "$1"
}

AGENTOS_BUILD_JOBS="${AGENTOS_BUILD_JOBS:-$(adaptive_jobs build)}"
AGENTOS_TEST_JOBS="${AGENTOS_TEST_JOBS:-$(adaptive_jobs host)}"
AGENTOS_QEMU_JOBS="${AGENTOS_QEMU_JOBS:-$(adaptive_jobs qemu)}"
HOSTCC="${HOST_CC}"
CC="${HOST_CC}"

for jobs in "${AGENTOS_BUILD_JOBS}" "${AGENTOS_TEST_JOBS}"; do
	if [[ ! "${jobs}" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
		echo "[full-verify] build/test jobs must be between 1 and 24" >&2
		exit 2
	fi
done
if [[ ! "${AGENTOS_QEMU_JOBS}" =~ ^([1-8])$ ]]; then
	echo "[full-verify] QEMU jobs must be between 1 and 8" >&2
	exit 2
fi

export TOOLPREFIX QEMU PYTHON_BIN HOST_CC HOSTCC CC
export AGENTOS_BUILD_JOBS AGENTOS_TEST_JOBS AGENTOS_QEMU_JOBS
export AGENTOS_ALLOW_UNSANITIZED_HOST_PROBES=0

trap 'echo "[full-verify] interrupted" >&2; exit 130' INT TERM

echo "[full-verify] dependency check"
(
	cd "${ROOT_DIR}"
	make doctor
)

echo "[full-verify] build, UAPI, stack, security and functional host tests"
(
	cd "${ROOT_DIR}"
	make local-check \
		TOOLPREFIX="${TOOLPREFIX}" \
		PYTHON_BIN="${PYTHON_BIN}" \
		HOST_CC="${HOST_CC}" \
		LOG=warn INIT_PROC=agentfinal_ucore CHAPTER=agent
)

echo "[full-verify] ch3 trace compatibility"
ch3_output="${ROOT_DIR}/build/ch3-trace"
(
	cd "${ROOT_DIR}"
	CH3_TRACE_OUTPUT_DIR="${ch3_output}" \
		CASE_TIMEOUT="${CASE_TIMEOUT}" \
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
		MARKER_GRACE_SECONDS="${MECHANISM_MARKER_GRACE_SECONDS}" \
		make ch3-trace-test \
			AGENTOS_BUILD_JOBS="${AGENTOS_BUILD_JOBS}" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" HOST_CC="${HOST_CC}"
)
"${PYTHON_BIN}" -I -S -B "${ROOT_DIR}/scripts/validate-kernel-test-log.py" \
	--log-file "${ch3_output}/guest.log" --tag ch3-trace --profile ch3-trace

echo "[full-verify] AgentOS kernel tests"
(
	cd "${ROOT_DIR}"
	env -u AGENT_TEST_CASE -u AGENT_TEST_TIMING_FILE \
		-u AGENT_TEST_GUEST_LOG_FILE \
		REQUIRE_FULL_SUITE=1 \
		TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
		PYTHON_BIN="${PYTHON_BIN}" CASE_TIMEOUT="${CASE_TIMEOUT}" \
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
		MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
		"${runner_shell[@]}" scripts/run-agent-tests.sh
)

echo "[full-verify] integrated Task 1-5 evaluation Guest"
(
	cd "${ROOT_DIR}"
	env AGENT_TEST_CASE=agenteval_ucore \
		TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
		PYTHON_BIN="${PYTHON_BIN}" CASE_TIMEOUT="${CASE_TIMEOUT}" \
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
		MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
		"${runner_shell[@]}" scripts/run-agent-tests.sh
)

resource_regression_targets=(
	proc-reap-test
	syscall-fairness-test
	file-resource-test
	thread-resource-test
	physical-resource-test
	virtio-disk-test
	workflow-teardown-race-test
	fs-enospc-test
	fs-allocator-fault-test
)
echo "[full-verify] resource regressions"
for target in "${resource_regression_targets[@]}"; do
	echo "[full-verify] ${target}"
	(
		cd "${ROOT_DIR}"
		make --no-print-directory "${target}" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" HOST_CC="${HOST_CC}"
	)
done

echo "[full-verify] filesystem ordered epoch power-cut tests"
(
	cd "${ROOT_DIR}"
	fs_epoch_jobs="${AGENTOS_QEMU_JOBS}"
	((fs_epoch_jobs > 3)) && fs_epoch_jobs=3
	env TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
		PYTHON_BIN="${PYTHON_BIN}" CASE_TIMEOUT="${CASE_TIMEOUT}" \
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
		FSEPOCH_QEMU_JOBS="${fs_epoch_jobs}" \
		"${runner_shell[@]}" scripts/run-fs-epoch-tests.sh
)

echo "[full-verify] configured build, QEMU, and resource checks passed"
