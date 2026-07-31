#!/usr/bin/env bash
# Filesystem allocator semantic fault/reboot matrix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-60s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
TMPDIR_FSALLOC="$(mktemp -d)"
PROFILE_KERNEL="${TMPDIR_FSALLOC}/fsalloc-profile-kernel"
MUTANT_KERNEL="${TMPDIR_FSALLOC}/fsalloc-delete-barrier-mutant-kernel"
EVIDENCE_ROOT="${FS_ALLOCATOR_ARTIFACT_DIR:-${TMPDIR_FSALLOC}/fs-allocator-evidence}"
EVIDENCE_ARCHIVE="${FS_ALLOCATOR_EVIDENCE_ARCHIVE:-${TMPDIR_FSALLOC}/fs-allocator-evidence.tar}"
PROFILE_COMPILE_ARGV_JSON="${TMPDIR_FSALLOC}/profile-compile-argv.json"
MUTANT_COMPILE_ARGV_JSON="${TMPDIR_FSALLOC}/mutant-compile-argv.json"
BASE_LAUNCH_ARGV_JSON="${TMPDIR_FSALLOC}/base-launch-argv.json"
PYTHON_BIN="$(command -v "${PYTHON_BIN}")"
QEMU="$(command -v "${QEMU}")"
had_initproc=0
if [[ -f os/initproc.S ]]; then
	had_initproc=1
	cp os/initproc.S "${TMPDIR_FSALLOC}/initproc.S"
fi
cleanup() {
	if [[ "${had_initproc}" -eq 1 ]]; then
		cp "${TMPDIR_FSALLOC}/initproc.S" os/initproc.S
	else
		rm -f os/initproc.S
	fi
	rm -rf "${TMPDIR_FSALLOC}"
}
trap cleanup EXIT
source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_FSALLOC}"

if [[ -n "${FS_ALLOCATOR_ARTIFACT_DIR:-}" &&
      "${FS_ALLOCATOR_ARTIFACT_DIR}" != /* ]]; then
	echo "[fs-allocator-fault] FS_ALLOCATOR_ARTIFACT_DIR must be absolute" >&2
	exit 64
fi
if [[ -n "${FS_ALLOCATOR_EVIDENCE_ARCHIVE:-}" &&
      "${FS_ALLOCATOR_EVIDENCE_ARCHIVE}" != /* ]]; then
	echo "[fs-allocator-fault] FS_ALLOCATOR_EVIDENCE_ARCHIVE must be absolute" >&2
	exit 64
fi
if [[ "${EVIDENCE_ARCHIVE##*/}" != fs-allocator-evidence.tar ]]; then
	echo "[fs-allocator-fault] evidence archive basename must be fs-allocator-evidence.tar" >&2
	exit 64
fi
if [[ -e "${EVIDENCE_ARCHIVE}" || -L "${EVIDENCE_ARCHIVE}" ]]; then
	echo "[fs-allocator-fault] evidence archive destination already exists" >&2
	exit 64
fi

write_argv_json() {
	local output="$1"
	shift
	"${PYTHON_BIN}" - "${output}" "$@" <<'PY'
import json
import os
import sys

path = sys.argv[1]
payload = (json.dumps(sys.argv[2:], ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
PY
}

RUN_ID="$(${PYTHON_BIN} -c 'import secrets; print(secrets.token_hex(32))')"
"${PYTHON_BIN}" scripts/fs-allocator-evidence.py capture-run \
	--root "${EVIDENCE_ROOT}" --source-root . --run-id "${RUN_ID}" \
	--qemu "${QEMU}" --python "${PYTHON_BIN}" --toolprefix "${TOOLPREFIX}"

read_unsigned_define() {
	local header="$1" name="$2"
	"${PYTHON_BIN}" - "${header}" "${name}" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="ascii").read()
matches = re.findall(rf"^#define[ \t]+{re.escape(sys.argv[2])}[ \t]+([0-9]+)U?[ \t]*$", text, re.MULTILINE)
if len(matches) != 1:
    raise SystemExit(f"expected one unsigned define for {sys.argv[2]}")
print(matches[0])
PY
}

write_mutant_selection_diff() {
	local output="$1"
	"${PYTHON_BIN}" - os/fs.c "${output}" <<'PY'
import hashlib
import os
import sys

source_path, output_path = sys.argv[1:]
raw = open(source_path, "rb").read()
text = raw.decode("ascii")
guard = """#ifdef FS_ALLOCATOR_DELETE_BARRIER_MUTANT
	/* Negative acceptance profile: the volatile overlay must expose this. */
	result = 0;
#else
	result = fs_durable_barrier_forward();
#endif"""
if text.count(guard) != 1:
    raise SystemExit("allocator barrier mutant guard is absent or ambiguous")
payload = """# source_sha256={source_sha256}
# compile_guard=FS_ALLOCATOR_DELETE_BARRIER_MUTANT
--- os/fs.c:baseline-selected
+++ os/fs.c:mutant-selected
@@ FS_ALLOCATOR_DELETE_BARRIER_MUTANT @@
-\tresult = fs_durable_barrier_forward();
+\tresult = 0; /* FS_ALLOCATOR_DELETE_BARRIER_MUTANT */
""".format(source_sha256=hashlib.sha256(raw).hexdigest()).encode("ascii")
with os.fdopen(os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
PY
}

"${PYTHON_BIN}" scripts/test-fs-allocator-image.py
"${PYTHON_BIN}" scripts/check-fs-allocator-state.py
OVERLAY_BLOCKS="$(read_unsigned_define fs_allocator_test_abi.h \
	FSALLOC_DURABILITY_OVERLAY_CAPACITY)"
BACKEND_ABI_VERSION="$(read_unsigned_define fs_allocator_test_abi.h \
	FSALLOC_DURABILITY_BACKEND_ABI_VERSION)"

if ! grep -q 'FS_ALLOCATOR_FAULT_TEST_PROFILE' os/fs.c ||
   ! grep -q 'SYS_fs_allocator_fault_test' os/syscall.c ||
   ! grep -q 'FS_ALLOCATOR_FAULT_TEST_PROFILE' Makefile; then
	echo "[fs-allocator-fault] dynamic profile is not wired; host raw-image tests passed" >&2
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

	PROFILE_BUILD_ARGV=(
		make build TOOLPREFIX="${TOOLPREFIX}" LOG=error
		BUILDDIR="${kernel_build}" INIT_PROC=fsallocfault_ucore
		FS_ALLOCATOR_FAULT_TEST_PROFILE=1
	)
	"${PROFILE_BUILD_ARGV[@]}"
	cp "${kernel_build}/kernel" "${PROFILE_KERNEL}"
	write_argv_json "${PROFILE_COMPILE_ARGV_JSON}" \
		"${PROFILE_BUILD_ARGV[@]}"
}

build_mutant_kernel() {
	local kernel_build="${TMPDIR_FSALLOC}/mutant-kernel-build"

	MUTANT_BUILD_ARGV=(
		make build TOOLPREFIX="${TOOLPREFIX}" LOG=error
		BUILDDIR="${kernel_build}" INIT_PROC=fsallocfault_ucore
		FS_ALLOCATOR_FAULT_TEST_PROFILE=1
		FS_ALLOCATOR_DELETE_BARRIER_MUTANT=1
	)
	"${MUTANT_BUILD_ARGV[@]}"
	cp "${kernel_build}/kernel" "${MUTANT_KERNEL}"
	write_argv_json "${MUTANT_COMPILE_ARGV_JSON}" \
		"${MUTANT_BUILD_ARGV[@]}"
}

build_case() {
	local tag="$1" op_id="$2" phase_id="$3" action_id="$4"
	local user_build="${TMPDIR_FSALLOC}/${tag}-user-build"
	local user_target="${TMPDIR_FSALLOC}/${tag}-user-target"
	local user_asm="${TMPDIR_FSALLOC}/${tag}-user-asm"
	local user_elf="${user_build}/riscv64/fsallocfault_ucore"
	local build_argv_json="${TMPDIR_FSALLOC}/${tag}-user-build-argv.json"

	USER_BUILD_ARGV=(make -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=agent \
		USER_EXTRA_CFLAGS="-Werror -DFSALLOC_FAULT_OP=${op_id} -DFSALLOC_FAULT_PHASE=${phase_id} -DFSALLOC_FAULT_ACTION=${action_id}" \
		build_dir="${user_build}" out_dir="${user_target}" \
		asm_dir="${user_asm}" "${user_elf}")
	"${USER_BUILD_ARGV[@]}"
	write_argv_json "${build_argv_json}" "${USER_BUILD_ARGV[@]}"
	mkdir -p "${user_target}/bin"
	cp "${user_build}/bin/fsallocfault_ucore" "${user_target}/bin/"
	"${PYTHON_BIN}" scripts/fs-allocator-evidence.py record-build \
		--root "${EVIDENCE_ROOT}" --case "${tag}" \
		--program "${user_target}/bin/fsallocfault_ucore" \
		--build-argv-json "${build_argv_json}"
	host_probe_compile "${TMPDIR_FSALLOC}/${tag}-mkfs" \
		nfs/fs.c nfs/host_image_snapshot.c
	host_probe_run "${TMPDIR_FSALLOC}/${tag}-mkfs" \
		"${TMPDIR_FSALLOC}/${tag}.img" \
		"${user_target}/bin/fsallocfault_ucore"
}

run_case() {
	local tag="$1" marker="$2" completion="$3" stage="$4"
	local kernel="${5:-${PROFILE_KERNEL}}"
	local image="${6:-${TMPDIR_FSALLOC}/${tag}.img}"
	local record_receipt="${7:-1}"
	local mutation="${8:-0}"
	local log="${TMPDIR_FSALLOC}/${tag}-${stage}.log"
	local input_image="${TMPDIR_FSALLOC}/${tag}-${stage}-input.img"
	local launch_argv_json="${TMPDIR_FSALLOC}/${tag}-${stage}-launch-argv.json"
	local completion_args=()
	local runner_argv=()
	local execution_args=()
	local grace="${MARKER_GRACE_SECONDS}"
	local runner_status=0 append_status=0
	local started_ns ended_ns
	if [[ "${completion}" != natural ]]; then
		completion_args+=(--completion-mode "${completion}")
	fi
	if [[ "${completion}" == powercut ]]; then
		grace=0s
	fi
	runner_argv=(
		"${PYTHON_BIN}" scripts/agent_test_runner.py
		--init-proc fsallocfault_ucore \
		--marker "${marker}" --marker-mode exact-line \
		--log-file "${log}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${grace}" \
		--qemu "${QEMU}" --kernel "${kernel}" \
		--image "${image}" "${completion_args[@]}"
	)
	write_argv_json "${launch_argv_json}" "${runner_argv[@]}"
	cp "${image}" "${input_image}"
	started_ns="$(date +%s%N)"
	if "${runner_argv[@]}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	ended_ns="$(date +%s%N)"
	if [[ -s "${log}" ]]; then
		if evidence_append_guest_log "fs-allocator:${tag}:${stage}" "${log}"; then
			append_status=0
		else
			append_status=$?
		fi
	else
		append_status=65
	fi
	[[ ${runner_status} -eq 0 ]] || return "${runner_status}"
	[[ ${append_status} -eq 0 ]] || return "${append_status}"
	execution_args=(
		"${PYTHON_BIN}" scripts/fs-allocator-evidence.py record-execution
		--root "${EVIDENCE_ROOT}" --case "${tag}" --stage "${stage}"
		--log "${log}" --launch-argv-json "${launch_argv_json}"
		--kernel "${kernel}" --input-image "${input_image}"
		--output-image "${image}" --started-ns "${started_ns}"
		--ended-ns "${ended_ns}" --returncode "${runner_status}"
	)
	if [[ "${mutation}" -eq 1 ]]; then
		execution_args+=(--mutation)
	fi
	"${execution_args[@]}"
	if [[ "${record_receipt}" -eq 1 ]]; then
		"${PYTHON_BIN}" scripts/fs-allocator-evidence.py record-stage \
			--root "${EVIDENCE_ROOT}" --case "${tag}" \
			--stage "${stage}" --log "${log}" \
			--launch-argv-json "${launch_argv_json}"
	fi
	LAST_RUN_LOG="${log}"
	LAST_RUN_ARGV_JSON="${launch_argv_json}"
}

build_profile_kernel
BASE_LAUNCH_ARGV=(
	"${PYTHON_BIN}" scripts/agent_test_runner.py
	--init-proc fsallocfault_ucore
)
write_argv_json "${BASE_LAUNCH_ARGV_JSON}" "${BASE_LAUNCH_ARGV[@]}"
"${PYTHON_BIN}" scripts/fs-allocator-evidence.py init-backend \
	--root "${EVIDENCE_ROOT}" --kernel "${PROFILE_KERNEL}" \
	--compile-argv-json "${PROFILE_COMPILE_ARGV_JSON}" \
	--launch-argv-json "${BASE_LAUNCH_ARGV_JSON}" \
	--capacity-bytes "$((OVERLAY_BLOCKS * 1024))" \
	--abi-version "${BACKEND_ABI_VERSION}"

for op_index in "${!operations[@]}"; do
	for phase_index in "${!phases[@]}"; do
		if ! case_supported "${operations[op_index]}" \
			"${phases[phase_index]}"; then
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
			"${PYTHON_BIN}" scripts/fs-allocator-evidence.py record-case \
				--root "${EVIDENCE_ROOT}" --case "${tag}" \
				--before-image "${TMPDIR_FSALLOC}/${tag}-before.img" \
				--fault-image "${TMPDIR_FSALLOC}/${tag}-fault.img" \
				--reboot-image "${TMPDIR_FSALLOC}/${tag}.img"
			cat "${EVIDENCE_ROOT}/cases/${tag}/verified.json"
		done
	done
done

build_mutant_kernel
mutation_tag="mutation-alloc-intent-crash"
mutation_image="${TMPDIR_FSALLOC}/${mutation_tag}.img"
mutation_before="${TMPDIR_FSALLOC}/${mutation_tag}-before.img"
mutation_fault="${TMPDIR_FSALLOC}/${mutation_tag}-fault.img"
mutation_reboot="${TMPDIR_FSALLOC}/${mutation_tag}-reboot.img"
mutation_selection="${TMPDIR_FSALLOC}/flush-deletion-selection.diff"
cp "${TMPDIR_FSALLOC}/alloc-intent-crash-before.img" "${mutation_before}"
cp "${mutation_before}" "${mutation_image}"
run_case "${mutation_tag}" \
	"fsallocfault_kernel: durability_receipt_failed=1" powercut fault \
	"${MUTANT_KERNEL}" "${mutation_image}" 0 1
mutation_log="${LAST_RUN_LOG}"
mutation_command_argv_json="${LAST_RUN_ARGV_JSON}"
cp "${mutation_image}" "${mutation_fault}"
run_case "${mutation_tag}" \
	"fsallocfault_ucore: case=alloc phase=intent action=crash reboot_ready=1" \
	natural reboot "${PROFILE_KERNEL}" "${mutation_image}" 0 1
cp "${mutation_image}" "${mutation_reboot}"
write_mutant_selection_diff "${mutation_selection}"
"${PYTHON_BIN}" scripts/fs-allocator-evidence.py record-mutation \
	--root "${EVIDENCE_ROOT}" \
	--baseline-kernel "${PROFILE_KERNEL}" --mutant-kernel "${MUTANT_KERNEL}" \
	--selection-diff "${mutation_selection}" \
	--before-image "${mutation_before}" --fault-image "${mutation_fault}" \
	--reboot-image "${mutation_reboot}" --log "${mutation_log}" \
	--baseline-compile-argv-json "${PROFILE_COMPILE_ARGV_JSON}" \
	--mutant-compile-argv-json "${MUTANT_COMPILE_ARGV_JSON}" \
	--command-argv-json "${mutation_command_argv_json}"

"${PYTHON_BIN}" scripts/fs-allocator-evidence.py seal-run \
	--root "${EVIDENCE_ROOT}"

"${PYTHON_BIN}" scripts/fs-allocator-evidence.py build --root "${EVIDENCE_ROOT}"
"${PYTHON_BIN}" scripts/fs-allocator-evidence.py verify --root "${EVIDENCE_ROOT}"
"${PYTHON_BIN}" scripts/fs-allocator-evidence.py pack \
	--root "${EVIDENCE_ROOT}" --output "${EVIDENCE_ARCHIVE}"
"${PYTHON_BIN}" scripts/fs-allocator-evidence.py verify-archive \
	--archive "${EVIDENCE_ARCHIVE}"

echo "[fs-allocator-fault] dynamic matrix, negative mutant, and raw evidence passed"
host_probe_report "fs-allocator-fault mkfs"
