#!/usr/bin/env bash
# Filesystem allocator semantic fault/reboot matrix.

# Re-enter before touching the repository or temporary storage. This strips
# BASH_ENV, exported functions, Make/Python injection variables, and every
# undeclared caller variable from the formal shell itself.
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
		FS_ALLOCATOR_ARTIFACT_DIR="${FS_ALLOCATOR_ARTIFACT_DIR:-}" \
		FS_ALLOCATOR_EVIDENCE_ARCHIVE="${FS_ALLOCATOR_EVIDENCE_ARCHIVE:-}" \
		EVIDENCE_GUEST_LOG_FILE="${EVIDENCE_GUEST_LOG_FILE:-}" \
		/bin/bash --noprofile --norc -p "$0" \
		--internal-hermetic-shell "$@"
fi
shift
set -euo pipefail
unset BASH_ENV ENV

if [[ "${1:-}" == "--hermetic-shell-selftest" ]]; then
	[[ $# -eq 1 && -z "${BASH_ENV:-}" && -z "${ENV:-}" ]]
	if env | grep -q '^BASH_FUNC_'; then
		exit 70
	fi
	echo "fsalloc-hermetic-shell: passed"
	exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."
LIVE_SOURCE_ROOT="$(pwd -P)"

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_BIN="${MAKE_TOOL:-make}"
CASE_TIMEOUT="${CASE_TIMEOUT:-60s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
TMPDIR_FSALLOC="$(mktemp -d)"
PRIVATE_HOME="${TMPDIR_FSALLOC}/home"
mkdir -m 700 "${PRIVATE_HOME}"
HOME="${PRIVATE_HOME}"
export HOME
unset ASAN_OPTIONS UBSAN_OPTIONS \
	AGENTOS_FSALLOC_ASAN_OPTIONS AGENTOS_FSALLOC_UBSAN_OPTIONS
SOURCE_SNAPSHOT="${TMPDIR_FSALLOC}/source-root"
PROFILE_KERNEL="${TMPDIR_FSALLOC}/fsalloc-profile-kernel"
MUTANT_KERNEL="${TMPDIR_FSALLOC}/fsalloc-delete-barrier-mutant-kernel"
EVIDENCE_ROOT="${FS_ALLOCATOR_ARTIFACT_DIR:-${TMPDIR_FSALLOC}/fs-allocator-evidence}"
EVIDENCE_ARCHIVE="${FS_ALLOCATOR_EVIDENCE_ARCHIVE:-${TMPDIR_FSALLOC}/fs-allocator-evidence.tar}"
PROFILE_COMPILE_ARGV_JSON="${TMPDIR_FSALLOC}/profile-compile-argv.json"
MUTANT_COMPILE_ARGV_JSON="${TMPDIR_FSALLOC}/mutant-compile-argv.json"
BASE_LAUNCH_ARGV_JSON="${TMPDIR_FSALLOC}/base-launch-argv.json"
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
TRUSTED_PYTHON_ENTRY="${LIVE_SOURCE_ROOT}/scripts/trusted-python-entry.py"
cleanup() {
	rm -rf "${TMPDIR_FSALLOC}"
}
trap cleanup EXIT

fsalloc_clean_exec() {
	"${PYTHON_BIN}" -I -S -B "${TRUSTED_PYTHON_ENTRY}" \
		scripts/fs-allocator-evidence.py clean-exec -- "$@"
}

fsalloc_trusted_python() {
	fsalloc_clean_exec "${PYTHON_BIN}" -I -S -B \
		"${TRUSTED_PYTHON_ENTRY}" "$@"
}

source_boundary() {
	fsalloc_trusted_python scripts/fs-allocator-evidence.py \
		verify-source-boundary --root "${EVIDENCE_ROOT}" \
		--source-root "${LIVE_SOURCE_ROOT}" \
		--snapshot-root "${SOURCE_SNAPSHOT}" --boundary "$1"
}

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
	fsalloc_clean_exec "${PYTHON_BIN}" -I -S -B - "${output}" "$@" <<'PY'
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

RUN_ID="$(fsalloc_clean_exec "${PYTHON_BIN}" -I -S -B -c \
	'import secrets; print(secrets.token_hex(32))')"
fsalloc_trusted_python scripts/fs-allocator-evidence.py capture-run \
	--root "${EVIDENCE_ROOT}" --source-root "${LIVE_SOURCE_ROOT}" --run-id "${RUN_ID}" \
	--qemu "${QEMU}" --python "${PYTHON_BIN}" --make "${MAKE_BIN}" \
	--host-cc "${HOST_CC}" --cross-gcc "${CROSS_GCC}" \
	--cross-ld "${CROSS_LD}" --cross-objcopy "${CROSS_OBJCOPY}" \
	--cross-objdump "${CROSS_OBJDUMP}"
PYTHON_BIN="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool python)"
QEMU="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool qemu)"
MAKE_BIN="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool make)"
HOST_CC="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool host_cc)"
CROSS_GCC="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool cross_gcc)"
CROSS_LD="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool cross_ld)"
CROSS_OBJCOPY="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool cross_objcopy)"
CROSS_OBJDUMP="$(fsalloc_trusted_python scripts/fs-allocator-evidence.py tool-path \
	--root "${EVIDENCE_ROOT}" --tool cross_objdump)"
TOOLPREFIX=""
export HOST_CC
TRUSTED_PYTHON_ENTRY="${EVIDENCE_ROOT}/sources/scripts/trusted-python-entry.py"
fsalloc_trusted_python scripts/fs-allocator-evidence.py materialize-source \
	--root "${EVIDENCE_ROOT}" --output "${SOURCE_SNAPSHOT}"
cd "${SOURCE_SNAPSHOT}"
source "${SOURCE_SNAPSHOT}/scripts/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_FSALLOC}" "${HOST_CC}"
unset ASAN_OPTIONS UBSAN_OPTIONS
source_boundary post-materialize

read_unsigned_define() {
	local header="$1" name="$2"
	fsalloc_clean_exec "${PYTHON_BIN}" -I -S -B - "${header}" "${name}" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
matches = re.findall(rf"^#define[ \t]+{re.escape(sys.argv[2])}[ \t]+([0-9]+)U?[ \t]*$", text, re.MULTILINE)
if len(matches) != 1:
    raise SystemExit(f"expected one unsigned define for {sys.argv[2]}")
print(matches[0])
PY
}

write_mutant_selection_diff() {
	local output="$1"
	fsalloc_clean_exec "${PYTHON_BIN}" -I -S -B - os/fs.c "${output}" <<'PY'
import hashlib
import os
import sys

source_path, output_path = sys.argv[1:]
raw = open(source_path, "rb").read()
text = raw.decode("utf-8")
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

fsalloc_trusted_python scripts/test-fs-allocator-image.py
fsalloc_trusted_python scripts/check-fs-allocator-state.py
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
		"${MAKE_BIN}" build TOOLPREFIX="${TOOLPREFIX}"
		CC="${CROSS_GCC}" AS="${CROSS_GCC}" LD="${CROSS_LD}"
		OBJCOPY="${CROSS_OBJCOPY}" OBJDUMP="${CROSS_OBJDUMP}"
		LOG=error
		BUILDDIR="${kernel_build}" INIT_PROC=fsallocfault_ucore
		PYTHON_BIN="${PYTHON_BIN}"
		FS_ALLOCATOR_FAULT_TEST_PROFILE=1
	)
	source_boundary before-profile-build
	fsalloc_clean_exec "${PROFILE_BUILD_ARGV[@]}"
	source_boundary after-profile-build
	cp "${kernel_build}/kernel" "${PROFILE_KERNEL}"
	write_argv_json "${PROFILE_COMPILE_ARGV_JSON}" \
		"${PROFILE_BUILD_ARGV[@]}"
}

build_mutant_kernel() {
	local kernel_build="${TMPDIR_FSALLOC}/mutant-kernel-build"

	MUTANT_BUILD_ARGV=(
		"${MAKE_BIN}" build TOOLPREFIX="${TOOLPREFIX}"
		CC="${CROSS_GCC}" AS="${CROSS_GCC}" LD="${CROSS_LD}"
		OBJCOPY="${CROSS_OBJCOPY}" OBJDUMP="${CROSS_OBJDUMP}"
		LOG=error
		BUILDDIR="${kernel_build}" INIT_PROC=fsallocfault_ucore
		PYTHON_BIN="${PYTHON_BIN}"
		FS_ALLOCATOR_FAULT_TEST_PROFILE=1
		FS_ALLOCATOR_DELETE_BARRIER_MUTANT=1
	)
	source_boundary before-mutant-build
	fsalloc_clean_exec "${MUTANT_BUILD_ARGV[@]}"
	source_boundary after-mutant-build
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
	local sealed_target="${TMPDIR_FSALLOC}/${tag}-sealed-target"
	local build_argv_json="${TMPDIR_FSALLOC}/${tag}-user-build-argv.json"

	USER_BUILD_ARGV=("${MAKE_BIN}" -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=agent \
		CC="${CROSS_GCC}" OBJCOPY="${CROSS_OBJCOPY}" \
		OBJDUMP="${CROSS_OBJDUMP}" \
		PYTHON_BIN="${PYTHON_BIN}" \
		USER_EXTRA_CFLAGS="-Werror -DFSALLOC_FAULT_OP=${op_id} -DFSALLOC_FAULT_PHASE=${phase_id} -DFSALLOC_FAULT_ACTION=${action_id}" \
		build_dir="${user_build}" out_dir="${user_target}" \
		asm_dir="${user_asm}" "${user_elf}")
	source_boundary "before-user-build-${tag}"
	fsalloc_clean_exec "${USER_BUILD_ARGV[@]}"
	source_boundary "after-user-build-${tag}"
	write_argv_json "${build_argv_json}" "${USER_BUILD_ARGV[@]}"
	mkdir -p "${user_target}/bin" "${user_target}/elf"
	cp "${user_build}/bin/fsallocfault_ucore" "${user_target}/bin/"
	cp "${user_elf}" "${user_target}/elf/"
	source_boundary "before-record-build-${tag}"
	fsalloc_trusted_python scripts/fs-allocator-evidence.py record-build \
		--root "${EVIDENCE_ROOT}" --case "${tag}" \
		--program "${user_target}/bin/fsallocfault_ucore" \
		--elf "${user_target}/elf/fsallocfault_ucore" \
		--build-argv-json "${build_argv_json}"
	source_boundary "after-record-build-${tag}"
	mkdir -p "${sealed_target}/bin" "${sealed_target}/elf"
	cp "${EVIDENCE_ROOT}/cases/${tag}/program.bin" \
		"${sealed_target}/bin/fsallocfault_ucore"
	cp "${EVIDENCE_ROOT}/cases/${tag}/program.elf" \
		"${sealed_target}/elf/fsallocfault_ucore"
	source_boundary "before-mkfs-build-${tag}"
	host_probe_compile "${TMPDIR_FSALLOC}/${tag}-mkfs" \
		nfs/fs.c nfs/host_image_snapshot.c
	source_boundary "after-mkfs-build-${tag}"
	source_boundary "before-mkfs-run-${tag}"
	host_probe_run "${TMPDIR_FSALLOC}/${tag}-mkfs" \
		"${TMPDIR_FSALLOC}/${tag}.img" \
		"${sealed_target}/bin/fsallocfault_ucore"
	source_boundary "after-mkfs-run-${tag}"
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
		"${PYTHON_BIN}" -I -S -B "${TRUSTED_PYTHON_ENTRY}"
		scripts/agent_test_runner.py
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
	source_boundary "before-qemu-${tag}-${stage}"
	started_ns="$(date +%s%N)"
	if fsalloc_clean_exec "${runner_argv[@]}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	ended_ns="$(date +%s%N)"
	source_boundary "after-qemu-${tag}-${stage}"
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
		scripts/fs-allocator-evidence.py record-execution
		--root "${EVIDENCE_ROOT}" --case "${tag}" --stage "${stage}"
		--log "${log}" --launch-argv-json "${launch_argv_json}"
		--kernel "${kernel}" --input-image "${input_image}"
		--output-image "${image}" --started-ns "${started_ns}"
		--ended-ns "${ended_ns}" --returncode "${runner_status}"
	)
	if [[ "${mutation}" -eq 1 ]]; then
		execution_args+=(--mutation)
	fi
	source_boundary "before-record-${tag}-${stage}"
	fsalloc_trusted_python "${execution_args[@]}"
	if [[ "${record_receipt}" -eq 1 ]]; then
		fsalloc_trusted_python scripts/fs-allocator-evidence.py record-stage \
			--root "${EVIDENCE_ROOT}" --case "${tag}" \
			--stage "${stage}" --log "${log}" \
			--launch-argv-json "${launch_argv_json}"
	fi
	source_boundary "after-record-${tag}-${stage}"
	LAST_RUN_LOG="${log}"
	LAST_RUN_ARGV_JSON="${launch_argv_json}"
}

build_profile_kernel
BASE_LAUNCH_ARGV=(
	"${PYTHON_BIN}" -I -S -B "${TRUSTED_PYTHON_ENTRY}"
	scripts/agent_test_runner.py
	--init-proc fsallocfault_ucore
)
write_argv_json "${BASE_LAUNCH_ARGV_JSON}" "${BASE_LAUNCH_ARGV[@]}"
source_boundary before-record-backend
fsalloc_trusted_python scripts/fs-allocator-evidence.py init-backend \
	--root "${EVIDENCE_ROOT}" --kernel "${PROFILE_KERNEL}" \
	--compile-argv-json "${PROFILE_COMPILE_ARGV_JSON}" \
	--launch-argv-json "${BASE_LAUNCH_ARGV_JSON}" \
	--capacity-bytes "$((OVERLAY_BLOCKS * 1024))" \
	--abi-version "${BACKEND_ABI_VERSION}"
source_boundary after-record-backend

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
			source_boundary "before-record-case-${tag}"
			fsalloc_trusted_python scripts/fs-allocator-evidence.py record-case \
				--root "${EVIDENCE_ROOT}" --case "${tag}" \
				--before-image "${TMPDIR_FSALLOC}/${tag}-before.img" \
				--fault-image "${TMPDIR_FSALLOC}/${tag}-fault.img" \
				--reboot-image "${TMPDIR_FSALLOC}/${tag}.img"
			source_boundary "after-record-case-${tag}"
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
source_boundary before-record-mutation
fsalloc_trusted_python scripts/fs-allocator-evidence.py record-mutation \
	--root "${EVIDENCE_ROOT}" \
	--baseline-kernel "${PROFILE_KERNEL}" --mutant-kernel "${MUTANT_KERNEL}" \
	--selection-diff "${mutation_selection}" \
	--before-image "${mutation_before}" --fault-image "${mutation_fault}" \
	--reboot-image "${mutation_reboot}" --log "${mutation_log}" \
	--baseline-compile-argv-json "${PROFILE_COMPILE_ARGV_JSON}" \
	--mutant-compile-argv-json "${MUTANT_COMPILE_ARGV_JSON}" \
	--command-argv-json "${mutation_command_argv_json}"
source_boundary after-record-mutation

source_boundary before-seal
fsalloc_trusted_python scripts/fs-allocator-evidence.py seal-run \
	--root "${EVIDENCE_ROOT}"
source_boundary after-seal

fsalloc_trusted_python scripts/fs-allocator-evidence.py build --root "${EVIDENCE_ROOT}"
fsalloc_trusted_python scripts/fs-allocator-evidence.py verify --root "${EVIDENCE_ROOT}"
source_boundary before-pack
fsalloc_trusted_python scripts/fs-allocator-evidence.py pack \
	--root "${EVIDENCE_ROOT}" --output "${EVIDENCE_ARCHIVE}"
fsalloc_trusted_python scripts/fs-allocator-evidence.py verify-archive \
	--archive "${EVIDENCE_ARCHIVE}"
source_boundary after-archive-verify

echo "[fs-allocator-fault] dynamic matrix, negative mutant, and raw evidence passed"
host_probe_report "fs-allocator-fault mkfs"
