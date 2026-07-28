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

modules="
agent
agent_background
agent_core
agent_context
agent_context_path
agent_durable_section
agent_file_state
agent_identity
agent_identity_lease
agent_ipc
agent_lifecycle
agent_metadata
agent_metadata_actions
agent_metadata_catalog
agent_metadata_directory
agent_metadata_objects
agent_metadata_prefetch
agent_metadata_probe
agent_metadata_query
agent_metadata_recovery
agent_metadata_scan
agent_metadata_store
agent_metadata_store_format
agent_metadata_store_io
agent_observe
agent_observe_audit_query
agent_observe_capacity
agent_observe_ledger
agent_observe_recovery
agent_observe_store
agent_observe_timeline
agent_tool_protocol
resource_controller
workflow_lifecycle
"

for module in ${modules}; do
	path="${ROOT_DIR}/os/${module}.c"
	[ -f "${path}" ] ||
		fail "missing subsystem implementation: os/${module}.c"

	# Private modules may reference the kernel proc pool, but never publish
	# writable AgentOS data for another module to mutate.
	if grep -n -E '^[[:space:]]*extern[[:space:]]+' "${path}" |
		grep -v -E 'extern struct proc pool\[NPROC\];' >"${TMP_FILE}"; then
		fail "os/${module}.c exports or imports writable data"
	fi
	: >"${TMP_FILE}"
done

# The ownership splits have exactly twenty reviewed size-optimized translation
# units. Keep the build rule target-local and reject silent policy expansion.
makefile="${ROOT_DIR}/Makefile"
size_optimized_modules="$(sed -n \
	's/^AGENT_SIZE_OPTIMIZED_MODULES[[:space:]]*:=[[:space:]]*//p' \
	"${makefile}")"
[ "${size_optimized_modules}" = \
	"agent_context_path agent_file_state agent_ipc agent_metadata agent_metadata_actions agent_metadata_catalog agent_metadata_directory agent_metadata_objects agent_metadata_prefetch agent_metadata_probe agent_metadata_query agent_metadata_recovery agent_metadata_scan agent_metadata_store agent_metadata_store_format agent_metadata_store_io agent_observe_capacity agent_observe_ledger agent_observe_recovery agent_observe_store" ] ||
	fail "Agent size-optimization allowlist drifted"
grep -q -F '$(AGENT_SIZE_OPTIMIZED_OBJS): private CFLAGS += -Os' \
	"${makefile}" || fail "Agent size optimization is not target-local"
if [ "$(grep -c -E -- '(^|[[:space:]])-Os([[:space:]]|$)' \
	"${makefile}")" -ne 1 ]; then
	fail "size optimization escaped the reviewed Agent owners"
fi

# Scheduler-context maintenance must execute the bounded core coordinator.
# Turning it back into a request silently strands a foreground submitter
# behind the primary-to-mirror phase once that submitter has gone to sleep.
facade_source="${ROOT_DIR}/os/agent.c"
core_source="${ROOT_DIR}/os/agent_core.c"
background_maintain="$(sed -n \
	'/^agent_background_maintain(void)/,/^}/p' "${core_source}")"
[ -n "${background_maintain}" ] ||
	fail "missing Agent background maintenance coordinator"
printf '%s\n' "${background_maintain}" | \
	grep -q -F 'agent_metadata_background_maintain();' ||
	fail "background maintenance lost its metadata coordinator"
if grep -n -E '^agent_background_(maintain|checkpoint)\(void\)' \
	"${facade_source}" >"${TMP_FILE}"; then
	fail "background coordinator regressed into a facade trampoline"
fi
: >"${TMP_FILE}"

# Producers publish an edge into a neutral latch.  They must never call back
# through the facade/core, which would join the control-plane owner graph.
background_source="${ROOT_DIR}/os/agent_background.c"
grep -q -F 'static int agent_background_pending;' "${background_source}" ||
	fail "neutral Agent background latch is missing"
grep -q -F '__atomic_store_n(&agent_background_pending, 1, __ATOMIC_RELEASE);' \
	"${background_source}" ||
	fail "Agent background latch cannot publish work"
grep -q -F '__atomic_exchange_n(&agent_background_pending, 0,' \
	"${background_source}" ||
	fail "Agent background latch cannot consume work"
grep -q -F '__ATOMIC_ACQ_REL);' "${background_source}" ||
	fail "Agent background latch lost acquire/release ordering"
if grep -n -E 'agent_background_pending[[:space:]]*=' \
	"${background_source}" >"${TMP_FILE}"; then
	fail "Agent background latch regressed to a non-atomic update"
fi
: >"${TMP_FILE}"
if grep -n -E 'agent_core_|agent_metadata_|agent_observe_|agent_ipc_' \
	"${background_source}" >"${TMP_FILE}"; then
	fail "neutral Agent background latch acquired an owner dependency"
fi
: >"${TMP_FILE}"
if grep -n -F 'agent_background_pending' "${ROOT_DIR}"/os/*.c | \
	grep -v -F '/agent_background.c:' >"${TMP_FILE}"; then
	fail "Agent background latch ownership escaped its neutral module"
fi
: >"${TMP_FILE}"
background_take_calls="$(grep -R -n -E \
	'agent_background_take[[:space:]]*\([[:space:]]*\)' \
	"${ROOT_DIR}/os" --include='*.c' || true)"
[ "$(printf '%s\n' "${background_take_calls}" | \
	grep -c -F '/agent_core.c:' || true)" -eq 1 ] ||
	fail "Agent background latch must have one core consumer"
if printf '%s\n' "${background_take_calls}" | \
	grep -v -F '/agent_core.c:' >"${TMP_FILE}"; then
	fail "Agent background latch consumer escaped the core coordinator"
fi
: >"${TMP_FILE}"

for path in "${ROOT_DIR}"/os/agent*.c; do
	module="$(basename "${path}" .c)"
	case "${module}" in
	agent | agent_background | agent_core | agent_context | agent_context_path | agent_durable_section | agent_file_state | agent_identity | agent_identity_lease | agent_ipc | \
		agent_lifecycle | agent_metadata | agent_metadata_actions | agent_metadata_objects | \
	agent_metadata_catalog | agent_metadata_directory | agent_metadata_probe | agent_metadata_query | agent_metadata_recovery | agent_metadata_recovery_test | agent_metadata_scan | \
	agent_metadata_prefetch | agent_metadata_store | agent_metadata_store_format | agent_metadata_store_io | agent_metadata_test | agent_observe | agent_observe_audit_query | agent_observe_capacity | \
	agent_observe_ledger | agent_observe_recovery | agent_observe_store | agent_observe_test | agent_observe_timeline | \
	agent_tool_protocol)
		;;
	*)
		fail "unregistered AgentOS implementation: os/${module}.c"
		;;
	esac
done

# Lifecycle identity retirement returns an immutable endpoint key to the core
# coordinator.  IPC remains the sole route-table owner and is called while the
# coordinator's interrupt boundary is still held.
lifecycle_source="${ROOT_DIR}/os/agent_lifecycle.c"
if grep -n -E '\bagent_ipc_[A-Za-z0-9_]*[[:space:]]*\(' \
	"${lifecycle_source}" >"${TMP_FILE}"; then
	fail "lifecycle owner acquired a reverse IPC dependency"
fi
: >"${TMP_FILE}"
controller_departure="$(sed -n \
	'/^agent_scope_controller_departing(struct proc \*p)/,/^}/p' \
	"${ROOT_DIR}/os/agent_core.c")"
[ -n "${controller_departure}" ] ||
	fail "missing core controller-departure coordinator"
departure_order="$(printf '%s\n' "${controller_departure}" | sed -n \
	'/intr_save()/,/intr_restore(enabled)/p')"
for operation in 'agent_lifecycle_controller_departing_locked(p)' \
	'agent_ipc_remove_source(control_id)'; do
	printf '%s\n' "${departure_order}" | grep -q -F "${operation}" ||
		fail "controller departure lost ordered owner operation: ${operation}"
done
if grep -R -n -F 'agent_lifecycle_controller_departing_locked(p)' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -F '/agent_core.c:' >"${TMP_FILE}"; then
	fail "locked lifecycle departure escaped the core coordinator"
fi
: >"${TMP_FILE}"

# Observation sections request persistence only through the durable-section
# provider; direct calls would reverse the metadata-store ownership edge.
observe_store_source="${ROOT_DIR}/os/agent_observe_store.c"
observe_recovery_source="${ROOT_DIR}/os/agent_observe_recovery.c"
if grep -n -E '\bagent_metadata_store_[A-Za-z0-9_]*[[:space:]]*\(' \
	"${observe_store_source}" >"${TMP_FILE}"; then
	fail "observation store acquired a reverse metadata-store dependency"
fi
: >"${TMP_FILE}"
grep -q -F 'agent_durable_section_persist_scope(' \
	"${observe_store_source}" ||
	fail "observation store lost durable persistence provider"
if grep -n -E '\bsys_agent_observe_recovery[[:space:]]*\(' \
	"${observe_store_source}" >"${TMP_FILE}"; then
	fail "Recovery ABI endpoint regressed into the persistence store"
fi
: >"${TMP_FILE}"
grep -q -E '^sys_agent_observe_recovery[[:space:]]*\(' \
	"${observe_recovery_source}" ||
	fail "Recovery module lost its syscall endpoint"
for operation in 'agent_obsstore_snapshot_begin(' \
	'agent_obsstore_snapshot_scope_capacity(' \
	'agent_obsstore_snapshot_record_capacity(' \
	'agent_obsstore_snapshot_scope(' 'agent_obsstore_snapshot_record(' \
	'agent_obsstore_snapshot_confirm(' 'agent_obsstore_reap_query('; do
	grep -q -F "${operation}" "${observe_recovery_source}" ||
		fail "Recovery endpoint bypassed store view: ${operation}"
done
if grep -n -E '\bagent_durable_section_[A-Za-z0-9_]*[[:space:]]*\(' \
	"${observe_recovery_source}" >"${TMP_FILE}"; then
	fail "Recovery ABI endpoint acquired durable image authority"
fi
: >"${TMP_FILE}"
grep -q -F 'agent_durable_section_set_store_provider(' \
	"${ROOT_DIR}/os/agent_metadata_store.c" ||
	fail "metadata store did not register the durable persistence provider"
provider_owners="$(grep -R -l -F 'agent_durable_section_set_store_provider(' \
	"${ROOT_DIR}/os" --include='*.c' | sort)"
[ "${provider_owners}" = "${ROOT_DIR}/os/agent_durable_section.c
${ROOT_DIR}/os/agent_metadata_store.c" ] ||
	fail "durable persistence provider ownership escaped its two endpoints"
for member in '.mark_dirty = agent_meta_durable_dirty' \
	'.replicated = agent_meta_durable_replicated' \
	'.persist_scope = agent_meta_durable_persist_scope'; do
	grep -q -F "${member}" "${ROOT_DIR}/os/agent_metadata_store.c" ||
		fail "durable persistence provider lost member: ${member}"
done

# Metadata crash attestation is compiled only in its explicit profile. Keep the
# production graph free of the object and limit the bridge to the store owner.
test_support="${ROOT_DIR}/os/agent_metadata_test.c"
test_header="${ROOT_DIR}/os/metadata_crash_test.h"
grep -q -F 'C_SRCS := $(filter-out $K/agent_metadata_test.c,$(C_SRCS))' \
	"${ROOT_DIR}/Makefile" ||
	fail "metadata test owner is not excluded from production objects"
grep -q -F '#ifdef AGENT_METADATA_CRASH_PHASE' "${test_support}" ||
	fail "metadata test owner lost its profile guard"
for symbol in agent_metadata_test_init agent_metadata_test_bind \
	agent_metadata_test_checkpoint agent_metadata_test_eio_start \
	agent_metadata_test_eio_cancel agent_metadata_test_eio_pre_io \
	agent_metadata_test_eio_commit sys_agent_metadata_test; do
	grep -q -F "${symbol}" "${test_support}" ||
		fail "metadata test owner lost narrow hook: ${symbol}"
done
if grep -R -l -F '#include "metadata_crash_test.h"' "${ROOT_DIR}/os"/*.c |
	grep -v -E '/agent_metadata_(store|test)\.c$' >"${TMP_FILE}"; then
	fail "production owner gained a metadata crash-test dependency"
fi
: >"${TMP_FILE}"
grep -q -F '#else' "${test_header}" &&
	grep -q -F 'static inline void' "${test_header}" ||
	fail "metadata test header lacks production inline no-op hooks"

recovery_test_support="${ROOT_DIR}/os/agent_metadata_recovery_test.c"
recovery_test_header="${ROOT_DIR}/os/agent_metadata_recovery_test.h"
grep -q -F 'C_SRCS := $(filter-out $K/agent_metadata_recovery_test.c,$(C_SRCS))' \
	"${ROOT_DIR}/Makefile" ||
	fail "metadata recovery test owner is not excluded from production objects"
for symbol in agent_metadata_recovery_test_init \
	agent_metadata_recovery_test_fault agent_metadata_recovery_test_retry \
	agent_metadata_recovery_test_admission; do
	grep -q -F "${symbol}" "${recovery_test_support}" ||
		fail "metadata recovery test owner lost narrow hook: ${symbol}"
done
if grep -R -l -F '#include "agent_metadata_recovery_test.h"' \
	"${ROOT_DIR}/os"/*.c |
	grep -v -E '/agent_(core|metadata_(probe|store|recovery_test))\.c$' \
	>"${TMP_FILE}"; then
	fail "production owner gained a metadata recovery-test dependency"
fi
: >"${TMP_FILE}"
grep -q -F '#else' "${recovery_test_header}" &&
	grep -q -F 'static inline' "${recovery_test_header}" ||
	fail "metadata recovery test header lacks production inline no-op hooks"

observe_test_support="${ROOT_DIR}/os/agent_observe_test.c"
observe_test_header="${ROOT_DIR}/os/agent_observe_test.h"
grep -q -F 'C_SRCS := $(filter-out $K/agent_observe_test.c,$(C_SRCS))' \
	"${ROOT_DIR}/Makefile" ||
	fail "observe test owner is not excluded from production objects"
grep -q -F '#ifdef AGENT_OBSERVE_TEST_PROFILE' "${observe_test_support}" ||
	fail "observe test owner lost its profile guard"
for symbol in agent_observe_test_operation agent_observe_test_execute; do
	grep -q -F "${symbol}" "${observe_test_support}" ||
		fail "observe test owner lost narrow hook: ${symbol}"
done
if grep -R -l -F '#include "agent_observe_test.h"' "${ROOT_DIR}/os"/*.c |
	grep -v -E '/agent_observe_(recovery|test)\.c$' >"${TMP_FILE}"; then
	fail "production owner gained an observe test dependency"
fi
: >"${TMP_FILE}"
if grep -q -F '#else' "${observe_test_header}" ||
	grep -q -F 'static inline' "${observe_test_header}"; then
	fail "observe test header leaks profile stubs into production"
fi
grep -q -F '#ifdef AGENT_OBSERVE_TEST_PROFILE' \
	"${ROOT_DIR}/os/agent_observe_recovery.c" ||
	fail "observe recovery test callsites lack an explicit profile guard"

wait_test_support="${ROOT_DIR}/os/wait_atomic_test.c"
wait_test_header="${ROOT_DIR}/os/wait_atomic_test.h"
grep -q -F 'C_SRCS := $(filter-out $K/wait_atomic_test.c,$(C_SRCS))' \
	"${ROOT_DIR}/Makefile" ||
	fail "atomic-wait test owner is not excluded from production objects"
grep -q -F '#ifdef WAIT_ATOMIC_TEST_PROFILE' "${wait_test_support}" ||
	fail "atomic-wait test owner lost its profile guard"
for symbol in sys_wait_atomic_test wait_atomic_test_begin \
	wait_atomic_test_complete wait_atomic_test_agent_wait \
	agent_ipc_wait_test_publish; do
	grep -q -F "${symbol}" "${wait_test_support}" ||
		fail "atomic-wait test owner lost narrow hook: ${symbol}"
done
if grep -R -l -F '#include "wait_atomic_test.h"' "${ROOT_DIR}/os"/*.c |
	grep -v -E '/(agent_ipc|proc|syscall|wait_atomic_test)\.c$' >"${TMP_FILE}"; then
	fail "production owner gained an atomic-wait test dependency"
fi
: >"${TMP_FILE}"
if grep -q -F '#else' "${wait_test_header}"; then
	fail "atomic-wait test header must not publish production stubs"
fi

fs_allocator_test_support="${ROOT_DIR}/os/fs_allocator_test.c"
fs_allocator_test_header="${ROOT_DIR}/os/fs_allocator_test.h"
grep -q -F 'C_SRCS := $(filter-out $K/fs_allocator_test.c,$(C_SRCS))' \
	"${ROOT_DIR}/Makefile" ||
	fail "filesystem allocator test owner is not excluded from production objects"
grep -q -F '#ifdef FS_ALLOCATOR_FAULT_TEST_PROFILE' \
	"${fs_allocator_test_support}" ||
	fail "filesystem allocator test owner lost its profile guard"
for symbol in fs_allocator_test_bind_boot_init fs_allocator_test_authorized \
	fs_allocator_test_arm fs_allocator_test_disarm fs_allocator_test_snapshot \
	fs_allocator_test_before fs_allocator_test_after; do
	grep -q -F "${symbol}" "${fs_allocator_test_support}" ||
		fail "filesystem allocator test owner lost narrow hook: ${symbol}"
done
grep -q -F 'fs_allocator_test_storage_snapshot' "${ROOT_DIR}/os/fs.c" ||
	fail "filesystem allocator profile lost its storage snapshot bridge"
if grep -R -l -F '#include "fs_allocator_test.h"' "${ROOT_DIR}/os"/*.c |
	grep -v -E '/(fs|fs_allocator_test|loader|syscall)\.c$' >"${TMP_FILE}"; then
	fail "production owner gained a filesystem allocator test dependency"
fi
: >"${TMP_FILE}"
if grep -q -F '#else' "${fs_allocator_test_header}"; then
	fail "filesystem allocator test header must not publish production stubs"
fi

metadata_private_headers="
agent_file_name_policy.h
agent_file_state_internal.h
agent_metadata_actions.h
agent_metadata_catalog.h
 agent_metadata_directory.h
 agent_metadata_disk.h
 agent_metadata_internal.h
 agent_metadata_probe.h
 agent_metadata_recovery.h
 agent_metadata_recovery_test.h
 agent_metadata_store_format.h
 agent_metadata_store_io.h
agent_metadata_query.h
agent_metadata_scan.h
agent_metadata_prefetch.h
"
metadata_disk_abi="${ROOT_DIR}/agent_metadata_disk_abi.h"
[ -f "${metadata_disk_abi}" ] || fail "shared metadata disk ABI is missing"
grep -q -F '#include "../agent_metadata_disk_abi.h"' \
	"${ROOT_DIR}/os/agent_metadata_disk.h" ||
	fail "kernel metadata header bypasses the shared disk ABI"
grep -q -F '#include "../agent_metadata_disk_abi.h"' \
	"${ROOT_DIR}/os/agent_durable_section.h" ||
	fail "durable arena header bypasses the shared disk ABI"
grep -q -F '#include "../agent_metadata_disk_abi.h"' \
	"${ROOT_DIR}/nfs/fs.c" ||
	fail "mkfs bypasses the shared metadata disk ABI"
grep -q -F 'agent_meta_disk_init_genesis(&genesis)' \
	"${ROOT_DIR}/nfs/fs.c" ||
	fail "mkfs does not use the canonical metadata genesis builder"
grep -q -F 'agent_durable_disk_init_empty(arena)' \
	"${ROOT_DIR}/os/agent_durable_section.c" ||
	fail "kernel durable initialization bypasses the shared disk builder"
for token in '#define AGENT_META_STORE_MAGIC ' \
	'struct agent_meta_store_header {' \
	'struct agent_durable_arena {'; do
	matches="$(grep -l -F "${token}" "${metadata_disk_abi}" \
		"${ROOT_DIR}"/os/*.h "${ROOT_DIR}"/nfs/*.h 2>/dev/null || true)"
	[ "${matches}" = "${metadata_disk_abi}" ] || {
		printf '%s\n' "${matches}" >"${TMP_FILE}"
		fail "metadata disk layout has multiple definition owners"
	}
done
registered_agent_headers="
agent.h
agent_context.h
agent_context_path.h
agent_durable_section.h
agent_identity_lease.h
agent_internal.h
agent_lifecycle.h
 agent_observe_internal.h
 agent_observe_capacity.h
 agent_observe_persist_context.h
agent_observe_recovery.h
agent_observe_recovery_store.h
agent_observe_store.h
agent_observe_test.h
agent_tool_protocol.h
${metadata_private_headers}
"

for path in "${ROOT_DIR}"/os/agent*.h; do
	[ -e "${path}" ] || continue
	header="$(basename "${path}")"
	registered=false
	for registered_header in ${registered_agent_headers}; do
		if [ "${header}" = "${registered_header}" ]; then
			registered=true
			break
		fi
	done
	[ "${registered}" = true ] || fail "unregistered Agent contract: os/${header}"
done

for header in ${registered_agent_headers}; do
	path="${ROOT_DIR}/os/${header}"
	[ -f "${path}" ] || fail "missing private contract: os/${header}"
	if grep -n -E '^[[:space:]]*extern[[:space:]]+' \
		"${path}" >"${TMP_FILE}"; then
		fail "os/${header} must expose operations, not writable data"
	fi
	: >"${TMP_FILE}"
done

if grep -R -n -E '#include[[:space:]]+"agent_context_path\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/agent_context(_path)?\.c:' >"${TMP_FILE}"; then
	fail "Context active-path contract escaped its owner"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_observe_store\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/(agent_core|agent_observe|agent_observe_capacity|agent_observe_ledger|agent_observe_store|agent_observe_test)\.c:' >"${TMP_FILE}"; then
	fail "observation checkpoint contract escaped its owner"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_observe_capacity\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/(agent_core|agent_observe|agent_observe_capacity|agent_observe_store)\.c:' >"${TMP_FILE}"; then
	fail "observation capacity contract escaped its endpoints"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_observe_recovery_store\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/agent_observe_(recovery|store)\.c:' >"${TMP_FILE}"; then
	fail "Recovery/store view contract escaped its two endpoints"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_observe_recovery\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/(agent_core|agent_observe|agent_observe_recovery|agent_observe_timeline)\.c:' >"${TMP_FILE}"; then
	fail "observation Recovery endpoint contract escaped its coordinator"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_observe_persist_context\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/agent_observe_store\.c:' >"${TMP_FILE}"; then
	fail "observation persistence context escaped its store owner"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_metadata_disk\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/agent_metadata_store_format\.c:' >"${TMP_FILE}"; then
	fail "metadata disk format contract escaped its store owner"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_metadata_store_format\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/agent_metadata_(probe|store(_format|_io)?)\.c:' >"${TMP_FILE}"; then
	fail "metadata store format contract escaped its owners"
fi
: >"${TMP_FILE}"

if grep -R -n -E '#include[[:space:]]+"agent_metadata_store_io\.h"' \
	"${ROOT_DIR}/os" --include='*.c' | \
	grep -v -E '/agent_metadata_(probe|store(_io)?)\.c:' >"${TMP_FILE}"; then
	fail "metadata bank I/O contract escaped its owners"
fi
: >"${TMP_FILE}"

# The shared facade may retain its existing lifecycle entry points, but owner
# internals must stay in budgeted private headers rather than escaping here.
shared_contract="${ROOT_DIR}/os/agent_internal.h"
if grep -n -E 'agent_(metadata_(actions|catalog|store|query|scan|directory)|file_(state|query|scan|directory)|query|scan|directory)_' \
	"${shared_contract}" >"${TMP_FILE}"; then
	fail "metadata owner APIs leaked into os/agent_internal.h"
fi
: >"${TMP_FILE}"
if grep -n -E '#include[[:space:]]+"agent_(metadata_(actions|catalog|internal|prefetch|probe|query|scan|directory|store_format|store_io)|file_(state_internal|name_policy)|query|scan|directory).*\.h"' \
	"${shared_contract}" >"${TMP_FILE}"; then
	fail "metadata private contract included by os/agent_internal.h"
fi
: >"${TMP_FILE}"

# Core may coordinate owner operations, but it must not reset proc-local state
# by reaching through another owner's fields. Keep the lifecycle boundaries as
# operations so future sidecars do not require another cross-module rewrite.
core_source="${ROOT_DIR}/os/agent_core.c"
core_clear="$(sed -n '/^void agent_core_clear_metadata(/,/^}/p' \
	"${core_source}")"
core_make="$(sed -n '/^int agent_make_role(/,/^}/p' \
	"${core_source}")"
core_exec="$(sed -n '/^int agent_core_exec_public_commit(/,/^}/p' \
	"${core_source}")"
core_tick="$(sed -n '/^void agent_core_tick(/,/^}/p' \
	"${core_source}")"
owner_fields='->(agent_ctx_|agent_shadow_|context_path_|latest_response_offset|records_offset|agent_current_|agent_context_chain_hash|mail_|agent_mailbox|agent_watch_|agent_ipc_|agent_event_|agent_wait_|heartbeat_interval|agent_last_heartbeat_tick|loop_state|agent_sched_|agent_timeline_|agent_observe_|agent_provenance_edges)'
for body_name in core_clear core_make core_exec core_tick; do
	body="$(eval "printf '%s' \"\${${body_name}}\"")"
	[ -n "${body}" ] || fail "missing Agent core owner boundary: ${body_name}"
	if printf '%s\n' "${body}" | grep -n -E -- "${owner_fields}" >"${TMP_FILE}"; then
		fail "Agent core bypassed a proc-state owner in ${body_name}"
	fi
	: >"${TMP_FILE}"
done
if printf '%s\n' "${core_exec}" | grep -n -E -- \
	'agent_control_state[[:space:]]*=' >"${TMP_FILE}"; then
	fail "Agent exec bypassed the identity control-state owner"
fi
: >"${TMP_FILE}"
for operation in 'agent_scope_controller_departing(p)' \
	'agent_identity_proc_reset(p, 1)' \
	'agent_context_proc_reset(p)' 'agent_observe_proc_reset(p)' \
	'agent_ipc_exec_public(p)'; do
	printf '%s\n' "${core_exec}" | grep -q -F "${operation}" ||
		fail "Agent exec lost owner operation: ${operation}"
done
if [ "$(printf '%s\n' "${core_exec}" | \
	grep -c -F 'agent_ipc_exec_public(p)')" -ne 2 ]; then
	fail "Agent exec must rotate the PUBLIC endpoint in both identity branches"
fi
if printf '%s\n' "${core_exec}" | \
	grep -q -F 'agent_ipc_proc_teardown(p)'; then
	fail "Agent exec incorrectly destroyed the live PUBLIC endpoint"
fi
for operation in 'agent_context_proc_reset(p)' \
	'agent_observe_proc_reset(p)' 'agent_ipc_proc_teardown(p)'; do
	printf '%s\n' "${core_clear}" | grep -q -F "${operation}" ||
		fail "Agent core clear lost owner operation: ${operation}"
done
if [ "$(printf '%s\n' "${core_clear}" | \
	grep -c -F 'agent_ipc_proc_teardown(p)')" -ne 1 ] || \
	printf '%s\n' "${core_clear}" | \
	grep -q -F 'agent_ipc_exec_public(p)'; then
	fail "Agent core clear must destroy, not rotate, its IPC endpoint"
fi
for operation in 'agent_context_proc_activate(p)' \
	'agent_ipc_proc_activate(p)' 'agent_observe_proc_init('; do
	printf '%s\n' "${core_make}" | grep -q -F "${operation}" ||
		fail "Agent core activation lost owner operation: ${operation}"
done
printf '%s\n' "${core_tick}" | grep -q -F 'agent_observe_tick_proc(p, now)' ||
	fail "Agent core tick bypassed the observe owner"

# Resource ownership survives credential-dropping exec. VFS scope controls
# access only; the immutable lifecycle key remains the accounting principal.
bio_source="${ROOT_DIR}/os/bio.c"
io_owner="$(sed -n '/^static uint io_owner_from_proc(/,/^}/p' \
	"${bio_source}")"
for operation in 'vfs_proc_lifecycle(p)' \
	'workflow_lifecycle_scope(lifecycle, &scope_id)'; do
	printf '%s\n' "${io_owner}" | grep -q -F "${operation}" ||
		fail "I/O owner lost lifecycle identity: ${operation}"
done
if printf '%s\n' "${io_owner}" | grep -n -F -- 'p->vfs_scope_id' >"${TMP_FILE}"; then
	fail "I/O owner regressed to mutable VFS credentials"
fi
: >"${TMP_FILE}"

# Catalog commits return deltas to the objects owner. A callback here creates
# an indirect stack edge and a hidden reverse dependency into projections.
catalog_source="${ROOT_DIR}/os/agent_metadata_catalog.c"
metadata_source="${ROOT_DIR}/os/agent_metadata.c"
actions_source="${ROOT_DIR}/os/agent_metadata_actions.c"
objects_source="${ROOT_DIR}/os/agent_metadata_objects.c"
prefetch_source="${ROOT_DIR}/os/agent_metadata_prefetch.c"
directory_source="${ROOT_DIR}/os/agent_metadata_directory.c"
scan_source="${ROOT_DIR}/os/agent_metadata_scan.c"
store_source="${ROOT_DIR}/os/agent_metadata_store.c"
if grep -n -E 'projection_commit|agent_file_catalog_sync|\(\*[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\)[[:space:]]*\(' \
	"${catalog_source}" >"${TMP_FILE}"; then
	fail "metadata catalog must not publish through callbacks"
fi
: >"${TMP_FILE}"
if grep -n -E 'agent_metadata_store_(load|reload|storage_init|install_empty)\([^;]*0\)' \
	"${ROOT_DIR}/os/agent_metadata_objects.c" >"${TMP_FILE}"; then
	fail "metadata objects must consume every store commit delta"
fi
: >"${TMP_FILE}"

if grep -n -E '\bscan_control\b|\bscan\.(offset|seen|next_tick|last_step_tick|started_tick|runs|entries|added|updated|removed)\b|root_dir\(' \
	"${objects_source}" >"${TMP_FILE}"; then
	fail "metadata objects retained scan-owned state or directory traversal"
fi
: >"${TMP_FILE}"
if grep -n -E '\bagent_(action_history|dependencies|dependency_generation|status_batch_undo)\b|agent_file_prefetch_(store|update|handoff)[[:space:]]*\(|^int[[:space:]]+sys_agent_file_prefetch_' \
	"${objects_source}" >"${TMP_FILE}"; then
	fail "metadata objects retained action/dependency or prefetch ownership"
fi
: >"${TMP_FILE}"
for actions_owner_operation in 'agent_metadata_actions_reclaim_scope(scope_id)' \
	'agent_metadata_actions_update_status_locked(' \
	'agent_metadata_actions_dependency_update('; do
	grep -q -F "${actions_owner_operation}" "${objects_source}" ||
		fail "metadata objects lost actions owner delegation: ${actions_owner_operation}"
done
for prefetch_owner_operation in 'agent_metadata_prefetch_update(' \
	'agent_metadata_prefetch_handoff(' \
	'sys_agent_file_prefetch_snapshot(' \
	'sys_agent_file_prefetch_span_snapshot('; do
	grep -q -F "${prefetch_owner_operation}" "${prefetch_source}" ||
		fail "metadata prefetch lost owner operation: ${prefetch_owner_operation}"
done
if grep -n -E '^static[[:space:]]+struct[[:space:]]+agent_(action_history_entry|dependency_entry|status_batch_undo)' \
	"${objects_source}" >"${TMP_FILE}"; then
	fail "metadata objects reacquired actions-owned writable tables"
fi
: >"${TMP_FILE}"
for actions_state in 'agent_action_history[' 'agent_dependencies[' \
	'agent_status_batch_undo['; do
	grep -q -F "${actions_state}" "${actions_source}" ||
		fail "metadata actions lost owned state: ${actions_state}"
done
if grep -n -E 'agent_file_maintain|agent_metadata_note_catalog_changes|agent_file_store_load|agent_metadata_query_|bio_background_|agent_metadata_txn_try_external|\(\*[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\)[[:space:]]*\(' \
	"${scan_source}" >"${TMP_FILE}"; then
	fail "metadata scan acquired a reverse dependency or callback"
fi
: >"${TMP_FILE}"
for scan_owner_operation in 'root_dir_status(&root_status)' \
	'root_status != FS_LOOKUP_FOUND' 'readi(' 'inode_get(' \
	'scan_bind_inode(ip, name, &bind_failed)' 'if (bind_failed)' \
	'scan.seen[slot] = 1' 'agent_metadata_store_mark_dirty' \
	'steps < SCAN_STEP'; do
	grep -q -F "${scan_owner_operation}" "${scan_source}" ||
		fail "metadata scan lost owner operation: ${scan_owner_operation}"
done

if grep -n -E '^void[[:space:]]+agent_fs_(note_create|note_write|sync_write|note_truncate|note_delete)[[:space:]]*\(' \
	"${objects_source}" >"${TMP_FILE}"; then
	fail "metadata objects retained a directory hook"
fi
: >"${TMP_FILE}"
if grep -n -E 'agent_metadata_query_|agent_file_store_load|bio_background_|agent_metadata_store_persist[[:space:]]*\(|agent_metadata_txn_lock[[:space:]]*\(|agent_dependency_generation|\bscan_control\b|\bscan\.(offset|seen|next_tick|last_step_tick|started_tick|runs|entries|added|updated|removed)\b|\(\*[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\)[[:space:]]*\(' \
	"${directory_source}" >"${TMP_FILE}"; then
	fail "metadata directory acquired coordination state or blocking work"
fi
: >"${TMP_FILE}"
if grep -n -E '^static[[:space:]]+(char|short|int|long|uint|uint64|struct[[:space:]]+[A-Za-z_][A-Za-z0-9_]*)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*([[:space:]]*\[[^]]*\])?[[:space:]]*(=[^;]*)?;' \
	"${directory_source}" >"${TMP_FILE}"; then
	fail "metadata directory acquired writable file-scope state"
fi
: >"${TMP_FILE}"
for directory_operation in 'agent_metadata_txn_try_external()' \
	'agent_metadata_scan_note_slot(slot)' \
	'agent_metadata_note_catalog_changes(AGENT_FILE_CHANGE_ALL)' \
	'agent_metadata_store_mark_dirty(scope_id)'; do
	grep -q -F "${directory_operation}" "${directory_source}" ||
		fail "metadata directory lost owner operation: ${directory_operation}"
done

# A pending catalog projection is a hard persistence barrier. Keep the guard
# at the shared finish boundary and at both physical writeback transitions so
# ordinary, repair, and background paths cannot depend on wrapper ordering.
projection_idle="$(sed -n \
	'/agent_metadata_txn_projection_require_idle(void)/,/^}/p' \
	"${metadata_source}" | tr -d '[:space:]')"
printf '%s\n' "${projection_idle}" | grep -q -F \
	'if(!agent_metadata_txn_owned(0)||txn_projection_pending)panic(' ||
	fail "metadata projection idle guard must fail closed"
store_finish="$(sed -n '/^agent_metadata_store_finish(/,/^}/p' \
	"${store_source}")"
if ! printf '%s\n' "${store_finish}" | awk '
	/agent_metadata_txn_projection_require_idle\(\);/ {
		guard_count++; if (!guard) guard = NR
	}
	/reload_owned != agent_metadata_reload_is_current\(\)/ && !owner {
		owner = NR
	}
	/agent_file_persist_system\(\)/ && !persist { persist = NR }
	/agent_metadata_reload_release\(\)/ && !release { release = NR }
	END {
		exit !(guard_count == 1 && guard < owner && owner < persist &&
		       persist < release)
	}'
then
	fail "metadata store finish must reject pending projection before persistence"
fi

for persist_function in agent_meta_persist_start_locked \
	agent_meta_persist_step_locked; do
	persist_body="$(sed -n "/^static int ${persist_function}(/,/^}/p" \
		"${store_source}")"
	if ! printf '%s\n' "${persist_body}" | awk '
		/agent_metadata_txn_projection_require_idle\(\);/ {
			guard_count++; if (!guard) guard = NR
		}
		/if \(agent_meta_persist.phase/ && !phase { phase = NR }
		END { exit !(guard_count == 1 && guard < phase) }'
	then
		fail "${persist_function} must guard projection before phase work"
	fi
done

# Scope teardown is a forward-only cross-owner protocol. Keep the expensive
# metadata sweep in BEGIN, resumable namespace work in FILES, and generation
# polling in METADATA; an immutable lifecycle key guards every publication.
vfs_source="${ROOT_DIR}/os/vfs_security.c"
reclaim_begin="$(sed -n '/^int agent_scope_reclaim_begin(/,/^}/p' \
	"${objects_source}")"
reclaim_done="$(sed -n '/^int agent_scope_reclaim_metadata_done(/,/^}/p' \
	"${objects_source}")"
reclaim_advance="$(sed -n '/^vfs_scope_reclaim_advance(/,/^}/p' \
	"${vfs_source}")"
reclaim_driver="$(sed -n '/^static void vfs_scope_reclaim_complete(/,/^}/p' \
	"${vfs_source}")"
target_retire="$(sed -n '/^agent_file_scope_state_retire(/,/^}/p' \
	"${store_source}")"
target_done="$(sed -n '/^agent_metadata_store_scope_target_done(/,/^}/p' \
	"${store_source}")"

[ -n "${reclaim_begin}" ] && [ -n "${reclaim_done}" ] &&
	[ -n "${reclaim_advance}" ] &&
	[ -n "${reclaim_driver}" ] && [ -n "${target_retire}" ] &&
	[ -n "${target_done}" ] ||
	fail "scope reclaim phases are missing"
mark_count="$(printf '%s\n' "${reclaim_begin}" |
	grep -c -F 'agent_metadata_store_mark_dirty(scope_id)' || true)"
[ "${mark_count}" -eq 1 ] ||
	fail "scope reclaim BEGIN must capture exactly one dirty generation"
printf '%s\n' "${reclaim_begin}" |
	grep -q -F '*metadata_target = agent_metadata_store_mark_dirty(scope_id)' ||
	fail "scope reclaim BEGIN did not retain its dirty generation"
if printf '%s\n' "${reclaim_begin}" |
	grep -E 'fs_reclaim_scope_files|scope_target_done|scope_retire|persist(_system)?\(|sleep\(|yield\(' \
	>"${TMP_FILE}"; then
	fail "scope reclaim BEGIN crossed a later teardown phase"
fi
: >"${TMP_FILE}"
printf '%s\n' "${reclaim_done}" |
	grep -q -F 'agent_metadata_store_scope_target_done(scope_id, metadata_target)' ||
	fail "scope reclaim METADATA must poll its captured generation"
if printf '%s\n' "${reclaim_done}" |
	grep -E 'mark_dirty|persist\(|persist_system|sleep\(|yield\(' \
	>"${TMP_FILE}"; then
	fail "scope reclaim METADATA may only poll its captured generation"
fi
: >"${TMP_FILE}"
target_lock_count="$(printf '%s\n' "${target_retire}" |
	grep -c -F 'intr_save()' || true)"
target_unlock_count="$(printf '%s\n' "${target_retire}" |
	grep -c -F 'intr_restore(enabled)' || true)"
[ "${target_lock_count}" -eq 1 ] && [ "${target_unlock_count}" -eq 1 ] ||
	fail "metadata target retirement must use one atomic snapshot"
for invariant in \
	'agent_meta_store_failed_closed' \
	'agent_durable_section_scope_pending(scope_id)' \
	'agent_file_writeback_generation_reached(' \
	'state->replicated_generation, target' \
	'state->dirty_generation != state->durable_generation' \
	'state->dirty_generation != state->replicated_generation' \
	'agent_file_writeback_scope_busy(scope_id)' \
	'retired = target == 0'; do
	printf '%s\n' "${target_retire}" | grep -q -F "${invariant}" ||
		fail "metadata target retirement lost invariant: ${invariant}"
done
printf '%s\n' "${target_done}" | awk '
	/agent_metadata_txn_lock\(0\)/ { lock_count++; if (!lock) lock = NR }
	/agent_file_scope_state_retire\(scope_id, target\)/ {
		retire_count++; if (!retire) retire = NR
	}
	/agent_metadata_txn_unlock\(\)/ { unlock_count++; if (!unlock) unlock = NR }
	END {
		exit !(lock_count == 1 && retire_count == 1 && unlock_count == 1 &&
		       lock < retire && retire < unlock)
	}' || fail "metadata target completion must check and retire under one transaction"
printf '%s\n' "${reclaim_done}" |
	grep -q -F 'agent_metadata_store_scope_target_done(scope_id, metadata_target)' ||
	fail "scope reclaim RETIRE must settle metadata ownership"
printf '%s\n' "${reclaim_advance}" |
	grep -q -F 'workflow_lifecycle_key_equal(ref->lifecycle, lifecycle)' ||
	fail "scope reclaim publication is not lifecycle-key guarded"
printf '%s\n' "${reclaim_advance}" |
	grep -q -F 'ref->reclaim_phase == expected' ||
	fail "scope reclaim publication is not expected-phase guarded"
printf '%s\n' "${reclaim_advance}" |
	grep -q -F 'ref->scope_id != scope_id || !ref->retiring' ||
	fail "scope reclaim publication is not retiring-scope guarded"
files_count="$(printf '%s\n' "${reclaim_driver}" |
	grep -c -F 'fs_reclaim_scope_files(scope_id)' || true)"
[ "${files_count}" -eq 1 ] ||
	fail "scope reclaim FILES must have one resumable cursor driver"
phase_reset_count="$(grep -c -F \
	'reclaim_phase = VFS_SCOPE_RECLAIM_BEGIN' "${vfs_source}" || true)"
target_reset_count="$(grep -c -F \
	'reclaim_metadata_target = 0' "${vfs_source}" || true)"
[ "${phase_reset_count}" -ge 2 ] && [ "${target_reset_count}" -ge 2 ] ||
	fail "scope create and release must reset reclaim state"
if grep -R -n -E 'agent_scope_reclaim[[:space:]]*\(' \
	"${ROOT_DIR}/os" >"${TMP_FILE}"; then
	fail "legacy all-in-one scope reclaim entry point returned"
fi
: >"${TMP_FILE}"
if ! "${PYTHON_BIN:-python3}" \
	"${ROOT_DIR}/scripts/check-teardown-protocol.py" \
	--root "${ROOT_DIR}"; then
	fail "workflow teardown protocol validation failed"
fi

facade_lines="$(wc -l <"${ROOT_DIR}/os/agent.c")"
if [ "${facade_lines}" -gt 200 ]; then
	fail "os/agent.c is no longer a thin facade (${facade_lines} lines)"
fi

if grep -n -E '#include[[:space:]]+\"agent[A-Za-z0-9_]*\.c\"' \
	"${ROOT_DIR}"/os/agent*.c >"${TMP_FILE}"; then
	fail "AgentOS modules must remain independent translation units"
fi

module_count="$(printf '%s\n' ${modules} | wc -l)"
echo "[agent-module-check] facade: ${facade_lines} lines"
echo "[agent-module-check] registered implementation units: ${module_count}"
echo "[agent-module-check] private writable data exports: absent"
echo "[agent-module-check] source inventory complete"
