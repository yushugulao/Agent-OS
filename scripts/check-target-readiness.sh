#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[target-readiness] target structure"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"

echo "[target-readiness] host contracts"
(
	cd "${ROOT_DIR}"
	"${PYTHON_BIN}" host_tools/test_check_host_platform_alignment.py
	"${PYTHON_BIN}" host_tools/test_check_host_action_kind_alignment.py
	"${PYTHON_BIN}" host_tools/test_check_seeded_action_state.py
	"${PYTHON_BIN}" host_tools/test_check_host_surface_alignment.py
	"${PYTHON_BIN}" host_tools/test_check_host_test_alignment.py
)

echo "[target-readiness] runtime comparison contracts"
(
	cd "${ROOT_DIR}"
	"${PYTHON_BIN}" host_tools/test_plain_ucore_action_runner.py
	"${PYTHON_BIN}" -m unittest discover -s . -p test_research_state_manifest.py
	"${PYTHON_BIN}" host_tools/test_plain_ucore_fs_extract.py
	"${PYTHON_BIN}" host_tools/test_compare_dual_platform_state.py
	"${PYTHON_BIN}" host_tools/test_backend_evidence_contract.py
	"${PYTHON_BIN}" host_tools/test_reference_catalog_contract.py
	"${PYTHON_BIN}" host_tools/test_measured_experiments.py
	"${PYTHON_BIN}" host_tools/test_dual_measurement_source_contract.py
	"${PYTHON_BIN}" host_tools/test_summarize_dual_platform_results.py
	"${PYTHON_BIN}" host_tools/test_result_bundle_contract.py
	"${PYTHON_BIN}" host_tools/test_chart_svg_layout_contract.py
)

echo "[target-readiness] quick target checks passed"
