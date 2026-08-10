#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_FILE="${TMPDIR:-/tmp}/agent-module-boundaries.$$"

cleanup() {
	rm -f "${TMP_FILE}"
}
trap cleanup EXIT

fail() {
	echo "[agent-module-check] failed: $1" >&2
	if [ -s "${TMP_FILE}" ]; then
		cat "${TMP_FILE}" >&2
	fi
	exit 1
}

active_modules="
agent
agent_background
agent_context
agent_context_path
agent_core
agent_evidence_ring
agent_execution_contract
agent_file_state
agent_identity
agent_identity_lease
agent_ipc
agent_lifecycle
agent_live_query_compat
agent_live_query_events
agent_metadata
agent_metadata_actions
agent_metadata_catalog
agent_metadata_directory
agent_metadata_objects
agent_metadata_query
agent_observe
agent_observe_audit_query
agent_observe_ledger
agent_observe_timeline
agent_provenance
agent_resource
agent_sha256
agent_task_bridge
agent_task_channel
agent_tool_protocol
agent_workflow_fence
resource_controller
workflow_credit_domain
workflow_lifecycle
workflow_scheduler
"

retired_modules="
agent_durable_section
agent_metadata_journal
agent_metadata_probe
agent_metadata_recovery
agent_metadata_recovery_test
agent_metadata_scan
agent_metadata_store
agent_metadata_store_format
agent_metadata_store_io
agent_metadata_test
agent_observe_capacity
agent_observe_recovery
agent_observe_store
"

test_only_modules="agent_observe_test"

contains_word() {
	printf '%s\n' "$1" | grep -q -F -x "$2"
}

for module in ${active_modules}; do
	path="${ROOT_DIR}/os/${module}.c"
	[ -f "${path}" ] || fail "missing active owner: os/${module}.c"
	if grep -n -E '^[[:space:]]*extern[[:space:]]+' "${path}" |
		grep -v -E 'extern struct proc pool\[NPROC\];' >"${TMP_FILE}"; then
		fail "os/${module}.c imports or exports writable owner state"
	fi
	: >"${TMP_FILE}"
done

for module in ${retired_modules}; do
	[ -f "${ROOT_DIR}/os/${module}.c" ] ||
		fail "missing reference-only retired source: os/${module}.c"
done

for path in "${ROOT_DIR}"/os/agent*.c; do
	module="$(basename "${path}" .c)"
	if contains_word "${active_modules}" "${module}" ||
	   contains_word "${retired_modules}" "${module}" ||
	   contains_word "${test_only_modules}" "${module}"; then
		continue
	fi
	fail "unregistered AgentOS implementation: os/${module}.c"
done

makefile="${ROOT_DIR}/Makefile"
grep -q -F 'C_SRCS := $(filter-out $(RETIRED_METADATA_C_SRCS),$(C_SRCS))' "${makefile}" || fail "metadata retirement is not applied to production C_SRCS"
grep -q -F 'C_SRCS := $(filter-out $(RETIRED_OBSERVE_C_SRCS),$(C_SRCS))' "${makefile}" || fail "observe retirement is not applied to production C_SRCS"
for module in ${retired_modules}; do
	grep -q -F "\$K/${module}.c" "${makefile}" ||
		fail "retired source is not declared by Makefile: os/${module}.c"
done
for module in ${active_modules}; do
	if sed -n '/^RETIRED_METADATA_C_SRCS :=/,/^C_SRCS :=/p; /^RETIRED_OBSERVE_C_SRCS :=/,/^C_SRCS :=/p' "${makefile}" | grep -q -F "\$K/${module}.c"; then
		fail "active owner was placed in a retired source set: os/${module}.c"
	fi
done

size_optimized_modules="$(sed -n 's/^AGENT_SIZE_OPTIMIZED_MODULES[[:space:]]*:=[[:space:]]*//p' "${makefile}")"
[ "${size_optimized_modules}" = "agent_context_path agent_file_state agent_ipc agent_metadata agent_metadata_actions agent_metadata_catalog agent_metadata_directory agent_metadata_objects agent_metadata_query agent_observe_ledger" ] ||
	fail "Agent size-optimization allowlist drifted"
grep -q -F '$(AGENT_SIZE_OPTIMIZED_OBJS): private CFLAGS += -Os' "${makefile}" || fail "Agent size optimization is not target-local"
[ "$(grep -c -E -- '(^|[[:space:]])-Os([[:space:]]|$)' "${makefile}")" -eq 1 ] ||
	fail "size optimization escaped the reviewed cold owners"

active_paths=""
for module in ${active_modules}; do
	active_paths="${active_paths} ${ROOT_DIR}/os/${module}.c"
done
if grep -n -E '#include[[:space:]]+"agent_(durable_section|metadata_(journal|probe|recovery|scan|store)|observe_(capacity|recovery|store))' ${active_paths} >"${TMP_FILE}"; then
	fail "active owner includes a retired persistence header"
fi
: >"${TMP_FILE}"
if grep -n -E '\b(agent_obsstore|agent_metadata_store_|agent_durable_section_|agent_observe_capacity_|agent_metadata_scan_)[A-Za-z0-9_]*[[:space:]]*\(' ${active_paths} >"${TMP_FILE}"; then
	fail "active owner calls a retired persistence implementation"
fi
: >"${TMP_FILE}"

background_source="${ROOT_DIR}/os/agent_background.c"
grep -q -F 'static int agent_background_pending;' "${background_source}" ||
	fail "neutral background latch is missing"
grep -q -F '__atomic_store_n(&agent_background_pending, 1, __ATOMIC_RELEASE);' "${background_source}" || fail "background publication lost release ordering"
grep -q -F '__atomic_exchange_n(&agent_background_pending, 0,' "${background_source}" || fail "background consumption lost atomic exchange"
if grep -n -E 'agent_core_|agent_metadata_|agent_observe_|agent_ipc_' "${background_source}" >"${TMP_FILE}"; then
	fail "neutral background latch acquired an owner dependency"
fi
: >"${TMP_FILE}"

credit_source="${ROOT_DIR}/os/resource_controller.c"
domain_source="${ROOT_DIR}/os/workflow_credit_domain.c"
for token in 'resource_credit_acquire_vector_locked' 'resource_credit_reclaim_pressure_locked' 'resource_account_trim_locked'; do
	grep -q -F "${token}" "${credit_source}" ||
		fail "Credit Domain lost exact accounting primitive: ${token}"
done
for token in 'workflow_credit_domain_fence' 'workflow_credit_domain_switch'; do
	grep -q -F "${token}" "${domain_source}" ||
		fail "Credit Domain lost workflow operation: ${token}"
done
grep -q -F 'workflow_credit_domain_switch(' "${ROOT_DIR}/os/proc.c" ||
	fail "scheduler dispatch no longer flushes cross-domain credit"

ring_source="${ROOT_DIR}/os/agent_evidence_ring.c"
for token in 'agent_evidence_reserve' 'AGENT_EVIDENCE_SLOT_BUSY' 'agent_evidence_seal(' 'sealed_ticket_highwater' 'kalloc_account_page('; do
	grep -q -F "${token}" "${ring_source}" ||
		fail "Evidence Ring lost required mechanism: ${token}"
done
if grep -n -E 'agent_(obsstore|durable_section|observe_capacity)_' "${ring_source}" >"${TMP_FILE}"; then
	fail "Evidence Ring regained a disk/capacity dependency"
fi
: >"${TMP_FILE}"

fence_source="${ROOT_DIR}/os/agent_workflow_fence.c"
for token in 'agent_metadata_quiescence_fence_snapshot_current(' 'fs_deferred_reclaim_drain_current(' 'fs_epoch_commit(' 'workflow_credit_domain_fence(' 'agent_evidence_seal('; do
	grep -q -F "${token}" "${fence_source}" ||
		fail "workflow fence lost ordered subsystem cut: ${token}"
done

syscall_source="${ROOT_DIR}/os/syscall.c"
recovery_case="$(sed -n '/case SYS_agent_observe_recovery:/,/break;/p' "${syscall_source}")"
printf '%s\n' "${recovery_case}" | grep -q -F 'AGENT_STATUS_BAD_PARAM' ||
	fail "retired observe recovery syscall is not fail-closed"
if printf '%s\n' "${recovery_case}" | grep -q -F 'sys_agent_observe_recovery('; then
	fail "retired observe recovery implementation remains reachable"
fi

for checker in check-agent-live-query-fs.py check-workflow-fence.py; do
	[ -f "${ROOT_DIR}/scripts/${checker}" ] ||
		fail "missing new architecture checker: scripts/${checker}"
done

echo "Agent module boundary check passed"
