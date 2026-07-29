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

echo "[full-verify] Agent duration calibration policy"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check-kernel-budgets.py" \
	--check agent-test-policy \
	--config "${ROOT_DIR}/ci/kernel-budgets.json"

agent_suite_guest_log=""
agent_suite_guest_log_owned=0
cleanup_full_verify() {
	if [[ "${agent_suite_guest_log_owned}" == "1" &&
	      -n "${agent_suite_guest_log}" ]]; then
		rm -f "${agent_suite_guest_log}"
	fi
}
trap cleanup_full_verify EXIT

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
		PYTHON_BIN="${PYTHON_BIN}" \
		LOG=warn \
		INIT_PROC=agentfinal_ucore \
		CHAPTER=agent
)
evidence_step_end "kernel-budgets"

evidence_step_begin
echo "[full-verify] 本地结果阅读器"
(
	cd "${ROOT_DIR}"
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

if evidence_enabled; then
	agent_suite_guest_log="${EVIDENCE_WORK_DIR}/agent-suite-guest.log"
else
	agent_suite_guest_log="${TMPDIR:-/tmp}/agent-suite-guest.$$"
	agent_suite_guest_log_owned=1
fi
evidence_step_begin
echo "[full-verify] AgentOS kernel tests"
(
	cd "${ROOT_DIR}"
	if evidence_enabled; then
		timing_file="${EVIDENCE_WORK_DIR}/agent-suite-timings.log"
		: >"${agent_suite_guest_log}"
		env -u AGENT_TEST_CASE -u AGENT_TEST_TIMING_FILE \
			-u AGENT_TEST_GUEST_LOG_FILE \
			AGENT_TEST_CALIBRATE=0 REQUIRE_FULL_SUITE=1 \
			AGENT_TEST_TIMING_FILE="${timing_file}" \
			AGENT_TEST_GUEST_LOG_FILE="${agent_suite_guest_log}" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" \
			CASE_TIMEOUT="${CASE_TIMEOUT}" \
			IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
			MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
			bash scripts/run-agent-tests.sh
		evidence_publish_file \
			"${timing_file}" "agent-suite-timings.log"
		evidence_publish_file \
			"${agent_suite_guest_log}" "agent-suite-guest.log"
	else
		: >"${agent_suite_guest_log}"
		env -u AGENT_TEST_CASE -u AGENT_TEST_TIMING_FILE \
			AGENT_TEST_CALIBRATE=0 REQUIRE_FULL_SUITE=1 \
			AGENT_TEST_GUEST_LOG_FILE="${agent_suite_guest_log}" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" \
			CASE_TIMEOUT="${CASE_TIMEOUT}" \
			IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
			MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
			bash scripts/run-agent-tests.sh
	fi
)
evidence_step_end "agent-suite" "agent-suite-timings.log" "agent-suite-guest.log"

mapfile -t mainflow_artifact_specs < <(
	PYTHONPATH="${ROOT_DIR}/host_tools" "${PYTHON_BIN}" -c '
from dual_state_evidence_contract import (
    BACKEND_REPORT_ARTIFACTS,
    MAIN_FLOW_SOURCE_ARTIFACTS, MAIN_FLOW_SOURCE_SPECS,
    MAIN_FLOW_TELEMETRY_ARTIFACT, PROGRAM_LEDGER_ARTIFACTS,
    RUN_RESULT_ARTIFACTS, RUN_RESULT_WORK_FILES, SEEDED_ACTION_SUMMARY_ARTIFACT,
    STATE_ARCHIVE_ARTIFACTS,
)
print("agentos-state/rp_agentos_mainflow\t" + MAIN_FLOW_TELEMETRY_ARTIFACT)
for spec in MAIN_FLOW_SOURCE_SPECS:
    print("agentos-state/" + spec.source + "\t" + MAIN_FLOW_SOURCE_ARTIFACTS[spec.source])
print("plain-state/rp_orch_timing\t" + PROGRAM_LEDGER_ARTIFACTS["plain"])
print("agentos-state/rp_orch_timing\t" + PROGRAM_LEDGER_ARTIFACTS["agentos"])
print("plain-state/rp_backend_exec\t" + BACKEND_REPORT_ARTIFACTS["plain"])
print("agentos-state/rp_backend_exec\t" + BACKEND_REPORT_ARTIFACTS["agentos"])
print(RUN_RESULT_WORK_FILES["plain"] + "\t" + RUN_RESULT_ARTIFACTS["plain"])
print(RUN_RESULT_WORK_FILES["agentos"] + "\t" + RUN_RESULT_ARTIFACTS["agentos"])
print("seeded-action-state.json\t" + SEEDED_ACTION_SUMMARY_ARTIFACT)
print("plain-complete-state.zip\t" + STATE_ARCHIVE_ARTIFACTS["plain"])
print("agentos-complete-state.zip\t" + STATE_ARCHIVE_ARTIFACTS["agentos"])
'
)
mainflow_artifacts=()
for pair in "${mainflow_artifact_specs[@]}"; do
	mainflow_artifacts+=("${pair#*$'\t'}")
done

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
		PYTHONPATH="${ROOT_DIR}/host_tools" "${PYTHON_BIN}" \
			"${ROOT_DIR}/host_tools/dual_state_archive.py" \
			--state-dir "${dual_dir}/plain-state" \
			--output "${dual_dir}/plain-complete-state.zip"
		PYTHONPATH="${ROOT_DIR}/host_tools" "${PYTHON_BIN}" \
			"${ROOT_DIR}/host_tools/dual_state_archive.py" \
			--state-dir "${dual_dir}/agentos-state" \
			--output "${dual_dir}/agentos-complete-state.zip"
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
		evidence_publish_file \
			"${dual_dir}/host-platform-alignment.json" \
			"host-platform-alignment.json"
		for pair in "${mainflow_artifact_specs[@]}"; do
			source="${pair%%$'\t'*}"
			artifact="${pair#*$'\t'}"
			evidence_publish_file \
				"${dual_dir}/${source}" "${artifact}"
		done
		evidence_publish_file \
			"${result_dir}/experiments/dual-targeted-agentbench-guest.log" \
			"dual-targeted-agentbench-guest.log"
		evidence_publish_file \
			"${result_dir}/experiments/measured-experiments.json" \
			"dual-measured-experiments.json"
		evidence_publish_file \
			"${result_dir}/experiments/raw/file-query-benchmark.csv" \
			"dual-file-query-benchmark.csv"
	else
		TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" bash scripts/run-dual-platforms.sh
	fi
)
evidence_step_end "dual-platforms" \
	"dual-plain-qemu.log" "dual-agentos-qemu.log" \
	"dual-stage-timings.csv" "dual-state-compare.json" \
	"dual-reader-compare.json" "host-platform-alignment.json" \
	"${mainflow_artifacts[@]}" "dual-targeted-agentbench-guest.log" \
	"dual-measured-experiments.json" "dual-file-query-benchmark.csv"

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
echo "[full-verify] physical memory resource tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"physical-resource" "physical-resource.log" \
		"scripts/run-physical-resource-tests.sh"
)
evidence_step_end "physical-resource" "physical-resource.log"

evidence_step_begin
echo "[full-verify] metadata crash recovery tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"metadata-recovery" "metadata-recovery.log" \
		"scripts/run-metadata-recovery-tests.sh"
)
evidence_step_end "metadata-recovery" "metadata-recovery.log"

evidence_step_begin
echo "[full-verify] observation durability recovery tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"observe-recovery" "observe-recovery.log" \
		"scripts/run-observe-recovery-tests.sh"
)
evidence_step_end "observe-recovery" "observe-recovery.log" \
	"observe-recovery-before-reap.img"

evidence_step_begin
echo "[full-verify] VirtIO disk fault tests"
(
	cd "${ROOT_DIR}"
	run_resource_regression \
		"virtio-disk" "virtio-disk.log" \
		"scripts/run-virtio-disk-tests.sh"
)
evidence_step_end "virtio-disk" "virtio-disk.log"

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

evidence_step_begin
echo "[full-verify] filesystem allocator consistency fault tests"
(
	cd "${ROOT_DIR}"
	if evidence_enabled; then
		fs_allocator_dir="${EVIDENCE_WORK_DIR}/fs-allocator-evidence"
		fs_allocator_archive="${EVIDENCE_WORK_DIR}/fs-allocator-evidence.tar"
		run_resource_regression \
			"fs-allocator-fault" "fs-allocator-fault.log" \
			"scripts/run-fs-allocator-fault-tests.sh" \
			FS_ALLOCATOR_ARTIFACT_DIR="${fs_allocator_dir}" \
			FS_ALLOCATOR_EVIDENCE_ARCHIVE="${fs_allocator_archive}"
		"${PYTHON_BIN}" scripts/fs-allocator-evidence.py verify-archive \
			--archive "${fs_allocator_archive}"
		evidence_publish_file "${fs_allocator_archive}" \
			"fs-allocator-evidence.tar"
	else
		run_resource_regression \
			"fs-allocator-fault" "fs-allocator-fault.log" \
			"scripts/run-fs-allocator-fault-tests.sh"
	fi
)
if evidence_enabled; then
	evidence_step_end "fs-allocator-fault" \
		"fs-allocator-fault.log" "fs-allocator-evidence.tar"
else
	evidence_step_end "fs-allocator-fault"
fi

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
