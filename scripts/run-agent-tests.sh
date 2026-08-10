#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
MAKE_TOOL="${MAKE_TOOL:-make}"
LOG="${LOG:-error}"
if [[ "${AGENT_TEST_CASE:-}" == "agentlive_ucore" ]]; then
	echo "[agent-tests] agentlive_ucore requires the bidirectional model relay; use 'make agent-live-demo'" >&2
	exit 2
fi
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
AGENTOS_BUILD_JOBS="${AGENTOS_BUILD_JOBS:-$("${PYTHON_BIN}" -I -S -B scripts/resource-jobs.py --kind build)}"
IDLE_NOTICE_SECONDS="${IDLE_NOTICE_SECONDS:-20}"
MARKER_GRACE_SECONDS="${MARKER_GRACE_SECONDS:-2s}"
REQUIRE_FULL_SUITE="${REQUIRE_FULL_SUITE:-0}"
# 避免宿主环境中的 make 变量污染实际测试构建；脚本需要的参数均显式传入。
readonly -a SANITIZED_MAKE_ENV=(
	MAKEFILES MAKEFLAGS MFLAGS MAKEOVERRIDES GNUMAKEFLAGS
	HOSTCC CC AS LD OBJCOPY OBJDUMP NM SIZE CFLAGS CPPFLAGS LDFLAGS ASFLAGS
	K U F BUILDDIR C_SRCS AS_SRCS C_OBJS AS_OBJS OBJS HEADER_DEP
	ARCH COMMON_CFLAGS LIB_C LIB_OBJS CRT_OBJ
	app_dir build_dir elf_dir obj_dir bin_dir generated_dir out_dir asm_dir arch_dir
	SRCS APPS SELECTED_APPS USER_BIN_DIR USER_ELF_DIR
	STORAGE_POLICY_CPPFLAGS FS_FUSE USER_BINS USER_ELFS EXEC_POLICY
)
for make_env in "${SANITIZED_MAKE_ENV[@]}"; do
	unset "${make_env}"
done
if [[ ! "${AGENTOS_BUILD_JOBS}" =~ ^([1-9]|1[0-9]|2[0-4])$ ]]; then
	echo "[agent-tests] AGENTOS_BUILD_JOBS must be between 1 and 24" >&2
	exit 1
fi
MAKE_JOB_ARGS=(-j "${AGENTOS_BUILD_JOBS}")
readonly -a MAKE_JOB_ARGS
echo "[agent-tests] build_jobs=${AGENTOS_BUILD_JOBS}"
CONTEXT_SYNC_TIMING_FILE="${TMPDIR:-/tmp}/agentos-context-sync-timings.$$"
CONTEXT_SYNC_USER_CFLAGS="-DAGENT_CONTEXT_SYNC_TEST_PROFILE -DWAIT_ATOMIC_TEST_PROFILE"
CONTEXT_RO_STORE_FAULT_MARKER="agentfinal_ucore: context_ro_store_fault_armed=1"
CONTEXT_PUBLIC_FAULT_MARKER="agentfinal_ucore: context_public_unmapped_fault_armed=1"
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
: >"${AGENT_TEST_TIMING_FILE}"

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
	ch8_cow_ucore)
		require_exact_case_marker "${log_file}" \
			"ch8_cow_ucore: passed"
		;;
	agenteval_ucore)
		"${PYTHON_BIN}" -I -S -B host_tools/evaluation_contract.py \
			validate-guest \
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
	agentcontract_ucore)
		require_exact_case_marker "${log_file}" \
			"agentcontract_ucore: dag24=1 lifecycle=1 schema=1 capability=1"
		require_exact_case_marker "${log_file}" \
			"agentcontract_ucore: dependency_sequence=1 provenance_file=1 provenance_cross_agent=1"
		require_exact_case_marker "${log_file}" \
			"agentcontract_ucore: planned_effect=1 unplanned_effect_denied=1 evidence=1"
		require_exact_case_marker "${log_file}" \
			"agentcontract_ucore: replay=1 retry=1 deadline=1 phase_atomic=1 phase_zero_leak=1"
		require_exact_case_marker "${log_file}" \
			"agentcontract_ucore: legacy_v2=1 enforce_bypass_denied=1"
		;;
	agent_eevdf_ucore)
		require_exact_case_marker "${log_file}" \
			"agent_eevdf_ucore: topology one_way=bootstrap four_way=bootstrap+3fresh amplification=bootstrap_peer+fresh4thread+2fresh_peers"
		require_exact_case_marker "${log_file}" \
			"agent_eevdf_ucore: wake_bucket_map=0:le1,1:le2,2:le8,3:gt8 p50_p99=histogram_approx probes=fresh_agents_only"
		require_exact_case_marker "${log_file}" \
			"agent_eevdf_ucore: thread_amplification scenario=44 amplified_threads=4 fresh_peers=2 bootstrap_peers=1 accounting=workflow"
		require_exact_case_marker "${log_file}" \
			"agent_eevdf_ucore: sixteen_arrivals=1 logical_samples=16 concurrency_cap=4 bootstrap_samples=4 fresh_samples=12 initial_fresh_attempts=15 initial_admitted=3 stable_no_space=12 waves=4 retry_policy=retry_only"
		;;
	agenttask_ucore)
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: perf_contract=steady_state_n16 quantiles=nearest_rank sample_semantics=pre_effect_context_service_start interval_origin=sequence_start_boundary service_metric=service_start_tick_intervals sequence_metric=agent_info_boundary_elapsed_ticks wall_clock=unavailable raw_cycles=not_claimed syscall_source=guest_call_sites"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: perf_observers=agent_info:2 boundary_overhead=start_return+end_entry_included context_query:16 post_sequence_excluded=1 kernel_path_syscall_counter=unavailable"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: perf_excluded batch=lifecycle_info:1 scalar_v3=lifecycle_info:1+contract:2 sq_cq=lifecycle_info:1+contract:2+channel_setup:1"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: sq_cq_copy_scope=sqe_private_copy+cqe_publish ack_clear_bytes=2048 user_ring_descriptor_bytes=4096 setup_abi_control_bytes=160 setup_copied_control_bytes=256"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: provider=synchronous_echo running_cancel_latency=unavailable terminal_pending_saturation=unavailable"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: perf_fp path=batch value=31"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: perf_fp path=scalar_v3 value=31"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: perf_fp path=sq_cq value=31"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: cq_full=1 backpressure=1 pending_preserved=1 recovery_enter_calls=2 resync_recovery=1"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: setup=1 single_issuer=1 resource_import_denied=1"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: submit=1 cq_ack=1 monotonic=1 resync=1"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: target_cancel_exactly_once=1 hard_deadline=1"
		require_exact_case_marker "${log_file}" \
			"agenttask_ucore: batch_fp=31 scalar_v3_fp=31 task_fp=31"
		;;
	agentfinal_ucore)
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_ro_mapping=1 low_agent_fault=-2 public_unmapped_fault=-2"
		require_exact_case_marker "${log_file}" \
			"agentfinal_ucore: context_commit_lane=1 sequence=1..3 hash=1"
		if [[ "${context_sync_profile}" == "1" ]]; then
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1"
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1"
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1"
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: event_wake_handoff waiters=1,4,8,15 wakeups=28 herd=0"
			require_exact_case_marker "${log_file}" \
				"agentfinal_ucore: event_baton_identity timeline_waiter=1 event_waiter=1 event_wakeups=1"
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
			"agentscan_ucore: explicit_admission ordinary_unindexed=1"
		require_exact_case_marker "${log_file}" \
			"agentscan_ucore: live_query enter=1 update=1 leave=1 indexed=1"
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
			"agentsecurity_ucore: legacy_mail_fail_closed=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: message_route_lifecycle=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: ipc_route_authorization=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: target_route_consent=1 unsolicited_response_denied=1"
		require_exact_case_marker "${log_file}" \
			"agentsecurity_ucore: route_slot_reclaimed=1"
		;;
	labdemo_ucore)
		require_exact_case_marker "${log_file}" \
			"labdemo_ucore: startup_barrier ready=3 released=3 chain_receipts=3"
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
			"agentscope_ucore: scope_storage_isolation=1 catalog_limit=112 workflow_created=113 peer_created=113 public_created=70 overflow_unindexed=1 explicit_no_space=1 reusable=1"
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

	# nfs/fs.img 依赖 user 目标。编译与镜像构建须置于同一次 make 调用，
	# 防止 profile 参数丢失。
	"${MAKE_TOOL}" "${MAKE_JOB_ARGS[@]}" -rR -f Makefile nfs/fs.img \
		TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}" \
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

	# A caller running one targeted Guest may retain its raw serial log for a
	# real comparison.  A shared destination is ambiguous for the full suite.
	if [[ -n "${AGENT_TEST_GUEST_LOG_FILE:-}" ]]; then
		if [[ -z "${AGENT_TEST_CASE:-}" ]]; then
			echo "[agent-tests] AGENT_TEST_GUEST_LOG_FILE requires AGENT_TEST_CASE" >&2
			return 2
		fi
		log_file="${AGENT_TEST_GUEST_LOG_FILE}"
	fi

	if [[ -n "${expected_bad_addr_marker}" ]]; then
		expected_fault_args+=(
			--expected-bad-addr-after "${expected_bad_addr_marker}"
		)
	fi
	if [[ "${init_proc}" == "agentfinal_ucore" ]]; then
		expected_fault_args+=(
			--expected-bad-addr-after "${CONTEXT_RO_STORE_FAULT_MARKER}"
			--expected-bad-addr-after "${CONTEXT_PUBLIC_FAULT_MARKER}"
		)
	fi
	if [[ "${context_sync_profile}" == "1" ]]; then
		build_profile_args+=(
			AGENT_CONTEXT_SYNC_TEST_PROFILE=1
			WAIT_ATOMIC_TEST_PROFILE=1
		)
		case_timing_file="${CONTEXT_SYNC_TIMING_FILE}"
	fi

	echo "[agent-tests] running ${init_proc}"
	rm -f nfs/fs-copy.img os/initproc.S build/os/initproc.o
	"${MAKE_TOOL}" "${MAKE_JOB_ARGS[@]}" -rR -f Makefile build \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}" \
		"${build_profile_args[@]}"
	cp nfs/fs.img nfs/fs-copy.img
	"${PYTHON_BIN}" -I -S -B scripts/agent_test_runner.py \
		--init-proc "${init_proc}" \
		--marker "${marker}" \
		--marker-mode exact-line \
		--expected-bad-addr-marker-mode exact-line \
		--log-file "${log_file}" \
		--case-timeout "${CASE_TIMEOUT}" \
		--idle-notice-seconds "${IDLE_NOTICE_SECONDS}" \
		--marker-grace-seconds "${MARKER_GRACE_SECONDS}" \
		--qemu "${QEMU}" \
		--timing-file "${case_timing_file}" \
		"${expected_fault_args[@]}"
	if [[ "${context_sync_profile}" == "1" ]]; then
		"${PYTHON_BIN}" -I -S -B scripts/validate-kernel-test-log.py \
			--log-file "${log_file}" \
			--tag "wait-atomic" \
			--profile wait-atomic
	fi
	if [[ "${init_proc}" == "agent_eevdf_ucore" ||
	      "${init_proc}" == "agenttask_ucore" ]]; then
		"${PYTHON_BIN}" -I -S -B scripts/validate-kernel-test-log.py \
			--log-file "${log_file}" \
			--tag "${init_proc}" \
			--profile agent-case \
			--case "${init_proc}"
	fi
	check_case_contract "${init_proc}" "${log_file}" \
		"${context_sync_profile}"
	echo "[agent-tests] ${init_proc} passed"
}

"${MAKE_TOOL}" -rR -C user -f Makefile clean
"${MAKE_TOOL}" -rR -f Makefile clean

if [[ -z "${AGENT_TEST_CASE:-}" ||
	  "${AGENT_TEST_CASE}" == "agentfinal_ucore" ]]; then
	: >"${CONTEXT_SYNC_TIMING_FILE}"
	build_user_image "${CONTEXT_SYNC_USER_CFLAGS}"
	run_case agentfinal_ucore "agentfinal_ucore: parent passed" "" 1
	"${MAKE_TOOL}" -rR -C user -f Makefile clean
	"${MAKE_TOOL}" -rR -f Makefile clean
fi

build_user_image
"${MAKE_TOOL}" "${MAKE_JOB_ARGS[@]}" -rR -f Makefile build \
	TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore \
	CHAPTER="${CHAPTER}"

if [[ -n "${AGENT_TEST_CASE:-}" ]]; then
	expected_bad_addr_marker=""
	case_marker="${AGENT_TEST_CASE}: parent passed"
	if [[ "${AGENT_TEST_CASE}" == "iobudget_ucore" ]]; then
		expected_bad_addr_marker="iobudget_ucore: fault_exit_armed=1"
	elif [[ "${AGENT_TEST_CASE}" == "ch8_cow_ucore" ]]; then
		case_marker="ch8_cow_ucore: passed"
	fi
	run_case "${AGENT_TEST_CASE}" "${case_marker}" \
		"${expected_bad_addr_marker}"
	echo "[agent-tests] targeted case passed"
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
run_case agentcontract_ucore "agentcontract_ucore: parent passed"
run_case agent_eevdf_ucore "agent_eevdf_ucore: parent passed"
run_case agenttask_ucore "agenttask_ucore: parent passed"
run_case ch8_cow_ucore "ch8_cow_ucore: passed"
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

echo "[agent-tests] all Agent-OS uCore checks passed"
