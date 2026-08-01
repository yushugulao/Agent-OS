#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

MODE="${1:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
BASH_BIN="${BASH_BIN:-bash}"
FORMAL_MICRO_BOOTS=7
FORMAL_SCENARIO_BOOTS=7
EVALUATION_BOOTS="${EVALUATION_BOOTS:-${FORMAL_MICRO_BOOTS}}"
EVALUATION_INCLUDE_SCENARIO="${EVALUATION_INCLUDE_SCENARIO:-1}"
EVALUATION_SCENARIO_BOOTS="${EVALUATION_SCENARIO_BOOTS:-${FORMAL_SCENARIO_BOOTS}}"
EVALUATION_SCENARIO_TIMEOUT="${EVALUATION_SCENARIO_TIMEOUT:-600}"
EVALUATION_MICRO_TIMEOUT="${EVALUATION_MICRO_TIMEOUT:-900}"
FORMAL_MICRO_TIMEOUT=900
EVALUATION_WSL_DISTRO="${EVALUATION_WSL_DISTRO:-Ubuntu}"
EVALUATION_OUTPUT_ROOT="${EVALUATION_OUTPUT_ROOT:-results/evaluation}"
CAMPAIGN_TOOL="host_tools/evaluation_campaign.py"
CONTRACT_TOOL="host_tools/evaluation_contract.py"
CONTRACT_SUITE="ci/evaluation-suite.json"
DASHBOARD_TOOL="host_tools/render_evaluation_dashboard.py"
SCENARIO_TOOL="host_tools/evaluation_scenario.py"
BUNDLE_TOOL="host_tools/evaluation_bundle.py"
FULL_VERIFICATION_TOOL="host_tools/full_verification_payload.py"
PLATFORM_TOOL="host_tools/evaluation_platform.py"
KERNEL_BUILD_TOOL="host_tools/evaluation_kernel_build.py"
KERNEL_COST_TOOL="host_tools/evaluation_kernel_cost.py"
MEASUREMENT_SOURCE_TOOL="host_tools/agenteval_measurement_source_contract.py"
SCENARIO_SOURCE_TOOL="host_tools/scenario_timing_source_contract.py"
COMPATIBILITY_TOOL="host_tools/compatibility_overhead.py"
MEASUREMENT_SOURCE_RECEIPT_NAME="measurement-source-receipt.json"
TRUSTED_PYTHON_ENTRY="scripts/trusted-python-entry.py"

run_repo_python() {
	"${PYTHON_BIN}" -I -S "${TRUSTED_PYTHON_ENTRY}" "$@"
}

usage() {
	cat >&2 <<'EOF'
usage: scripts/run-evaluation-suite.sh {doctor|smoke|run|verify|kernel-cost|full-verify|dashboard|package|verify-package}

Environment:
  EVALUATION_BOOTS       development boot count; formal campaigns require exactly 7
  EVALUATION_INCLUDE_SCENARIO  1 for formal research scenario, 0 for development only
  EVALUATION_SCENARIO_BOOTS    development scenario count; formal campaigns require exactly 7
  EVALUATION_SCENARIO_TIMEOUT  per-target runner budget (60..3600 seconds); the paired deadline is derived
  EVALUATION_MICRO_TIMEOUT     sealed micro boot watchdog (formal value: 900 seconds)
  EVALUATION_RUN_ID      development run id; formal runs require formal-<commit>
  EVALUATION_RUN_DIR     explicit existing run directory for verify/cost/dashboard/package
  EVALUATION_OUTPUT_ROOT Git-ignored output root (default: results/evaluation)
  EVALUATION_BUNDLE_DIR  package output or existing bundle to verify
  EVALUATION_FULL_VERIFY_TIMEOUT  detached full-verify budget in seconds (default: 18000)
  MAKE_TOOL, SIZE_TOOL   explicit build and GNU size executables for kernel-cost
  TOOLPREFIX, QEMU       RISC-V toolchain prefix and QEMU executable
  PYTHON_BIN, BASH_BIN   host Python and Bash executables
  EVALUATION_WSL_DISTRO  exact WSL distribution used for every formal stage on Windows
  EVALUATION_WSL_TOOLPREFIX, EVALUATION_WSL_QEMU  tools inside that WSL distribution
  Native MSYS2 is detected from its POSIX Python/runtime and always re-enters env -i
EOF
	exit 2
}

python_host_os() {
	"${PYTHON_BIN}" -I -S -c 'import os; print(os.name)'
}

python_platform_name() {
	"${PYTHON_BIN}" -I -S -c 'import sys; print(sys.platform)'
}

platform_arguments() {
	local host_os toolprefix qemu python_bin shell_bin
	host_os="$(python_host_os)"
	toolprefix="${TOOLPREFIX}"
	qemu="${QEMU}"
	python_bin="${PYTHON_BIN}"
	shell_bin="${BASH_BIN}"
	if [[ "${host_os}" == "nt" ]]; then
		toolprefix="${EVALUATION_WSL_TOOLPREFIX:-riscv64-linux-gnu-}"
		qemu="${EVALUATION_WSL_QEMU:-qemu-system-riscv64}"
		python_bin="${EVALUATION_WSL_PYTHON:-python3}"
		shell_bin="${EVALUATION_WSL_BASH:-bash}"
	fi
	PLATFORM_ARGS=(
		--repo "${ROOT}" --distro "${EVALUATION_WSL_DISTRO}"
		--toolprefix "${toolprefix}" --qemu "${qemu}"
		--python-bin "${python_bin}" --shell-bin "${shell_bin}"
	)
}

route_formal_domain() {
	local host_os host_platform entry_domain
	case "${MODE}" in
	run|verify|kernel-cost|full-verify|dashboard|package)
		host_os="$(python_host_os)"
		if [[ "${host_os}" == "nt" ]]; then
			require_file "${PLATFORM_TOOL}"
			platform_arguments
			exec "${PYTHON_BIN}" -I -S "${TRUSTED_PYTHON_ENTRY}" "${PLATFORM_TOOL}" wsl-exec \
				"${PLATFORM_ARGS[@]}" \
				--script-relative scripts/run-evaluation-suite.sh --mode "${MODE}"
		fi
		host_platform="$(python_platform_name)"
		if [[ "${host_os}" == "posix" && "${host_platform}" == "cygwin" ]]; then
			require_file "${PLATFORM_TOOL}"
			platform_arguments
			entry_domain="${AGENTOS_EVALUATION_EXECUTION_DOMAIN:-}"
			if [[ -z "${entry_domain}" ]]; then
				exec "${PYTHON_BIN}" -I -S "${TRUSTED_PYTHON_ENTRY}" "${PLATFORM_TOOL}" msys-exec \
					"${PLATFORM_ARGS[@]}" \
					--script-relative scripts/run-evaluation-suite.sh --mode "${MODE}"
			fi
			[[ "${entry_domain}" == "native-msys2" ]] || {
				echo "[evaluation] invalid MSYS2 execution-domain marker" >&2
				exit 2
			}
			# This is not a marker-only shortcut: the inner process rehashes the
			# runtime and tools and verifies the complete env -i allowlist.
			run_platform_doctor >/dev/null
		fi
		;;
	esac
}

run_platform_doctor() {
	require_file "${PLATFORM_TOOL}"
	platform_arguments
	run_repo_python "${PLATFORM_TOOL}" doctor "${PLATFORM_ARGS[@]}"
}

require_file() {
	[[ -f "$1" && ! -L "$1" ]] || {
		echo "[evaluation] required file is unavailable: $1" >&2
		exit 2
	}
}

normalize_tool_path_for_python() {
	local tool_path="$1" python_os cygpath_path
	python_os="$("${PYTHON_BIN}" -I -S -c 'import os; print(os.name)')"
	if [[ "${python_os}" != "nt" ]]; then
		printf '%s\n' "${tool_path}"
		return
	fi
	set +e
	cygpath_path="$(command -v -- cygpath)"
	local cygpath_status=$?
	set -e
	[[ "${cygpath_status}" -eq 0 && -n "${cygpath_path}" ]] || {
		echo "[evaluation] Windows Python requires cygpath for resolved tool paths" >&2
		return 2
	}
	"${cygpath_path}" -w -- "${tool_path}"
}

pipeline_status_selftest() {
	local -a pipeline_status
	local left right
	set +e
	(exit 23) | true
	pipeline_status=("${PIPESTATUS[@]}")
	set -e
	left="${pipeline_status[0]}"
	right="${pipeline_status[1]}"
	if [[ "${left}" -ne 23 || "${right}" -ne 0 ]]; then
		echo "[evaluation] Bash PIPESTATUS capture is unavailable" >&2
		exit 2
	fi
}

write_pointer() {
	local name="$1" value="$2" partial
	mkdir -p "${EVALUATION_OUTPUT_ROOT}"
	partial="${EVALUATION_OUTPUT_ROOT}/.${name}.tmp.$$"
	printf '%s\n' "${value}" >"${partial}"
	mv "${partial}" "${EVALUATION_OUTPUT_ROOT}/${name}"
}

write_measurement_source_receipt() {
	local commit="$1"
	require_file "${MEASUREMENT_SOURCE_TOOL}"
	require_file "${SCENARIO_SOURCE_TOOL}"
	run_repo_python "${MEASUREMENT_SOURCE_TOOL}" \
		--repo "${ROOT}" \
		--write-receipt "${RUN_DIR}/${MEASUREMENT_SOURCE_RECEIPT_NAME}" \
		--source-commit "${commit}"
}

verify_measurement_source_receipt() {
	local commit
	require_file "${MEASUREMENT_SOURCE_TOOL}"
	require_file "${SCENARIO_SOURCE_TOOL}"
	require_file "${RUN_DIR}/${MEASUREMENT_SOURCE_RECEIPT_NAME}"
	commit="$(git rev-parse HEAD)"
	run_repo_python "${MEASUREMENT_SOURCE_TOOL}" \
		--repo "${ROOT}" \
		--verify-receipt "${RUN_DIR}/${MEASUREMENT_SOURCE_RECEIPT_NAME}" \
		--source-commit "${commit}"
}

resolve_run_dir() {
	if [[ -n "${EVALUATION_RUN_DIR:-}" ]]; then
		RUN_DIR="${EVALUATION_RUN_DIR}"
	elif [[ -n "${EVALUATION_RUN_ID:-}" ]]; then
		[[ "${EVALUATION_RUN_ID}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]] || {
			echo "[evaluation] invalid EVALUATION_RUN_ID" >&2
			exit 2
		}
		RUN_DIR="${EVALUATION_OUTPUT_ROOT}/runs/${EVALUATION_RUN_ID}"
	else
		local pointer="${EVALUATION_OUTPUT_ROOT}/latest-run.txt"
		require_file "${pointer}"
		local latest
		IFS= read -r latest <"${pointer}"
		[[ "${latest}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]] || {
			echo "[evaluation] latest run pointer is invalid" >&2
			exit 2
		}
		RUN_DIR="${EVALUATION_OUTPUT_ROOT}/runs/${latest}"
	fi
	[[ -d "${RUN_DIR}" && ! -L "${RUN_DIR}" ]] || {
		echo "[evaluation] run directory is unavailable: ${RUN_DIR}" >&2
		exit 2
	}
}

contract_args() {
	CONTRACT_ARGS=(
		--suite "${CONTRACT_SUITE}"
		--run-plan "${RUN_DIR}/run-plan.json"
		--source-root "${RUN_DIR}/raw"
		--summary "${RUN_DIR}/summary.json"
		--rows "${RUN_DIR}/metrics.jsonl"
	)
	if [[ -f "${RUN_DIR}/scenario/report.json" ]]; then
		CONTRACT_ARGS+=(
			--scenario-plan "${RUN_DIR}/scenario/scenario-plan.json"
			--scenario-report "${RUN_DIR}/scenario/report.json"
		)
	fi
}

scenario_collector_args() {
	local plan="$1" count run_id commit number boot_id work_dir order
	count="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-metadata \
		--manifest "${plan}" --field boots)"
	run_id="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-metadata \
		--manifest "${plan}" --field run_id)"
	commit="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-metadata \
		--manifest "${plan}" --field commit)"
	SCENARIO_COLLECTOR_ARGS=(--commit "${commit}" --run-id "${run_id}")
	for ((number = 1; number <= count; number++)); do
		printf -v boot_id 'boot-%02d' "${number}"
		work_dir="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-field \
			--manifest "${plan}" --boot-id "${boot_id}" --field work_dir)"
		order="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-field \
			--manifest "${plan}" --boot-id "${boot_id}" --field target_order)"
		if [[ "${order}" == "plain-agentos" ]]; then order=AB; else order=BA; fi
		SCENARIO_COLLECTOR_ARGS+=(--boot "${work_dir}" --target-order "${order}")
	done
}

verify_scenario() {
	local plan="${RUN_DIR}/scenario/scenario-plan.json"
	local report="${RUN_DIR}/scenario/report.json"
	[[ -e "${plan}" || -e "${report}" ]] || return 0
	require_file "${plan}"
	require_file "${report}"
	require_file "${SCENARIO_TOOL}"
	run_repo_python "${CAMPAIGN_TOOL}" check-scenario \
		--repo "${ROOT}" --manifest "${plan}" \
		--micro-manifest "${RUN_DIR}/campaign.json"
	scenario_collector_args "${plan}"
	local replay="${RUN_DIR}/scenario/.report.verify.$$.json"
	local replay_log="${RUN_DIR}/scenario/.report.verify.$$.log"
	run_repo_python "${SCENARIO_TOOL}" "${SCENARIO_COLLECTOR_ARGS[@]}" \
		--json-out "${replay}" >"${replay_log}"
	cmp -s "${report}" "${replay}" || {
		echo "[evaluation] scenario report differs from a raw-source replay" >&2
		exit 2
	}
	rm -f "${replay}" "${replay_log}"
}

verify_run() {
	require_file "${CAMPAIGN_TOOL}"
	require_file "${CONTRACT_TOOL}"
	require_file "${CONTRACT_SUITE}"
	require_file "${RUN_DIR}/campaign.json"
	require_file "${RUN_DIR}/run-plan.json"
	verify_measurement_source_receipt
	run_repo_python "${CAMPAIGN_TOOL}" check \
		--repo "${ROOT}" \
		--manifest "${RUN_DIR}/campaign.json" \
		--require-collected
	if [[ "$(basename "${RUN_DIR}")" == formal-* ]]; then
		require_file "${COMPATIBILITY_TOOL}"
		require_file "${RUN_DIR}/compatibility/compatibility-overhead.json"
		run_repo_python "${COMPATIBILITY_TOOL}" verify \
			"${RUN_DIR}/compatibility/compatibility-overhead.json" \
			--micro-manifest "${RUN_DIR}/campaign.json"
	fi
	local replay_plan="${RUN_DIR}/.run-plan.verify.$$.json"
	run_repo_python "${CAMPAIGN_TOOL}" export-plan \
		--manifest "${RUN_DIR}/campaign.json" \
		--output "${replay_plan}"
	cmp -s "${RUN_DIR}/run-plan.json" "${replay_plan}" || {
		echo "[evaluation] run plan differs from the sealed campaign" >&2
		exit 2
	}
	rm -f "${replay_plan}"
	verify_scenario
	contract_args
	if [[ ! -e "${RUN_DIR}/summary.json" && ! -e "${RUN_DIR}/metrics.jsonl" ]]; then
		run_repo_python "${CONTRACT_TOOL}" build "${CONTRACT_ARGS[@]}"
	elif [[ ! -f "${RUN_DIR}/summary.json" || ! -f "${RUN_DIR}/metrics.jsonl" ]]; then
		echo "[evaluation] partial derived output is forbidden" >&2
		exit 2
	fi
	run_repo_python "${CONTRACT_TOOL}" verify "${CONTRACT_ARGS[@]}"
}

run_smoke() {
	pipeline_status_selftest
	local tests=(
		host_tools/test_plain_ucore_action_runner.py
		host_tools/test_evaluation_platform.py
		host_tools/test_evaluation_campaign.py
		host_tools/test_agenteval_measurement_source.py
		host_tools/test_functional_acceptance_source.py
		host_tools/test_scenario_timing_source.py
		host_tools/test_evaluation_contract.py
		host_tools/test_evaluation_kernel_build.py
		host_tools/test_evaluation_kernel_cost.py
		host_tools/test_check_seeded_action_state.py
		host_tools/test_evaluation_scenario.py
		host_tools/test_evaluation_dashboard.py
		host_tools/test_full_verification_payload.py
		host_tools/test_evidence_delivery_contract.py
		host_tools/test_evaluation_bundle.py
		host_tools/test_compatibility_overhead.py
	)
	local test
	for test in "${tests[@]}"; do
		require_file "${test}"
		"${PYTHON_BIN}" "${test}"
	done
	"${PYTHON_BIN}" -m py_compile \
		"${CAMPAIGN_TOOL}" "${CONTRACT_TOOL}" "${PLATFORM_TOOL}" \
		"${KERNEL_BUILD_TOOL}" "${KERNEL_COST_TOOL}" \
		host_tools/evaluation_scenario.py \
		host_tools/agenteval_measurement_source_contract.py \
		host_tools/scenario_timing_source_contract.py "${DASHBOARD_TOOL}" \
		"${COMPATIBILITY_TOOL}" host_tools/compatibility_overhead_contract.py
	"${PYTHON_BIN}" -m py_compile "${BUNDLE_TOOL}" "${FULL_VERIFICATION_TOOL}"
	echo "[evaluation] host contracts and static wiring passed; no performance claim was produced"
}

run_scenario_campaign() {
	local micro_manifest="$1"
	local scenario_dir="${RUN_DIR}/scenario"
	local scenario_plan="${scenario_dir}/scenario-plan.json"
	local number boot_id challenge order work_dir rc
	require_file "${SCENARIO_TOOL}"
	require_file host_tools/check_seeded_action_state.py
	require_file "${scenario_plan}"

	for ((number = 1; number <= EVALUATION_SCENARIO_BOOTS; number++)); do
		printf -v boot_id 'boot-%02d' "${number}"
		challenge="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-field \
			--manifest "${scenario_plan}" --boot-id "${boot_id}" --field challenge)"
		order="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-field \
			--manifest "${scenario_plan}" --boot-id "${boot_id}" --field target_order)"
		work_dir="$(run_repo_python "${CAMPAIGN_TOOL}" get-scenario-field \
			--manifest "${scenario_plan}" --boot-id "${boot_id}" --field work_dir)"
		mkdir -p "${work_dir}"
		echo "[evaluation] scenario ${boot_id}/${EVALUATION_SCENARIO_BOOTS}: ${order}, ${challenge}"
		set +e
		run_repo_python "${CAMPAIGN_TOOL}" run-scenario-boot \
			--repo "${ROOT}" --manifest "${scenario_plan}" \
			--boot-id "${boot_id}"
		rc=$?
		set -e
		if [[ "${rc}" -ne 0 ]]; then
			echo "[evaluation] scenario ${boot_id} failed; raw target logs were retained" >&2
			exit "${rc}"
		fi
	done

	scenario_collector_args "${scenario_plan}"
	set +e
	run_repo_python "${SCENARIO_TOOL}" "${SCENARIO_COLLECTOR_ARGS[@]}" \
		--json-out "${scenario_dir}/report.json" \
		2>&1 | tee "${scenario_dir}/collector.log"
	pipeline_status=("${PIPESTATUS[@]}")
	rc="${pipeline_status[0]}"
	tee_rc="${pipeline_status[1]}"
	set -e
	if [[ "${tee_rc}" -ne 0 ]]; then rc=74; fi
	if [[ "${rc}" -ne 0 ]]; then
		echo "[evaluation] scenario collector failed; report and log were retained" >&2
		exit "${rc}"
	fi
	run_repo_python "${CAMPAIGN_TOOL}" record-scenario-report \
		--repo "${ROOT}" --manifest "${scenario_plan}" \
		--report "${scenario_dir}/report.json"
	run_repo_python "${CAMPAIGN_TOOL}" seal-scenario --manifest "${scenario_plan}"
}

run_campaign() {
	require_file "${CAMPAIGN_TOOL}"
	require_file scripts/run-agent-tests.sh
	run_platform_doctor
	[[ "${EVALUATION_BOOTS}" =~ ^[0-9]+$ ]] || {
		echo "[evaluation] EVALUATION_BOOTS must be an integer" >&2
		exit 2
	}
	[[ "${EVALUATION_INCLUDE_SCENARIO}" == "0" || "${EVALUATION_INCLUDE_SCENARIO}" == "1" ]] || {
		echo "[evaluation] EVALUATION_INCLUDE_SCENARIO must be 0 or 1" >&2
		exit 2
	}
	[[ "${EVALUATION_SCENARIO_BOOTS}" =~ ^[0-9]+$ ]] || {
		echo "[evaluation] EVALUATION_SCENARIO_BOOTS must be an integer" >&2
		exit 2
	}
	[[ "${EVALUATION_MICRO_TIMEOUT}" =~ ^[0-9]+$ ]] &&
		(( EVALUATION_MICRO_TIMEOUT == FORMAL_MICRO_TIMEOUT )) || {
		echo "[evaluation] formal micro timeout must be ${FORMAL_MICRO_TIMEOUT} seconds" >&2
		exit 2
	}
	if [[ "${EVALUATION_INCLUDE_SCENARIO}" == "1" ]]; then
		(( EVALUATION_BOOTS == FORMAL_MICRO_BOOTS )) || {
			echo "[evaluation] formal micro evaluation uses the precommitted ${FORMAL_MICRO_BOOTS}-boot stopping rule" >&2
			exit 2
		}
		(( EVALUATION_SCENARIO_BOOTS == FORMAL_SCENARIO_BOOTS )) || {
			echo "[evaluation] formal scenario evaluation uses the precommitted ${FORMAL_SCENARIO_BOOTS}-boot stopping rule" >&2
			exit 2
		}
		[[ "${EVALUATION_SCENARIO_TIMEOUT}" =~ ^[0-9]+$ ]] &&
			(( EVALUATION_SCENARIO_TIMEOUT >= 60 && EVALUATION_SCENARIO_TIMEOUT <= 3600 )) || {
			echo "[evaluation] scenario target runner timeout must be between 60 and 3600 seconds" >&2
			exit 2
		}
		[[ "${EVALUATION_WSL_DISTRO}" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || {
			echo "[evaluation] invalid EVALUATION_WSL_DISTRO" >&2
			exit 2
		}
	fi
	local commit short_commit generated_id formal_id run_id manifest preflight_rc tee_rc
	local -a pipeline_status
	commit="$(git rev-parse HEAD)"
	short_commit="${commit:0:12}"
	generated_id="$(date -u +%Y%m%dT%H%M%SZ)-${short_commit}"
	formal_id="formal-${commit}"
	if [[ "${EVALUATION_INCLUDE_SCENARIO}" == "1" ]]; then
		if [[ -n "${EVALUATION_RUN_ID:-}" && "${EVALUATION_RUN_ID}" != "${formal_id}" ]]; then
			echo "[evaluation] formal EVALUATION_RUN_ID must equal ${formal_id}" >&2
			exit 2
		fi
		run_id="${formal_id}"
	else
		run_id="${EVALUATION_RUN_ID:-${generated_id}}"
	fi
	[[ "${run_id}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]] || {
		echo "[evaluation] invalid EVALUATION_RUN_ID" >&2
		exit 2
	}
	RUN_DIR="${EVALUATION_OUTPUT_ROOT}/runs/${run_id}"
	[[ ! -e "${RUN_DIR}" ]] || {
		echo "[evaluation] refusing to reuse run directory: ${RUN_DIR}" >&2
		exit 2
	}
	mkdir -p "${RUN_DIR}"
	write_pointer last-attempt.txt "${run_id}"
	manifest="${RUN_DIR}/campaign.json"
	write_measurement_source_receipt "${commit}"

	set +e
	run_repo_python "${CAMPAIGN_TOOL}" create \
		--repo "${ROOT}" \
		--output "${manifest}" \
		--run-id "${run_id}" \
		--boots "${EVALUATION_BOOTS}" \
		--toolprefix "${TOOLPREFIX}" \
		--qemu "${QEMU}" \
		--python-bin "${PYTHON_BIN}" \
		--shell-bin "${BASH_BIN}" \
		--timeout "${EVALUATION_MICRO_TIMEOUT}" \
		2>&1 | tee "${RUN_DIR}/preflight.log"
	pipeline_status=("${PIPESTATUS[@]}")
	preflight_rc="${pipeline_status[0]}"
	tee_rc="${pipeline_status[1]}"
	set -e
	if [[ "${tee_rc}" -ne 0 ]]; then
		echo "[evaluation] failed to retain preflight log" >&2
		exit 74
	fi
	if [[ "${preflight_rc}" -ne 0 ]]; then
		echo "[evaluation] preflight failed closed; log retained at ${RUN_DIR}/preflight.log" >&2
		exit "${preflight_rc}"
	fi
	run_repo_python "${CAMPAIGN_TOOL}" check-preflight \
		--manifest "${manifest}" --receipt "${RUN_DIR}/preflight.log"
	if [[ "${EVALUATION_INCLUDE_SCENARIO}" == "1" ]]; then
		mkdir -p "${RUN_DIR}/scenario"
		set +e
		run_repo_python "${CAMPAIGN_TOOL}" create-scenario \
			--repo "${ROOT}" \
			--micro-manifest "${manifest}" \
			--output "${RUN_DIR}/scenario/scenario-plan.json" \
			--boots "${EVALUATION_SCENARIO_BOOTS}" \
			--timeout "${EVALUATION_SCENARIO_TIMEOUT}" \
			--wsl-distro "${EVALUATION_WSL_DISTRO}" \
			2>&1 | tee "${RUN_DIR}/scenario-preflight.log"
		pipeline_status=("${PIPESTATUS[@]}")
		preflight_rc="${pipeline_status[0]}"
		tee_rc="${pipeline_status[1]}"
		set -e
		if [[ "${tee_rc}" -ne 0 ]]; then
			echo "[evaluation] failed to retain scenario preflight log" >&2
			exit 74
		fi
		if [[ "${preflight_rc}" -ne 0 ]]; then
			echo "[evaluation] scenario preflight failed closed; log retained at ${RUN_DIR}/scenario-preflight.log" >&2
			exit "${preflight_rc}"
		fi
		run_repo_python "${CAMPAIGN_TOOL}" check-preflight \
			--manifest "${RUN_DIR}/scenario/scenario-plan.json" \
			--receipt "${RUN_DIR}/scenario-preflight.log"
	fi

	local number boot_id challenge rc
	for ((number = 1; number <= EVALUATION_BOOTS; number++)); do
		printf -v boot_id 'boot-%02d' "${number}"
		challenge="$(run_repo_python "${CAMPAIGN_TOOL}" get-boot-field \
			--manifest "${manifest}" --boot-id "${boot_id}" --field challenge)"
		[[ "${challenge}" =~ ^[0-9a-f]{16}$ && "${challenge}" != "0000000000000000" ]] || {
			echo "[evaluation] invalid precommitted challenge for ${boot_id}" >&2
			exit 2
		}
		echo "[evaluation] ${boot_id}/${EVALUATION_BOOTS}: fresh build, image and QEMU boot"
		set +e
		run_repo_python "${CAMPAIGN_TOOL}" run-boot \
			--repo "${ROOT}" --manifest "${manifest}" --boot-id "${boot_id}" \
			--timeout "${EVALUATION_MICRO_TIMEOUT}"
		rc=$?
		set -e
		if [[ "${rc}" -ne 0 ]]; then
			echo "[evaluation] ${boot_id} failed; raw logs and campaign state were retained" >&2
			exit "${rc}"
		fi
	done

	run_repo_python "${CAMPAIGN_TOOL}" seal --manifest "${manifest}"
	run_repo_python "${CAMPAIGN_TOOL}" export-plan \
		--manifest "${manifest}" --output "${RUN_DIR}/run-plan.json"
	if [[ "${EVALUATION_INCLUDE_SCENARIO}" == "1" ]]; then
		require_file "${COMPATIBILITY_TOOL}"
		run_repo_python "${COMPATIBILITY_TOOL}" run \
			--repo "${ROOT}" \
			--work-dir "${RUN_DIR}/compatibility" \
			--micro-manifest "${manifest}" \
			--timeout "${EVALUATION_MICRO_TIMEOUT}"
		run_scenario_campaign "${manifest}"
	else
		echo "[evaluation] scenario disabled: this development run cannot support task 6"
	fi
	write_pointer latest-run.txt "${run_id}"
	echo "[evaluation] collection complete: ${RUN_DIR}"
	echo "[evaluation] run 'make evaluation-verify' before interpreting results"
}

route_formal_domain

case "${MODE}" in
doctor)
	run_platform_doctor
	;;
smoke)
	run_smoke
	;;
run)
	exec "${PYTHON_BIN}" -I -S "${TRUSTED_PYTHON_ENTRY}" "${CAMPAIGN_TOOL}" with-campaign-lock \
		--repo "${ROOT}" -- "${BASH_BIN}" \
		"${SCRIPT_DIR}/run-evaluation-suite.sh" __run_locked
	;;
__run_locked)
	lock_token="${AGENTOS_EVALUATION_CAMPAIGN_TOKEN:-}"
	[[ "${lock_token}" =~ ^[0-9a-f]{64}$ ]] || {
		echo "[evaluation] private collection mode requires a verified campaign lock" >&2
		exit 2
	}
	run_repo_python "${CAMPAIGN_TOOL}" verify-campaign-lock \
		--repo "${ROOT}" --token "${lock_token}"
	unset AGENTOS_EVALUATION_CAMPAIGN_TOKEN
	run_campaign
	;;
verify)
	resolve_run_dir
	verify_run
	echo "[evaluation] verified summary: ${RUN_DIR}/summary.json"
	;;
kernel-cost)
	resolve_run_dir
	verify_run
	require_file "${KERNEL_BUILD_TOOL}"
	require_file "${KERNEL_COST_TOOL}"
	make_candidate="${MAKE_TOOL:-make}"
	size_candidate="${SIZE_TOOL:-${TOOLPREFIX}size}"
	gcc_candidate="${TOOLPREFIX}gcc"
	set +e
	make_path="$(command -v -- "${make_candidate}")"
	make_status=$?
	size_path="$(command -v -- "${size_candidate}")"
	size_status=$?
	gcc_path="$(command -v -- "${gcc_candidate}")"
	gcc_status=$?
	set -e
	[[ "${make_status}" -eq 0 && -n "${make_path}" && "${make_path}" == /* ]] || {
		echo "[evaluation] MAKE_TOOL is unavailable or not absolute: ${make_candidate}" >&2
		exit 2
	}
	[[ "${size_status}" -eq 0 && -n "${size_path}" && "${size_path}" == /* ]] || {
		echo "[evaluation] SIZE_TOOL is unavailable or not absolute: ${size_candidate}" >&2
		exit 2
	}
	[[ "${gcc_status}" -eq 0 && -n "${gcc_path}" && "${gcc_path}" == /* ]] || {
		echo "[evaluation] TOOLPREFIX gcc is unavailable: ${gcc_candidate}" >&2
		exit 2
	}
	case "${gcc_path}" in
		*gcc.exe) toolprefix_path="${gcc_path%gcc.exe}" ;;
		*gcc) toolprefix_path="${gcc_path%gcc}" ;;
		*) echo "[evaluation] cannot derive an absolute TOOLPREFIX from ${gcc_path}" >&2; exit 2 ;;
	esac
	for tool_name in gcc ld objcopy objdump; do
		set +e
		resolved="$(command -v -- "${TOOLPREFIX}${tool_name}")"
		resolved_status=$?
		set -e
		[[ "${resolved_status}" -eq 0 ]] || resolved=""
		case "${resolved}" in
			"${toolprefix_path}${tool_name}"|"${toolprefix_path}${tool_name}.exe") ;;
			*) echo "[evaluation] ${tool_name} does not share the resolved TOOLPREFIX" >&2; exit 2 ;;
		esac
	done
	make_python_path="$(normalize_tool_path_for_python "${make_path}")"
	size_python_path="$(normalize_tool_path_for_python "${size_path}")"
	toolprefix_python_path="$(normalize_tool_path_for_python "${toolprefix_path}")"
	run_id="$(basename "${RUN_DIR}")"
	kernel_dir="${RUN_DIR}/kernel-build"
	kernel_config="${RUN_DIR}/kernel-cost-config.json"
	for output in "${kernel_dir}" "${kernel_config}" \
		"${RUN_DIR}/kernel-cost-report.json" "${RUN_DIR}/kernel-cost-fragment.json"; do
		[[ ! -e "${output}" && ! -L "${output}" ]] || {
			echo "[evaluation] refusing to replace kernel-cost evidence: ${output}" >&2
			exit 2
		}
	done
	cp -- ci/evaluation-kernel-cost.json "${kernel_config}"
	run_repo_python "${KERNEL_BUILD_TOOL}" build \
		--config ci/evaluation-kernel-cost.json --repository-root "${ROOT}" \
		--make-tool "${make_python_path}" --toolprefix "${toolprefix_python_path}" \
		--run-id "${run_id}" --output-dir "${kernel_dir}" \
		--evidence-root "${RUN_DIR}"
	run_repo_python "${KERNEL_COST_TOOL}" collect \
		--config "${kernel_config}" --repository-root "${ROOT}" \
		--environment-manifest "${kernel_dir}/environment.json" \
		--build-manifest "${kernel_dir}/kernel-build.json" \
		--size-tool "${size_python_path}" --evidence-root "${RUN_DIR}" \
		--output "${RUN_DIR}/kernel-cost-report.json"
	run_repo_python "${KERNEL_COST_TOOL}" verify \
		--config "${kernel_config}" \
		--report "${RUN_DIR}/kernel-cost-report.json" --evidence-root "${RUN_DIR}"
	run_repo_python "${KERNEL_COST_TOOL}" fragment \
		--config "${kernel_config}" \
		--report "${RUN_DIR}/kernel-cost-report.json" --evidence-root "${RUN_DIR}" \
		--output "${RUN_DIR}/kernel-cost-fragment.json"
	echo "[evaluation] trusted kernel cost: ${RUN_DIR}/kernel-cost-report.json"
	;;
full-verify)
	resolve_run_dir
	verify_run
	require_file "${FULL_VERIFICATION_TOOL}"
	commit="$("${PYTHON_BIN}" -I -S -c '
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
commit = value.get("run", {}).get("commit")
if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
    raise SystemExit("evaluation summary commit is invalid")
print(commit)
' "${RUN_DIR}/summary.json")"
	[[ "$(basename "${RUN_DIR}")" == "formal-${commit}" ]] || {
		echo "[evaluation] full-verification is unavailable for development runs" >&2
		exit 2
	}
	run_repo_python "${FULL_VERIFICATION_TOOL}" collect \
		--repo-root "${ROOT}" --output "${RUN_DIR}/full-verification" \
		--expected-commit "${commit}" --toolprefix "${TOOLPREFIX}" \
		--qemu "${QEMU}" --python "${PYTHON_BIN}" \
		--make "${MAKE_TOOL:-make}" --bash "${BASH_BIN}" \
		--host-cc "${HOST_CC:-cc}" \
		--command-timeout "${EVALUATION_FULL_VERIFY_TIMEOUT:-18000}"
	echo "[evaluation] verified full-verify payload: ${RUN_DIR}/full-verification"
	;;
dashboard)
	resolve_run_dir
	verify_run
	require_file "${DASHBOARD_TOOL}"
	run_repo_python "${DASHBOARD_TOOL}" \
		"${RUN_DIR}/summary.json" "${RUN_DIR}/dashboard"
	echo "[evaluation] dashboard: ${RUN_DIR}/dashboard/index.html"
	;;
package)
	resolve_run_dir
	verify_run
	require_file "${DASHBOARD_TOOL}"
	require_file "${BUNDLE_TOOL}"
	run_repo_python "${DASHBOARD_TOOL}" \
		"${RUN_DIR}/summary.json" "${RUN_DIR}/dashboard"
	run_id="$(basename "${RUN_DIR}")"
	bundle_dir="${EVALUATION_BUNDLE_DIR:-evidence/releases/evaluation-${run_id,,}}"
	run_repo_python "${BUNDLE_TOOL}" create \
		--run-dir "${RUN_DIR}" --suite "${CONTRACT_SUITE}" \
		--output "${bundle_dir}" --repo-root "${ROOT}"
	echo "[evaluation] portable evidence bundle: ${bundle_dir}"
	;;
verify-package)
	[[ -n "${EVALUATION_BUNDLE_DIR:-}" ]] || {
		echo "[evaluation] EVALUATION_BUNDLE_DIR is required for verify-package" >&2
		exit 2
	}
	require_file "${BUNDLE_TOOL}"
	run_repo_python "${BUNDLE_TOOL}" verify \
		--bundle "${EVALUATION_BUNDLE_DIR}" \
		--repo-root "${ROOT}" --require-committed
	;;
*)
	usage
	;;
esac
