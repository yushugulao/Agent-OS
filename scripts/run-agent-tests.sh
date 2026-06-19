#!/usr/bin/env bash
set -euo pipefail

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
LOG="${LOG:-error}"
CHAPTER="${CHAPTER:-agent}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"

run_case() {
	local init_proc="$1"
	local marker="$2"
	local log_file="/tmp/agentos-${init_proc}.log"

	echo "[agent-tests] running ${init_proc}"
	timeout "${CASE_TIMEOUT}" make run \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}" | tee "${log_file}"
	grep -q "${marker}" "${log_file}"
	if grep -Eq "check failed|panic|unknown syscall" "${log_file}"; then
		echo "[agent-tests] ${init_proc} log contains failure text" >&2
		return 1
	fi
	echo "[agent-tests] ${init_proc} passed"
}

make -C user clean
make clean
make user nfs/fs.img TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}"
make build TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore

run_case agentfinal_ucore "agentfinal_ucore: parent passed"
run_case agentbench_ucore "agentbench_ucore: parent passed"
run_case labdemo_ucore "labdemo_ucore: parent passed"
run_case agentsecurity_ucore "agentsecurity_ucore: parent passed"

echo "[agent-tests] all Agent-OS uCore checks passed"
