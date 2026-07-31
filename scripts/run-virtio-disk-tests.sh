#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HOSTCC="${HOSTCC:-cc}"
CASE_TIMEOUT="${CASE_TIMEOUT:-150s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
TMPDIR_VIRTIO="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_VIRTIO}"' EXIT
source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_VIRTIO}"

"${PYTHON_BIN}" scripts/test-virtio-disk-wiring.py
"${PYTHON_BIN}" scripts/test-validate-virtio-disk-log.py
make -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=virtio_disk \
    USER_EXTRA_CFLAGS="-Werror" \
    build_dir="${TMPDIR_VIRTIO}/user-build" \
    out_dir="${TMPDIR_VIRTIO}/user-target" \
    asm_dir="${TMPDIR_VIRTIO}/user-asm"
host_probe_compile "${TMPDIR_VIRTIO}/mkfs" \
	nfs/fs.c nfs/host_image_snapshot.c
host_probe_run "${TMPDIR_VIRTIO}/mkfs" "${TMPDIR_VIRTIO}/master.img" \
    "${TMPDIR_VIRTIO}/user-target/bin/virtiodisk_ucore"
make -B build TOOLPREFIX="${TOOLPREFIX}" LOG=error \
    INIT_PROC=virtiodisk_ucore VIRTIO_DISK_TEST=1
cp build/kernel "${TMPDIR_VIRTIO}/kernel"
cp "${TMPDIR_VIRTIO}/master.img" "${TMPDIR_VIRTIO}/run.img"

log_file="${TMPDIR_VIRTIO}/guest.log"
runner_status=0
append_status=0
if "${PYTHON_BIN}" scripts/agent_test_runner.py \
    --init-proc virtiodisk_ucore \
    --marker "virtiodisk_ucore: parent passed" \
    --marker-mode exact-line \
    --log-file "${log_file}" \
    --case-timeout "${CASE_TIMEOUT}" \
    --idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
    --marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
    --qemu "${QEMU}" \
    --kernel "${TMPDIR_VIRTIO}/kernel" \
    --image "${TMPDIR_VIRTIO}/run.img"; then
    runner_status=0
else
    runner_status=$?
fi
if [[ -s "${log_file}" ]]; then
    if evidence_append_guest_log "virtio-disk:fault-matrix" "${log_file}"; then
        append_status=0
    else
        append_status=$?
    fi
else
    append_status=65
fi
if [[ ${runner_status} -ne 0 ]]; then
    exit "${runner_status}"
fi
if [[ ${append_status} -ne 0 ]]; then
    exit "${append_status}"
fi
"${PYTHON_BIN}" scripts/validate-virtio-disk-log.py --log-file "${log_file}"
echo "[virtio-disk] fault matrix passed"
host_probe_report "virtio-disk mkfs"
