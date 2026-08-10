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
CAMPAIGN_SAMPLES="${CONTEST_DEMO_SAMPLES:-4}"

case "${CAMPAIGN_SAMPLES}" in
	''|*[!0-9]*)
		echo "[contest-demo] sample count must be an integer" >&2
		exit 2
		;;
esac
if (( CAMPAIGN_SAMPLES < 4 || CAMPAIGN_SAMPLES > 16 || CAMPAIGN_SAMPLES % 2 != 0 )); then
	echo "[contest-demo] use an even sample count from 4 to 16" >&2
	exit 2
fi
if [[ -L "${OUTPUT_DIR}" ]]; then
	echo "[contest-demo] output directory must not be a symlink" >&2
	exit 2
fi

mkdir -p "${OUTPUT_DIR}"
rm -f "${OUTPUT_DIR}"/sample-*-qemu.log \
	"${OUTPUT_DIR}/summary.json" "${OUTPUT_DIR}/measurements.csv" \
	"${OUTPUT_DIR}/report.md"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agentos-contest-demo.XXXXXX")"
cleanup() {
	rm -f -- "${WORK_DIR}"/*
	rmdir -- "${WORK_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

guest_nonce="$("${PYTHON_BIN}" -I -S -c \
	'import secrets; print(f"{secrets.randbits(64) or 1:016x}")')"
started_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import time; print(time.monotonic())')"

echo "[contest-demo] 1/3 building ${CAMPAIGN_SAMPLES} AB/BA query samples"
"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile clean
rm -f nfs/fs.img nfs/fs-copy.img os/initproc.S build/os/initproc.o
for ((sample = 1; sample <= CAMPAIGN_SAMPLES; sample++)); do
	if (( sample % 2 == 1 )); then
		native_first=0
	else
		native_first=1
	fi
	user_extra_cflags="-Werror -DLABDEMO_RUN_NONCE=0x${guest_nonce}ULL -DLABDEMO_SAMPLE_ID=${sample} -DLABDEMO_NATIVE_FIRST=${native_first}"
	rm -f nfs/fs.img
	"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile nfs/fs.img \
		TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
		CHAPTER=agent INIT_PROC=labdemo_ucore \
		CH_TESTS="labdemo_ucore labdemo_execprobe_ucore" \
		USER_EXTRA_CFLAGS="${user_extra_cflags}"
	if (( sample == 1 )); then
		"${MAKE_TOOL}" --no-print-directory -s -rR -f Makefile build \
			TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" \
			LOG=error INIT_PROC=labdemo_ucore CHAPTER=agent \
			CH_TESTS="labdemo_ucore labdemo_execprobe_ucore" \
			USER_EXTRA_CFLAGS="${user_extra_cflags}"
		cp build/kernel "${WORK_DIR}/kernel"
	fi
	printf -v sample_tag '%02d' "${sample}"
	cp nfs/fs.img "${WORK_DIR}/sample-${sample_tag}-fs.img"
done

echo "[contest-demo] 2/3 running isolated QEMU samples"
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
		--kernel "${WORK_DIR}/kernel" \
		--image "${WORK_DIR}/sample-${sample_tag}-fs.img" \
		--qemu "${QEMU}"
done

finished_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import time; print(time.monotonic())')"
elapsed_seconds="$("${PYTHON_BIN}" -I -S -c \
	'import sys; print(float(sys.argv[2]) - float(sys.argv[1]))' \
	"${started_seconds}" "${finished_seconds}")"

echo "[contest-demo] 3/3 validating traversal/indexed results"
summary_args=(
	host_tools/contest_demo.py
	--elapsed-seconds "${elapsed_seconds}"
	--output-dir "${OUTPUT_DIR}"
)
for ((sample = 1; sample <= CAMPAIGN_SAMPLES; sample++)); do
	printf -v sample_tag '%02d' "${sample}"
	summary_args+=(--lab-log "${OUTPUT_DIR}/sample-${sample_tag}-qemu.log")
done
"${PYTHON_BIN}" -I -S -B "${summary_args[@]}"
echo "[contest-demo] raw logs: ${OUTPUT_DIR}/sample-*-qemu.log"
echo "[contest-demo] measurements: ${OUTPUT_DIR}/measurements.csv"
