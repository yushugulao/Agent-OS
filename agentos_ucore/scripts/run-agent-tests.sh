#!/usr/bin/env bash
set -euo pipefail

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
LOG="${LOG:-error}"
CHAPTER="${CHAPTER:-agent}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
QEMU="${QEMU:-qemu-system-riscv64}"

run_case() {
	local init_proc="$1"
	local marker="$2"
	local log_file="/tmp/agentos-${init_proc}.log"

	echo "[agent-tests] running ${init_proc}"
	rm -f nfs/fs-copy.img os/initproc.S build/os/initproc.o
	make build \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}"
	cp nfs/fs.img nfs/fs-copy.img
	timeout "${CASE_TIMEOUT}" "${QEMU}" \
		-nographic \
		-machine virt \
		-bios default \
		-kernel build/kernel \
		-drive file=nfs/fs-copy.img,if=none,format=raw,id=x0 \
		-device virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0 | tee "${log_file}"
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

echo "[agent-tests] all Agent-OS uCore checks passed"
