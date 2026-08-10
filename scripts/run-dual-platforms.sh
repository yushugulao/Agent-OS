#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DUAL_LOG_DIR_REQUESTED="${DUAL_LOG_DIR:-}"
DUAL_LOG_DIR=""
BACKEND_CONTRACT="${ROOT_DIR}/host_tools/backend_evidence_contract.py"
export TOOLPREFIX QEMU PYTHON_BIN

current_stage=""
current_stage_start=0
measurement_guest_log=""
measurement_csv=""

cleanup_dual_run() {
	local code="$?"
	set +e
	trap - EXIT HUP INT TERM
	if [ "${code}" -ne 0 ] && [ -n "${current_stage:-}" ]; then
		stage_finish failed
	fi
	if [ "${code}" -ne 0 ] && [ -n "${DUAL_LOG_DIR:-}" ]; then
		echo "[dual-platform] failed run artifacts kept at: ${DUAL_LOG_DIR}" >&2
	fi
	exit "${code}"
}

trap cleanup_dual_run EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

case "${DUAL_LOG_DIR_REQUESTED}" in
*$'\r'*|*$'\n'*|*$'\t'*)
	echo "[dual-platform] DUAL_LOG_DIR contains a control character" >&2
	exit 1
	;;
esac
if [ -n "${DUAL_LOG_DIR_REQUESTED}" ]; then
	DUAL_LOG_DIR="${DUAL_LOG_DIR_REQUESTED}"
	if [ -e "${DUAL_LOG_DIR}" ] || [ -L "${DUAL_LOG_DIR}" ]; then
		echo "[dual-platform] DUAL_LOG_DIR already exists: ${DUAL_LOG_DIR}" >&2
		exit 1
	fi
	mkdir -m 700 -- "${DUAL_LOG_DIR}"
else
	DUAL_LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agentos-dual-platform.XXXXXX")"
fi
DUAL_LOG_DIR="$(cd "${DUAL_LOG_DIR}" && pwd)"
export DUAL_LOG_DIR
umask 077
stage_timings="${DUAL_LOG_DIR}/stage-timings.csv"
printf "stage,start_epoch,end_epoch,duration_seconds,status\n" > "${stage_timings}"

stage_begin() {
	current_stage="$1"
	current_stage_start="$(date +%s)"
	echo "[dual-platform] stage start: ${current_stage}"
}

stage_finish() {
	local status="$1"
	local end
	local duration

	end="$(date +%s)"
	duration=$((end - current_stage_start))
	printf "%s,%s,%s,%s,%s\n" "${current_stage}" "${current_stage_start}" "${end}" "${duration}" "${status}" >> "${stage_timings}"
	echo "[dual-platform] stage ${status}: ${current_stage} (${duration}s)"
	current_stage=""
	current_stage_start=0
}

echo "[dual-platform] checking target structure"
stage_begin "structure-check"
bash "${ROOT_DIR}/scripts/verify-dual-target-structure.sh"
stage_finish ready

require_log() {
	local logfile="$1"
	local pattern="$2"
	local message="$3"

	if ! grep -qE "${pattern}" "${logfile}"; then
		echo "[dual-platform] missing: ${message}" >&2
		tail -80 "${logfile}" >&2
		exit 1
	fi
}

reject_log() {
	local logfile="$1"
	local pattern="$2"
	local message="$3"

	if grep -qE "${pattern}" "${logfile}"; then
		echo "[dual-platform] unexpected: ${message}" >&2
		grep -nE "${pattern}" "${logfile}" >&2
		tail -80 "${logfile}" >&2
		exit 1
	fi
}

require_verified_compare() {
	local logfile="$1"
	local line
	local executed
	local passed

	line="$(grep -E '^rp_compare_plain: evidence_generation=runtime runtime_assertions_executed=[1-9][0-9]* runtime_assertions_passed=[1-9][0-9]* status=verified[[:space:]]*$' "${logfile}" | tail -1 || true)"
	if [ -z "${line}" ]; then
		echo "[dual-platform] missing: AgentOS runtime compare evidence" >&2
		tail -80 "${logfile}" >&2
		exit 1
	fi
	executed="$(printf '%s\n' "${line}" | sed -n 's/.* runtime_assertions_executed=\([0-9][0-9]*\) .*/\1/p')"
	passed="$(printf '%s\n' "${line}" | sed -n 's/.* runtime_assertions_passed=\([0-9][0-9]*\) .*/\1/p')"
	if [ "${executed}" != "${passed}" ]; then
		echo "[dual-platform] invalid: AgentOS compare assertion totals" >&2
		printf '%s\n' "${line}" >&2
		exit 1
	fi
}

require_plain_program_inventory() {
	local logfile="$1"
	local programs="$2"
	local pattern

	pattern="^rp_orch: evidence_role=demo_reference evidence_generation=runtime observation_source=guest_runtime program_source=rp_orch_timing program_source_bytes=[1-9][0-9]* program_source_hash=[1-9][0-9]* program_names_digest=[1-9][0-9]* programs_observed=${programs} status=reference_observed[[:space:]]*$"
	require_log "${logfile}" "${pattern}" "plain source-bound program inventory"
	reject_log "${logfile}" '^rp_orch: .*evidence_role=runtime_verified' "plain evidence impersonates AgentOS runtime verification"
}

require_agentos_program_inventory() {
	local logfile="$1"
	local programs="$2"
	local pattern

	pattern="^rp_orch: evidence_role=runtime_verified evidence_generation=runtime program_source=rp_orch_timing program_source_bytes=[1-9][0-9]* program_source_hash=[1-9][0-9]* program_names_digest=[1-9][0-9]* programs_observed=${programs} status=verified[[:space:]]*$"
	require_log "${logfile}" "${pattern}" "AgentOS source-bound program inventory"
	reject_log "${logfile}" '^rp_orch: .*evidence_role=demo_reference' "AgentOS runtime inventory downgraded to demo reference"
}

platform_program_count() {
	local makefile="$1"
	local tests
	local count=0
	local app

	tests="$(grep '^PLATFORM_TESTS :=' "${makefile}" | sed 's/^PLATFORM_TESTS :=//')"
	for app in ${tests}; do
		case "${app}" in
		rp_orch) ;;
		rp_*) count=$((count + 1)) ;;
		esac
	done
	echo "${count}"
}

expected_programs="$(platform_program_count "${ROOT_DIR}/baseline_ucore/user/Makefile")"

seeded_work_dir="${DUAL_LOG_DIR}/seeded-action-state"
seeded_summary="${DUAL_LOG_DIR}/seeded-action-state.json"
mapfile -t run_result_names < <(
	PYTHONPATH="${ROOT_DIR}/host_tools" "${PYTHON_BIN}" -c '
from dual_state_evidence_contract import RUN_RESULT_WORK_FILES
print(RUN_RESULT_WORK_FILES["plain"])
print(RUN_RESULT_WORK_FILES["agentos"])
'
)
if [ "${#run_result_names[@]}" -ne 2 ]; then
	echo "[dual-platform] Host run result contract is invalid" >&2
	exit 1
fi
plain_run_result="${DUAL_LOG_DIR}/${run_result_names[0]}"
agentos_run_result="${DUAL_LOG_DIR}/${run_result_names[1]}"
rm -f "${plain_run_result}" "${agentos_run_result}"

echo "[dual-platform] running seeded dual-target research platform"
stage_begin "seeded-dual-run"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_seeded_action_state.py" \
	--work-dir "${seeded_work_dir}" \
	--timeout "${SEEDED_ACTION_TIMEOUT:-600}" \
	--target-order "${SEEDED_TARGET_ORDER:-plain-agentos}" \
	--json-out "${seeded_summary}"
stage_finish ready

plain_log="${seeded_work_dir}/plain/ucore-run.log"
agentos_log="${seeded_work_dir}/agentos/ucore-run.log"
plain_state_src="${seeded_work_dir}/plain/state-extracted"
agentos_state_src="${seeded_work_dir}/agentos/state-extracted"

echo "[dual-platform] plain uCore research platform log: ${plain_log}"
stage_begin "qemu-log-marker-check"
require_log "${plain_log}" "rp_orch: passed" "plain rp_orch passed marker"
require_log "${plain_log}" "rp_orch: programs_ok=${expected_programs} programs_total=${expected_programs}" "plain complete program count"
require_plain_program_inventory "${plain_log}" "${expected_programs}"
require_log "${plain_log}" "^rp_compare_plain: evidence_role=demo_reference catalog_generation=demo_expected status=reference_ready[[:space:]]*$" "plain demo/reference summary"
if ! plain_backend_summary="$("${PYTHON_BIN}" "${BACKEND_CONTRACT}" verify-log \
	--target plain --source "${ROOT_DIR}/baseline_ucore/user/src/rp_backend.c" \
	--log "${plain_log}")"; then
	tail -80 "${plain_log}" >&2
	exit 1
fi
reject_log "${plain_log}" "child_failed|IllegalInstruction|unknown syscall|bad addr|rp_orch: failed|status=failed" "plain platform failure marker"

echo "[dual-platform] AgentOS-uCore research platform log: ${agentos_log}"
require_log "${agentos_log}" "rp_agentos_orch: passed" "AgentOS rp_agentos_orch passed marker"
require_log "${agentos_log}" "rp_agentos_orch: kernel_agent=1 workflow=rp_orch status=ready" "AgentOS kernel-backed orchestrator marker"
require_log "${agentos_log}" "rp_orch: programs_ok=${expected_programs} programs_total=${expected_programs}" "AgentOS complete program count"
require_agentos_program_inventory "${agentos_log}" "${expected_programs}"
require_verified_compare "${agentos_log}"
if ! agentos_backend_summary="$("${PYTHON_BIN}" "${BACKEND_CONTRACT}" verify-log \
	--target agentos --source "${ROOT_DIR}/user/src/rp_backend.c" \
	--log "${agentos_log}")"; then
	tail -80 "${agentos_log}" >&2
	exit 1
fi
reject_log "${agentos_log}" "child_failed|IllegalInstruction|unknown syscall|bad addr|rp_orch: failed|status=failed" "AgentOS platform failure marker"
stage_finish ready

stage_begin "state-extract-copy"
rm -rf "${DUAL_LOG_DIR}/plain-state" "${DUAL_LOG_DIR}/agentos-state"
rm -f "${plain_run_result}" "${agentos_run_result}"
cp -a "${plain_state_src}" "${DUAL_LOG_DIR}/plain-state"
cp -a "${agentos_state_src}" "${DUAL_LOG_DIR}/agentos-state"
copy_run_result() {
	local label="$1"
	local source="$2"
	local destination="$3"
	if [ -L "${source}" ] || [ ! -f "${source}" ]; then
		echo "[dual-platform] ${label} run result is missing or unsafe" >&2
		exit 1
	fi
	cp "${source}" "${destination}"
}
copy_run_result "plain" \
	"${seeded_work_dir}/plain/state-next/rp_host_run_result" "${plain_run_result}"
copy_run_result "AgentOS" \
	"${seeded_work_dir}/agentos/state-next/rp_host_run_result" "${agentos_run_result}"

plain_count="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["extracted_state_files"])' "${DUAL_LOG_DIR}/plain-state/extract-summary.json")"
agentos_count="$("${PYTHON_BIN}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["extracted_state_files"])' "${DUAL_LOG_DIR}/agentos-state/extract-summary.json")"
if [ "${plain_count}" -lt 240 ]; then
	echo "[dual-platform] plain extracted too few state files: ${plain_count} < 240" >&2
	exit 1
fi
if [ "${agentos_count}" -lt 240 ]; then
	echo "[dual-platform] AgentOS extracted too few state files: ${agentos_count} < 240" >&2
	exit 1
fi
for file in rp_backend rp_agentcmp rp_orch_timing rp_web_bundle rp_api_compare rp_package rp_review_dashboard; do
	if [ ! -f "${DUAL_LOG_DIR}/plain-state/${file}" ]; then
		echo "[dual-platform] plain extracted state is missing ${file}" >&2
		exit 1
	fi
done
for file in rp_backend rp_agentcmp rp_orch_timing rp_agentos_kernel rp_agentos_mainflow rp_agentos_conflict rp_web_bundle rp_api_compare; do
	if [ ! -f "${DUAL_LOG_DIR}/agentos-state/${file}" ]; then
		echo "[dual-platform] AgentOS extracted state is missing ${file}" >&2
		exit 1
	fi
done
echo "[dual-platform] plain extracted state files: ${plain_count}"
echo "[dual-platform] AgentOS extracted state files: ${agentos_count}"
stage_finish ready

stage_begin "host-alignment"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_platform_alignment.py" \
	--plain-state-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-state-dir "${DUAL_LOG_DIR}/agentos-state" \
	--plain-profile seeded \
	--plain-log "${plain_log}" \
	--agentos-log "${agentos_log}" \
	--json-out "${DUAL_LOG_DIR}/host-platform-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_action_kind_alignment.py" \
	--json-out "${DUAL_LOG_DIR}/host-action-kind-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_test_alignment.py" \
	--plain-state-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-state-dir "${DUAL_LOG_DIR}/agentos-state" \
	--json-out "${DUAL_LOG_DIR}/host-test-alignment.json"

"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/check_host_surface_alignment.py" \
	--plain-state-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-state-dir "${DUAL_LOG_DIR}/agentos-state" \
	--json-out "${DUAL_LOG_DIR}/host-surface-alignment.json"
stage_finish ready

stage_begin "state-compare"
"${PYTHON_BIN}" "${ROOT_DIR}/host_tools/compare_dual_platform_state.py" \
	--plain-dir "${DUAL_LOG_DIR}/plain-state" \
	--agentos-dir "${DUAL_LOG_DIR}/agentos-state" \
	--plain-run-result "${plain_run_result}" \
	--agentos-run-result "${agentos_run_result}" \
	--plain-log "${plain_log}" \
	--agentos-log "${agentos_log}" \
	--seeded-summary "${seeded_summary}" \
	--min-common-files 240 \
	--json-out "${DUAL_LOG_DIR}/state-compare-summary.json"
stage_finish ready

stage_begin "measured-file-query"
measurement_guest_log="${DUAL_LOG_DIR}/dual-targeted-agentbench-guest.log"
measurement_csv="${DUAL_LOG_DIR}/file-query-benchmark.csv"
: >"${measurement_guest_log}"
(
	cd "${ROOT_DIR}"
	env AGENT_TEST_CASE=agentbench_ucore \
		AGENT_TEST_GUEST_LOG_FILE="${measurement_guest_log}" \
		TOOLPREFIX="${TOOLPREFIX}" QEMU="${QEMU}" \
		PYTHON_BIN="${PYTHON_BIN}" \
		bash scripts/run-agent-tests.sh
)
if [ ! -s "${measurement_guest_log}" ] || [ -L "${measurement_guest_log}" ]; then
	echo "[dual-platform] measured Agent Guest log is missing or unsafe" >&2
	exit 1
fi
"${PYTHON_BIN}" - "${measurement_guest_log}" "${measurement_csv}" <<'PY'
import csv
import sys
from pathlib import Path

prefix = "agentbench_ucore: file_query_benchmark "
pass_marker = "agentbench_ucore: parent passed"
expected = {
    "schema", "unit", "load",
    "traversal_ops", "traversal_records", "traversal_duration_us",
    "cold_index_ops", "cold_index_records", "cold_index_duration_us",
    "cold_rebuild_records", "cold_rebuild_included",
    "warm_index_ops", "warm_index_records", "warm_index_duration_us",
    "status",
}

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
pending = None
rows = []
for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
    if line.startswith(prefix):
        if pending is not None:
            raise SystemExit("benchmark marker was not followed by a pass marker")
        fields = {}
        for token in line[len(prefix):].split():
            if token.count("=") != 1:
                raise SystemExit(f"invalid benchmark token at line {line_number}")
            key, value = token.split("=", 1)
            if not key or not value or key in fields:
                raise SystemExit(f"duplicate or empty benchmark token at line {line_number}")
            fields[key] = value
        if set(fields) != expected:
            raise SystemExit(f"benchmark schema mismatch at line {line_number}")
        if fields["schema"] != "2" or fields["unit"] != "us" or fields["status"] != "measured":
            raise SystemExit(f"invalid benchmark marker at line {line_number}")
        pending = (line_number, fields)
    elif line == pass_marker and pending is not None:
        line_number, fields = pending
        trial = len(rows) // 3 + 1
        try:
            load = int(fields["load"])
            rebuild_records = int(fields["cold_rebuild_records"])
            values = {
                path: {
                    "operations": int(fields[f"{path}_ops"]),
                    "records": int(fields[f"{path}_records"]),
                    "duration_us": int(fields[f"{path}_duration_us"]),
                }
                for path in ("traversal", "cold_index", "warm_index")
            }
        except ValueError as error:
            raise SystemExit(f"non-integer benchmark value at line {line_number}") from error
        if (
            load <= 0
            or rebuild_records < 0
            or fields["cold_rebuild_included"] != "1"
            or any(value["operations"] <= 0 or value["records"] <= 0 or value["duration_us"] < 0
                   for value in values.values())
            or load > values["traversal"]["records"]
            or values["cold_index"]["records"] > values["traversal"]["records"]
            or values["warm_index"]["records"] != values["cold_index"]["records"]
        ):
            raise SystemExit(f"inconsistent benchmark values at line {line_number}")
        for path, value in values.items():
            rows.append({
                "trial": trial,
                "path": path,
                "load": load,
                **value,
                "rebuild_records": rebuild_records if path == "cold_index" else 0,
                "source_line": line_number,
            })
        pending = None

if pending is not None:
    raise SystemExit("benchmark marker was not followed by a pass marker")
if not rows:
    raise SystemExit("Guest log has no completed file-query benchmark")

fieldnames = (
    "trial", "path", "load", "operations", "records", "duration_us",
    "rebuild_records", "source_line",
)
with destination.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
print(f"[dual-platform] file-query benchmark rows: {len(rows)}")
PY
stage_finish ready

echo "[dual-platform] plain and AgentOS platforms both passed"
echo "[dual-platform] research platform programs: ${expected_programs}"
echo "[dual-platform] ${plain_backend_summary}"
echo "[dual-platform] ${agentos_backend_summary}"
echo "[dual-platform] benchmark CSV: ${measurement_csv}"
echo "[dual-platform] raw artifacts: ${DUAL_LOG_DIR}"
