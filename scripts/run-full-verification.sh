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
HOST_CC="${HOST_CC:-${HOSTCC:-${CC:-cc}}}"
adaptive_jobs() {
	"${PYTHON_BIN}" -I -S -B "${ROOT_DIR}/scripts/resource-jobs.py" --kind "$1"
}
AGENTOS_BUILD_JOBS="${AGENTOS_BUILD_JOBS:-$(adaptive_jobs build)}"
AGENTOS_TEST_JOBS="${AGENTOS_TEST_JOBS:-$(adaptive_jobs host)}"
AGENTOS_QEMU_JOBS="${AGENTOS_QEMU_JOBS:-$(adaptive_jobs qemu)}"
HOSTCC="${HOST_CC}"
CC="${HOST_CC}"
AGENT_TEST_DURATION_PROFILE="${AGENT_TEST_DURATION_PROFILE:-local-e3}"
for jobs in "${AGENTOS_BUILD_JOBS}" "${AGENTOS_TEST_JOBS}"; do
	if [[ ! "${jobs}" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
		echo "[full-verify] build/test jobs must be between 1 and 24" >&2
		exit 2
	fi
done
if [[ ! "${AGENTOS_QEMU_JOBS}" =~ ^([1-8])$ ]]; then
	echo "[full-verify] QEMU jobs must be between 1 and 8" >&2
	exit 2
fi
export HOST_CC HOSTCC CC AGENT_TEST_DURATION_PROFILE
export AGENTOS_BUILD_JOBS AGENTOS_TEST_JOBS AGENTOS_QEMU_JOBS

case "${AGENT_TEST_DURATION_PROFILE}" in
local-e3|none)
	;;
*)
	echo "[full-verify] AGENT_TEST_DURATION_PROFILE must be local-e3 or none" >&2
	exit 2
	;;
esac

if [[ "${AGENTOS_ALLOW_UNSANITIZED_HOST_PROBES:-0}" != "0" ]]; then
	echo "[full-verify] unsanitized host probes are forbidden" >&2
	exit 2
fi
export AGENTOS_ALLOW_UNSANITIZED_HOST_PROBES=0

source "${ROOT_DIR}/scripts/evidence-wiring.sh"
evidence_initialize

case "${AGENT_TEST_DURATION_PROFILE}" in
local-e3)
	echo "[full-verify] Agent duration policy profile=local-e3 status=enforced runner=serial"
	"${PYTHON_BIN}" "${ROOT_DIR}/scripts/check-kernel-budgets.py" \
		--check agent-test-policy \
		--config "${ROOT_DIR}/ci/kernel-budgets.json"
	;;
none)
	echo "[full-verify] Agent duration policy profile=none status=skipped-different-runner"
	;;
esac

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

evidence_step_begin
echo "[full-verify] target structure"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"
evidence_step_end "target-structure"

evidence_step_begin
echo "[full-verify] kernel growth budgets"
(
	cd "${ROOT_DIR}"
	make local-check \
		TOOLPREFIX="${TOOLPREFIX}" \
		PYTHON_BIN="${PYTHON_BIN}" \
		LOG=warn \
		INIT_PROC=agentfinal_ucore \
		CHAPTER=agent
)
evidence_step_end "kernel-budgets"

evidence_step_begin
echo "[full-verify] host platform alignment"
(
	cd "${ROOT_DIR}"
	"${PYTHON_BIN}" -I -S -B scripts/run-parallel-tests.py \
		--jobs "${AGENTOS_TEST_JOBS}" \
		--python "${PYTHON_BIN}" \
		host_tools/check_host_platform_alignment.py \
		host_tools/check_host_action_kind_alignment.py \
		host_tools/check_host_surface_alignment.py \
		host_tools/check_host_test_alignment.py
)
evidence_step_end "host-platform-alignment"

evidence_step_begin
echo "[full-verify] AgentOS kernel tests"
if [[ "${AGENT_TEST_DURATION_PROFILE}" == "local-e3" ]]; then
	if evidence_enabled; then
		agent_output="${EVIDENCE_WORK_DIR}/agent-serial"
	else
		agent_output="${ROOT_DIR}/build/agent-serial-$(date +%s)-$$"
	fi
	mkdir -p "${agent_output}"
	: >"${agent_output}/agent-suite-guest.log"
	(
		cd "${ROOT_DIR}"
		env -u AGENT_TEST_CASE -u AGENT_TEST_TIMING_FILE \
			-u AGENT_TEST_GUEST_LOG_FILE \
			AGENT_TEST_CALIBRATE=0 REQUIRE_FULL_SUITE=1 \
			AGENT_TEST_DURATION_PROFILE=local-e3 \
			AGENT_TEST_TIMING_FILE="${agent_output}/agent-suite-timings.log" \
			AGENT_TEST_GUEST_LOG_FILE="${agent_output}/agent-suite-guest.log" \
			TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
			PYTHON_BIN="${PYTHON_BIN}" CASE_TIMEOUT="${CASE_TIMEOUT}" \
			IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
			MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS}" \
			bash scripts/run-agent-tests.sh
	)
	if evidence_enabled; then
		evidence_publish_file "${agent_output}/agent-suite-timings.log" \
			"agent-suite-timings.log"
		evidence_publish_file "${agent_output}/agent-suite-guest.log" \
			"agent-suite-guest.log"
	fi
else
	if evidence_enabled; then
		parallel_agent_output="${EVIDENCE_WORK_DIR}/agent-qemu-lanes"
	else
		parallel_agent_output="${ROOT_DIR}/build/agent-qemu-lanes-$(date +%s)-$$"
	fi
	echo "[full-verify] AgentOS kernel test lanes=${AGENTOS_QEMU_JOBS}"
	"${PYTHON_BIN}" -I -S -B \
		"${ROOT_DIR}/scripts/run-parallel-qemu-regressions.py" \
		--root "${ROOT_DIR}" --output-dir "${parallel_agent_output}" \
		--suite agent --jobs "${AGENTOS_QEMU_JOBS}" \
		--build-jobs "${AGENTOS_BUILD_JOBS}"
	"${PYTHON_BIN}" -I -S -B \
		"${ROOT_DIR}/scripts/check-kernel-budgets.py" \
		--check agent-test-timing-inventory \
		--config "${ROOT_DIR}/ci/kernel-budgets.json" \
		--agent-test-timing-file \
		"${parallel_agent_output}/agent-suite-timings.log"
	if evidence_enabled; then
		evidence_verify_parallel_run "${parallel_agent_output}" agent
		evidence_import_parallel_agent_suite "${parallel_agent_output}"
	fi
fi
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
	"host-platform-alignment.json" \
	"${mainflow_artifacts[@]}" "dual-targeted-agentbench-guest.log" \
	"dual-measured-experiments.json" "dual-file-query-benchmark.csv"

resource_regression_cases=(
	proc-reap syscall-fairness file-resource thread-resource
	physical-resource metadata-recovery observe-recovery virtio-disk
	workflow-teardown-race fs-enospc fs-allocator-fault
)
resource_case_args=()
for case_name in "${resource_regression_cases[@]}"; do
	resource_case_args+=(--case "${case_name}")
done
if evidence_enabled; then
	parallel_resource_output="${EVIDENCE_WORK_DIR}/resource-qemu-lanes"
else
	parallel_resource_output="${ROOT_DIR}/build/qemu-regressions-$(date +%s)-$$"
fi
echo "[full-verify] resource regressions lanes=${AGENTOS_QEMU_JOBS}"
"${PYTHON_BIN}" -I -S -B \
	"${ROOT_DIR}/scripts/run-parallel-qemu-regressions.py" \
	--root "${ROOT_DIR}" \
	--output-dir "${parallel_resource_output}" \
	--jobs "${AGENTOS_QEMU_JOBS}" \
	--build-jobs "${AGENTOS_BUILD_JOBS}" \
	"${resource_case_args[@]}"
if evidence_enabled; then
	evidence_verify_parallel_run "${parallel_resource_output}" resource \
		"${resource_regression_cases[@]}"
	for case_name in "${resource_regression_cases[@]}"; do
		evidence_record_parallel_case \
			"${parallel_resource_output}" "${case_name}"
	done
fi

# This campaign is a hard acceptance gate. Its raw-image receipts remain in
# the full-verification log until the final evidence schema gains a dedicated
# fs_epoch artifact without changing an existing profile in place.
echo "[full-verify] filesystem ordered epoch power-cut tests"
(
	cd "${ROOT_DIR}"
	fs_epoch_jobs="${AGENTOS_QEMU_JOBS}"
	((fs_epoch_jobs > 3)) && fs_epoch_jobs=3
	env TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
		PYTHON_BIN="${PYTHON_BIN}" CASE_TIMEOUT="${CASE_TIMEOUT}" \
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS}" \
		FSEPOCH_QEMU_JOBS="${fs_epoch_jobs}" \
		bash scripts/run-fs-epoch-tests.sh
)

if evidence_enabled; then
	"${PYTHON_BIN}" -I -S "${ROOT_DIR}/scripts/trusted-python-entry.py" \
		"scripts/capture-final-evidence.py" write-summary \
		--stage "${FINAL_EVIDENCE_STAGE}" \
		--steps "${EVIDENCE_STEPS_FILE}" \
		--commit "$(git -C "${ROOT_DIR}" rev-parse HEAD)" \
		--agent-grace "${MARKER_GRACE_SECONDS}" \
		--mechanism-grace "${MECHANISM_MARKER_GRACE_SECONDS}" \
		--workflow-runs 3
fi
echo "[full-verify] all checks passed"
