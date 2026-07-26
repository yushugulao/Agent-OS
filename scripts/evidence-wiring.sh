#!/usr/bin/env bash

# Minimal transport only. Test semantics stay in each fail-closed runner.
evidence_enabled() { [[ -n "${FINAL_EVIDENCE_STAGE:-}" ]]; }

evidence_initialize() {
	evidence_enabled || return 0
	[[ "${FINAL_EVIDENCE_STAGE}" == /* ]] || {
		echo "[evidence] FINAL_EVIDENCE_STAGE must be absolute" >&2; return 1;
	}
	EVIDENCE_INCOMING_DIR="${FINAL_EVIDENCE_STAGE}/incoming"
	EVIDENCE_WORK_DIR="${FINAL_EVIDENCE_STAGE}/runtime/full-verify"
	[[ -d "${EVIDENCE_INCOMING_DIR}" && ! -L "${EVIDENCE_INCOMING_DIR}" ]] || return 1
	[[ -z "$(find "${EVIDENCE_INCOMING_DIR}" -mindepth 1 -print -quit)" ]] || return 1
	[[ ! -e "${EVIDENCE_WORK_DIR}" ]] || return 1
	mkdir -p "${EVIDENCE_WORK_DIR}"
	EVIDENCE_STEPS_FILE="${EVIDENCE_WORK_DIR}/steps.tsv"
	: >"${EVIDENCE_STEPS_FILE}"
	export EVIDENCE_INCOMING_DIR EVIDENCE_WORK_DIR EVIDENCE_STEPS_FILE
}

evidence_publish_file() {
	local source="$1" filename="$2" destination partial
	evidence_enabled || return 1
	[[ "${filename}" =~ ^[a-z0-9][a-z0-9._-]{0,95}$ ]] || return 1
	[[ -s "${source}" && ! -L "${source}" ]] || return 1
	destination="${EVIDENCE_INCOMING_DIR}/${filename}"
	partial="${EVIDENCE_INCOMING_DIR}/.${filename}.partial.$$"
	[[ ! -e "${destination}" && ! -e "${partial}" ]] || return 1
	cp "${source}" "${partial}" || { rm -f "${partial}"; return 1; }
	mv "${partial}" "${destination}" || { rm -f "${partial}"; return 1; }
}

evidence_append_guest_log() {
	local tag="$1" source="$2" destination="${3:-${EVIDENCE_GUEST_LOG_FILE:-}}"
	[[ -n "${destination}" ]] || return 0
	[[ "${tag}" =~ ^[A-Za-z0-9:_-]{1,128}$ ]] || return 1
	[[ -s "${source}" && ! -L "${source}" && ! -L "${destination}" ]] || return 1
	{
		printf '===== guest:%s =====\n' "${tag}"
		cat "${source}"
		printf '\n===== end-guest:%s =====\n' "${tag}"
	} >>"${destination}"
}

evidence_capture() {
	local output="$1" had_errexit=0
	local pipeline_status=()
	shift
	case "$-" in *e*) had_errexit=1;; esac
	set +e
	"$@" 2>&1 | tee "${output}"
	pipeline_status=("${PIPESTATUS[@]}")
	if [[ ${had_errexit} -eq 1 ]]; then set -e; else set +e; fi
	[[ ${pipeline_status[0]} -eq 0 ]] || return "${pipeline_status[0]}"
	[[ ${pipeline_status[1]} -eq 0 ]] || return 74
}

evidence_capture_stdout() {
	local filename="$1" output="${EVIDENCE_WORK_DIR}/$1.stdout"
	shift
	evidence_capture "${output}" "$@" || return $?
	evidence_publish_file "${output}" "${filename}"
}
