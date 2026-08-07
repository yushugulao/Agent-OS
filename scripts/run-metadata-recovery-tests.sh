#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CASE_TIMEOUT="${CASE_TIMEOUT:-120s}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20s}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-5s}"
METADATA_MATRIX_LEGS="${METADATA_MATRIX_LEGS:-primary mirror}"
METADATA_MATRIX_PHASES="${METADATA_MATRIX_PHASES:-6 1 2 3 4 5 7 8}"
METADATA_MATRIX_ONLY="${METADATA_MATRIX_ONLY:-0}"

case "${METADATA_MATRIX_ONLY}" in
0|1) ;;
*) echo "METADATA_MATRIX_ONLY must be 0 or 1" >&2; exit 2 ;;
esac
read -r -a requested_legs <<< "${METADATA_MATRIX_LEGS}"
read -r -a requested_phases <<< "${METADATA_MATRIX_PHASES}"
[[ ${#requested_legs[@]} -gt 0 ]] || {
	echo "METADATA_MATRIX_LEGS must not be empty" >&2
	exit 2
}
[[ ${#requested_phases[@]} -gt 0 ]] || {
	echo "METADATA_MATRIX_PHASES must not be empty" >&2
	exit 2
}

matrix_legs=()
for candidate in primary mirror; do
	seen=0
	for leg in "${requested_legs[@]}"; do
		[[ "${leg}" == primary || "${leg}" == mirror ]] || {
			echo "invalid metadata matrix leg: ${leg}" >&2
			exit 2
		}
		if [[ "${leg}" == "${candidate}" ]]; then
			[[ "${seen}" -eq 0 ]] || {
				echo "duplicate metadata matrix leg: ${leg}" >&2
				exit 2
			}
			seen=1
		fi
	done
	[[ "${seen}" -eq 0 ]] || matrix_legs+=("${candidate}")
done

matrix_phases=()
for candidate in 6 1 2 3 4 5 7 8; do
	seen=0
	for phase in "${requested_phases[@]}"; do
		[[ "${phase}" =~ ^[1-8]$ ]] || {
			echo "invalid metadata matrix phase: ${phase}" >&2
			exit 2
		}
		if [[ "${phase}" == "${candidate}" ]]; then
			[[ "${seen}" -eq 0 ]] || {
				echo "duplicate metadata matrix phase: ${phase}" >&2
				exit 2
			}
			seen=1
		fi
	done
	[[ "${seen}" -eq 0 ]] || matrix_phases+=("${candidate}")
done

needs_reference=0
has_primary=0
has_phase6=0
[[ "${METADATA_MATRIX_ONLY}" -eq 1 ]] || needs_reference=1
for leg in "${matrix_legs[@]}"; do
	[[ "${leg}" != primary ]] || has_primary=1
	[[ "${leg}" != mirror ]] || needs_reference=1
done
for phase in "${matrix_phases[@]}"; do
	[[ "${phase}" != 6 ]] || has_phase6=1
	if [[ "${phase}" -ge 3 && "${phase}" -ne 6 ]]; then
		needs_reference=1
	fi
done
if [[ "${needs_reference}" -eq 1 &&
	( "${has_primary}" -ne 1 || "${has_phase6}" -ne 1 ) ]]; then
	echo "selected matrix needs primary phase 6 as its reference" >&2
	exit 2
fi

TMPDIR_META="$(mktemp -d)"
had_initproc=0
if [[ -f os/initproc.S ]]; then
	had_initproc=1
	cp os/initproc.S "${TMPDIR_META}/initproc.S"
fi
cleanup() {
	if [[ "${had_initproc}" -eq 1 ]]; then
		cp "${TMPDIR_META}/initproc.S" os/initproc.S
	else
		rm -f os/initproc.S
	fi
	rm -rf "${TMPDIR_META}"
}
trap cleanup EXIT
source "${SCRIPT_DIR}/host-probe-toolchain.sh"
host_probe_setup "${TMPDIR_META}"

"${PYTHON_BIN}" scripts/test-agent-metadata-disk-format.py
"${PYTHON_BIN}" scripts/test-validate-metadata-crash-log.py
"${PYTHON_BIN}" scripts/test-metadata-boot-reprobe.py
"${PYTHON_BIN}" scripts/test-validate-metadata-reprobe-log.py
"${PYTHON_BIN}" scripts/check-agent-metadata-disk-format.py \
	--cc "${TOOLPREFIX}gcc" --objcopy "${TOOLPREFIX}objcopy"

make -C user TOOLPREFIX="${TOOLPREFIX}" CHAPTER=metadata_recovery \
	build_dir="${TMPDIR_META}/user-build" \
	out_dir="${TMPDIR_META}/user-target" \
	asm_dir="${TMPDIR_META}/user-asm"
host_probe_compile "${TMPDIR_META}/mkfs" nfs/fs.c nfs/host_image_snapshot.c

build_kernel() {
	local tag="$1" init_proc="$2"
	local build_dir="${TMPDIR_META}/kernel-build-${tag}"
	shift 2
	local make_args=(
		-B "${build_dir}/kernel"
		"BUILDDIR=${build_dir}"
		"TOOLPREFIX=${TOOLPREFIX}"
		LOG=error
		"INIT_PROC=${init_proc}"
		"$@"
	)

	make "${make_args[@]}"
	cp "${build_dir}/kernel" "${TMPDIR_META}/kernel-${tag}"
}

run_guest() {
	local tag="$1" marker="$2" mode="$3" kernel="$4" image="$5"
	local grace="${MARKER_GRACE_SECONDS}"
	local runner_status=0 append_status=0

	if [[ "${mode}" == powercut ]]; then
		grace=0s
	fi
	if "${PYTHON_BIN}" scripts/agent_test_runner.py \
		--init-proc "${tag}" \
		--marker "${marker}" --marker-mode exact-line \
		--log-file "${TMPDIR_META}/${tag}.log" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${grace}" \
		--qemu "${QEMU}" \
		--kernel "${kernel}" --image "${image}" \
		--completion-mode "${mode}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	if [[ -s "${TMPDIR_META}/${tag}.log" ]]; then
		if evidence_append_guest_log "metadata-${tag}" \
			"${TMPDIR_META}/${tag}.log"; then
			append_status=0
		else
			append_status=$?
		fi
	else
		append_status=65
	fi
	[[ ${runner_status} -eq 0 ]] || return "${runner_status}"
	[[ ${append_status} -eq 0 ]] || return "${append_status}"
}

make_image() {
	local image="$1"

	host_probe_run "${TMPDIR_META}/mkfs" "${image}" \
		"${TMPDIR_META}/user-target/bin/agentmetacrash_ucore" \
		"${TMPDIR_META}/user-target/bin/agentmetarecover_ucore" \
		"${TMPDIR_META}/user-target/bin/agentmetaeio_ucore" \
		"${TMPDIR_META}/user-target/bin/agentmetalarge_ucore" \
		"${TMPDIR_META}/user-target/bin/agentmetatransient_ucore"
	"${PYTHON_BIN}" host_tools/agent_metadata_disk_format.py \
		--image "${image}" --stage genesis
}

validate_banks() {
	local image="$1" stage="$2" interrupted_leg="${3:-none}"
	local phase="${4:-}" reference_image="${5:-}"
	local args=(
		--image "${image}"
		--stage "${stage}"
		--interrupted-leg "${interrupted_leg}"
	)

	if [[ -n "${phase}" ]]; then
		args+=(--phase "${phase}")
	fi
	if [[ -n "${reference_image}" ]]; then
		args+=(--reference-image "${reference_image}")
	fi
	"${PYTHON_BIN}" host_tools/agent_metadata_disk_format.py "${args[@]}"
}

newer_bank_for_image() {
	local image="$1"

	"${PYTHON_BIN}" - "${image}" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, "host_tools")
from agent_metadata_disk_format import DEFAULT_CONTRACT, inspect_image, load_contract

layout = load_contract(DEFAULT_CONTRACT)
banks = inspect_image(
    Path(sys.argv[1]), layout, "interrupted-update",
    interrupted_leg="primary", phase=6,
)
updated = [bank for bank in banks if bank.get("metafile_status") == "updated"]
if len(updated) != 1:
    raise SystemExit("expected exactly one newer metadata bank")
print(f"bank{layout.bank_names.index(updated[0]['name'])} {updated[0]['generation']}")
PY
}

max_generation_for_image() {
	local image="$1"

	"${PYTHON_BIN}" - "${image}" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, "host_tools")
from agent_metadata_disk_format import DEFAULT_CONTRACT, inspect_image, load_contract

layout = load_contract(DEFAULT_CONTRACT)
banks = inspect_image(Path(sys.argv[1]), layout, "recovered")
print(max(bank["generation"] for bank in banks if bank["state"] == "valid"))
PY
}

mutate_large_peer() {
	local image="$1" state="$2"

	"${PYTHON_BIN}" - "${image}" "${state}" <<'PY'
import struct
import sys
from pathlib import Path

sys.path.insert(0, "host_tools")
import plain_ucore_fs_extract as ucore_fs
from agent_metadata_disk_format import DEFAULT_CONTRACT, load_contract

path = Path(sys.argv[1])
state = sys.argv[2]
raw = bytearray(path.read_bytes())
sb = ucore_fs.read_superblock(raw)
layout = load_contract(DEFAULT_CONTRACT)
target = layout.bank_names[0]
entries = {name: inum for inum, name in ucore_fs.root_entries(raw, sb)}
inum = entries[target]
if state == "absent":
    root = ucore_fs.read_inode(raw, sb, ucore_fs.ROOTINO)
    blocks = [block for block in root.addrs[:ucore_fs.NDIRECT] if block]
    found = 0
    for block in blocks:
        start = block * ucore_fs.BSIZE
        for offset in range(0, ucore_fs.BSIZE, 16):
            name = ucore_fs.dir_name(raw[start + offset + 2:start + offset + 16])
            if name == target:
                raw[start + offset:start + offset + 2] = b"\0\0"
                found += 1
    if found != 1:
        raise SystemExit(f"expected one {target} dirent, got {found}")
else:
    inode = ucore_fs.read_inode(raw, sb, inum)
    if not inode.addrs[0]:
        raise SystemExit("metadata bank has no first data block")
    start = inode.addrs[0] * ucore_fs.BSIZE
    if state == "uncommitted":
        raw[start:start + layout.header_bytes] = bytes(layout.header_bytes)
    elif state == "corrupt":
        raw[start] ^= 0x80
    else:
        raise SystemExit(f"unknown terminal state {state}")
path.write_bytes(raw)
PY
}

validate_large_terminal() {
	local image="$1" expected="$2"

	"${PYTHON_BIN}" - "${image}" "${expected}" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, "host_tools")
import plain_ucore_fs_extract as ucore_fs
from agent_metadata_disk_format import (
    DEFAULT_CONTRACT, BankError, load_contract, parse_bank,
)

path = Path(sys.argv[1])
expected = sys.argv[2]
image = path.read_bytes()
sb = ucore_fs.read_superblock(image)
layout = load_contract(DEFAULT_CONTRACT)
entries = {name: inum for inum, name in ucore_fs.root_entries(image, sb)}
states = []
for index, name in enumerate(layout.bank_names):
    if name not in entries:
        states.append("absent")
        continue
    inode = ucore_fs.read_inode(image, sb, entries[name])
    try:
        bank = parse_bank(ucore_fs.read_file(image, inode), name, layout)
    except BankError:
        states.append("corrupt")
        continue
    states.append(bank["state"])
    if index == 1:
        if bank["count"] < 32 or len(bank["store_image"]) <= 16 * ucore_fs.BSIZE:
            raise SystemExit("valid metadata bank does not exceed background burst")
if states != [expected, "valid"]:
    raise SystemExit(f"unexpected large-bank states {states!r}")
print(f"metadata_large_bank_check: peer={expected} selected=valid over_burst=1")
PY
}

require_crash_hook_absent() {
	local kernel="$1"
	local symbols printable

	if ! symbols="$("${TOOLPREFIX}nm" "${kernel}")"; then
		echo "cannot inspect production kernel symbols" >&2
		return 1
	fi
	if ! printable="$("${TOOLPREFIX}strings" "${kernel}")"; then
		echo "cannot inspect production kernel strings" >&2
		return 1
	fi
	if printf '%s\n' "${symbols}" | grep -Eq \
		'[[:space:]](sys_agent_metadata_test|agent_meta_crash_[A-Za-z0-9_]*)$'; then
		echo "metadata crash hook leaked into the production kernel" >&2
		return 1
	fi
	if printf '%s\n' "${printable}" | grep -Fq \
		'agentmetacrash_ucore: target_'; then
		echo "metadata crash marker leaked into the production kernel" >&2
		return 1
	fi
	if printf '%s\n' "${printable}" | grep -Fq \
		'agentmeta_boot_fault:'; then
		echo "metadata boot fault marker leaked into the production kernel" >&2
		return 1
	fi
}

require_line_once() {
	local line="$1" log="$2"
	local count

	count="$(grep -Fxc "${line}" "${log}" || true)"
	if [[ "${count}" -ne 1 ]]; then
		echo "expected exactly one line: ${line}; observed ${count}" >&2
		return 1
	fi
}

build_kernel recovery agentmetarecover_ucore
require_crash_hook_absent "${TMPDIR_META}/kernel-recovery"
authority_image="${TMPDIR_META}/disk-authority-newer.img"

# mkfs 提供完整镜像的第 1 代权威；Guest 先推进相对代际再进入指定检查点。
# Host 仅在精确阶段标记后首发 SIGKILL，并在恢复启动修复前检查原始 bank。
for interrupted_leg in "${matrix_legs[@]}"; do
	# 阶段 6 生成已验证参考，证明阶段 3/4 合并的 INVALIDATE+payload epoch 完整。
	for phase in "${matrix_phases[@]}"; do
		tag="${interrupted_leg}-${phase}"
		image="${TMPDIR_META}/disk-${tag}.img"
		make_image "${image}"
		build_kernel "crash-${tag}" agentmetacrash_ucore \
			"AGENT_METADATA_CRASH_PHASE=${phase}" \
			"AGENT_METADATA_CRASH_BANK=${interrupted_leg}" \
			DURABILITY_POWERCUT_TEST_PROFILE=1
		run_guest "agentmetacrash_ucore-${tag}" \
			"agentmetacrash_ucore: metadata_phase=${phase}" powercut \
			"${TMPDIR_META}/kernel-crash-${tag}" "${image}"
		"${PYTHON_BIN}" scripts/validate-metadata-crash-log.py \
			--log-file \
			"${TMPDIR_META}/agentmetacrash_ucore-${tag}.log" \
			--bank "${interrupted_leg}" --phase "${phase}"
		reference_image=""
		if [[ -f "${authority_image}" ]]; then
			reference_image="${authority_image}"
		fi
		validate_banks "${image}" interrupted-update \
			"${interrupted_leg}" "${phase}" "${reference_image}" | tee \
			"${TMPDIR_META}/bank-interrupted-${tag}.log"
		if [[ "${interrupted_leg}" == primary && "${phase}" -eq 6 ]]; then
			cp "${image}" "${authority_image}"
		fi

		run_guest "agentmetarecover_ucore-${tag}" \
			"agentmetarecover_ucore: parent passed" natural \
			"${TMPDIR_META}/kernel-recovery" "${image}"
		require_line_once \
			"agentmetarecover_ucore: readonly_recovery=1 metadata_available=1" \
			"${TMPDIR_META}/agentmetarecover_ucore-${tag}.log"
		require_line_once "agentmetarecover_ucore: query_found=0 returned=0" \
			"${TMPDIR_META}/agentmetarecover_ucore-${tag}.log"
		validate_banks "${image}" recovered | tee \
			"${TMPDIR_META}/bank-recovered-${tag}.log"
	done
done

if [[ "${METADATA_MATRIX_ONLY}" -eq 1 ]]; then
	echo "metadata crash matrix: ok legs=${matrix_legs[*]} phases=${matrix_phases[*]}"
	exit 0
fi

# 为并发故障用例构建无故障且完整复制的镜像。
select_image="${TMPDIR_META}/disk-select-fault.img"
make_image "${select_image}"
run_guest agentmetarecover_ucore-select-baseline \
	"agentmetarecover_ucore: parent passed" natural \
	"${TMPDIR_META}/kernel-recovery" "${select_image}"
require_line_once "agentmetarecover_ucore: query_found=0 returned=0" \
	"${TMPDIR_META}/agentmetarecover_ucore-select-baseline.log"
validate_banks "${select_image}" baseline | tee \
	"${TMPDIR_META}/bank-select-baseline.log"

# 可读 bank 较旧且新 peer 暂不可读时权威仍未决；同时测试 BUSY/EIO 下双 bank 不可用。
read -r newer_bank authority_generation < <(
	newer_bank_for_image "${authority_image}"
)
newer_bank_index="${newer_bank#bank}"
for fault_kind in busy io interrupted; do
	for fault_target in all newer; do
		fault_bank=all
		source_image="${select_image}"
		if [[ "${fault_target}" == newer ]]; then
			fault_bank="${newer_bank}"
			source_image="${authority_image}"
		fi
		boot_image="${TMPDIR_META}/disk-boot-${fault_kind}-${fault_target}.img"
		cp "${source_image}" "${boot_image}"
		build_kernel "boot-${fault_kind}-${fault_target}" agentmetatransient_ucore \
		"AGENT_METADATA_BOOT_READ_FAULT=${fault_kind}" \
		"AGENT_METADATA_BOOT_READ_FAULT_BANK=${fault_bank}" \
		AGENT_METADATA_BOOT_READ_FAULT_COUNT=3
		run_guest "agentmetatransient_ucore-boot-${fault_kind}-${fault_target}" \
		"agentmetatransient_ucore: parent passed" natural \
		"${TMPDIR_META}/kernel-boot-${fault_kind}-${fault_target}" "${boot_image}"
		fault_banks=(0 1)
		validator_bank=all
		if [[ "${fault_target}" == newer ]]; then
			fault_banks=("${newer_bank_index}")
			validator_bank="${newer_bank_index}"
		fi
		for bank in "${fault_banks[@]}"; do
			for remaining in 2 1 0; do
				require_line_once \
					"agentmeta_boot_fault: kind=${fault_kind} bank=${bank} remaining=${remaining}" \
					"${TMPDIR_META}/agentmetatransient_ucore-boot-${fault_kind}-${fault_target}.log"
			done
		done
		"${PYTHON_BIN}" scripts/validate-metadata-reprobe-log.py \
			--log-file \
			"${TMPDIR_META}/agentmetatransient_ucore-boot-${fault_kind}-${fault_target}.log" \
			--fault-kind "${fault_kind}" --fault-bank "${validator_bank}"
		require_line_once \
			"agentmetatransient_ucore: unavailable_seen=1 recovered=1" \
			"${TMPDIR_META}/agentmetatransient_ucore-boot-${fault_kind}-${fault_target}.log"
		validate_banks "${boot_image}" recovered | tee \
			"${TMPDIR_META}/bank-boot-${fault_kind}-${fault_target}-recovered.log"
		if [[ "${fault_target}" == newer ]]; then
			recovered_generation="$(max_generation_for_image "${boot_image}")"
			if (( recovered_generation <= authority_generation )); then
				echo "metadata authority rolled back: before=${authority_generation} after=${recovered_generation}" >&2
				exit 1
			fi
			echo "metadata_authority_check: kind=${fault_kind} newer_bank=${newer_bank_index} before=${authority_generation} after=${recovered_generation} rollback=0"
		fi
	done
done

# 播种大于 SYSTEM_BACKGROUND burst 的 bank；断电前执行前台在线重载，证明
# 不可恢复 syscall 路径会偿还 I/O debt 并在一次调用内完成。
large_image="${TMPDIR_META}/disk-large-seed.img"
make_image "${large_image}"
build_kernel large-seed agentmetalarge_ucore
require_crash_hook_absent "${TMPDIR_META}/kernel-large-seed"
run_guest agentmetalarge_ucore-large-seed \
	"agentmetalarge_ucore: seed_ready=1 records=32" powercut \
	"${TMPDIR_META}/kernel-large-seed" "${large_image}"
require_line_once "agentmetalarge_ucore: runtime_reload_completed=1" \
	"${TMPDIR_META}/agentmetalarge_ucore-large-seed.log"
validate_large_terminal "${large_image}" valid

# 已缓存的终态 peer 不得逐出大型有效 bank 的可恢复扫描；各用例也覆盖有界后台
# 预算下的选定 bank 确认和 catalog-plan 续跑。
build_kernel boot-large-terminal agentmetatransient_ucore \
	AGENT_METADATA_BOOT_READ_FAULT=busy \
	AGENT_METADATA_BOOT_READ_FAULT_BANK=bank1 \
	AGENT_METADATA_BOOT_READ_FAULT_COUNT=3
for terminal in absent uncommitted corrupt; do
	boot_image="${TMPDIR_META}/disk-large-${terminal}.img"
	cp "${large_image}" "${boot_image}"
	mutate_large_peer "${boot_image}" "${terminal}"
	validate_large_terminal "${boot_image}" "${terminal}"
	run_guest "agentmetatransient_ucore-large-${terminal}" \
		"agentmetatransient_ucore: parent passed" natural \
		"${TMPDIR_META}/kernel-boot-large-terminal" "${boot_image}"
	"${PYTHON_BIN}" scripts/validate-metadata-reprobe-log.py \
		--log-file "${TMPDIR_META}/agentmetatransient_ucore-large-${terminal}.log" \
		--fault-kind busy --fault-bank 1 --require-progress
	validate_banks "${boot_image}" recovered | tee \
		"${TMPDIR_META}/bank-large-${terminal}-recovered.log"
done

# 一次瞬态头部 flush EIO 应得到明确未决结果并从已验证 peer 修复；无故障双 bank
# 基线用于防止启动安装误通过。
eio_image="${TMPDIR_META}/disk-eio.img"
make_image "${eio_image}"
run_guest agentmetarecover_ucore-eio-baseline \
	"agentmetarecover_ucore: parent passed" natural \
	"${TMPDIR_META}/kernel-recovery" "${eio_image}"
require_line_once "agentmetarecover_ucore: query_found=0 returned=0" \
	"${TMPDIR_META}/agentmetarecover_ucore-eio-baseline.log"
validate_banks "${eio_image}" baseline | tee \
	"${TMPDIR_META}/bank-eio-baseline.log"
build_kernel eio agentmetaeio_ucore AGENT_METADATA_EIO_PHASE=6
run_guest agentmetaeio_ucore-eio \
	"agentmetaeio_ucore: parent passed" natural \
	"${TMPDIR_META}/kernel-eio" "${eio_image}"
require_line_once "agentmetaeio_ucore: transient_eio_repaired=1" \
	"${TMPDIR_META}/agentmetaeio_ucore-eio.log"

echo "[metadata-recovery] power-cut, bounded boot reprobe, over-burst terminal-peer recovery, and EIO recovery passed"
host_probe_report "metadata-recovery mkfs"
