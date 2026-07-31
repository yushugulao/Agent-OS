#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_TOOL="${MAKE_TOOL:-make}"
CASE_TIMEOUT="${CONTEST_DEMO_CASE_TIMEOUT:-150s}"
OUTPUT_DIR="${CONTEST_DEMO_OUTPUT:-results/contest-demo}"

if [[ -L "${OUTPUT_DIR}" ]]; then
	echo "[contest-demo] output directory must not be a symlink" >&2
	exit 2
fi

# Bind every result to committed source. Generated build/results paths are
# ignored, so the same check remains valid after the Guest runs finish.
commit="$("${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py \
	host_tools/contest_demo.py identity --root .)"
run_id="$("${PYTHON_BIN}" -I -S -c \
	'import secrets; print(secrets.token_hex(8))')"
started_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import time; print(time.monotonic())')"

mkdir -p "${OUTPUT_DIR}"
rm -f "${OUTPUT_DIR}"/*-qemu.log "${OUTPUT_DIR}"/*-kernel \
	"${OUTPUT_DIR}"/*-fs.img "${OUTPUT_DIR}/summary.json" \
	"${OUTPUT_DIR}/report.md" "${OUTPUT_DIR}/index.html"

build_and_run() {
	local init_proc="$1"
	local label="$2"
	local log_file="${OUTPUT_DIR}/${label}-qemu.log"

	echo "[contest-demo] 构建隔离 Guest: ${init_proc}"
	rm -f nfs/fs.img nfs/fs-copy.img os/initproc.S build/os/initproc.o
	"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile nfs/fs.img \
		TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
		HOST_CC="${HOST_CC:-cc}" CHAPTER=agent \
		"CH_TESTS=${init_proc}" FUNCTIONAL_REVIEW_BUILD=1
	"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile build \
		TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
		LOG=error INIT_PROC="${init_proc}" CHAPTER=agent \
		FUNCTIONAL_REVIEW_BUILD=1
	cp build/kernel "${OUTPUT_DIR}/${label}-kernel"
	cp nfs/fs.img "${OUTPUT_DIR}/${label}-fs.img"
	cp nfs/fs.img nfs/fs-copy.img
	"${PYTHON_BIN}" -I -S scripts/agent_test_runner.py \
		--init-proc "${init_proc}" \
		--marker "${init_proc}: parent passed" \
		--marker-mode exact-line \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds 15 \
		--marker-grace-seconds 2s \
		--qemu "${QEMU}"
}

echo "[contest-demo] 1/5 清理并准备完全离线的真实 QEMU 演示"
"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile clean \
	FUNCTIONAL_REVIEW_BUILD=1

echo "[contest-demo] 2/5 动态验证任务 1-5"
build_and_run agentfinal_ucore functional

echo "[contest-demo] 3/5 采集真实 metadata 对照计时"
build_and_run agentbench_ucore benchmark

echo "[contest-demo] 4/5 动态运行任务 6 多 Agent 恢复场景"
build_and_run labdemo_ucore lab

finished_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import time; print(time.monotonic())')"
elapsed_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import sys; print(float(sys.argv[2]) - float(sys.argv[1]))' \
	"${started_seconds}" "${finished_seconds}")"

echo "[contest-demo] 5/5 从本轮原始 Guest 日志核验并生成离线报告"
"${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py \
	host_tools/contest_demo.py render \
	--source-root . \
	--functional-log "${OUTPUT_DIR}/functional-qemu.log" \
	--benchmark-log "${OUTPUT_DIR}/benchmark-qemu.log" \
	--lab-log "${OUTPUT_DIR}/lab-qemu.log" \
	--run-id "${run_id}" \
	--commit "${commit}" \
	--elapsed-seconds "${elapsed_seconds}" \
	--artifact "${OUTPUT_DIR}/functional-kernel" \
	--artifact "${OUTPUT_DIR}/functional-fs.img" \
	--artifact "${OUTPUT_DIR}/benchmark-kernel" \
	--artifact "${OUTPUT_DIR}/benchmark-fs.img" \
	--artifact "${OUTPUT_DIR}/lab-kernel" \
	--artifact "${OUTPUT_DIR}/lab-fs.img" \
	--output-dir "${OUTPUT_DIR}"
