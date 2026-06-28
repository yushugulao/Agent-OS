#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DUAL_LOG_DIR="${DUAL_LOG_DIR:-/tmp/agentos-dual-platform}"
STATE_DIR="${STATE_DIR:-${DUAL_LOG_DIR}/agentos-state}"
OUT_DIR="${OUT_DIR:-${DUAL_LOG_DIR}/agentos-reader-live}"
PORT="${PORT:-8767}"

if [ ! -d "${STATE_DIR}" ]; then
	echo "[demo-reader] 找不到状态目录：${STATE_DIR}" >&2
	echo "[demo-reader] 请先运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

if [ ! -f "${STATE_DIR}/rp_agentos_mainflow" ]; then
	echo "[demo-reader] AgentOS 状态不完整，缺少：${STATE_DIR}/rp_agentos_mainflow" >&2
	echo "[demo-reader] 请重新运行：make dual-platform-run TOOLPREFIX=riscv64-linux-gnu-" >&2
	exit 1
fi

mkdir -p "${OUT_DIR}"
echo "[demo-reader] 状态目录：${STATE_DIR}"
echo "[demo-reader] 输出目录：${OUT_DIR}"
echo "[demo-reader] 页面地址：http://127.0.0.1:${PORT}/"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/plain_ucore_reader.py" \
	--state-dir "${STATE_DIR}" \
	--out-dir "${OUT_DIR}" \
	--host-platform-alignment "${DUAL_LOG_DIR}/host-platform-alignment.json" \
	--host-test-alignment "${DUAL_LOG_DIR}/host-test-alignment.json" \
	--host-surface-alignment "${DUAL_LOG_DIR}/host-surface-alignment.json" \
	--seeded-action-state "${DUAL_LOG_DIR}/seeded-action-state.json" \
	--serve \
	--port "${PORT}"
