#!/usr/bin/env bash
set -euo pipefail

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
LOG="${LOG:-error}"
CHAPTER="${CHAPTER:-agent}"

run_case() {
	local init_proc="$1"
	local marker="$2"
	local log_file="/tmp/agentos-${init_proc}.log"

	echo "[agent-tests] running ${init_proc}"
	timeout 90s make run \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}" | tee "${log_file}"
	grep -q "${marker}" "${log_file}"
	echo "[agent-tests] ${init_proc} passed"
}

make user nfs/fs.img TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}"
make build TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore

run_case agentfinal_ucore "agentfinal_ucore: passed"
run_case agentbench_ucore "agentbench_ucore: passed"
run_case labdemo_ucore "labdemo_ucore: passed"

echo "[agent-tests] all Agent-OS uCore checks passed"
