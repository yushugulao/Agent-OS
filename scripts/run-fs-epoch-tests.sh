#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_BIN="${MAKE_TOOL:-make}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
INFLIGHT_DELAY_CANDIDATES="${INFLIGHT_DELAY_CANDIDATES:-0s 0.00005s 0.0001s 0.00025s 0.0005s 0.001s 0.002s 0.004s}"
INFLIGHT_MAX_ATTEMPTS="${INFLIGHT_MAX_ATTEMPTS:-12}"
FSEPOCH_QEMU_JOBS="${FSEPOCH_QEMU_JOBS:-3}"
SEALED_INIT_IMAGE=workflow_teardown_race_ucore
TMPDIR_FSEPOCH="$(mktemp -d)"
pids=()
had_initproc=0
if [[ -f os/initproc.S ]]; then
	had_initproc=1
	cp os/initproc.S "${TMPDIR_FSEPOCH}/initproc.S"
fi
collect_descendants() {
	local parent="$1" child

	command -v pgrep >/dev/null 2>&1 || return 0
	while read -r child; do
		[[ -n "${child}" ]] || continue
		printf '%s\n' "${child}"
		collect_descendants "${child}"
	done < <(pgrep -P "${parent}" 2>/dev/null || true)
}

terminate_pending() {
	local pid child attempt alive index
	local descendants=()

	for pid in "${pids[@]}"; do
		descendants=()
		kill -0 "${pid}" 2>/dev/null || continue
		mapfile -t descendants < <(collect_descendants "${pid}")
		for child in "${descendants[@]}"; do
			kill -TERM "${child}" 2>/dev/null || true
		done
		kill -TERM "${pid}" 2>/dev/null || true
	done
	for attempt in $(seq 1 30); do
		alive=0
		for pid in "${pids[@]}"; do
			if kill -0 "${pid}" 2>/dev/null; then
				alive=1
				break
			fi
		done
		((alive == 0)) && break
		sleep 0.1
	done
	for pid in "${pids[@]}"; do
		if kill -0 "${pid}" 2>/dev/null; then
			mapfile -t descendants < <(collect_descendants "${pid}")
			for ((index = ${#descendants[@]} - 1; index >= 0; index--)); do
				kill -KILL "${descendants[index]}" 2>/dev/null || true
			done
			kill -KILL "${pid}" 2>/dev/null || true
		fi
		wait "${pid}" 2>/dev/null || true
	done
	pids=()
}

cleanup() {
	local status=$?
	trap - EXIT INT TERM HUP
	terminate_pending
	if [[ "${had_initproc}" -eq 1 ]]; then
		cp "${TMPDIR_FSEPOCH}/initproc.S" os/initproc.S
	else
		rm -f os/initproc.S
	fi
	if [[ "${KEEP_FSEPOCH_TMP:-0}" == 1 ]]; then
		echo "[fs-epoch] retained ${TMPDIR_FSEPOCH}" >&2
	else
		rm -rf "${TMPDIR_FSEPOCH}"
	fi
	exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

case "${FSEPOCH_QEMU_JOBS}" in
	''|*[!0-9]*) echo "[fs-epoch] FSEPOCH_QEMU_JOBS must be an integer" >&2; exit 64 ;;
esac
if ((FSEPOCH_QEMU_JOBS < 1 || FSEPOCH_QEMU_JOBS > 3)); then
	echo "[fs-epoch] FSEPOCH_QEMU_JOBS must be between 1 and 3" >&2
	exit 64
fi
case "${INFLIGHT_MAX_ATTEMPTS}" in
	''|*[!0-9]*) echo "[fs-epoch] INFLIGHT_MAX_ATTEMPTS must be an integer" >&2; exit 64 ;;
esac
read -r -a inflight_delays <<<"${INFLIGHT_DELAY_CANDIDATES}"
if ((${#inflight_delays[@]} == 0 ||
     ${#inflight_delays[@]} > INFLIGHT_MAX_ATTEMPTS)); then
	echo "[fs-epoch] inflight delay campaign must contain 1-${INFLIGHT_MAX_ATTEMPTS} attempts" >&2
	exit 64
fi
read -r -a cases <<<"${FSEPOCH_CASES:-dirty inflight durable}"
if ((${#cases[@]} == 0)); then
	echo "[fs-epoch] FSEPOCH_CASES must select at least one case" >&2
	exit 64
fi
declare -A selected_cases
for case_name in "${cases[@]}"; do
	case "${case_name}" in
		dirty|inflight|durable) ;;
		*) echo "[fs-epoch] unsupported case ${case_name}" >&2; exit 64 ;;
	esac
	if [[ -n "${selected_cases[$case_name]+selected}" ]]; then
		echo "[fs-epoch] duplicate case ${case_name}" >&2
		exit 64
	fi
	selected_cases["${case_name}"]=1
done

source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_FSEPOCH}" "${HOST_CC}"

"${PYTHON_BIN}" scripts/test-fs-epoch-regression.py
"${PYTHON_BIN}" -m unittest \
	scripts.test-agent-test-runner.AgentTestRunnerTests.test_powercut_delay_requires_powercut_completion \
	scripts.test-agent-test-runner.AgentTestRunnerTests.test_powercut_delay_monitors_guest_until_cut_deadline

host_probe_compile "${TMPDIR_FSEPOCH}/mkfs" \
	nfs/fs.c nfs/host_image_snapshot.c

kernel="${TMPDIR_FSEPOCH}/kernel-build/kernel"
"${MAKE_BIN}" -B "${kernel}" TOOLPREFIX="${TOOLPREFIX}" \
	BUILDDIR="${TMPDIR_FSEPOCH}/kernel-build" LOG="${FSEPOCH_KERNEL_LOG:-error}" \
	INIT_PROC="${SEALED_INIT_IMAGE}" DURABILITY_POWERCUT_TEST_PROFILE=1

build_case() {
	local case_name="$1" case_id="$2"
	local user_build="${TMPDIR_FSEPOCH}/${case_name}-user-build"
	local user_target="${TMPDIR_FSEPOCH}/${case_name}-user-target"
	local user_asm="${TMPDIR_FSEPOCH}/${case_name}-user-asm"
	local elf="${user_build}/riscv64/fsepoch_ucore"

	"${MAKE_BIN}" -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=agent \
		USER_EXTRA_CFLAGS="-Werror -DFSEPOCH_CASE=${case_id}" \
		build_dir="${user_build}" out_dir="${user_target}" \
		asm_dir="${user_asm}" "${elf}"
	mkdir -p "${user_target}/bin" "${user_target}/elf"
	# 复用现有可信 bootstrap/public carrier 对，使每次重启进入同一持久 PUBLIC 资源域。
	cp "${user_build}/bin/fsepoch_ucore" \
		"${user_target}/bin/${SEALED_INIT_IMAGE}"
	cp "${elf}" "${user_target}/elf/${SEALED_INIT_IMAGE}"
	host_probe_run "${TMPDIR_FSEPOCH}/mkfs" \
		"${TMPDIR_FSEPOCH}/${case_name}.img" \
		"${user_target}/bin/${SEALED_INIT_IMAGE}"
}

for case_name in "${cases[@]}"; do
	case "${case_name}" in
		dirty) build_case dirty 1 ;;
		inflight) build_case inflight 2 ;;
		durable) build_case durable 3 ;;
	esac
done

run_boot() {
	local case_name="$1" stage="$2" marker="$3" completion="$4"
	local delay="${5:-0s}"
	local image="${6:-${TMPDIR_FSEPOCH}/${case_name}.img}"
	local log="${TMPDIR_FSEPOCH}/${case_name}-${stage}.log"
	local host_log="${TMPDIR_FSEPOCH}/${case_name}-${stage}.host.log"
	local args=(
		"${PYTHON_BIN}" scripts/agent_test_runner.py
		--init-proc "fsepoch_ucore-${case_name}-${stage}"
		--marker "${marker}" --marker-mode exact-line
		--log-file "${log}" --case-timeout "${CASE_TIMEOUT}"
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}"
		--marker-grace-seconds 0s
		--qemu "${QEMU}" --kernel "${kernel}"
		--image "${image}"
		--completion-mode "${completion}"
	)
	if [[ "${delay}" != 0s ]]; then
		args+=(--powercut-delay-seconds "${delay}")
	fi
	if ! "${args[@]}" >"${host_log}" 2>&1; then
		cat "${host_log}" >&2
		return 1
	fi
	[[ -s "${log}" ]] || return 65
}

select_inflight_fault() {
	local case_name="$1"
	local image="${TMPDIR_FSEPOCH}/${case_name}.img"
	local prepared_image="${TMPDIR_FSEPOCH}/${case_name}-prepared.img"
	local calibration="${TMPDIR_FSEPOCH}/${case_name}-calibration.tsv"
	local attempt=0 delay stage candidate probe status outcome

	cp "${image}" "${prepared_image}"
	printf 'attempt\tdelay\tprobe_status\toutcome\n' >"${calibration}"
	for delay in "${inflight_delays[@]}"; do
		attempt=$((attempt + 1))
		printf -v stage 'fault-attempt-%02d' "${attempt}"
		candidate="${TMPDIR_FSEPOCH}/${case_name}-${stage}.img"
		probe="${TMPDIR_FSEPOCH}/${case_name}-${stage}.probe.json"
		cp "${prepared_image}" "${candidate}"
		run_boot "${case_name}" "${stage}" \
			"fsepoch_ucore: powercut_window case=inflight point=fsync_enter" \
			powercut "${delay}" "${candidate}"
		snapshot "${candidate}" \
			"${TMPDIR_FSEPOCH}/${case_name}-${stage}.json"
		if "${PYTHON_BIN}" scripts/verify-fs-epoch-image.py \
			--case inflight --probe-inflight \
			--before "${TMPDIR_FSEPOCH}/${case_name}-before.json" \
			--fault "${TMPDIR_FSEPOCH}/${case_name}-${stage}.json" \
			--output "${probe}"; then
			status=0
			outcome=selected
		else
			status=$?
			if [[ "${status}" -eq 3 ]]; then
				outcome=window-miss
			else
				cat "${probe}" 2>/dev/null || true
				cat "${TMPDIR_FSEPOCH}/${case_name}-${stage}.log" >&2
				return "${status}"
			fi
		fi
		printf '%d\t%s\t%d\t%s\n' \
			"${attempt}" "${delay}" "${status}" "${outcome}" \
			>>"${calibration}"
		if [[ "${status}" -eq 0 ]]; then
			cp "${candidate}" "${image}"
			cp "${TMPDIR_FSEPOCH}/${case_name}-${stage}.json" \
				"${TMPDIR_FSEPOCH}/${case_name}-fault.json"
			cp "${TMPDIR_FSEPOCH}/${case_name}-${stage}.log" \
				"${TMPDIR_FSEPOCH}/${case_name}-fault.log"
			cp "${TMPDIR_FSEPOCH}/${case_name}-${stage}.host.log" \
				"${TMPDIR_FSEPOCH}/${case_name}-fault.host.log"
			printf '%d\n' "${attempt}" \
				>"${TMPDIR_FSEPOCH}/${case_name}-selected-attempt"
			printf '%s\n' "${delay}" \
				>"${TMPDIR_FSEPOCH}/${case_name}-selected-delay"
			cat "${calibration}"
			cat "${probe}"
			return 0
		fi
	done
	cat "${calibration}" >&2
	echo "[fs-epoch] no replay landed in the measured inflight window" >&2
	return 1
}

snapshot() {
	local image="$1" output="$2"
	"${PYTHON_BIN}" scripts/fs-allocator-image.py snapshot \
		"${image}" --output "${output}"
}

run_case() {
	local case_name="$1"
	local image="${TMPDIR_FSEPOCH}/${case_name}.img"
	local fault_marker delay selected_attempt selected_delay
	local verify_args

	run_boot "${case_name}" prepare \
		"fsepoch_ucore: prepared case=${case_name} blocks=8" checkpoint
	snapshot "${image}" "${TMPDIR_FSEPOCH}/${case_name}-before.json"
	case "${case_name}" in
		dirty)
			fault_marker="fsepoch_ucore: powercut_window case=dirty point=before_fsync"
			delay=0s
			;;
		inflight)
			fault_marker="fsepoch_ucore: powercut_window case=inflight point=fsync_enter"
			delay=0s
			;;
		durable)
			fault_marker="fsepoch_ucore: powercut_window case=durable point=after_fsync"
			delay=0s
			;;
		*) return 64 ;;
	esac
	if [[ "${case_name}" == inflight ]]; then
		select_inflight_fault "${case_name}"
	else
		run_boot "${case_name}" fault "${fault_marker}" powercut "${delay}"
		snapshot "${image}" "${TMPDIR_FSEPOCH}/${case_name}-fault.json"
	fi
	run_boot "${case_name}" retry \
		"fsepoch_ucore: retry_durable_checkpoint case=${case_name}" powercut
	snapshot "${image}" "${TMPDIR_FSEPOCH}/${case_name}-retry.json"
	run_boot "${case_name}" final \
		"fsepoch_ucore: parent passed case=${case_name} blocks=9" checkpoint
	snapshot "${image}" "${TMPDIR_FSEPOCH}/${case_name}-final.json"
	"${PYTHON_BIN}" scripts/verify-fs-epoch-log.py --case "${case_name}" \
		--fault-log "${TMPDIR_FSEPOCH}/${case_name}-fault.log" \
		--retry-log "${TMPDIR_FSEPOCH}/${case_name}-retry.log" \
		--final-log "${TMPDIR_FSEPOCH}/${case_name}-final.log"
	verify_args=(
		"${PYTHON_BIN}" scripts/verify-fs-epoch-image.py --case "${case_name}"
		--before "${TMPDIR_FSEPOCH}/${case_name}-before.json"
		--fault "${TMPDIR_FSEPOCH}/${case_name}-fault.json"
		--retry "${TMPDIR_FSEPOCH}/${case_name}-retry.json"
		--final "${TMPDIR_FSEPOCH}/${case_name}-final.json"
		--output "${TMPDIR_FSEPOCH}/${case_name}-verified.json"
	)
	if [[ "${case_name}" == inflight ]]; then
		selected_attempt="$(<"${TMPDIR_FSEPOCH}/${case_name}-selected-attempt")"
		selected_delay="$(<"${TMPDIR_FSEPOCH}/${case_name}-selected-delay")"
		verify_args+=(--calibration-attempt "${selected_attempt}" \
			--calibration-delay "${selected_delay}")
	fi
	"${verify_args[@]}"
}

active=0
declare -A pid_case
wait_case() {
	local pid="$1" case_name="${pid_case[$1]}"

	if ! wait "${pid}"; then
		cat "${TMPDIR_FSEPOCH}/${case_name}.run.log" >&2
		return 1
	fi
}
for case_name in "${cases[@]}"; do
	if [[ "${case_name}" == inflight ]]; then
		if ! run_case "${case_name}" \
			>"${TMPDIR_FSEPOCH}/${case_name}.run.log" 2>&1; then
			cat "${TMPDIR_FSEPOCH}/${case_name}.run.log" >&2
			exit 1
		fi
	fi
done
for case_name in "${cases[@]}"; do
	[[ "${case_name}" == inflight ]] && continue
	run_case "${case_name}" >"${TMPDIR_FSEPOCH}/${case_name}.run.log" 2>&1 &
	pids+=("$!")
	pid_case["$!"]="${case_name}"
	active=$((active + 1))
	if ((active == FSEPOCH_QEMU_JOBS)); then
		wait_case "${pids[0]}"
		pids=("${pids[@]:1}")
		active=$((active - 1))
	fi
done
for pid in "${pids[@]}"; do
	wait_case "${pid}"
done

for case_name in "${cases[@]}"; do
	cat "${TMPDIR_FSEPOCH}/${case_name}.run.log"
	cat "${TMPDIR_FSEPOCH}/${case_name}-verified.json"
	if [[ "${case_name}" == inflight ]]; then
		cat "${TMPDIR_FSEPOCH}/${case_name}-calibration.tsv"
	fi
done

if [[ -n "${FS_EPOCH_ARTIFACT_DIR:-}" ]]; then
	mkdir -p "${FS_EPOCH_ARTIFACT_DIR}"
	for case_name in "${cases[@]}"; do
		cp "${TMPDIR_FSEPOCH}/${case_name}-verified.json" \
			"${FS_EPOCH_ARTIFACT_DIR}/"
		cp "${TMPDIR_FSEPOCH}/${case_name}-"*.log \
			"${FS_EPOCH_ARTIFACT_DIR}/"
		if [[ "${case_name}" == inflight ]]; then
			cp "${TMPDIR_FSEPOCH}/${case_name}-calibration.tsv" \
				"${FS_EPOCH_ARTIFACT_DIR}/"
		fi
	done
fi

echo "[fs-epoch] ordered batching, fsync, reboot replay, and power-cut windows verified"
echo "[fs-epoch] same-epoch EIO retry is not claimed: no authorized normal-I/O one-shot hook"
host_probe_report "fs-epoch mkfs"
