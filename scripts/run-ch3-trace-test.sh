#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
AGENTOS_BUILD_JOBS="${AGENTOS_BUILD_JOBS:-$(
	"${PYTHON_BIN}" -I -S -B scripts/resource-jobs.py --kind build
)}"
OUTPUT_DIR="${CH3_TRACE_OUTPUT_DIR:-${ROOT_DIR}/build/ch3-trace}"
TMPDIR_CH3="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_CH3}"' EXIT

if [[ ! "${AGENTOS_BUILD_JOBS}" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
	echo "[ch3-trace] AGENTOS_BUILD_JOBS 必须是 1 到 24 的整数" >&2
	exit 2
fi

mkdir -p "${OUTPUT_DIR}"
rm -f "${OUTPUT_DIR}/guest.log"

# 清除外层 make 的 jobserver 状态，再按本机资源分配编译任务。
run_make() {
	env -u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES -u GNUMAKEFLAGS -u MAKEFILES \
		make -j"${AGENTOS_BUILD_JOBS}" "$@"
}

source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_CH3}"

run_make -C user \
	TOOLPREFIX="${TOOLPREFIX}" CHAPTER=3 \
	build_dir="${TMPDIR_CH3}/user-build" \
	out_dir="${TMPDIR_CH3}/user-target" \
	asm_dir="${TMPDIR_CH3}/user-asm"
host_probe_compile "${TMPDIR_CH3}/mkfs" \
	nfs/fs.c nfs/host_image_snapshot.c
host_probe_run "${TMPDIR_CH3}/mkfs" "${TMPDIR_CH3}/master.img" \
	"${TMPDIR_CH3}/user-target/bin/ch3_trace"

# 内核和磁盘都使用本用例的私有副本，QEMU 不进入并行 lane。
run_make -B build \
	TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
	BUILDDIR="${TMPDIR_CH3}/kernel-build" \
	LOG=error INIT_PROC=ch3_trace CHAPTER=3
cp "${TMPDIR_CH3}/master.img" "${TMPDIR_CH3}/run.img"

"${PYTHON_BIN}" scripts/agent_test_runner.py \
	--init-proc ch3_trace \
	--marker "Test trace OK!" \
	--marker-mode exact-line \
	--log-file "${OUTPUT_DIR}/guest.log" \
	--case-timeout "${CASE_TIMEOUT}" \
	--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
	--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
	--qemu "${QEMU}" \
	--kernel "${TMPDIR_CH3}/kernel-build/kernel" \
	--image "${TMPDIR_CH3}/run.img"

if ! grep -Fxq "string from task trace test" "${OUTPUT_DIR}/guest.log"; then
	echo "[ch3-trace] 缺少 trace 前置写路径输出" >&2
	exit 65
fi

marker_count="$(grep -Fxc "Test trace OK!" "${OUTPUT_DIR}/guest.log" || true)"
if [[ "${marker_count}" != "1" ]]; then
	echo "[ch3-trace] 完成标记数量异常: ${marker_count}" >&2
	exit 65
fi

echo "[ch3-trace] dynamic syscall compatibility passed"
host_probe_report "ch3 trace mkfs"
