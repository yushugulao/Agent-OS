#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/evidence-wiring.sh"
cd "${SCRIPT_DIR}/.."

TOOLPREFIX="${TOOLPREFIX:-riscv64-linux-gnu-}"
MAKE_TOOL="${MAKE_TOOL:-make}"
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
CONTEXT_SYNC_TIMING_FILE="${TMPDIR:-/tmp}/agentos-context-sync-timings.$$"
CONTEXT_SYNC_USER_CFLAGS="-DAGENT_CONTEXT_SYNC_TEST_PROFILE -DWAIT_ATOMIC_TEST_PROFILE"
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
if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
	if [[ "${REQUIRE_FULL_SUITE}" != "1" ]]; then
		echo "[agent-tests] calibration requires REQUIRE_FULL_SUITE=1" >&2
		exit 1
	fi
	if [[ "${timing_file_owned}" == "1" ]]; then
		echo "[agent-tests] calibration requires a persistent AGENT_TEST_TIMING_FILE" >&2
		exit 1
	fi
fi
: >"${AGENT_TEST_TIMING_FILE}"
"${PYTHON_BIN}" scripts/test-sync-owner-wiring.py
"${PYTHON_BIN}" scripts/test-wait-atomic-wiring.py
"${PYTHON_BIN}" scripts/check-wait-queue-contract.py
if [[ -z "${AGENT_TEST_CASE:-}" && "${AGENT_TEST_CALIBRATE}" == "0" ]]; then
	"${PYTHON_BIN}" scripts/check-kernel-budgets.py \
		--check agent-test-policy \
		--config ci/kernel-budgets.json
fi

check_suite_budget() {
	local calibration_args=()
	if [[ "${AGENT_TEST_CALIBRATE}" == "1" ]]; then
		calibration_args+=(--agent-test-calibration)
	fi
	"${PYTHON_BIN}" scripts/check-kernel-budgets.py \
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
		require_exact_case_marker "${log_file}" \
			"agenteval_ucore: worker passed"
		"${PYTHON_BIN}" - "${log_file}" <<'PY'
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "host_tools"))
from evaluation_contract import (  # noqa: E402
    _expected_result,
    _expected_workload,
    _operations_for,
    _parse_diagnostic,
    _parse_marker,
    load_suite,
    validate_functional_log,
)

log_path = sys.argv[1]
suite = load_suite(Path("ci/evaluation-suite.json"))
experiments = {item["id"]: item for item in suite["experiments"]}
variants = {
    item["id"]: (
        (item["baseline"]["id"], item["baseline"]["cache"]),
        (item["treatment"]["id"], item["treatment"]["cache"]),
    )
    for item in suite["experiments"]
}
pairs = range(1, 8)
samples = {}
diagnostics = {}
challenge = None
physical_events = []

with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
    for line_number, raw_line in enumerate(handle, start=1):
        line = raw_line.rstrip("\r\n")
        if line.startswith("agenteval_ucore: challenge="):
            match = re.fullmatch(r"agenteval_ucore: challenge=([0-9a-f]{16})", line)
            if match is None or challenge is not None or int(match.group(1), 16) == 0:
                raise SystemExit(f"invalid or duplicate agenteval challenge: {line}")
            challenge = int(match.group(1), 16)
        elif line.startswith("agenteval_ucore: sample "):
            record = _parse_marker(line, line_number)
            experiment = experiments.get(record["experiment"])
            if experiment is None or record["load"] not in experiment["loads"]:
                raise SystemExit(f"unconfigured agenteval sample line: {line}")
            role = next(
                (
                    role for role in ("baseline", "treatment")
                    if experiment[role]["id"] == record["variant"]
                ),
                None,
            )
            if role is None:
                raise SystemExit(f"unconfigured agenteval variant line: {line}")
            record["role"] = role
            record["line"] = line_number
            samples.setdefault(
                (record["experiment"], record["load"], record["pair"]), []
            ).append(record)
            physical_events.append(
                (line_number, "sample", record["experiment"], record["load"], record["pair"], role)
            )
        elif line.startswith("agenteval_ucore: diagnostic "):
            diagnostic = _parse_diagnostic(line, line_number)
            load = diagnostic["load"]
            if load in diagnostics:
                raise SystemExit(f"duplicate agenteval diagnostic load={load}")
            diagnostics[load] = diagnostic
            physical_events.append(
                (line_number, "diagnostic", "file_query", load, 0, "readiness")
            )

expected_keys = {
    (experiment["id"], load, pair)
    for experiment in suite["experiments"]
    for load in experiment["loads"]
    for pair in pairs
}
if set(samples) != expected_keys:
    missing = sorted(expected_keys - set(samples))
    extra = sorted(set(samples) - expected_keys)
    raise SystemExit(f"agenteval sample matrix mismatch missing={missing} extra={extra}")
if challenge is None:
    raise SystemExit("agenteval log lacks a nonzero workload challenge")
expected_challenge_text = os.environ.get("AGENT_EVAL_CHALLENGE_HEX", "")
if not re.fullmatch(r"[0-9a-f]{16}", expected_challenge_text):
    raise SystemExit("agenteval validator lacks the planned challenge")
if challenge != int(expected_challenge_text, 16):
    raise SystemExit("agenteval Guest challenge differs from the build plan")
challenge_text = f"{challenge:016x}"

expected_physical = []
for experiment in suite["experiments"]:
    for load in experiment["loads"]:
        if experiment["id"] == "file_query":
            expected_physical.append(
                ("diagnostic", "file_query", load, 0, "readiness")
            )
        for pair in pairs:
            order = "AB" if (pair & 1) == (challenge & 1) else "BA"
            roles = (
                ("baseline", "treatment")
                if order == "AB"
                else ("treatment", "baseline")
            )
            expected_physical.extend(
                ("sample", experiment["id"], load, pair, role)
                for role in roles
            )
if [event[1:] for event in sorted(physical_events)] != expected_physical:
    raise SystemExit("agenteval physical marker order differs from preregistration")

for key in sorted(expected_keys):
    experiment, load, pair = key
    experiment_contract = experiments[experiment]
    records = samples[key]
    if len(records) != 2:
        raise SystemExit(f"agenteval pair must contain two samples: {key}")
    expected_order = "AB" if (pair & 1) == (challenge & 1) else "BA"
    expected_variants = variants[experiment]
    if expected_order == "BA":
        expected_variants = tuple(reversed(expected_variants))
    observed = tuple((record["variant"], record["cache"]) for record in records)
    if observed != expected_variants:
        raise SystemExit(f"agenteval execution order mismatch {key}: {observed}")
    if any(record["order"] != expected_order for record in records):
        raise SystemExit(f"agenteval order label mismatch: {key}")
    operations = _operations_for(experiment_contract, load)
    if any(record["operations"] != operations for record in records):
        raise SystemExit(f"agenteval operation count mismatch: {key}")
    if any(record["result_items"] != operations for record in records):
        raise SystemExit(f"agenteval result count mismatch: {key}")
    if experiment == "file_query":
        by_variant = {record["variant"]: record for record in records}
        if any(record["dataset_size"] != load for record in records):
            raise SystemExit(f"agenteval file dataset size mismatch: {key}")
        if any(
            record["work_units"] != record["records_examined"]
            for record in records
        ):
            raise SystemExit(f"agenteval file work receipt mismatch: {key}")
        if by_variant["scan"]["work_units"] < load * operations:
            raise SystemExit(f"agenteval file work is not measured traversal: {key}")
        if by_variant["index"]["work_units"] != operations:
            raise SystemExit(f"agenteval index work was not measured: {key}")
    elif (
        any(record["dataset_size"] != 0 or record["records_examined"] != 0 for record in records)
        or records[0]["work_units"] != records[1]["work_units"]
        or records[0]["work_units"] != operations
    ):
        raise SystemExit(f"agenteval completed work mismatch: {key}")
    if records[0]["workload_fingerprint"] != records[1]["workload_fingerprint"]:
        raise SystemExit(f"agenteval workload mismatch: {key}")
    expected_workload = _expected_workload(
        experiment_contract, load, pair, challenge_text
    )
    if records[0]["workload_fingerprint"] != expected_workload:
        raise SystemExit(f"agenteval workload fingerprint is not challenge-bound: {key}")
    expected_result = _expected_result(
        experiment_contract, load, pair, challenge_text
    )
    if any(record["result_fingerprint"] != expected_result for record in records):
        raise SystemExit(f"agenteval result differs from Host semantic oracle: {key}")
    if records[0]["result_fingerprint"] != records[1]["result_fingerprint"]:
        raise SystemExit(f"agenteval result mismatch: {key}")
    if int(records[0]["workload_fingerprint"], 16) == 0 or int(records[0]["result_fingerprint"], 16) == 0:
        raise SystemExit(f"agenteval zero fingerprint: {key}")
file_loads = set(experiments["file_query"]["loads"])
if set(diagnostics) != file_loads:
    raise SystemExit("agenteval requires one index-readiness diagnostic per load")
for load, diagnostic in diagnostics.items():
    if diagnostic["work_units"] <= 0:
        raise SystemExit(f"agenteval diagnostic lacks measured work load={load}")
    if diagnostic["cache"] == "cold-rebuild" and diagnostic["index_rebuild_records"] == 0:
        raise SystemExit(f"agenteval cold rebuild lacks measured work load={load}")
    if diagnostic["cache"] == "ready" and diagnostic["index_rebuild_records"] != 0:
        raise SystemExit(f"agenteval readiness label conflicts with rebuild load={load}")
    first_sample_line = min(
        record["line"]
        for pair in pairs
        for record in samples[("file_query", load, pair)]
    )
    if diagnostic["line"] >= first_sample_line:
        raise SystemExit(f"agenteval readiness diagnostic is not before samples load={load}")
validate_functional_log(
    Path(log_path).read_text(encoding="utf-8").splitlines(),
    challenge_text,
    [record for records in samples.values() for record in records],
)
PY
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
	"${MAKE_TOOL}" nfs/fs.img TOOLPREFIX="${TOOLPREFIX}" CHAPTER="${CHAPTER}" \
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

	echo "[agent-tests] running ${init_proc}"
	rm -f nfs/fs-copy.img os/initproc.S build/os/initproc.o
	"${MAKE_TOOL}" build \
		TOOLPREFIX="${TOOLPREFIX}" \
		LOG="${LOG}" \
		INIT_PROC="${init_proc}" \
		CHAPTER="${CHAPTER}" \
		"${build_profile_args[@]}"
	cp nfs/fs.img nfs/fs-copy.img
	if "${PYTHON_BIN}" scripts/agent_test_runner.py \
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
	if [[ "${context_sync_profile}" == "1" ]]; then
		"${PYTHON_BIN}" scripts/validate-kernel-test-log.py \
			--log-file "${log_file}" \
			--tag "wait-atomic" \
			--profile wait-atomic
	fi
	check_case_contract "${init_proc}" "${log_file}" \
		"${context_sync_profile}"
	echo "[agent-tests] ${init_proc} passed"
}

"${MAKE_TOOL}" -C user clean
"${MAKE_TOOL}" clean

if [[ -z "${AGENT_TEST_CASE:-}" ||
	  "${AGENT_TEST_CASE}" == "agentfinal_ucore" ]]; then
	: >"${CONTEXT_SYNC_TIMING_FILE}"
	build_user_image "${CONTEXT_SYNC_USER_CFLAGS}"
	run_case agentfinal_ucore "agentfinal_ucore: parent passed" "" 1
	"${MAKE_TOOL}" -C user clean
	"${MAKE_TOOL}" clean
fi

build_user_image
"${MAKE_TOOL}" build TOOLPREFIX="${TOOLPREFIX}" LOG=warn INIT_PROC=agentfinal_ucore

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

check_suite_budget
echo "[agent-tests] all Agent-OS uCore checks passed"
