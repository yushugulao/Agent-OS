#!/usr/bin/env bash
# Run the filesystem allocator fault/reboot matrix and inspect the resulting images.

if [[ "${1:-}" != "--internal-hermetic-shell" ]]; then
	builtin exec /usr/bin/env -i \
		PATH=/usr/bin:/bin LC_ALL=C LANG=C TZ=UTC \
		TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}" \
		HOST_CC="${HOST_CC:-${HOSTCC:-cc}}" \
		QEMU="${QEMU:-qemu-system-riscv64}" \
		PYTHON_BIN="${PYTHON_BIN:-python3}" MAKE_TOOL="${MAKE_TOOL:-make}" \
		CASE_TIMEOUT="${CASE_TIMEOUT:-60s}" \
		IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}" \
		MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}" \
		/bin/bash --noprofile --norc -p "$0" \
		--internal-hermetic-shell "$@"
fi
shift
set -euo pipefail
unset BASH_ENV ENV ASAN_OPTIONS UBSAN_OPTIONS

if [[ "${1:-}" == "--hermetic-shell-selftest" ]]; then
	[[ $# -eq 1 && -z "${BASH_ENV:-}" && -z "${ENV:-}" ]]
	if env | grep -q '^BASH_FUNC_'; then
		exit 70
	fi
	echo "fsalloc-hermetic-shell: passed"
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_BIN="${MAKE_TOOL:-make}"
CASE_TIMEOUT="${CASE_TIMEOUT:-60s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"

if [[ "${HOST_CC}" =~ [[:space:]] ]]; then
	echo "[fs-allocator-fault] HOST_CC must name one executable" >&2
	exit 64
fi

PYTHON_BIN="$(command -v "${PYTHON_BIN}")"
QEMU="$(command -v "${QEMU}")"
MAKE_BIN="$(command -v "${MAKE_BIN}")"
HOST_CC="$(command -v "${HOST_CC}")"
CROSS_GCC="$(command -v "${TOOLPREFIX}gcc")"
CROSS_LD="$(command -v "${TOOLPREFIX}ld")"
CROSS_OBJCOPY="$(command -v "${TOOLPREFIX}objcopy")"
CROSS_OBJDUMP="$(command -v "${TOOLPREFIX}objdump")"
export HOST_CC

TMPDIR_FSALLOC="$(mktemp -d)"
PROFILE_KERNEL="${TMPDIR_FSALLOC}/fsalloc-profile-kernel"
MUTANT_KERNEL="${TMPDIR_FSALLOC}/fsalloc-delete-barrier-mutant-kernel"
MKFS_BIN="${TMPDIR_FSALLOC}/mkfs"
cleanup() {
	[[ -n "${TMPDIR_FSALLOC:-}" && "${TMPDIR_FSALLOC}" == /tmp/* ]] || return
	rm -rf -- "${TMPDIR_FSALLOC}"
}
trap cleanup EXIT

source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_FSALLOC}" "${HOST_CC}"

"${PYTHON_BIN}" -I -S -B scripts/test-fs-allocator-image.py
"${PYTHON_BIN}" -I -S -B scripts/check-fs-allocator-state.py

if ! grep -q 'FS_ALLOCATOR_FAULT_TEST_PROFILE' os/fs.c ||
   ! grep -q 'SYS_fs_allocator_fault_test' os/syscall.c ||
   ! grep -q 'FS_ALLOCATOR_FAULT_TEST_PROFILE' Makefile; then
	echo "[fs-allocator-fault] dynamic fault profile is not wired" >&2
	exit 77
fi

operations=(alloc free ialloc ifree)
operation_ids=(1 2 3 4)
phases=(intent bitmap owner refund)
phase_ids=(1 2 3 4)
actions=(busy eio crash)
action_ids=(1 2 3)

case_supported() {
	local op="$1" phase="$2"
	case "${op}:${phase}" in
		alloc:intent|alloc:bitmap|alloc:owner|\
		free:intent|free:bitmap|free:owner|free:refund|\
		ialloc:intent|ialloc:owner|\
		ifree:intent|ifree:owner|ifree:refund) return 0 ;;
		*) return 1 ;;
	esac
}

build_profile_kernel() {
	local kernel_build="${TMPDIR_FSALLOC}/kernel-build"
	"${MAKE_BIN}" build TOOLPREFIX="${TOOLPREFIX}" \
		CC="${CROSS_GCC}" AS="${CROSS_GCC}" LD="${CROSS_LD}" \
		OBJCOPY="${CROSS_OBJCOPY}" OBJDUMP="${CROSS_OBJDUMP}" \
		LOG=error BUILDDIR="${kernel_build}" INIT_PROC=fsallocfault_ucore \
		PYTHON_BIN="${PYTHON_BIN}" FS_ALLOCATOR_FAULT_TEST_PROFILE=1
	cp "${kernel_build}/kernel" "${PROFILE_KERNEL}"
}

build_mutant_kernel() {
	local kernel_build="${TMPDIR_FSALLOC}/mutant-kernel-build"
	"${MAKE_BIN}" build TOOLPREFIX="${TOOLPREFIX}" \
		CC="${CROSS_GCC}" AS="${CROSS_GCC}" LD="${CROSS_LD}" \
		OBJCOPY="${CROSS_OBJCOPY}" OBJDUMP="${CROSS_OBJDUMP}" \
		LOG=error BUILDDIR="${kernel_build}" INIT_PROC=fsallocfault_ucore \
		PYTHON_BIN="${PYTHON_BIN}" FS_ALLOCATOR_FAULT_TEST_PROFILE=1 \
		FS_ALLOCATOR_DELETE_BARRIER_MUTANT=1
	cp "${kernel_build}/kernel" "${MUTANT_KERNEL}"
	if cmp -s "${PROFILE_KERNEL}" "${MUTANT_KERNEL}"; then
		echo "[fs-allocator-fault] delete-FLUSH mutant kernel is unchanged" >&2
		exit 1
	fi
}

build_case() {
	local tag="$1" op_id="$2" phase_id="$3" action_id="$4"
	local user_build="${TMPDIR_FSALLOC}/${tag}-user-build"
	local user_target="${TMPDIR_FSALLOC}/${tag}-user-target"
	local user_asm="${TMPDIR_FSALLOC}/${tag}-user-asm"
	local user_elf="${user_build}/riscv64/fsallocfault_ucore"

	"${MAKE_BIN}" -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=agent \
		CC="${CROSS_GCC}" OBJCOPY="${CROSS_OBJCOPY}" \
		OBJDUMP="${CROSS_OBJDUMP}" PYTHON_BIN="${PYTHON_BIN}" \
		USER_EXTRA_CFLAGS="-Werror -DFSALLOC_FAULT_OP=${op_id} -DFSALLOC_FAULT_PHASE=${phase_id} -DFSALLOC_FAULT_ACTION=${action_id}" \
		build_dir="${user_build}" out_dir="${user_target}" \
		asm_dir="${user_asm}" "${user_elf}"
	mkdir -p "${user_target}/bin" "${user_target}/elf"
	cp "${user_build}/bin/fsallocfault_ucore" "${user_target}/bin/"
	cp "${user_elf}" "${user_target}/elf/"
	host_probe_run "${MKFS_BIN}" "${TMPDIR_FSALLOC}/${tag}.img" \
		"${user_target}/bin/fsallocfault_ucore"
}

run_case() {
	local tag="$1" marker="$2" completion="$3" stage="$4"
	local kernel="${5:-${PROFILE_KERNEL}}"
	local image="${6:-${TMPDIR_FSALLOC}/${tag}.img}"
	local log="${TMPDIR_FSALLOC}/${tag}-${stage}.log"
	local grace="${MARKER_GRACE_SECONDS}"
	local completion_args=()
	local runner_argv=()
	local status

	if [[ "${completion}" != natural ]]; then
		completion_args+=(--completion-mode "${completion}")
	fi
	if [[ "${completion}" == powercut ]]; then
		grace=0s
	fi
	runner_argv=(
		"${PYTHON_BIN}" -I -S -B scripts/agent_test_runner.py
		--init-proc fsallocfault_ucore
		--marker "${marker}" --marker-mode exact-line
		--log-file "${log}"
		--case-timeout "${CASE_TIMEOUT}"
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}"
		--marker-grace-seconds "${grace}"
		--qemu "${QEMU}" --kernel "${kernel}"
		--image "${image}" "${completion_args[@]}"
	)
	if "${runner_argv[@]}"; then
		status=0
	else
		status=$?
	fi
	[[ ${status} -eq 0 ]] || return "${status}"
	if [[ ! -s "${log}" ]]; then
		echo "[fs-allocator-fault] ${tag}/${stage} Guest log is empty" >&2
		return 65
	fi
	LAST_RUN_LOG="${log}"
}

verify_case() {
	local before="$1" fault="$2" reboot="$3"
	local operation="$4" phase="$5" action="$6" output="$7"
	"${PYTHON_BIN}" -I -S -B scripts/fs-allocator-image.py verify-case-raw \
		"${before}" "${fault}" "${reboot}" \
		--operation "${operation}" --phase "${phase}" --action "${action}" \
		--require-metadata-cow --output "${output}"
}

build_profile_kernel
host_probe_compile "${MKFS_BIN}" nfs/fs.c nfs/host_image_snapshot.c

case_count=0
for op_index in "${!operations[@]}"; do
	for phase_index in "${!phases[@]}"; do
		if ! case_supported "${operations[op_index]}" "${phases[phase_index]}"; then
			continue
		fi
		for action_index in "${!actions[@]}"; do
			op="${operations[op_index]}"
			phase="${phases[phase_index]}"
			action="${actions[action_index]}"
			tag="${op}-${phase}-${action}"
			build_case "${tag}" "${operation_ids[op_index]}" \
				"${phase_ids[phase_index]}" "${action_ids[action_index]}"
			run_case "${tag}" \
				"fsallocfault_ucore: case=${op} phase=${phase} action=${action} prepared=1" \
				checkpoint prepare
			cp "${TMPDIR_FSALLOC}/${tag}.img" \
				"${TMPDIR_FSALLOC}/${tag}-before.img"
			if [[ "${action}" == crash ]]; then
				marker="fsallocfault_kernel: case=${op} phase=${phase} crash_checkpoint=1"
				run_case "${tag}" "${marker}" powercut fault
			else
				run_case "${tag}" \
					"fsallocfault_ucore: runtime_verified=1" natural fault
				grep -Fxq "fsallocfault_ucore: runtime_verified=1" \
					"${TMPDIR_FSALLOC}/${tag}-fault.log"
			fi
			cp "${TMPDIR_FSALLOC}/${tag}.img" \
				"${TMPDIR_FSALLOC}/${tag}-fault.img"
			run_case "${tag}" \
				"fsallocfault_ucore: case=${op} phase=${phase} action=${action} reboot_ready=1" \
				natural reboot
			verify_case "${TMPDIR_FSALLOC}/${tag}-before.img" \
				"${TMPDIR_FSALLOC}/${tag}-fault.img" \
				"${TMPDIR_FSALLOC}/${tag}.img" "${op}" "${phase}" "${action}" \
				"${TMPDIR_FSALLOC}/${tag}-verified.json"
			case_count=$((case_count + 1))
			echo "[fs-allocator-fault] verified ${tag}"
		done
	done
done

if [[ ${case_count} -ne 36 ]]; then
	echo "[fs-allocator-fault] expected 36 cases, ran ${case_count}" >&2
	exit 1
fi

build_mutant_kernel
mutation_tag="mutation-alloc-intent-crash"
mutation_image="${TMPDIR_FSALLOC}/${mutation_tag}.img"
mutation_before="${TMPDIR_FSALLOC}/${mutation_tag}-before.img"
mutation_fault="${TMPDIR_FSALLOC}/${mutation_tag}-fault.img"
mutation_reboot="${TMPDIR_FSALLOC}/${mutation_tag}-reboot.img"
cp "${TMPDIR_FSALLOC}/alloc-intent-crash-before.img" "${mutation_before}"
cp "${mutation_before}" "${mutation_image}"
run_case "${mutation_tag}" \
	"fsallocfault_kernel: durability_receipt_failed=1" powercut fault \
	"${MUTANT_KERNEL}" "${mutation_image}"
mutation_log="${LAST_RUN_LOG}"
cp "${mutation_image}" "${mutation_fault}"
run_case "${mutation_tag}" \
	"fsallocfault_ucore: case=alloc phase=intent action=crash reboot_ready=1" \
	natural reboot "${PROFILE_KERNEL}" "${mutation_image}"
cp "${mutation_image}" "${mutation_reboot}"

if [[ $(grep -Fxc "fsallocfault_kernel: durability_receipt_failed=1" \
	"${mutation_log}" || true) -ne 1 ]]; then
	echo "[fs-allocator-fault] mutant did not report one durability failure" >&2
	exit 1
fi
if [[ $(grep -Ec '^fsalloc-cache: mutation=delete-flush target=allocator-phase-barrier durable_epoch=[1-9][0-9]* pending_at_powercut=1 discarded_on_powercut=1 powercut=1$' \
	"${mutation_log}" || true) -ne 1 ]]; then
	echo "[fs-allocator-fault] mutant did not lose exactly one pending qmap write" >&2
	exit 1
fi

"${PYTHON_BIN}" -I -S -B scripts/fs-allocator-image.py snapshot \
	"${mutation_fault}" --output "${TMPDIR_FSALLOC}/mutation-fault-snapshot.json"
"${PYTHON_BIN}" -I -S -B - "${TMPDIR_FSALLOC}/mutation-fault-snapshot.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    snapshot = json.load(handle)
if any(entry["state"] == "ALLOCATING" for entry in snapshot["qmap_entries"].values()):
    raise SystemExit("mutant unexpectedly persisted the bypassed alloc intent")
PY

verify_case "${mutation_before}" "${mutation_fault}" "${mutation_reboot}" \
	alloc intent busy "${TMPDIR_FSALLOC}/mutation-busy-control.json"
mutation_error="${TMPDIR_FSALLOC}/mutation-verifier.stderr"
if verify_case "${mutation_before}" "${mutation_fault}" "${mutation_reboot}" \
	alloc intent crash "${TMPDIR_FSALLOC}/mutation-crash-result.json" \
	2>"${mutation_error}"; then
	echo "[fs-allocator-fault] delete-FLUSH mutant escaped image verification" >&2
	exit 1
fi
if ! grep -Fq '"code":"FS_ALLOCATOR_IMAGE_INVALID"' "${mutation_error}"; then
	echo "[fs-allocator-fault] mutant failed for an unexpected verifier reason" >&2
	cat "${mutation_error}" >&2
	exit 1
fi

echo "[fs-allocator-fault] 36 fault/reboot cases and delete-FLUSH mutant passed"
host_probe_report "fs-allocator-fault mkfs"
