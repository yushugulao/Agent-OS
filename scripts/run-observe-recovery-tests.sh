#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
TMPDIR_OBSERVE="$(mktemp -d)"
had_initproc=0
if [[ -f os/initproc.S ]]; then
	had_initproc=1
	cp os/initproc.S "${TMPDIR_OBSERVE}/initproc.S"
fi
cleanup() {
	if [[ "${had_initproc}" -eq 1 ]]; then
		cp "${TMPDIR_OBSERVE}/initproc.S" os/initproc.S
	else
		rm -f os/initproc.S
	fi
	rm -rf "${TMPDIR_OBSERVE}"
}
trap cleanup EXIT
source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_OBSERVE}"

"${PYTHON_BIN}" scripts/test-observe-recovery-contract.py
bash scripts/test-identity-lease-deferred.sh
bash scripts/test-durable-dirty-retry.sh
bash scripts/test-observe-reap-state.sh
make -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=observe_recovery \
	USER_EXTRA_CFLAGS="-DAGENT_OBSERVE_TEST_PROFILE" \
	build_dir="${TMPDIR_OBSERVE}/user-build" \
	out_dir="${TMPDIR_OBSERVE}/user-target" \
	asm_dir="${TMPDIR_OBSERVE}/user-asm"
host_probe_compile "${TMPDIR_OBSERVE}/mkfs" \
	-DAGENT_OBSERVE_PHASE_CONTROL_PROFILE \
	nfs/fs.c nfs/host_image_snapshot.c
make -B "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_store.o" \
	"${TMPDIR_OBSERVE}/prod-build/os/agent_observe_recovery.o" \
	"${TMPDIR_OBSERVE}/prod-build/os/agent_observe_timeline.o" \
	TOOLPREFIX="${TOOLPREFIX}" BUILDDIR="${TMPDIR_OBSERVE}/prod-build" \
	LOG=error
"${TOOLPREFIX}gcc" -Wall -Werror -ffreestanding -Ios \
	-c os/agent_observe_test.c \
	-o "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_test.o"
if [[ "$("${TOOLPREFIX}size" "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_test.o" | \
	awk 'NR == 2 { print $1 ":" $2 ":" $3 }')" != "0:0:0" ]] || \
	[[ -n "$("${TOOLPREFIX}nm" "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_test.o")" ]] || \
	"${TOOLPREFIX}nm" "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_store.o" | \
	grep -q 'agent_observe_test_' || \
	"${TOOLPREFIX}nm" "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_recovery.o" | \
	grep -q 'agent_observe_test_' || \
	"${TOOLPREFIX}nm" "${TMPDIR_OBSERVE}/prod-build/os/agent_observe_timeline.o" | \
	grep -q 'agent_observe_test_'; then
	echo "[observe-recovery] test owner leaked into production build" >&2
	exit 1
fi
# 崩溃测试内核把写入保留在持久 overlay 中，使 boot0 能模拟真实断电并校验
# 从最近提交镜像恢复。
make -B "${TMPDIR_OBSERVE}/kernel-build/kernel" \
	BUILDDIR="${TMPDIR_OBSERVE}/kernel-build" \
	TOOLPREFIX="${TOOLPREFIX}" LOG=error \
	INIT_PROC=agentobsreboot_ucore AGENT_OBSERVE_TEST_PROFILE=1 \
	DURABILITY_POWERCUT_TEST_PROFILE=1

image="${TMPDIR_OBSERVE}/observe-reboot.img"
kernel="${TMPDIR_OBSERVE}/kernel-build/kernel"
host_probe_run "${TMPDIR_OBSERVE}/mkfs" "${image}" \
	"${TMPDIR_OBSERVE}/user-target/bin/agentobsreboot_ucore"
"${PYTHON_BIN}" host_tools/agent_observe_phase_control.py verify \
	--image "${image}" --expect empty

run_boot() {
	local tag="$1" marker="$2" completion="$3"
	local marker_mode="${4:-exact-line}"
	local log="${TMPDIR_OBSERVE}/${tag}.log"
	local runner_status=0

	if "${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "agentobsreboot_ucore-${tag}" \
		--marker "${marker}" --marker-mode "${marker_mode}" \
		--log-file "${log}" --case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" --kernel "${kernel}" --image "${image}" \
		--completion-mode "${completion}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	[[ ${runner_status} -eq 0 ]] || return "${runner_status}"
	if [[ ! -s "${log}" ]]; then
		echo "[observe-recovery] ${tag} Guest log is empty" >&2
		return 65
	fi
}

snapshot_image_exclusive() {
	local target="$1"
	local parent partial

	parent="$(dirname "${target}")"
	partial="${target}.partial.$$"
	[[ -d "${parent}" && ! -L "${parent}" &&
	   ! -e "${target}" && ! -e "${partial}" ]] || return 65
	cp "${image}" "${partial}"
	mv "${partial}" "${target}"
}

# 第一次启动在所有分配器类别消耗 ID 后立即打印的内核标记处终止；经崩溃测试的
# ledger 写入会持久化。只有 Host 推进受保护的固定 slot。每次转换都要求精确前驱，
# phase0 之后还要求精确的已认证 Guest 证据。
run_boot boot0-cut \
	"agentobsreboot_ucore: lease_cut_alloc " powercut line-prefix
"${PYTHON_BIN}" host_tools/agent_observe_phase_control.py advance \
	--image "${image}" --from empty --to phase0
run_boot boot1 \
	"agentobsreboot_ucore: boot1_checkpoint_ready=1" checkpoint
grep -Fxq "agentobsreboot_ucore: audit_drop_only_first_success=1" \
	"${TMPDIR_OBSERVE}/boot1.log"
"${PYTHON_BIN}" host_tools/agent_observe_phase_control.py advance \
	--image "${image}" --from phase0 --to phase1 \
	--guest-log "${TMPDIR_OBSERVE}/boot1.log" \
	--cut-log "${TMPDIR_OBSERVE}/boot0-cut.log"
"${PYTHON_BIN}" host_tools/agent_observe_disk_evidence.py \
	--image "${image}" --guest-log "${TMPDIR_OBSERVE}/boot1.log"
if [[ -n "${OBSERVE_RECOVERY_SNAPSHOT_FILE:-}" ]]; then
	snapshot_image_exclusive "${OBSERVE_RECOVERY_SNAPSHOT_FILE}"
fi
run_boot boot2 \
	"agentobsreboot_ucore: boot2_reap_replicated=1" checkpoint
grep -Fxq "agentobsreboot_ucore: audit_drop_recovered=1" \
	"${TMPDIR_OBSERVE}/boot2.log"
grep -Fxq "agentobsreboot_ucore: checkpoint_v8_recovered=1 records=6" \
	"${TMPDIR_OBSERVE}/boot2.log"
"${PYTHON_BIN}" host_tools/agent_observe_phase_control.py advance \
	--image "${image}" --from phase1 --to phase2 \
	--guest-log "${TMPDIR_OBSERVE}/boot2.log"
if [[ -n "${OBSERVE_RECOVERY_ERASED_SNAPSHOT_FILE:-}" ]]; then
	snapshot_image_exclusive "${OBSERVE_RECOVERY_ERASED_SNAPSHOT_FILE}"
fi
run_boot boot3 \
	"agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1" \
	natural

grep -Fxq \
	"agentobsreboot_ucore: boot3_erased=1 generation_isolated=1 stable_identity=1" \
	"${TMPDIR_OBSERVE}/boot3.log"
grep -Fxq \
	"agentobsreboot_ucore: timeline_wait_epoch_recheck=1 injection=2 retries=1 bounded_timeout=1" \
	"${TMPDIR_OBSERVE}/boot3.log"
grep -Fxq \
	"agentobsreboot_ucore: timeline_wait_threads=1 filters=2 deadlines=2 targeted=1 timeout=1 cleanup=1" \
	"${TMPDIR_OBSERVE}/boot3.log"
grep -Fxq "agentobsreboot_ucore: boot3_identity_successor=1" \
	"${TMPDIR_OBSERVE}/boot3.log"
grep -Fxq \
	"agentobsreboot_ucore: receipt_pending_not_evidence=1 receipt_durable_exact=1 receipt_fake_stale=1 receipt_window_not_evidence=1" \
	"${TMPDIR_OBSERVE}/boot1.log"
grep -Fxq "agentobsreboot_ucore: receipt_teardown_stale=1" \
	"${TMPDIR_OBSERVE}/boot2.log"
grep -Fxq "agentobsreboot_ucore: live_reload_ledger_monotonic=1" \
	"${TMPDIR_OBSERVE}/boot2.log"
grep -Fxq "agentobsreboot_ucore: receipt_permission_recovery_denied=1" \
	"${TMPDIR_OBSERVE}/boot2.log"
grep -Fxq \
	"agentobsreboot_ucore: receipt_recovery_exact=1 receipt_v1_compatible=1 bank_generation_bound=1" \
	"${TMPDIR_OBSERVE}/boot2.log"
grep -Fxq "agentobsreboot_ucore: receipt_permission_not_agent=1" \
	"${TMPDIR_OBSERVE}/boot0-cut.log"
grep -Fxq "agentobsreboot_ucore: parent passed" \
	"${TMPDIR_OBSERVE}/boot3.log"

echo "[observe-recovery] power-cut lease and three-boot durable evidence lifecycle passed"
host_probe_report "observe-recovery mkfs"
