#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
CASE_TIMEOUT="${CASE_TIMEOUT:-240s}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-2s}"
MECHANISM_MARKER_GRACE_SECONDS="${MECHANISM_MARKER_GRACE_SECONDS:-5s}"

source "${ROOT_DIR}/scripts/evidence-wiring.sh"
evidence_initialize

evidence_step_begin() {
	evidence_enabled && EVIDENCE_STEP_START="$(date +%s.%N)"
	return 0
}

evidence_step_end() {
	local name="$1" ended
	shift
	evidence_enabled || return 0
	ended="$(date +%s.%N)"
	printf '%s\t%s\t%s' "${name}" "${EVIDENCE_STEP_START}" "${ended}" \
		>>"${EVIDENCE_STEPS_FILE}"
	if [[ $# -gt 0 ]]; then
		printf '\t%s' "$@" >>"${EVIDENCE_STEPS_FILE}"
	fi
	printf '\n' >>"${EVIDENCE_STEPS_FILE}"
}

run_resource_regression() {
	local label="$1"
	local filename="$2"
	local runner="$3"
	shift 3
	local common_env=(
		TOOLPREFIX="${TOOLPREFIX}"
		QEMU="${QEMU}"
		PYTHON_BIN="${PYTHON_BIN}"
		CASE_TIMEOUT="${CASE_TIMEOUT}"
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}"
		MARKER_GRACE_SECONDS="${MECHANISM_MARKER_GRACE_SECONDS}"
	)

	if evidence_enabled; then
		local stdout_file="${EVIDENCE_WORK_DIR}/${label}.stdout"
		local guest_file="${EVIDENCE_WORK_DIR}/${label}.guest"
		local combined_file="${EVIDENCE_WORK_DIR}/${label}.combined"
		: >"${guest_file}"
		evidence_capture "${stdout_file}" env \
			EVIDENCE_GUEST_LOG_FILE="${guest_file}" \
			"${common_env[@]}" "$@" bash "${runner}"
		[[ -s "${guest_file}" ]]
		{
			printf '===== runner-stdout:%s =====\n' "${label}"
			cat "${stdout_file}"
			printf '\n===== runner-guest-logs:%s =====\n' "${label}"
			cat "${guest_file}"
		} >"${combined_file}"
		evidence_publish_file "${combined_file}" "${filename}"
	else
		env "${common_env[@]}" "$@" bash "${runner}"
	fi
}

evidence_step_begin
echo "[full-verify] target structure"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"
evidence_step_end "target-structure"

evidence_step_begin
echo "[full-verify] kernel growth budgets"
(
	cd "${ROOT_DIR}"
	make ci-check \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG=warn \
		INIT_PROC=agentfinal_ucore \
		CHAPTER=agent
)
evidence_step_end "kernel-budgets"

evidence_step_begin
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
	if evidence_enabled; then
		reader_raw_dir="${EVIDENCE_WORK_DIR}/reader-e2e-raw"
		reader_artifact_list="${EVIDENCE_WORK_DIR}/reader-e2e-artifacts.txt"
		: >"${reader_artifact_list}"
		evidence_capture_stdout \
			"reader-e2e.log" \
			env PLAIN_UCORE_READER_E2E_LOG_DIR="${reader_raw_dir}" \
			"${PYTHON_BIN}" host_tools/test_plain_ucore_reader_e2e.py
		printf '%s\n' "reader-e2e.log" >>"${reader_artifact_list}"
		[[ -s "${reader_raw_dir}/reader-e2e-log-manifest.json" ]]
		evidence_publish_file "${reader_raw_dir}/reader-e2e-log-manifest.json" \
			"reader-e2e-log-manifest.json"
		printf '%s\n' "reader-e2e-log-manifest.json" >>"${reader_artifact_list}"
		[[ -z "$(find "${reader_raw_dir}" -type l -print -quit)" ]]
		while IFS= read -r -d '' reader_log; do
			relative="${reader_log#"${reader_raw_dir}/"}"
			artifact="reader-e2e-${relative//\//-}"
			evidence_publish_file "${reader_log}" "${artifact}"
			printf '%s\n' "${artifact}" >>"${reader_artifact_list}"
		done < <(find "${reader_raw_dir}" -mindepth 2 -maxdepth 2 -type f -print0 | sort -z)
		[[ "$(wc -l <"${reader_artifact_list}")" -gt 2 ]]
	else
		"${PYTHON_BIN}" host_tools/test_plain_ucore_reader_e2e.py
	fi
)
if evidence_enabled; then
	mapfile -t reader_artifacts <"${EVIDENCE_WORK_DIR}/reader-e2e-artifacts.txt"
	evidence_step_end "reader-e2e" "${reader_artifacts[@]}"
else
	evidence_step_end "reader-e2e"
fi

evidence_step_begin
echo "[full-verify] host platform alignment"
(
	cd "${ROOT_DIR}"
	"${PYTHON_BIN}" host_tools/check_host_platform_alignment.py
	"${PYTHON_BIN}" host_tools/check_host_action_kind_alignment.py
	"${PYTHON_BIN}" host_tools/check_host_surface_alignment.py
	"${PYTHON_BIN}" host_tools/check_host_test_alignment.py
)
evidence_step_end "host-platform-alignment"

evidence_step_begin
echo "[full-verify] dual platforms"
(
	cd "${ROOT_DIR}"
	if evidence_enabled; then
		dual_dir="${EVIDENCE_WORK_DIR}/dual"
		result_dir="${EVIDENCE_WORK_DIR}/result"
		DUAL_LOG_DIR="${dual_dir}" RESULT_DIR="${result_dir}" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" bash scripts/run-dual-platforms.sh
		evidence_publish_file \
			"${dual_dir}/seeded-action-state/plain/ucore-run.log" \
			"dual-plain-qemu.log"
		evidence_publish_file \
			"${dual_dir}/seeded-action-state/agentos/ucore-run.log" \
			"dual-agentos-qemu.log"
		evidence_publish_file \
			"${dual_dir}/stage-timings.csv" \
			"dual-stage-timings.csv"
		evidence_publish_file \
			"${dual_dir}/state-compare-summary.json" \
			"dual-state-compare.json"
		evidence_publish_file \
			"${dual_dir}/reader-compare-summary.json" \
			"dual-reader-compare.json"
	else
		TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" bash scripts/run-dual-platforms.sh
	fi
)
evidence_step_end "dual-platforms" \
	"dual-plain-qemu.log" "dual-agentos-qemu.log" \
	"dual-stage-timings.csv" "dual-state-compare.json" \
	"dual-reader-compare.json"

evidence_step_begin
echo "[full-verify] AgentOS kernel tests"
(
	cd "${ROOT_DIR}"
	if evidence_enabled; then
		timing_file="${EVIDENCE_WORK_DIR}/agent-suite-timings.log"
		guest_file="${EVIDENCE_WORK_DIR}/agent-suite-guest.log"
		: >"${guest_file}"
		env -u AGENT_TEST_CASE -u AGENT_TEST_TIMING_FILE \
			-u AGENT_TEST_GUEST_LOG_FILE \
			AGENT_TEST_CALIBRATE=0 REQUIRE_FULL_SUITE=1 \
			AGENT_TEST_TIMING_FILE="${timing_file}" \
			AGENT_TEST_GUEST_LOG_FILE="${guest_file}" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" \
			CASE_TIMEOUT="${CASE_TIMEOUT}" \
			IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
			MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
			bash scripts/run-agent-tests.sh
		evidence_publish_file \
			"${timing_file}" "agent-suite-timings.log"
		evidence_publish_file \
			"${guest_file}" "agent-suite-guest.log"
	else
		env -u AGENT_TEST_CASE -u AGENT_TEST_TIMING_FILE \
			AGENT_TEST_CALIBRATE=0 REQUIRE_FULL_SUITE=1 \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" \
			CASE_TIMEOUT="${CASE_TIMEOUT}" \
			IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
			MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
			bash scripts/run-agent-tests.sh
	fi
)
evidence_step_end "agent-suite" "agent-suite-timings.log" "agent-suite-guest.log"

evidence_step_begin
echo "[full-verify] process reaper tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"proc-reap" "proc-reap.log" \
		"scripts/run-proc-reap-tests.sh"
)
evidence_step_end "proc-reap" "proc-reap.log"

evidence_step_begin
echo "[full-verify] syscall fairness tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"syscall-fairness" "syscall-fairness.log" \
		"scripts/run-syscall-fairness-tests.sh"
)
evidence_step_end "syscall-fairness" "syscall-fairness.log"

evidence_step_begin
echo "[full-verify] file resource tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"file-resource" "file-resource.log" \
		"scripts/run-file-resource-tests.sh"
)
evidence_step_end "file-resource" "file-resource.log"

evidence_step_begin
echo "[full-verify] thread resource tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"thread-resource" "thread-resource.log" \
		"scripts/run-thread-resource-tests.sh"
)
evidence_step_end "thread-resource" "thread-resource.log"

evidence_step_begin
echo "[full-verify] workflow teardown race tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"workflow-teardown-race" "workflow-teardown-race.log" \
		"scripts/run-workflow-teardown-race-tests.sh" \
		WORKFLOW_TEARDOWN_STABILITY_RUNS=3
)
evidence_step_end "workflow-teardown-race" "workflow-teardown-race.log"

evidence_step_begin
echo "[full-verify] filesystem ENOSPC tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"fs-enospc" "fs-enospc.log" \
		"scripts/run-fs-enospc-tests.sh"
)
evidence_step_end "fs-enospc" "fs-enospc.log"

if evidence_enabled; then
	"${PYTHON_BIN}" "${ROOT_DIR}/scripts/capture-final-evidence.py" write-summary \
		--stage "${FINAL_EVIDENCE_STAGE}" \
		--steps "${EVIDENCE_STEPS_FILE}" \
		--commit "$(git -C "${ROOT_DIR}" rev-parse HEAD)" \
		--agent-grace "${MARKER_GRACE_SECONDS}" \
		--mechanism-grace "${MECHANISM_MARKER_GRACE_SECONDS}" \
		--workflow-runs 3
fi
echo "[full-verify] all checks passed"
