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

reject_text() {
	if grep -R -E -n -- "$2" "${ROOT_DIR}/$1" >"${TMP_FILE}" 2>/dev/null; then
		fail "$3: $1"
	fi
	: >"${TMP_FILE}"
}

make_var_words() {
	grep "^$2 :=" "$1" | sed "s/^$2 :=//" | xargs
}

# 只检查双目标的真实结构边界：基线不包含 AgentOS ABI，且两边
# 构建并运行同一组对照程序。运行时行为由 run-dual-platforms.sh 验证。
for path in \
	Makefile baseline_ucore/os baseline_ucore/user/Makefile \
	baseline_ucore/user/src os/agent.c os/agent.h user/Makefile user/src \
	user/src/rp_agentos_orch.c scripts/run-agent-tests.sh \
	scripts/run-dual-platforms.sh
do
	require_path "${path}" "required dual-target surface is missing"
done

plain_kernel_pattern='SYS_agent_|AGENT_CONTEXT|AGENT_TOOL_|AGENT_CAP_|agent_create|agent_run|context_snapshot|agent_file_|agent_wait|agent_heartbeat|[.]agentmeta'
reject_text "baseline_ucore/os" "${plain_kernel_pattern}" \
	"baseline kernel contains AgentOS-specific symbols"
reject_text "baseline_ucore/user/include" "${plain_kernel_pattern}" \
	"baseline user ABI contains AgentOS-specific symbols"
reject_text "baseline_ucore/user/lib" "${plain_kernel_pattern}" \
	"baseline user library contains AgentOS-specific symbols"

plain_platform_tests="$(make_var_words "${ROOT_DIR}/baseline_ucore/user/Makefile" PLATFORM_TESTS)"
agentos_platform_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" PLATFORM_TESTS)"
plain_seeded_tests="$(make_var_words "${ROOT_DIR}/baseline_ucore/user/Makefile" PLATFORM_SEEDED_TESTS)"
agentos_seeded_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" PLATFORM_SEEDED_TESTS)"
[ -n "${plain_platform_tests}" ] || fail "platform build list is empty"
[ -n "${plain_seeded_tests}" ] || fail "seeded platform build list is empty"
[ "${plain_platform_tests}" = "${agentos_platform_tests}" ] ||
	fail "AgentOS and baseline platform build lists differ"
[ "${plain_seeded_tests}" = "${agentos_seeded_tests}" ] ||
	fail "AgentOS and baseline seeded build lists differ"

platform_program_count=0
for app in $(printf '%s\n' ${plain_platform_tests} ${plain_seeded_tests} | sort -u); do
	platform_program_count=$((platform_program_count + 1))
	require_path "baseline_ucore/user/src/${app}.c" "baseline platform source is missing"
	require_path "user/src/${app}.c" "AgentOS platform mirror is missing"
done

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

echo "[dual-target-check] baseline AgentOS surface: absent"
echo "[dual-target-check] platform source coverage: ${plain_source_count} baseline rp sources mirrored"
echo "[dual-target-check] platform app coverage: ${platform_program_count} build-list apps mirrored"
echo "[dual-target-check] platform source sync: identical=${plain_source_identical_count} adapted=${agentos_adapted_count}"
echo "[dual-target-check] dual-target program inventories: compatible"
