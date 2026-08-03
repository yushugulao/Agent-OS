#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 2 ]]; then
	echo "usage: $0 <artifact-label> <runner>" >&2
	exit 64
fi

label="$1"
runner="$2"
artifact_dir="${CI_ARTIFACT_DIR:-ci-artifacts}"
if [[ ! "${label}" =~ ^[a-z0-9][a-z0-9-]{0,63}$ || ! -f "${runner}" ]]; then
	echo "[ci-mechanism] invalid label or runner" >&2
	exit 64
fi

mkdir -p "${artifact_dir}"
guest_log="${artifact_dir}/${label}-guest.log"
job_log="${artifact_dir}/${label}-job.log"
combined_log="${artifact_dir}/${label}-combined.log"
: >"${guest_log}"

set +e
runner_shell=(bash)
if [[ "${label}" == "fs-allocator-fault" ]]; then
	runner_shell=(/bin/bash --noprofile --norc -p)
fi
env EVIDENCE_GUEST_LOG_FILE="${guest_log}" \
	"${runner_shell[@]}" "${runner}" 2>&1 | tee "${job_log}"
pipeline_status=("${PIPESTATUS[@]}")
set -e

{
	printf '===== runner-stdout:%s =====\n' "${label}"
	cat "${job_log}"
	printf '\n===== runner-guest-logs:%s =====\n' "${label}"
	cat "${guest_log}"
} >"${combined_log}"

if [[ ${pipeline_status[0]:-1} -ne 0 ]]; then
	exit "${pipeline_status[0]}"
fi
if [[ ${pipeline_status[1]:-1} -ne 0 ]]; then
	exit 74
fi
if [[ ! -s "${guest_log}" ]]; then
	echo "[ci-mechanism] runner produced no Guest log: ${label}" >&2
	exit 65
fi

echo "[ci-mechanism] ${label} evidence captured"
