#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_FILE="${TMPDIR:-/tmp}/agentos-dual-target-check.$$"
trap 'rm -f "${TMP_FILE}"' EXIT

fail() {
	echo "[dual-target-check] failed: $1" >&2
	if [ -s "${TMP_FILE}" ]; then
		cat "${TMP_FILE}" >&2
	fi
	exit 1
}

require_path() {
	[ -e "${ROOT_DIR}/$1" ] || fail "$2: $1"
}

require_text() {
	if ! grep -R -E -n -- "$2" "${ROOT_DIR}/$1" >"${TMP_FILE}" 2>/dev/null; then
		fail "$3: $1"
	fi
	: >"${TMP_FILE}"
}

reject_text() {
	if grep -R -E -n -- "$2" "${ROOT_DIR}/$1" >"${TMP_FILE}" 2>/dev/null; then
		fail "$3: $1"
	fi
	: >"${TMP_FILE}"
}

make_var_words() {
	grep "^$2 :=" "$1" | sed "s/^$2 :=//" | xargs
}

first_number_after_key() {
	local value
	value="$(grep -o "$2=[0-9][0-9]*" "$1" | head -1 | sed 's/.*=//')"
	[ -n "${value}" ] || fail "missing numeric field $2 in ${1#${ROOT_DIR}/}"
	echo "${value}"
}

# This gate checks repository topology and target parity only. Mechanism
# behavior belongs to the executable contract and QEMU suites, not a second
# inventory of source-code substrings.
for path in \
	.gitlab-ci.yml Makefile ci/kernel-budgets.json ci/evaluation-suite.json \
	baseline_ucore/os baseline_ucore/user/Makefile os/agent.c os/agent.h \
	user/Makefile user/src/rp_agentos_orch.c user/src/agenteval_ucore.c \
	scripts/check-agent-module-boundaries.sh scripts/check-kernel-budgets.py \
	scripts/run-agent-tests.sh scripts/run-dual-platforms.sh \
	scripts/run-full-verification.sh scripts/check-target-readiness.sh \
	scripts/run-parallel-tests.py scripts/run-parallel-qemu-regressions.py \
	host_tools/render_evaluation_dashboard.py \
	evidence/README.md docs/verification.md docs/agentos/verification.md
do
	require_path "${path}" "required contest surface is missing"
done

python3 - "${ROOT_DIR}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for relative in ("ci/kernel-budgets.json", "ci/evaluation-suite.json"):
    with (root / relative).open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SystemExit(f"{relative} must contain a JSON object")
PY

bash "${ROOT_DIR}/scripts/check-agent-module-boundaries.sh"

plain_kernel_pattern='SYS_agent_|AGENT_CONTEXT|AGENT_TOOL_|AGENT_CAP_|agent_create|agent_run|context_snapshot|agent_file_|agent_wait|agent_heartbeat|[.]agentmeta'
reject_text "baseline_ucore/os" "${plain_kernel_pattern}" \
	"baseline kernel contains AgentOS-specific symbols"
reject_text "baseline_ucore/user/include" "${plain_kernel_pattern}" \
	"baseline user ABI contains AgentOS-specific symbols"
reject_text "baseline_ucore/user/lib" "${plain_kernel_pattern}" \
	"baseline user library contains AgentOS-specific symbols"

for target in plain-platform-run agentos-platform-run dual-platform-run \
	target-readiness full-verify local-check kernel-budget-check \
	agent-module-check agentos-test evaluation-smoke
do
	require_text "Makefile" "^${target}:" "Make target is missing"
done
require_text "scripts/check-target-readiness.sh" "verify-dual-target-structure" \
	"target readiness bypasses the topology gate"
require_text "scripts/run-dual-platforms.sh" "verify-dual-target-structure" \
	"dual-platform runner bypasses the topology gate"
require_text "scripts/run-full-verification.sh" "verify-dual-target-structure" \
	"full verification bypasses the topology gate"
# Public evidence contracts: '^SCHEMA_VERSION = 8$' and
# '^FULL_VERIFY_PROFILE_VERSION = 6$'.
require_text "scripts/capture-final-evidence.py" "^SCHEMA_VERSION = 8$" \
	"final evidence schema drifted"
require_text "scripts/capture-final-evidence.py" "^FULL_VERIFY_PROFILE_VERSION = 6$" \
	"full verification profile drifted"

agent_step="$(grep -n 'AgentOS kernel tests' "${ROOT_DIR}/scripts/run-full-verification.sh" | head -1 | cut -d: -f1)"
dual_step="$(grep -n '\[full-verify\] dual platforms' "${ROOT_DIR}/scripts/run-full-verification.sh" | head -1 | cut -d: -f1)"
if [ -z "${agent_step}" ] || [ -z "${dual_step}" ] ||
   [ "${agent_step}" -ge "${dual_step}" ]; then
	fail "Agent Guest measurements must precede dual-platform evaluation"
fi

plain_platform_tests="$(make_var_words "${ROOT_DIR}/baseline_ucore/user/Makefile" PLATFORM_TESTS)"
agentos_platform_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" PLATFORM_TESTS)"
plain_seeded_tests="$(make_var_words "${ROOT_DIR}/baseline_ucore/user/Makefile" PLATFORM_SEEDED_TESTS)"
agentos_seeded_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" PLATFORM_SEEDED_TESTS)"
[ "${plain_platform_tests}" = "${agentos_platform_tests}" ] ||
	fail "AgentOS and baseline platform build lists differ"
[ "${plain_seeded_tests}" = "${agentos_seeded_tests}" ] ||
	fail "AgentOS and baseline seeded build lists differ"

plain_platform_count=0
for app in ${plain_platform_tests}; do
	case "${app}" in
	rp_*)
		plain_platform_count=$((plain_platform_count + 1))
		require_path "user/src/${app}.c" "AgentOS platform mirror is missing"
		;;
	esac
done
[ "${plain_platform_count}" -ge 60 ] ||
	fail "platform program inventory is unexpectedly small: ${plain_platform_count}"

plain_source_count=0
plain_source_identical_count=0
agentos_adapted_count=0
while IFS= read -r source_path; do
	[ -n "${source_path}" ] || continue
	source_name="$(basename "${source_path}")"
	agentos_source="${ROOT_DIR}/user/src/${source_name}"
	[ -f "${agentos_source}" ] ||
		fail "AgentOS platform mirror is missing: ${source_name}"
	plain_source_count=$((plain_source_count + 1))
	if cmp -s "${source_path}" "${agentos_source}" ||
	   diff -q --strip-trailing-cr "${source_path}" "${agentos_source}" >/dev/null 2>&1; then
		plain_source_identical_count=$((plain_source_identical_count + 1))
	else
		agentos_adapted_count=$((agentos_adapted_count + 1))
	fi
done <<EOF
$(find "${ROOT_DIR}/baseline_ucore/user/src" -maxdepth 1 -type f -name 'rp_*.c' | sort)
EOF
[ "${plain_source_count}" -ge "${plain_platform_count}" ] ||
	fail "mirrored source inventory is smaller than the build list"

plain_backend="${ROOT_DIR}/baseline_ucore/user/src/rp_backend.c"
agentos_backend="${ROOT_DIR}/user/src/rp_backend.c"
plain_cases="$(first_number_after_key "${plain_backend}" reference_cases)"
agentos_cases="$(first_number_after_key "${agentos_backend}" cases)"
[ "${agentos_cases}" -ge "${plain_cases}" ] ||
	fail "AgentOS backend exposes fewer scenarios than the baseline"

plain_cost_count=0
while IFS= read -r cost; do
	[ -n "${cost}" ] || continue
	plain_cost_count=$((plain_cost_count + 1))
	grep -q "plain_cost=${cost}" "${agentos_backend}" ||
		fail "AgentOS backend dropped baseline cost evidence: ${cost}"
done <<EOF
$(grep -o 'plain_cost=[^;"]*' "${plain_backend}" | sed 's/plain_cost=//' | sort -u)
EOF
[ "${plain_cost_count}" -ge 7 ] ||
	fail "baseline cost inventory is unexpectedly small"

for marker in context_trusted=kernel_shadow metadata_query=used_index \
	agent_event_notify=kernel_queue failure_recovery=generic_action \
	provenance_audit=kernel_ledger permission_control=sentinel_action_denied \
	timeline_observe=kernel_snapshot edit_lease=kernel_exclusive
do
	grep -q "${marker}" "${agentos_backend}" ||
		fail "AgentOS backend marker is missing: ${marker}"
done

require_text "os" "SYS_agent_create" "AgentOS syscall table is missing"
require_text "os" "AGENT_CONTEXT" "AgentOS context ABI is missing"
require_text "os" "AGENT_TOOL_LLM_REQUEST" "AgentOS tool ABI is missing"
require_text "user/Makefile" "agenteval_ucore" "evaluation Guest is not built"
require_text "scripts/run-agent-tests.sh" "require_exact_case_marker" \
	"Agent suite does not enforce exact-line Guest markers"

if grep -E -n 'AGENT_TOOL_(RERUN_STAGE|WRITE_REPORT)' \
	"${ROOT_DIR}"/user/src/rp_*.c >"${TMP_FILE}" 2>/dev/null; then
	fail "AgentOS platform code uses retired sample tool ids"
fi
: >"${TMP_FILE}"

sample_run_marker='RUN-''042'
sample_kernel_pattern="lab-gene-x|${sample_run_marker}|nightly-regression|/lab/projects|INC-RUN|PLAN-RUN|MSG-RUN|minimal_rerun|memory_limit|recovery report|rerun completed"
reject_text "os" "${sample_kernel_pattern}" \
	"AgentOS kernel contains research-demo constants"

bad_git_commit="git ""commit"
bad_git_push="git ""push"
bad_glpat="gl""pat-"
bad_oauth2="oauth""2:"
bad_auth="Authorization:"" Basic"
bad_tokens="tokens""[.]txt"
doc_pattern="${bad_glpat}|${bad_oauth2}|${bad_auth}|${bad_tokens}|${bad_git_commit}|${bad_git_push}"
reject_text "README.md" "${doc_pattern}" "README contains operational secrets or commands"
reject_text "docs" "${doc_pattern}" "public docs contain operational secrets or commands"

echo "[dual-target-check] baseline AgentOS surface: absent"
echo "[dual-target-check] AgentOS kernel: present"
echo "[dual-target-check] platform source coverage: ${plain_source_count} baseline rp sources mirrored"
echo "[dual-target-check] platform app coverage: ${plain_platform_count} build-list apps mirrored"
echo "[dual-target-check] platform source sync: identical=${plain_source_identical_count} adapted=${agentos_adapted_count}"
echo "[dual-target-check] backend evidence coverage: plain=${plain_cases} agentos=${agentos_cases} preserved_costs=${plain_cost_count}"
echo "[dual-target-check] product-facing topology: valid"
