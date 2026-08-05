#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_TOOL="${MAKE_TOOL:-make}"
CASE_TIMEOUT="${CONTEST_DEMO_CASE_TIMEOUT:-150s}"
OUTPUT_DIR="${CONTEST_DEMO_OUTPUT:-results/contest-demo}"
CAMPAIGN_SAMPLES="${CONTEST_DEMO_SAMPLES:-8}"
QEMU_JOBS="${CONTEST_DEMO_QEMU_JOBS:-1}"

case "${CAMPAIGN_SAMPLES}" in
	''|*[!0-9]*) echo "[contest-demo] sample count must be an integer" >&2; exit 2 ;;
esac
case "${QEMU_JOBS}" in
	''|*[!0-9]*) echo "[contest-demo] QEMU jobs must be an integer" >&2; exit 2 ;;
esac
if (( CAMPAIGN_SAMPLES < 8 || CAMPAIGN_SAMPLES > 64 || CAMPAIGN_SAMPLES % 2 != 0 )); then
	echo "[contest-demo] campaign needs an even sample count from 8 to 64" >&2
	exit 2
fi
if (( QEMU_JOBS != 1 )); then
	echo "[contest-demo] formal latency measurement requires one isolated QEMU" >&2
	exit 2
fi

if [[ -L "${OUTPUT_DIR}" ]]; then
	echo "[contest-demo] output directory must not be a symlink" >&2
	exit 2
fi

# The report is a source-bound measurement artifact, not a worktree preview.
commit="$("${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py \
	host_tools/contest_demo.py identity --root .)"
run_id="$("${PYTHON_BIN}" -I -S -c \
	'import secrets; print(secrets.token_hex(8))')"
started_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import time; print(time.monotonic())')"

mkdir -p "${OUTPUT_DIR}"
rm -f "${OUTPUT_DIR}"/*-qemu.log "${OUTPUT_DIR}"/*-kernel \
	"${OUTPUT_DIR}"/*-fs.img "${OUTPUT_DIR}"/*-labdemo.elf \
	"${OUTPUT_DIR}/summary.json" \
	"${OUTPUT_DIR}/dashboard-data.json" "${OUTPUT_DIR}/timeline.json" \
	"${OUTPUT_DIR}/report.md" "${OUTPUT_DIR}/index.html"

echo "[contest-demo] 1/3 构建 ${CAMPAIGN_SAMPLES} 个 AB/BA Guest 样本"
"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile clean \
	FUNCTIONAL_REVIEW_BUILD=1
rm -f nfs/fs.img nfs/fs-copy.img os/initproc.S build/os/initproc.o
for ((sample = 1; sample <= CAMPAIGN_SAMPLES; sample++)); do
	if (( sample % 2 == 1 )); then
		native_first=0
	else
		native_first=1
	fi
	user_extra_cflags="-Werror -DLABDEMO_RUN_NONCE=0x${run_id}ULL -DLABDEMO_SAMPLE_ID=${sample} -DLABDEMO_NATIVE_FIRST=${native_first}"
	rm -f nfs/fs.img
	"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile nfs/fs.img \
		TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
		HOST_CC="${HOST_CC:-cc}" CHAPTER=agent INIT_PROC=labdemo_ucore \
		CH_TESTS="labdemo_ucore labdemo_execprobe_ucore" \
		USER_EXTRA_CFLAGS="${user_extra_cflags}" FUNCTIONAL_REVIEW_BUILD=1
	if (( sample == 1 )); then
		"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile build \
			TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
			LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent \
			CH_TESTS="labdemo_ucore labdemo_execprobe_ucore" \
			USER_EXTRA_CFLAGS="${user_extra_cflags}" \
			FUNCTIONAL_REVIEW_BUILD=1
		cp build/kernel "${OUTPUT_DIR}/showcase-kernel"
	fi
	printf -v sample_tag '%02d' "${sample}"
	cp nfs/fs.img "${OUTPUT_DIR}/sample-${sample_tag}-fs.img"
	cp user/target/elf/labdemo_ucore \
		"${OUTPUT_DIR}/sample-${sample_tag}-labdemo.elf"
done

echo "[contest-demo] 2/3 用单个隔离 QEMU 槽运行正式实测"
pids=()
campaign_failed=0
wait_qemu_batch() {
	local pid
	for pid in "${pids[@]}"; do
		if ! wait "${pid}"; then
			campaign_failed=1
		fi
	done
	pids=()
}
for ((sample = 1; sample <= CAMPAIGN_SAMPLES; sample++)); do
	printf -v sample_tag '%02d' "${sample}"
	"${PYTHON_BIN}" -I -S scripts/agent_test_runner.py \
		--init-proc labdemo_ucore \
		--marker "labdemo_ucore: parent passed" \
		--marker-mode exact-line \
		--log-file "${OUTPUT_DIR}/sample-${sample_tag}-qemu.log" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds 15 \
		--marker-grace-seconds 2s \
		--kernel "${OUTPUT_DIR}/showcase-kernel" \
		--image "${OUTPUT_DIR}/sample-${sample_tag}-fs.img" \
		--qemu "${QEMU}" &
	pids+=("$!")
	if (( ${#pids[@]} == QEMU_JOBS )); then
		wait_qemu_batch
	fi
done
wait_qemu_batch
if (( campaign_failed != 0 )); then
	echo "[contest-demo] at least one QEMU sample failed" >&2
	exit 1
fi

finished_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import time; print(time.monotonic())')"
elapsed_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import sys; print(float(sys.argv[2]) - float(sys.argv[1]))' \
	"${started_seconds}" "${finished_seconds}")"

echo "[contest-demo] 3/3 严格核验 Guest 事件并生成实测 Dashboard"
render_args=(
	host_tools/contest_demo.py render
	--source-root .
	--run-id "${run_id}"
	--commit "${commit}"
	--elapsed-seconds "${elapsed_seconds}"
	--artifact "${OUTPUT_DIR}/showcase-kernel"
	--output-dir "${OUTPUT_DIR}"
)
for ((sample = 1; sample <= CAMPAIGN_SAMPLES; sample++)); do
	printf -v sample_tag '%02d' "${sample}"
	render_args+=(
		--lab-log "${OUTPUT_DIR}/sample-${sample_tag}-qemu.log"
		--artifact "${OUTPUT_DIR}/sample-${sample_tag}-fs.img"
		--artifact "${OUTPUT_DIR}/sample-${sample_tag}-labdemo.elf"
	)
done
"${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py "${render_args[@]}"
