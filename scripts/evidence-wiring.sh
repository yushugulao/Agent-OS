#!/usr/bin/env bash

# 仅提供最小传输；测试语义保留在各闭锁式 runner 中。
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
	local source="$1" filename="$2" expected_size="${3:-}" expected_sha="${4:-}"
	local destination partial actual
	evidence_enabled || return 1
	[[ "${filename}" =~ ^[a-z0-9][a-z0-9._-]{0,95}$ ]] || return 1
	[[ -s "${source}" && ! -L "${source}" ]] || return 1
	destination="${EVIDENCE_INCOMING_DIR}/${filename}"
	partial="${EVIDENCE_INCOMING_DIR}/.${filename}.partial.$$"
	[[ ! -e "${destination}" && ! -e "${partial}" ]] || return 1
	cp "${source}" "${partial}" || { rm -f "${partial}"; return 1; }
	if [[ -n "${expected_size}" || -n "${expected_sha}" ]]; then
		[[ "${expected_size}" =~ ^[0-9]+$ &&
		   "${expected_sha}" =~ ^[0-9a-f]{64}$ ]] || {
			rm -f "${partial}"; return 1;
		}
		actual=$("${PYTHON_BIN}" -I -S -B -c \
			'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]).read_bytes(); print(len(p),hashlib.sha256(p).hexdigest(),sep="\t")' \
			"${partial}") || { rm -f "${partial}"; return 1; }
		[[ "${actual}" == "${expected_size}"$'\t'"${expected_sha}" ]] || {
			rm -f "${partial}"; return 1;
		}
	fi
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

evidence_verify_parallel_run() {
	local run_dir="$1" suite="$2"
	shift 2
	local case_args=()
	local case_name
	evidence_enabled || return 1
	for case_name in "$@"; do
		case_args+=(--case "${case_name}")
	done
	"${PYTHON_BIN}" -I -S -B \
		"${ROOT_DIR}/scripts/run-parallel-qemu-regressions.py" \
		--root "${ROOT_DIR}" --output-dir "${run_dir}" \
		--suite "${suite}" --verify-report --emit-import-plan \
		"${case_args[@]}"
	EVIDENCE_PARALLEL_PLAN="${run_dir}/verified-import.tsv"
}

evidence_import_parallel_case() {
	local run_dir="$1" label="$2"
	local row_label started ended name relative size sha extra found=0
	EVIDENCE_IMPORTED_ARTIFACTS=()
	EVIDENCE_IMPORTED_STEP_START=""
	EVIDENCE_IMPORTED_STEP_END=""
	[[ "${EVIDENCE_PARALLEL_PLAN:-}" == "${run_dir}/verified-import.tsv" ]] || return 1
	while IFS=$'\t' read -r row_label started ended name relative size sha extra; do
		[[ -n "${row_label}" && -n "${started}" && -n "${ended}" &&
		   -n "${name}" && -n "${relative}" && -n "${size}" &&
		   -n "${sha}" && -z "${extra}" ]] || return 1
		if [[ "${row_label}" == "${label}" ]]; then
			if [[ ${found} -eq 0 ]]; then
				EVIDENCE_IMPORTED_STEP_START="${started}"
				EVIDENCE_IMPORTED_STEP_END="${ended}"
			elif [[ "${started}" != "${EVIDENCE_IMPORTED_STEP_START}" ||
			        "${ended}" != "${EVIDENCE_IMPORTED_STEP_END}" ]]; then
				return 1
			fi
			found=$((found + 1))
			evidence_publish_file "${run_dir}/${relative}" "${name}" "${size}" "${sha}"
			EVIDENCE_IMPORTED_ARTIFACTS+=("${name}")
		fi
	done <"${EVIDENCE_PARALLEL_PLAN}"
	[[ ${found} -ge 1 ]]
}

evidence_record_parallel_case() {
	local run_dir="$1" label="$2" artifact
	evidence_import_parallel_case "${run_dir}" "${label}" || return $?
	printf '%s\t%s\t%s' "${label}" \
		"${EVIDENCE_IMPORTED_STEP_START}" "${EVIDENCE_IMPORTED_STEP_END}" \
		>>"${EVIDENCE_STEPS_FILE}"
	for artifact in "${EVIDENCE_IMPORTED_ARTIFACTS[@]}"; do
		printf '\t%s' "${artifact}" >>"${EVIDENCE_STEPS_FILE}"
	done
	printf '\n' >>"${EVIDENCE_STEPS_FILE}"
}

evidence_import_parallel_agent_suite() {
	local run_dir="$1"
	local row_label started ended name relative size sha extra
	local timings_size="" timings_sha="" guest_size="" guest_sha=""
	[[ "${EVIDENCE_PARALLEL_PLAN:-}" == "${run_dir}/verified-import.tsv" ]] || return 1
	while IFS=$'\t' read -r row_label started ended name relative size sha extra; do
		[[ -n "${row_label}" && -n "${started}" && -n "${ended}" &&
		   -n "${name}" && -n "${relative}" && -n "${size}" &&
		   -n "${sha}" && -z "${extra}" ]] || return 1
		[[ "${row_label}" == "agent-suite" ]] || continue
		case "${name}:${relative}" in
		agent-suite-timings.log:agent-suite-timings.log)
			[[ -z "${timings_size}" ]] || return 1
			timings_size="${size}"; timings_sha="${sha}"
			;;
		agent-suite-guest.log:agent-suite-guest.log)
			[[ -z "${guest_size}" ]] || return 1
			guest_size="${size}"; guest_sha="${sha}"
			;;
		*) return 1 ;;
		esac
	done <"${EVIDENCE_PARALLEL_PLAN}"
	[[ -n "${timings_size}" && -n "${guest_size}" ]] || return 1
	evidence_publish_file "${run_dir}/agent-suite-timings.log" \
		"agent-suite-timings.log" "${timings_size}" "${timings_sha}"
	evidence_publish_file "${run_dir}/agent-suite-guest.log" \
		"agent-suite-guest.log" "${guest_size}" "${guest_sha}"
}
