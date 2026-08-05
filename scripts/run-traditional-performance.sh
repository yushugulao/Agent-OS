#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_TOOL="${MAKE_TOOL:-make}"
HOST_CC="${HOST_CC:-${HOSTCC:-cc}}"
unset CC HOSTCC
BUILD_JOBS="${TRADPERF_BUILD_JOBS:-${AGENTOS_BUILD_JOBS:-$("${PYTHON_BIN:-python3}" -I -S -B scripts/resource-jobs.py --kind build)}}"
SAMPLES="${TRADPERF_SAMPLES:-8}"
CASE_TIMEOUT="${TRADPERF_CASE_TIMEOUT:-180s}"
OUTPUT_DIR="${TRADPERF_OUTPUT:-results/traditional-performance}"

case "${BUILD_JOBS}" in
	''|*[!0-9]*) echo "[traditional-performance] build jobs must be an integer" >&2; exit 2 ;;
esac
case "${SAMPLES}" in
	''|*[!0-9]*) echo "[traditional-performance] sample count must be an integer" >&2; exit 2 ;;
esac
if (( BUILD_JOBS < 1 || BUILD_JOBS > 24 )); then
	echo "[traditional-performance] build jobs must be between 1 and 24" >&2
	exit 2
fi
if (( SAMPLES < 8 || SAMPLES > 32 || SAMPLES % 2 != 0 )); then
	echo "[traditional-performance] use an even sample count from 8 to 32" >&2
	exit 2
fi
if [[ -e "${OUTPUT_DIR}" || -L "${OUTPUT_DIR}" ]]; then
	echo "[traditional-performance] output already exists: ${OUTPUT_DIR}" >&2
	exit 2
fi

run_id="$(${PYTHON_BIN} -I -S -c 'import secrets; print(secrets.token_hex(32))')"
round_nonce="$(${PYTHON_BIN} -I -S -c \
	'import hashlib,sys; print(hashlib.sha256((sys.argv[1]+"|round").encode("ascii")).hexdigest())' \
	"${run_id}")"
guest_nonce_hex="${run_id:0:16}"
guest_nonce="$(${PYTHON_BIN} -I -S -c \
	'import sys; print(int(sys.argv[1], 16))' "${guest_nonce_hex}")"
commit="$(${PYTHON_BIN} -I -S scripts/trusted-python-entry.py \
	host_tools/traditional_performance.py identity --root .)"
source_tree="$(git rev-parse --verify "${commit}^{tree}")"

mkdir -p "${OUTPUT_DIR}/artifacts/agentos" \
	"${OUTPUT_DIR}/artifacts/baseline" "${OUTPUT_DIR}/runs"
: > "${OUTPUT_DIR}/artifacts/build-agentos.log"
: > "${OUTPUT_DIR}/artifacts/build-baseline.log"

run_logged() {
	local log="$1"
	local label="$2"
	shift 2
	printf '[%s]\n' "${label}" >> "${log}"
	"$@" >> "${log}" 2>&1
}

build_agentos_sample() {
	local sample="$1"
	local slot="$2"
	local tag="$3"
	local pair_dir="${OUTPUT_DIR}/artifacts/pair-${tag}"
	local log="${OUTPUT_DIR}/artifacts/build-agentos.log"

	run_logged "${log}" "sample-${tag} user" \
		"${MAKE_TOOL}" --no-print-directory -j"${BUILD_JOBS}" -rR \
		-C user -f Makefile target TOOLPREFIX="${TOOLPREFIX}" \
		PYTHON_BIN="${PYTHON_BIN}" CHAPTER=traditional_perf \
		INIT_PROC=tradperf CH_TESTS="tradperf tradexec" \
		TRADPERF_RUN_NONCE="${guest_nonce}" \
		TRADPERF_SAMPLE_ID="${sample}" TRADPERF_ORDER_SLOT="${slot}" \
		FUNCTIONAL_REVIEW_BUILD=1
	run_logged "${log}" "sample-${tag} filesystem" \
		"${MAKE_TOOL}" --no-print-directory -j"${BUILD_JOBS}" -rR \
		-C nfs -f Makefile fs.img \
		CHAPTER=traditional_perf INIT_PROC=tradperf \
		CH_TESTS="tradperf tradexec" FUNCTIONAL_REVIEW_BUILD=1
	cp nfs/fs.img "${pair_dir}/agentos-fs.img"
	cp user/target/bin/tradperf "${pair_dir}/agentos-guest.bin"
	cp user/target/elf/tradperf "${pair_dir}/agentos-guest.elf"
	cp user/target/bin/tradexec "${pair_dir}/agentos-exec.bin"
	cp user/target/elf/tradexec "${pair_dir}/agentos-exec.elf"
}

build_baseline_sample() {
	local sample="$1"
	local slot="$2"
	local tag="$3"
	local pair_dir="${OUTPUT_DIR}/artifacts/pair-${tag}"
	local log="${OUTPUT_DIR}/artifacts/build-baseline.log"

	run_logged "${log}" "sample-${tag} user" \
		"${MAKE_TOOL}" --no-print-directory -j"${BUILD_JOBS}" -rR \
		-C baseline_ucore/user -f Makefile target TOOLPREFIX="${TOOLPREFIX}" \
		CHAPTER=traditional_perf CH_TESTS="tradperf tradexec" \
		TRADPERF_RUN_NONCE="${guest_nonce}" \
		TRADPERF_SAMPLE_ID="${sample}" TRADPERF_ORDER_SLOT="${slot}"
	run_logged "${log}" "sample-${tag} filesystem" \
		"${MAKE_TOOL}" --no-print-directory -j"${BUILD_JOBS}" -rR \
		-C baseline_ucore/nfs -f Makefile fs.img HOSTCC="${HOST_CC:-cc}"
	cp baseline_ucore/nfs/fs.img "${pair_dir}/baseline-fs.img"
	cp baseline_ucore/user/target/bin/tradperf \
		"${pair_dir}/baseline-guest.bin"
	cp baseline_ucore/user/target/elf/tradperf \
		"${pair_dir}/baseline-guest.elf"
	cp baseline_ucore/user/target/bin/tradexec \
		"${pair_dir}/baseline-exec.bin"
	cp baseline_ucore/user/target/elf/tradexec \
		"${pair_dir}/baseline-exec.elf"
}

echo "[traditional-performance] clearing stale user artifacts"
run_logged "${OUTPUT_DIR}/artifacts/build-agentos.log" "clean user" \
	"${MAKE_TOOL}" --no-print-directory -rR -C user -f Makefile clean &
agentos_clean_pid="$!"
run_logged "${OUTPUT_DIR}/artifacts/build-baseline.log" "clean user" \
	"${MAKE_TOOL}" --no-print-directory -rR -C baseline_ucore/user \
	-f Makefile clean &
baseline_clean_pid="$!"
clean_failed=0
wait "${agentos_clean_pid}" || clean_failed=1
wait "${baseline_clean_pid}" || clean_failed=1
if (( clean_failed != 0 )); then
	echo "[traditional-performance] user cleanup failed" >&2
	exit 1
fi

echo "[traditional-performance] building ${SAMPLES} paired images with ${BUILD_JOBS} jobs per target"
for ((sample = 1; sample <= SAMPLES; sample++)); do
	printf -v tag '%02d' "${sample}"
	pair_dir="${OUTPUT_DIR}/artifacts/pair-${tag}"
	mkdir -p "${pair_dir}"
	if (( sample % 2 == 1 )); then
		agentos_slot=1
		baseline_slot=2
	else
		agentos_slot=2
		baseline_slot=1
	fi
	build_agentos_sample "${sample}" "${agentos_slot}" "${tag}" &
	agentos_pid="$!"
	build_baseline_sample "${sample}" "${baseline_slot}" "${tag}" &
	baseline_pid="$!"
	build_failed=0
	wait "${agentos_pid}" || build_failed=1
	wait "${baseline_pid}" || build_failed=1
	if (( build_failed != 0 )); then
		echo "[traditional-performance] sample ${tag} build failed" >&2
		exit 1
	fi
done

echo "[traditional-performance] building both kernels"
run_logged "${OUTPUT_DIR}/artifacts/build-agentos.log" "kernel" \
	"${MAKE_TOOL}" --no-print-directory -j"${BUILD_JOBS}" -rR -f Makefile \
	build TOOLPREFIX="${TOOLPREFIX}" PYTHON_BIN="${PYTHON_BIN}" LOG=error \
	INIT_PROC=tradperf CHAPTER=traditional_perf FUNCTIONAL_REVIEW_BUILD=1
cp build/kernel "${OUTPUT_DIR}/artifacts/agentos/kernel"
run_logged "${OUTPUT_DIR}/artifacts/build-baseline.log" "kernel" \
	"${MAKE_TOOL}" --no-print-directory -j"${BUILD_JOBS}" -rR \
	-C baseline_ucore -f Makefile build TOOLPREFIX="${TOOLPREFIX}" \
	PYTHON_BIN="${PYTHON_BIN}" LOG=error INIT_PROC=tradperf \
	CHAPTER=traditional_perf
cp baseline_ucore/build/kernel "${OUTPUT_DIR}/artifacts/baseline/kernel"

"${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py \
	host_tools/traditional_performance.py prepare \
	--root . --campaign-dir "${OUTPUT_DIR}" --run-id "${run_id}" \
	--round-nonce "${round_nonce}" --samples "${SAMPLES}" \
	--make-tool "${MAKE_TOOL}" --qemu "${QEMU}" \
	--toolchain-cc "${TOOLPREFIX}gcc" --build-jobs "${BUILD_JOBS}"
plan_sha="$(${PYTHON_BIN} -I -S -c \
	'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
	"${OUTPUT_DIR}/build-manifest.json")"

derive_nonce() {
	"${PYTHON_BIN}" -I -S -c \
		'import hashlib,sys; print(hashlib.sha256((sys.argv[1]+"|"+sys.argv[2]).encode("ascii")).hexdigest())' \
		"${run_id}" "$1"
}

run_target() {
	local sample="$1"
	local tag="$2"
	local target="$3"
	local run_dir="${OUTPUT_DIR}/runs/pair-${tag}"
	local artifact_dir="${OUTPUT_DIR}/artifacts/pair-${tag}"
	local kernel="${OUTPUT_DIR}/artifacts/${target}/kernel"
	local input_image="${artifact_dir}/${target}-fs.img"
	local run_image="${run_dir}/${target}-run.img"
	local log="${run_dir}/${target}.log"
	local attestation="${run_dir}/${target}.attestation.json"
	local session_nonce
	local execution_nonce

	mkdir -p "${run_dir}"
	cp "${input_image}" "${run_image}"
	session_nonce="$(derive_nonce "pair-${tag}|${target}|session")"
	execution_nonce="$(derive_nonce "pair-${tag}|${target}|execution")"
	"${PYTHON_BIN}" -I -S -B scripts/agent_test_runner.py \
		--init-proc tradperf --marker "tradperf: complete" \
		--marker-mode exact-line --log-file "${log}" \
		--case-timeout "${CASE_TIMEOUT}" --idle-notice-seconds 15 \
		--marker-grace-seconds 2s --qemu "${QEMU}" \
		--kernel "${kernel}" --image "${run_image}" \
		--attestation-file "${attestation}" \
		--run-id "${session_nonce}" --execution-id "${execution_nonce}" \
		--evidence-scope local_e3_unsigned --source-commit "${commit}" \
		--source-tree "${source_tree}" --campaign-nonce "${run_id}" \
		--calibration-plan-sha256 "${plan_sha}" \
		--round-nonce "${round_nonce}" --session-nonce "${session_nonce}" \
		--execution-nonce "${execution_nonce}" \
		--toolchain-cc "${TOOLPREFIX}gcc"
}

echo "[traditional-performance] running ${SAMPLES} AB/BA pairs in one formal QEMU slot"
for ((sample = 1; sample <= SAMPLES; sample++)); do
	printf -v tag '%02d' "${sample}"
	if (( sample % 2 == 1 )); then
		run_target "${sample}" "${tag}" agentos
		run_target "${sample}" "${tag}" baseline
	else
		run_target "${sample}" "${tag}" baseline
		run_target "${sample}" "${tag}" agentos
	fi
done

"${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py \
	host_tools/traditional_performance.py render --root . \
	--campaign-dir "${OUTPUT_DIR}" --output-dir "${OUTPUT_DIR}/report"
echo "[traditional-performance] dashboard: ${OUTPUT_DIR}/report/index.html"
