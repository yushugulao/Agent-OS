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
agent_metadata_objects
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
		agent_metadata_store | agent_observe)
		;;
	*)
		fail "unregistered AgentOS implementation: os/${module}.c"
		;;
	esac
done

for header in agent_internal.h agent_metadata_internal.h agent_context.h \
	agent_file_state_internal.h agent_file_name_policy.h; do
	path="${ROOT_DIR}/os/${header}"
	[ -f "${path}" ] || fail "missing private contract: os/${header}"
	if grep -n -E '^[[:space:]]*extern[[:space:]]+' \
		"${path}" >"${TMP_FILE}"; then
		fail "os/${header} must expose operations, not writable data"
	fi
	: >"${TMP_FILE}"
done

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
