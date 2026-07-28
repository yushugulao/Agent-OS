#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
TMPDIR_PHYSICAL="$(mktemp -d)"
INITPROC_PATH="os/initproc.S"
INITPROC_BACKUP="${TMPDIR_PHYSICAL}/initproc.S"
INITPROC_PRESENT=0
if [[ -f "${INITPROC_PATH}" ]]; then
	cp -p "${INITPROC_PATH}" "${INITPROC_BACKUP}"
	INITPROC_PRESENT=1
fi
cleanup_physical_test() {
	if [[ "${INITPROC_PRESENT}" == 1 ]]; then
		cp -p "${INITPROC_BACKUP}" "${INITPROC_PATH}"
	else
		rm -f "${INITPROC_PATH}"
	fi
	rm -rf "${TMPDIR_PHYSICAL}"
}
trap cleanup_physical_test EXIT

policy_probe="scripts/probes/physical-page-policy.c"
capacity_probe="scripts/probes/physical-page-capacity.c"
"${PYTHON_BIN}" scripts/test-physical-brk-wiring.py
"${PYTHON_BIN}" scripts/test-resource-kind-policy.py
"${TOOLPREFIX}gcc" -std=gnu11 -ffreestanding -fsyntax-only \
	"${capacity_probe}"
if "${TOOLPREFIX}gcc" -std=gnu11 -ffreestanding -fsyntax-only \
	-DPHYSICAL_PAGE_SYSTEM_RESERVE=1024 "${capacity_probe}" \
	>/dev/null 2>&1; then
	echo "[physical-resource] underfunded production policy compiled" >&2
	exit 1
fi
"${TOOLPREFIX}gcc" -std=gnu11 -ffreestanding -fsyntax-only \
	-DPHYSICAL_PAGE_SYSTEM_RESERVE=64 \
	-DEXPECTED_PHYSICAL_DOMAIN_LIMIT=10 "${policy_probe}"
if "${TOOLPREFIX}gcc" -std=gnu11 -ffreestanding -fsyntax-only \
	-DPHYSICAL_PAGE_SYSTEM_RESERVE=64 \
	-DPHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT=11 \
	-DEXPECTED_PHYSICAL_DOMAIN_LIMIT=11 "${policy_probe}" \
	>/dev/null 2>&1; then
	echo "[physical-resource] oversized reserved-domain promise compiled" >&2
	exit 1
fi
for rejected in \
	"-DPHYSICAL_PAGE_SYSTEM_RESERVE=64 -DPHYSICAL_PAGE_STORAGE_SYSTEM_RESERVED_LIMIT=1 -DPHYSICAL_PAGE_STORAGE_DOMAIN_RESERVED_LIMIT=1073741824U" \
	"-DPHYSICAL_PAGE_SYSTEM_RESERVE=4294967296ULL" \
	"-DPHYSICAL_PAGE_SYSTEM_RESERVE=40000U" \
	"-DPHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT=40000U" \
	"-DPHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT=4294967296ULL"; do
	if "${TOOLPREFIX}gcc" -std=gnu11 -ffreestanding -fsyntax-only \
		${rejected} \
		-DEXPECTED_PHYSICAL_DOMAIN_LIMIT=PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT \
		"${policy_probe}" >/dev/null 2>&1; then
		echo "[physical-resource] invalid 64-bit page policy compiled: ${rejected}" >&2
		exit 1
	fi
done

make -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=physical_resource \
	build_dir="${TMPDIR_PHYSICAL}/user-build" \
	out_dir="${TMPDIR_PHYSICAL}/user-target" \
	asm_dir="${TMPDIR_PHYSICAL}/user-asm"
cc nfs/fs.c nfs/host_image_snapshot.c -o "${TMPDIR_PHYSICAL}/mkfs"
"${TMPDIR_PHYSICAL}/mkfs" "${TMPDIR_PHYSICAL}/physical.img" \
	"${TMPDIR_PHYSICAL}/user-target/bin/physicalresource_ucore"
PROD_BUILDDIR="${TMPDIR_PHYSICAL}/prod-build"
make -B "${PROD_BUILDDIR}/os/syscall.o" \
	"${PROD_BUILDDIR}/os/resource_controller.o" \
	TOOLPREFIX="${TOOLPREFIX}" BUILDDIR="${PROD_BUILDDIR}" LOG=error \
	PHYSICAL_PAGE_SYSTEM_RESERVE=512 \
	PHYSICAL_PAGE_ORDINARY_LIMIT=96 \
	PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT=48 \
	PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT=122
prod_nm="${TMPDIR_PHYSICAL}/production-symbols.txt"
if ! "${TOOLPREFIX}nm" "${PROD_BUILDDIR}/os/syscall.o" \
	"${PROD_BUILDDIR}/os/resource_controller.o" >"${prod_nm}"; then
	echo "[physical-resource] failed to inspect production symbols" >&2
	exit 1
fi
if grep -Eq 'physical_page_test|resource_policy_reserved_snapshot' \
	"${prod_nm}"; then
	echo "[physical-resource] test hook leaked into production objects" >&2
	exit 1
fi
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	BUILDDIR="${TMPDIR_PHYSICAL}/kernel-build" \
	INIT_PROC=physicalresource_ucore CHAPTER=physical_resource \
	PHYSICAL_PAGE_TEST_HOOKS=1 \
	PHYSICAL_PAGE_SYSTEM_RESERVE=512 \
	PHYSICAL_PAGE_ORDINARY_LIMIT=96 \
	PHYSICAL_PAGE_DOMAIN_ORDINARY_LIMIT=48 \
	PHYSICAL_PAGE_DOMAIN_RESERVED_LIMIT=122
cp "${TMPDIR_PHYSICAL}/kernel-build/kernel" "${TMPDIR_PHYSICAL}/kernel"
cp "${TMPDIR_PHYSICAL}/physical.img" "${TMPDIR_PHYSICAL}/run.img"

log_file="${TMPDIR_PHYSICAL}/physical-resource.log"
runner_status=0
append_status=0
if "${PYTHON_BIN}" scripts/agent_test_runner.py \
	--init-proc physical-resource \
	--marker "physicalresource_ucore: parent passed" \
	--marker-mode exact-line \
	--log-file "${log_file}" \
	--case-timeout "${CASE_TIMEOUT}" \
	--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
	--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
	--qemu "${QEMU}" --kernel "${TMPDIR_PHYSICAL}/kernel" \
	--image "${TMPDIR_PHYSICAL}/run.img"; then
	runner_status=0
else
	runner_status=$?
fi
if [[ -s "${log_file}" ]]; then
	if evidence_append_guest_log "physical-resource" "${log_file}"; then
		append_status=0
	else
		append_status=$?
	fi
else
	append_status=65
fi
[[ ${runner_status} -eq 0 ]] || exit "${runner_status}"
[[ ${append_status} -eq 0 ]] || exit "${append_status}"
"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
	--log-file "${log_file}" --tag physical-resource \
	--profile physical-resource
echo "[physical-resource] all checks passed"
