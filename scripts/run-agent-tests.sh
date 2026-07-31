#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
MAKE_TOOL="${MAKE_TOOL:-make}"
GIT_BIN="${GIT_BIN:-git}"
LOG="${LOG:-error}"
if [[ "${AGENT_TEST_CASE:-}" == "agenteval_ucore" ]]; then
	CHAPTER="${CHAPTER:-agent_eval}"
	if [[ "${CHAPTER}" != "agent_eval" ]]; then
		echo "[agent-tests] agenteval_ucore requires CHAPTER=agent_eval" >&2
		exit 1
	fi
	if [[ -z "${AGENT_EVAL_CHALLENGE_HEX:-}" ]]; then
		AGENT_EVAL_CHALLENGE_HEX="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
	fi
	if [[ ! "${AGENT_EVAL_CHALLENGE_HEX}" =~ ^[0-9a-f]{16}$ ||
	      "${AGENT_EVAL_CHALLENGE_HEX}" == "0000000000000000" ]]; then
		echo "[agent-tests] AGENT_EVAL_CHALLENGE_HEX must be 16 nonzero lowercase hex digits" >&2
		exit 1
	fi
	export AGENT_EVAL_CHALLENGE_HEX
else
	CHAPTER="${CHAPTER:-agent}"
fi
CASE_TIMEOUT="${CASE_TIMEOUT:-180s}"
QEMU="${QEMU:-qemu-system-riscv64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-2s}"
REQUIRE_FULL_SUITE="${REQUIRE_FULL_SUITE:-0}"
AGENT_TEST_CALIBRATE="${AGENT_TEST_CALIBRATE:-0}"
AGENT_TEST_DURATION_PROFILE="${AGENT_TEST_DURATION_PROFILE:-local-e3}"
CONTEXT_SYNC_TIMING_FILE="${TMPDIR:-/tmp}/agentos-context-sync-timings.$$"
CONTEXT_SYNC_USER_CFLAGS="-DAGENT_CONTEXT_SYNC_TEST_PROFILE -DWAIT_ATOMIC_TEST_PROFILE"
calibration_case_ordinal=0
timing_file_owned=0
if [[ -z "${AGENT_TEST_TIMING_FILE:-}" ]]; then
	AGENT_TEST_TIMING_FILE="${TMPDIR:-/tmp}/agentos-agent-timings.$$"
	timing_file_owned=1
fi

cleanup() {
	rm -f "${CONTEXT_SYNC_TIMING_FILE}"
	if [[ "${timing_file_owned}" == "1" ]]; then
		rm -f "${AGENT_TEST_TIMING_FILE}"
	fi
}
trap cleanup EXIT

if [[ "${REQUIRE_FULL_SUITE}" != "0" && "${REQUIRE_FULL_SUITE}" != "1" ]]; then
	echo "[agent-tests] REQUIRE_FULL_SUITE must be 0 or 1" >&2
	exit 1
fi
if [[ "${REQUIRE_FULL_SUITE}" == "1" && -n "${AGENT_TEST_CASE:-}" ]]; then
	echo "[agent-tests] AGENT_TEST_CASE is forbidden for a required full suite" >&2
	exit 1
fi
if [[ "${AGENT_TEST_CALIBRATE}" != "0" && "${AGENT_TEST_CALIBRATE}" != "1" ]]; then
	echo "[agent-tests] AGENT_TEST_CALIBRATE must be 0 or 1" >&2
	exit 1
fi
if [[ "${AGENT_TEST_DURATION_PROFILE}" != "local-e3" &&
      "${AGENT_TEST_DURATION_PROFILE}" != "none" ]]; then
	echo "[agent-tests] AGENT_TEST_DURATION_PROFILE must be local-e3 or none" >&2
	exit 1
fi
if [[ "${AGENT_TEST_CALIBRATE}" == "1" &&
      "${AGENT_TEST_DURATION_PROFILE}" != "local-e3" ]]; then
	echo "[agent-tests] calibration requires the local-e3 duration profile" >&2
	exit 1
fi
if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
	if [[ "${REQUIRE_FULL_SUITE}" != "1" ]]; then
		echo "[agent-tests] calibration requires REQUIRE_FULL_SUITE=1" >&2
		exit 1
	fi
	if [[ "${timing_file_owned}" == "1" ]]; then
		echo "[agent-tests] calibration requires a persistent AGENT_TEST_TIMING_FILE" >&2
		exit 1
	fi
	for required in \
		AGENT_TEST_CALIBRATION_PLAN \
		AGENT_TEST_CALIBRATION_ROUND \
		AGENT_TEST_CAMPAIGN_NONCE \
		AGENT_TEST_ROUND_NONCE \
		AGENT_TEST_SOURCE_COMMIT \
		AGENT_TEST_SOURCE_TREE \
		AGENT_TEST_ATTESTATION_DIR \
		AGENT_TEST_GUEST_LOG_FILE; do
		if [[ -z "${!required:-}" ]]; then
			echo "[agent-tests] calibration requires ${required}" >&2
			exit 1
		fi
	done
	if [[ ! "${AGENT_TEST_CALIBRATION_ROUND}" =~ ^0[123]$ ||
	      ! "${AGENT_TEST_CAMPAIGN_NONCE}" =~ ^[0-9a-f]{64}$ ||
	      ! "${AGENT_TEST_ROUND_NONCE}" =~ ^[0-9a-f]{64}$ ||
	      ! "${AGENT_TEST_SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ||
	      ! "${AGENT_TEST_SOURCE_TREE}" =~ ^[0-9a-f]{40}$ ]]; then
		echo "[agent-tests] calibration identity is malformed" >&2
		exit 1
	fi
	for absolute in \
		"${AGENT_TEST_CALIBRATION_PLAN}" \
		"${AGENT_TEST_ATTESTATION_DIR}" \
		"${AGENT_TEST_GUEST_LOG_FILE}" \
		"${AGENT_TEST_TIMING_FILE}"; do
		if [[ "${absolute}" != /* ]]; then
			echo "[agent-tests] calibration paths must be absolute" >&2
			exit 1
		fi
	done
	if [[ ! -f "${AGENT_TEST_CALIBRATION_PLAN}" ||
	      -L "${AGENT_TEST_CALIBRATION_PLAN}" ||
	      ! -d "${AGENT_TEST_ATTESTATION_DIR}" ||
	      -L "${AGENT_TEST_ATTESTATION_DIR}" ||
	      -n "$(find "${AGENT_TEST_ATTESTATION_DIR}" -mindepth 1 -print -quit)" ||
	      -e "${AGENT_TEST_TIMING_FILE}" ||
	      -e "${AGENT_TEST_GUEST_LOG_FILE}" ]]; then
		echo "[agent-tests] calibration outputs must start new and empty" >&2
		exit 1
	fi
	AGENT_TEST_CALIBRATION_PLAN_SHA256="$("${PYTHON_BIN}" -I -S - "${AGENT_TEST_CALIBRATION_PLAN}" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
	: >"${AGENT_TEST_GUEST_LOG_FILE}"
	declare -A calibration_nonce_seen=()
	calibration_nonce_seen["${AGENT_TEST_CAMPAIGN_NONCE}"]=1
	calibration_nonce_seen["${AGENT_TEST_ROUND_NONCE}"]=1
	new_calibration_nonce() {
		local output_name="$1"
		local nonce
		while :; do
			nonce="$("${PYTHON_BIN}" -I -S -c 'import secrets; print(secrets.token_hex(32))')"
			if [[ -z "${calibration_nonce_seen[${nonce}]:-}" ]]; then
				calibration_nonce_seen["${nonce}"]=1
				printf -v "${output_name}" '%s' "${nonce}"
				return 0
			fi
		done
	}
	if [[ -n "$("${GIT_BIN}" status --porcelain=v1 --untracked-files=all)" ||
	      "$("${GIT_BIN}" rev-parse --verify HEAD)" != "${AGENT_TEST_SOURCE_COMMIT}" ||
	      "$("${GIT_BIN}" rev-parse HEAD^{tree})" != "${AGENT_TEST_SOURCE_TREE}" ||
	      -n "$("${GIT_BIN}" symbolic-ref -q HEAD || true)" ]]; then
		echo "[agent-tests] calibration requires the declared clean detached checkout" >&2
		exit 1
	fi
fi
if [[ "${AGENT_TEST_CALIBRATE}" == "0" ]]; then
	: >"${AGENT_TEST_TIMING_FILE}"
fi
"${PYTHON_BIN}" -I -S scripts/test-sync-owner-wiring.py
"${PYTHON_BIN}" -I -S scripts/test-wait-atomic-wiring.py
"${PYTHON_BIN}" -I -S scripts/check-wait-queue-contract.py
if [[ -z "${AGENT_TEST_CASE:-}" && "${AGENT_TEST_CALIBRATE}" == "0" &&
      "${AGENT_TEST_DURATION_PROFILE}" == "local-e3" ]]; then
	"${PYTHON_BIN}" -I -S scripts/check-kernel-budgets.py \
		--check agent-test-policy \
		--config ci/kernel-budgets.json
fi

check_suite_budget() {
	local calibration_args=()
	if [[ "${AGENT_TEST_DURATION_PROFILE}" == "none" ]]; then
		"${PYTHON_BIN}" -I -S scripts/check-kernel-budgets.py \
			--check agent-test-timing-inventory \
			--config ci/kernel-budgets.json \
			--agent-test-timing-file "${AGENT_TEST_TIMING_FILE}"
		echo "[agent-tests] duration-profile profile=none gate=skipped reason=different-runner"
		return
	fi
	if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
		calibration_args+=(--agent-test-calibration)
	fi
	"${PYTHON_BIN}" -I -S scripts/check-kernel-budgets.py \
		--check agent-tests \
		--config ci/kernel-budgets.json \
		--agent-test-timing-file "${AGENT_TEST_TIMING_FILE}" \
		"${calibration_args[@]}"
}

require_exact_case_marker() {
	local log_file="$1"
	local marker="$2"

	if ! grep -Fxq "${marker}" "${log_file}"; then
		echo "[agent-tests] missing mechanism marker: ${marker}" >&2
		tail -80 "${log_file}" >&2
		return 1
	fi
}

check_case_contract() {
	local init_proc="$1"
	local log_file="$2"
	local context_sync_profile="${3:-0}"

	case "${init_proc}" in
	agenteval_ucore)
		"${PYTHON_BIN}" -I -S scripts/trusted-python-entry.py \
			host_tools/evaluation_contract.py validate-guest \
			--suite ci/evaluation-suite.json \
			--log "${log_file}" \
			--challenge "${AGENT_EVAL_CHALLENGE_HEX}"
		;;
	agentloop_ucore)
		require_exact_case_marker "${log_file}" \
			"agentloop_ucore: broadcast_slow_watcher_isolated=1"
		require_exact_case_marker "${log_file}" \
			"agentloop_ucore: heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1"
		;;
	agentfinal_ucore)
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1"
		if [[ "${context_sync_profile}" == "1" ]]; then
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1"
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1"
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1"
		fi
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_rollback_branch=1 sequence_reuse=0 provenance_bound=1"
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1"
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_rollback_negative nonexistent=1 evicted=1"
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: fifo oldest=66 latest=193 dropped=65 policy=1"
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_query_cache=1 user_managed=1 kernel_cache_hit=0"
		;;
	agentscan_ucore)
		require_exact_case_marker "${log_file}" \
			"agentscan_ucore: scan_admission trusted=1 worker=0"
		;;
	agenttoolabi_ucore)
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: tool_list_contract=1"
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: optional_schema=1 heartbeat_zero_stop=1"
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: schema_generated=1 validated=25"
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: v1_compatible=1"
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: v2_typed_reordered=1"
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1"
		require_exact_case_marker "${log_file}" \
			"agenttoolabi_ucore: strict_negative_matrix=1"
		;;
	agentsecurity_ucore)
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_lazy_empty=1 first_alloc_pages=2"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_queue_full=1 capacity=16"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_read_failure_atomic=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_endpoint_reuse_isolated=1 stale_pid_denied=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_exec_endpoint_rotated=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_ordinary_domain_isolation=1 same_account_compat=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_active_workflow_isolation=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_scoped_public=1 same_lineage=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_cross_scope_denied=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: mail_missing_controller_denied=1"
		;;
	labdemo_ucore)
		require_exact_case_marker "${log_file}" \
			"labdemo_ucore: startup_barrier ready=3 event_queued=1 released=3"
		;;
	usersafety_ucore)
		require_exact_case_marker "${log_file}" \
			"usersafety_ucore: argv_layout_budget=1024 boundary_accept=1 over_limit_rejected=1 caller_live=1"
		;;
	blocking_semantics_ucore)
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: owner_slot_reuse=16 generation_safe=1"
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: process_exit_multilock=1 baton_revoke=1 cond_sem_interrupt_refund=1"
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: exec_sync_reset=1 stale_ids_rejected=1"
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: atomic_wait_publication=512 cond=1 semaphore=1 count_stable=1"
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: mutex_fifo_waiters=64 dispatch_stable=1"
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: mutex_owner=1 nonowner_rejected=1 recursive_rejected=1 owner_exit_handoff=1"
		require_exact_case_marker "${log_file}" \
			"blocking_semantics_ucore: waittid_sleep=1 pipe_wait_queue=1 close_wake_all=1"
		;;
	agentscope_ucore)
		require_exact_case_marker "${log_file}" \
			"agentscope_ucore: scope_storage_isolation=1 catalog_limit=112 autoscan_limit=96 explicit_reserve=16 workflow_created=97 peer_created=97 public_created=70 overflow_unindexed=1 autoscan_flag_no_space=1 explicit_no_space=1 reusable=1"
		require_exact_case_marker "${log_file}" \
			"agentscope_ucore: scope_controller_exit_revoke=1 public_lineage=1"
		require_exact_case_marker "${log_file}" \
			"agentscope_ucore: lifecycle_reclamation=1"
		;;
	agentvfs_ucore)
		require_exact_case_marker "${log_file}" \
			"agentvfs_ucore: fstat_reauthorize=1"
		;;
	iobudget_ucore)
		require_exact_case_marker "${log_file}" \
			"iobudget_ucore: lineage_rate_accounting=1 immutable_owner=1"
		if grep -Fq "Unexpected mutex id" "${log_file}"; then
			echo "[agent-tests] child inherited a stale stdio mutex" >&2
			return 1
		fi
		;;
	esac
}

build_user_image() {
	local user_extra_cflags="${1:-}"

	# nfs/fs.img depends on the user target.  Keep both compilation and image
	# construction in one make invocation so profile flags cannot be dropped.
	"${MAKE_TOOL}" -rR -f Makefile nfs/fs.img \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}" \
		FUNCTIONAL_REVIEW_BUILD=1 \
		USER_EXTRA_CFLAGS="${user_extra_cflags}"
}

run_case() {
	local init_proc="$1"
	local marker="$2"
	local expected_bad_addr_marker="${3:-}"
	local context_sync_profile="${4:-0}"
	local log_file="${TMPDIR:-/tmp}/agentos-${init_proc}.$$.log"
	local expected_fault_args=()
	local build_profile_args=()
	local case_timing_file="${AGENT_TEST_TIMING_FILE}"
	local evidence_key="agent-case:${init_proc}"
	local runner_status
	local attestation_args=()
	local timing_args=()
	local attestation_key=""
	local case_session_nonce=""
	local case_execution_nonce=""

	if [[ -n "${expected_bad_addr_marker}" ]]; then
		expected_fault_args+=(
			--expected-bad-addr-after "${expected_bad_addr_marker}"
		)
	fi
	if [[ "${context_sync_profile}" == "1" ]]; then
		build_profile_args+=(AGENT_CONTEXT_SYNC_TEST_PROFILE=1 WAIT_ATOMIC_TEST_PROFILE=1)
		case_timing_file="${CONTEXT_SYNC_TIMING_FILE}"
		evidence_key="agent-mechanism:context-sync-atomicity"
	fi
	if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
		if [[ "${context_sync_profile}" == "1" ]]; then
			attestation_key="00-context-sync-atomicity"
		else
			calibration_case_ordinal=$((calibration_case_ordinal + 1))
			printf -v attestation_key '%02d-%s' \
				"${calibration_case_ordinal}" "${init_proc}"
		fi
		new_calibration_nonce case_session_nonce
		new_calibration_nonce case_execution_nonce
		attestation_args=(
			--attestation-file \
			"${AGENT_TEST_ATTESTATION_DIR}/${attestation_key}.json"
			--run-id "${case_session_nonce}"
			--execution-id "${case_execution_nonce}"
			--evidence-scope local_e3_unsigned
			--source-commit "${AGENT_TEST_SOURCE_COMMIT}"
			--source-tree "${AGENT_TEST_SOURCE_TREE}"
			--campaign-nonce "${AGENT_TEST_CAMPAIGN_NONCE}"
			--calibration-plan-sha256 \
			"${AGENT_TEST_CALIBRATION_PLAN_SHA256}"
			--round-nonce "${AGENT_TEST_ROUND_NONCE}"
			--session-nonce "${case_session_nonce}"
			--execution-nonce "${case_execution_nonce}"
			--toolchain-cc "${TOOLPREFIX}gcc"
		)
	else
		timing_args=(--timing-file "${case_timing_file}")
	fi

	echo "[agent-tests] running ${init_proc}"
	rm -f nfs/fs-copy.img os/initproc.S build/os/initproc.o
	"${MAKE_TOOL}" -rR -f Makefile build \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}" \
		FUNCTIONAL_REVIEW_BUILD=1 \
		"${build_profile_args[@]}"
	cp nfs/fs.img nfs/fs-copy.img
	if "${PYTHON_BIN}" -I -S scripts/agent_test_runner.py \
		--init-proc "${init_proc}" \
		--marker "${marker}" \
		--marker-mode exact-line \
		--expected-bad-addr-marker-mode exact-line \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" \
		"${timing_args[@]}" \
		"${attestation_args[@]}" \
		"${expected_fault_args[@]}"; then
		runner_status=0
	else
		runner_status=$?
	fi
	evidence_append_guest_log \
		"${evidence_key}" "${log_file}" \
		"${AGENT_TEST_GUEST_LOG_FILE:-}"
	if [[ ${runner_status} -ne 0 ]]; then
		return "${runner_status}"
	fi
	if [[ "${AGENT_TEST_CALIBRATE}" == "1" &&
	      ! -s "${AGENT_TEST_ATTESTATION_DIR}/${attestation_key}.json" ]]; then
		echo "[agent-tests] missing case attestation: ${attestation_key}" >&2
		return 1
	fi
	if [[ "${context_sync_profile}" == "1" ]]; then
		"${PYTHON_BIN}" -I -S scripts/validate-kernel-test-log.py \
			--log-file "${log_file}" \
			--tag "wait-atomic" \
			--profile wait-atomic
	fi
	check_case_contract "${init_proc}" "${log_file}" \
		"${context_sync_profile}"
	echo "[agent-tests] ${init_proc} passed"
}

"${MAKE_TOOL}" -rR -C user -f Makefile clean
"${MAKE_TOOL}" -rR -f Makefile clean FUNCTIONAL_REVIEW_BUILD=1
if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
	"${PYTHON_BIN}" -I -S scripts/agent_test_calibration.py check-source \
		--root . \
		--source-commit "${AGENT_TEST_SOURCE_COMMIT}"
fi

if [[ -z "${AGENT_TEST_CASE:-}" ||
	  "${AGENT_TEST_CASE}" == "agentfinal_ucore" ]]; then
	: >"${CONTEXT_SYNC_TIMING_FILE}"
	build_user_image "${CONTEXT_SYNC_USER_CFLAGS}"
	run_case agentfinal_ucore "agentfinal_ucore: parent passed" "" 1
	"${MAKE_TOOL}" -rR -C user -f Makefile clean
	"${MAKE_TOOL}" -rR -f Makefile clean FUNCTIONAL_REVIEW_BUILD=1
fi

build_user_image
"${MAKE_TOOL}" -rR -f Makefile build \
	TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore \
	FUNCTIONAL_REVIEW_BUILD=1

if [[ -n "${AGENT_TEST_CASE:-}" ]]; then
	expected_bad_addr_marker=""
	if [[ "${AGENT_TEST_CASE}" == "iobudget_ucore" ]]; then
		expected_bad_addr_marker="iobudget_ucore: fault_exit_armed=1"
	fi
	run_case "${AGENT_TEST_CASE}" "${AGENT_TEST_CASE}: parent passed" \
		"${expected_bad_addr_marker}"
	echo "[agent-tests] full-suite duration budget skipped for targeted run"
	exit 0
fi

run_case agentfinal_ucore "agentfinal_ucore: parent passed"
run_case agentfs_ucore "agentfs_ucore: parent passed"
run_case agentscan_ucore "agentscan_ucore: parent passed"
run_case agentloop_ucore "agentloop_ucore: parent passed"
run_case agentsched_ucore "agentsched_ucore: parent passed"
run_case agentconflict_ucore "agentconflict_ucore: parent passed"
run_case agentllm_ucore "agentllm_ucore: parent passed"
run_case agentbench_ucore "agentbench_ucore: parent passed"
run_case labbench_ucore "labbench_ucore: parent passed"
run_case labdemo_ucore "labdemo_ucore: parent passed"
run_case agentsecurity_ucore "agentsecurity_ucore: parent passed"
run_case agenttoolabi_ucore "agenttoolabi_ucore: parent passed"
run_case agentscope_ucore "agentscope_ucore: parent passed"
run_case agenttrust_ucore "agenttrust_ucore: parent passed"
run_case agentvfs_ucore "agentvfs_ucore: parent passed"
run_case iobudget_ucore "iobudget_ucore: parent passed" \
	"iobudget_ucore: fault_exit_armed=1"
run_case usersafety_ucore "usersafety_ucore: parent passed"
run_case blocking_semantics_ucore "blocking_semantics_ucore: parent passed"

if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
	if [[ "${calibration_case_ordinal}" != "18" ]]; then
		echo "[agent-tests] calibration did not execute exactly 18 cases" >&2
		exit 1
	fi
	"${PYTHON_BIN}" -I -S scripts/agent_test_calibration.py derive-round \
		--root . \
		--plan "${AGENT_TEST_CALIBRATION_PLAN}" \
		--round "$((10#${AGENT_TEST_CALIBRATION_ROUND}))" \
		--attestation-dir "${AGENT_TEST_ATTESTATION_DIR}" \
		--guest-log "${AGENT_TEST_GUEST_LOG_FILE}" \
		--timing-file "${AGENT_TEST_TIMING_FILE}"
fi
check_suite_budget
echo "[agent-tests] all Agent-OS uCore checks passed"
