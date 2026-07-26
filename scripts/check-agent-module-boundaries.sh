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
agent_core
agent_context
agent_file_state
agent_identity
agent_ipc
agent_lifecycle
agent_metadata
agent_metadata_catalog
agent_metadata_objects
agent_metadata_query
agent_metadata_store
agent_observe
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

for path in "${ROOT_DIR}"/os/agent*.c; do
	module="$(basename "${path}" .c)"
	case "${module}" in
	agent | agent_core | agent_context | agent_file_state | agent_identity | agent_ipc | \
		agent_lifecycle | agent_metadata | agent_metadata_objects | \
		agent_metadata_catalog | agent_metadata_query | agent_metadata_store | agent_observe)
		;;
	*)
		fail "unregistered AgentOS implementation: os/${module}.c"
		;;
	esac
done

metadata_private_headers="
agent_file_name_policy.h
agent_file_state_internal.h
agent_metadata_catalog.h
agent_metadata_internal.h
agent_metadata_query.h
"
registered_agent_headers="
agent.h
agent_context.h
agent_internal.h
agent_lifecycle.h
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

# The shared facade may retain its existing lifecycle entry points, but owner
# internals must stay in budgeted private headers rather than escaping here.
shared_contract="${ROOT_DIR}/os/agent_internal.h"
if grep -n -E 'agent_(metadata_(catalog|store|query|scan|directory)|file_(state|query|scan|directory)|query|scan|directory)_' \
	"${shared_contract}" >"${TMP_FILE}"; then
	fail "metadata owner APIs leaked into os/agent_internal.h"
fi
: >"${TMP_FILE}"
if grep -n -E '#include[[:space:]]+"agent_(metadata_(catalog|internal|query|scan|directory)|file_(state_internal|name_policy)|query|scan|directory).*\.h"' \
	"${shared_contract}" >"${TMP_FILE}"; then
	fail "metadata private contract included by os/agent_internal.h"
fi
: >"${TMP_FILE}"

# Catalog commits return deltas to the objects owner. A callback here creates
# an indirect stack edge and a hidden reverse dependency into projections.
catalog_source="${ROOT_DIR}/os/agent_metadata_catalog.c"
metadata_source="${ROOT_DIR}/os/agent_metadata.c"
objects_source="${ROOT_DIR}/os/agent_metadata_objects.c"
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
target_done="$(sed -n '/^static int agent_file_writeback_scope_reached(/,/^}/p' \
	"${store_source}")"

[ -n "${reclaim_begin}" ] && [ -n "${reclaim_done}" ] &&
	[ -n "${reclaim_advance}" ] &&
	[ -n "${reclaim_driver}" ] && [ -n "${target_done}" ] ||
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
target_lock_count="$(printf '%s\n' "${target_done}" |
	grep -c -F 'intr_save()' || true)"
target_unlock_count="$(printf '%s\n' "${target_done}" |
	grep -c -F 'intr_restore(enabled)' || true)"
[ "${target_lock_count}" -eq 1 ] && [ "${target_unlock_count}" -eq 1 ] ||
	fail "metadata target completion must use one atomic snapshot"
for invariant in \
	'agent_file_writeback_generation_reached(' \
	'!settled || state->dirty_generation ==' \
	'settled && agent_file_writeback_scope_busy(scope_id)' \
	'reached = 0'; do
	printf '%s\n' "${target_done}" | grep -q -F "${invariant}" ||
		fail "metadata target completion lost invariant: ${invariant}"
done
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
