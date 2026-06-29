#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
CASE_TIMEOUT="${CASE_TIMEOUT:-240s}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[full-verify] target structure"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"

echo "[full-verify] 本地结果阅读器"
(
	cd "${ROOT_DIR}"
	"${PYTHON_BIN}" host_tools/test_check_host_platform_alignment.py
	"${PYTHON_BIN}" host_tools/test_check_host_action_kind_alignment.py
	"${PYTHON_BIN}" host_tools/test_check_seeded_action_state.py
	"${PYTHON_BIN}" host_tools/test_check_host_surface_alignment.py
	"${PYTHON_BIN}" host_tools/test_check_host_test_alignment.py
	"${PYTHON_BIN}" host_tools/test_plain_ucore_action_runner.py
	"${PYTHON_BIN}" host_tools/test_plain_ucore_fs_extract.py
	"${PYTHON_BIN}" host_tools/test_plain_ucore_llm_relay.py
	"${PYTHON_BIN}" host_tools/test_llm_relay_mode_contract.py
	"${PYTHON_BIN}" host_tools/test_check_reader_output.py
	"${PYTHON_BIN}" host_tools/test_compare_dual_platform_reader.py
	"${PYTHON_BIN}" host_tools/test_compare_dual_platform_state.py
	"${PYTHON_BIN}" host_tools/test_summarize_dual_platform_results.py
	"${PYTHON_BIN}" host_tools/test_chart_svg_layout_contract.py
	"${PYTHON_BIN}" host_tools/test_plain_ucore_reader.py
	"${PYTHON_BIN}" host_tools/test_plain_ucore_reader_e2e.py
)

echo "[full-verify] host platform alignment"
(
	cd "${ROOT_DIR}"
	"${PYTHON_BIN}" host_tools/check_host_platform_alignment.py
	"${PYTHON_BIN}" host_tools/check_host_action_kind_alignment.py
	"${PYTHON_BIN}" host_tools/check_host_surface_alignment.py
	"${PYTHON_BIN}" host_tools/check_host_test_alignment.py
)

echo "[full-verify] dual platforms"
(
	cd "${ROOT_DIR}"
	TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" bash scripts/run-dual-platforms.sh
)

echo "[full-verify] AgentOS kernel tests"
(
	cd "${ROOT_DIR}"
	TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" CASE_TIMEOUT="${CASE_TIMEOUT}" bash scripts/run-agent-tests.sh
)

echo "[full-verify] all checks passed"
