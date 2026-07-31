#!/usr/bin/env bash
# Shared fail-closed setup for Host C programs exercised by verification.

host_probe_setup() {
	local directory="$1"
	local script_dir python record kind value assignment name

	script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	python="${PYTHON_BIN:-python3}"
	if ! record="$("${python}" "${script_dir}/host_probe_toolchain.py" \
		--directory "${directory}")"; then
		return 2
	fi

	HOST_PROBE_CC=()
	HOST_PROBE_SANITIZER_FLAGS=()
	HOST_PROBE_MODE=""
	while IFS=$'\t' read -r kind value; do
		case "${kind}" in
		compiler)
			HOST_PROBE_CC+=("${value}")
			;;
		flag)
			HOST_PROBE_SANITIZER_FLAGS+=("${value}")
			;;
		environment)
			assignment="${value}"
			name="${assignment%%=*}"
			value="${assignment#*=}"
			case "${name}" in
			ASAN_OPTIONS | UBSAN_OPTIONS)
				printf -v "${name}" '%s' "${value}"
				export "${name}"
				;;
			*)
				echo "host-probe-toolchain: invalid environment record" >&2
				return 2
				;;
			esac
			;;
		mode)
			HOST_PROBE_MODE="${value}"
			;;
		*)
			echo "host-probe-toolchain: invalid setup record" >&2
			return 2
			;;
		esac
	done <<<"${record}"

	if [[ ${#HOST_PROBE_CC[@]} -eq 0 || -z "${HOST_PROBE_MODE}" ]]; then
		echo "host-probe-toolchain: incomplete setup record" >&2
		return 2
	fi
	if [[ "${HOST_PROBE_MODE}" == "ASan/UBSan" &&
	      ${#HOST_PROBE_SANITIZER_FLAGS[@]} -eq 0 ]]; then
		echo "host-probe-toolchain: sanitizer mode lacks flags" >&2
		return 2
	fi
}

host_probe_compile() {
	local output="$1"
	shift
	"${HOST_PROBE_CC[@]}" "${HOST_PROBE_SANITIZER_FLAGS[@]}" \
		"$@" -o "${output}"
}

host_probe_run() {
	"$@"
}

host_probe_report() {
	local label="$1"
	printf '[host-probe] %s; mode=%s\n' "${label}" "${HOST_PROBE_MODE}"
}
