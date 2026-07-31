#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOSTCC="${HOSTCC:-cc}"
CASE_TIMEOUT="${CASE_TIMEOUT:-300s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
STABILITY_RUNS="${WORKFLOW_TEARDOWN_STABILITY_RUNS:-3}"
LOG_DIR="${WORKFLOW_TEARDOWN_LOG_DIR:-${PWD}/build/test-logs/workflow-teardown-race}"
DOMAIN_FILE_CAP="${WORKFLOW_TEARDOWN_DOMAIN_FILE_CAP:-14}"
GLOBAL_RESERVED_CAP="${WORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP:-64}"
INIT_PROC=workflow_teardown_race_ucore
FINAL_MARKER="${INIT_PROC}: parent passed"
MAX_STABILITY_RUNS=20
MAX_DOMAIN_FILE_CAP=14
MAX_GLOBAL_RESERVED_CAP=64
MAX_USER_FRAME_BYTES=1024
SHELL_ARITH_MAX=9223372036854775807

run_logged() {
	local log_file="$1"
	shift
	local pipeline_status=()

	if "$@" 2>&1 | tee -a "${log_file}"; then
		pipeline_status=("${PIPESTATUS[@]}")
	else
		pipeline_status=("${PIPESTATUS[@]}")
	fi
	if [[ ${pipeline_status[1]} -ne 0 ]]; then
		echo "[workflow-teardown] tee failed for ${log_file}" >&2
		return 74
	fi
	return "${pipeline_status[0]}"
}

if ! [[ "${STABILITY_RUNS}" =~ ^[1-9][0-9]?$ ]] ||
   ((STABILITY_RUNS < 3 || STABILITY_RUNS > MAX_STABILITY_RUNS)); then
	echo "[workflow-teardown] stability runs must be in [3, ${MAX_STABILITY_RUNS}]" >&2
	exit 2
fi
if ! [[ "${DOMAIN_FILE_CAP}" =~ ^[1-9][0-9]?$ ]] ||
   ! [[ "${GLOBAL_RESERVED_CAP}" =~ ^[1-9][0-9]?$ ]] ||
   ((DOMAIN_FILE_CAP <= 6 ||
     DOMAIN_FILE_CAP > MAX_DOMAIN_FILE_CAP ||
     GLOBAL_RESERVED_CAP < DOMAIN_FILE_CAP ||
     GLOBAL_RESERVED_CAP > MAX_GLOBAL_RESERVED_CAP)); then
	echo "[workflow-teardown] invalid file-object capacities" >&2
	exit 2
fi
if ((GLOBAL_RESERVED_CAP > SHELL_ARITH_MAX / 2 ||
     GLOBAL_RESERVED_CAP > SHELL_ARITH_MAX - 1)); then
	echo "[workflow-teardown] file-object capacity arithmetic overflow" >&2
	exit 2
fi
FILE_POOL_SIZE=$((GLOBAL_RESERVED_CAP * 2))
FILE_ORDINARY_LIMIT=$((FILE_POOL_SIZE - GLOBAL_RESERVED_CAP))
# Workflow children retain their creator's caller frames until their root exits.
# Keep every phase frame below one quarter of the fixed 4 KiB user stack.
USER_EXTRA_CFLAGS="-Werror -Wframe-larger-than=${MAX_USER_FRAME_BYTES} -Wstack-usage=${MAX_USER_FRAME_BYTES} -DWORKFLOW_TEARDOWN_DOMAIN_FILE_CAP=${DOMAIN_FILE_CAP} -DWORKFLOW_TEARDOWN_GLOBAL_RESERVED_CAP=${GLOBAL_RESERVED_CAP}"

TMPDIR_WORKFLOW_TEARDOWN="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_WORKFLOW_TEARDOWN}"' EXIT
source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_WORKFLOW_TEARDOWN}"
mkdir -p "${LOG_DIR}"
SETUP_LOG="${LOG_DIR}/setup.log"
: >"${SETUP_LOG}"
echo "[workflow-teardown] persistent logs: ${LOG_DIR}"

run_logged "${SETUP_LOG}" make -C user \
	TOOLPREFIX="${TOOLPREFIX}" \
	CHAPTER=workflow_teardown \
	USER_EXTRA_CFLAGS="${USER_EXTRA_CFLAGS}" \
	build_dir="${TMPDIR_WORKFLOW_TEARDOWN}/user-build" \
	out_dir="${TMPDIR_WORKFLOW_TEARDOWN}/user-target" \
	asm_dir="${TMPDIR_WORKFLOW_TEARDOWN}/user-asm"

USER_BIN="${TMPDIR_WORKFLOW_TEARDOWN}/user-target/bin/${INIT_PROC}"
test -f "${USER_BIN}"
run_logged "${SETUP_LOG}" host_probe_compile \
	"${TMPDIR_WORKFLOW_TEARDOWN}/mkfs" \
	nfs/fs.c nfs/host_image_snapshot.c
run_logged "${SETUP_LOG}" host_probe_run \
	"${TMPDIR_WORKFLOW_TEARDOWN}/mkfs" \
	"${TMPDIR_WORKFLOW_TEARDOWN}/master.img" "${USER_BIN}"

# The guest fills the per-workflow boundary in each round, then runs for one
# more lifecycle than the global reserved class could tolerate if one object
# leaked per teardown. Both capacities come from the same runner contract.
run_logged "${SETUP_LOG}" make -B build \
	TOOLPREFIX="${TOOLPREFIX}" \
	LOG=error \
	CHAPTER=workflow_teardown \
	INIT_PROC="${INIT_PROC}" \
	FILE_RESOURCE_POOL_SIZE="${FILE_POOL_SIZE}" \
	FILE_RESOURCE_ORDINARY_LIMIT="${FILE_ORDINARY_LIMIT}" \
	FILE_RESOURCE_DOMAIN_ORDINARY_LIMIT="${DOMAIN_FILE_CAP}" \
	FILE_RESOURCE_DOMAIN_RESERVED_LIMIT="${DOMAIN_FILE_CAP}"
cp build/kernel "${TMPDIR_WORKFLOW_TEARDOWN}/kernel"

# agent_test_runner accepts a QEMU executable but no trailing arguments.
QEMU_WRAPPER="${TMPDIR_WORKFLOW_TEARDOWN}/qemu-single-hart"
printf '%s\n' \
	'#!/usr/bin/env bash' \
	'exec "${WORKFLOW_TEARDOWN_REAL_QEMU:?}" -smp 1 "$@"' \
	>"${QEMU_WRAPPER}"
chmod +x "${QEMU_WRAPPER}"
export WORKFLOW_TEARDOWN_REAL_QEMU="${QEMU}"

for ((run = 1; run <= STABILITY_RUNS; run++)); do
	run_image="${TMPDIR_WORKFLOW_TEARDOWN}/run-${run}.img"
	cp "${TMPDIR_WORKFLOW_TEARDOWN}/master.img" "${run_image}"
	log_file="${LOG_DIR}/run-${run}.guest.log"
	runner_log="${LOG_DIR}/run-${run}.runner.log"
	validator_log="${LOG_DIR}/run-${run}.validator.log"
	: >"${runner_log}"
	: >"${validator_log}"

	echo "[workflow-teardown] stability run ${run}/${STABILITY_RUNS}"
	if run_logged "${runner_log}" \
		"${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "${INIT_PROC}-run-${run}" \
		--marker "${FINAL_MARKER}" \
		--marker-mode exact-line \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU_WRAPPER}" \
		--kernel "${TMPDIR_WORKFLOW_TEARDOWN}/kernel" \
		--image "${run_image}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	evidence_append_guest_log "workflow-teardown:${run}" "${log_file}"
	if [[ ${runner_status} -ne 0 ]]; then
		exit "${runner_status}"
	fi
	run_logged "${validator_log}" \
		"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
		--log-file "${log_file}" \
		--tag "workflow-teardown:${run}" \
		--profile workflow-teardown-race \
		--workflow-domain-file-cap "${DOMAIN_FILE_CAP}" \
		--workflow-global-reserved-cap "${GLOBAL_RESERVED_CAP}"
done

echo "[workflow-teardown] ${STABILITY_RUNS} stable runs passed"
host_probe_report "workflow-teardown mkfs"
