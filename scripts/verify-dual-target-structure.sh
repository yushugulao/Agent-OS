#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_FILE="${TMPDIR:-/tmp}/agentos-dual-target-check.$$"
PLAIN_BASE_REF="${UCORE_PLAIN_BASE_REF:-origin/main}"

cleanup() {
	rm -f "${TMP_FILE}"
}
trap cleanup EXIT

fail() {
	echo "[dual-target-check] failed: $1" >&2
	if [ -s "${TMP_FILE}" ]; then
		cat "${TMP_FILE}" >&2
	fi
	exit 1
}

require_path() {
	local path="$1"
	local message="$2"

	if [ ! -e "${ROOT_DIR}/${path}" ]; then
		fail "${message}: ${path}"
	fi
}

require_text() {
	local path="$1"
	local pattern="$2"
	local message="$3"

	if ! grep -R -E -n "${pattern}" "${ROOT_DIR}/${path}" >"${TMP_FILE}" 2>/dev/null; then
		fail "${message}: ${path}"
	fi
	: >"${TMP_FILE}"
}

reject_text() {
	local path="$1"
	local pattern="$2"
	local message="$3"

	if grep -R -E -n "${pattern}" "${ROOT_DIR}/${path}" >"${TMP_FILE}" 2>/dev/null; then
		fail "${message}: ${path}"
	fi
	: >"${TMP_FILE}"
}

first_number_after_key() {
	local file="$1"
	local key="$2"
	local value

	value="$(grep -o "${key}=[0-9][0-9]*" "${file}" | head -1 | sed 's/.*=//')"
	if [ -z "${value}" ]; then
		fail "missing numeric field ${key} in ${file#${ROOT_DIR}/}"
	fi
	echo "${value}"
}

make_var_words() {
	local file="$1"
	local name="$2"

	grep "^${name} :=" "${file}" | sed "s/^${name} :=//" | xargs
}

same_source_content() {
	local left="$1"
	local right="$2"

	cmp -s "${left}" "${right}" ||
		diff -q --strip-trailing-cr "${left}" "${right}" >/dev/null 2>&1
}

agentos_adapted_sources="
rp_agent_collab.c
rp_analysisres.c
rp_auditor.c
rp_backend.c
rp_calculation.c
rp_campaign.c
rp_compare_plain.c
rp_consistency.c
rp_controlplane.c
rp_coherenceplane.c
rp_decsupport.c
rp_execobs.c
rp_expsched.c
rp_integrityplane.c
rp_mature.c
rp_metrics.c
rp_modelreg.c
rp_orch.c
rp_opsboard.c
rp_package.c
rp_portability.c
rp_prov_query.c
rp_prov_view.c
rp_projectrel.c
rp_publication.c
rp_query.c
rp_realtask.c
rp_reldossier.c
rp_repair.c
rp_revdash.c
rp_reviewboard.c
rp_runbooks.c
rp_service_surface.c
rp_stdesign.c
rp_studyproto.c
rp_sysreview.c
rp_test_suite.c
rp_traincomp.c
rp_ui_export.c
rp_usable.c
rp_usableproject.c
rp_web_export.c
rp_workbench.c
"

is_agentos_adapted_source() {
	local name="$1"
	local adapted

	for adapted in ${agentos_adapted_sources}; do
		if [ "${adapted}" = "${name}" ]; then
			return 0
		fi
	done
	return 1
}

require_path "os" "plain kernel directory is missing"
require_path "user/src/rp_orch.c" "plain platform orchestrator is missing"
require_path "user/src/rp_backend.c" "plain platform backend is missing"
require_path "agentos_ucore/os/agent.c" "AgentOS kernel module is missing"
require_path "agentos_ucore/user/src/rp_agentos_orch.c" "AgentOS platform orchestrator is missing"
require_path "agentos_ucore/user/src/agentconflict_ucore.c" "AgentOS edit lease test is missing"
require_path "agentos_ucore/user/src/agentllm_ucore.c" "AgentOS LLM relay test is missing"
require_path "host_tools/check_host_platform_alignment.py" "host platform alignment checker is missing"
require_path "host_tools/check_host_action_kind_alignment.py" "host action kind alignment checker is missing"
require_path "host_tools/check_seeded_action_state.py" "seeded action state checker is missing"
require_path "host_tools/check_host_surface_alignment.py" "host Web/API/action surface alignment checker is missing"
require_path "host_tools/check_host_test_alignment.py" "host test alignment checker is missing"
require_path "host_tools/summarize_dual_platform_results.py" "dual platform result summarizer is missing"
require_path "host_tools/test_summarize_dual_platform_results.py" "dual platform result summarizer test is missing"
require_path "scripts/check-target-readiness.sh" "target readiness checker is missing"
require_path "scripts/run-dual-platforms.sh" "dual target runner is missing"
require_path "scripts/run-full-verification.sh" "full verification runner is missing"

if ! git -C "${ROOT_DIR}" rev-parse --verify "${PLAIN_BASE_REF}^{commit}" >/dev/null 2>&1; then
	fail "plain kernel base ref is not available: ${PLAIN_BASE_REF}"
fi
if ! git -C "${ROOT_DIR}" diff --quiet "${PLAIN_BASE_REF}" -- os bootloader; then
	git -C "${ROOT_DIR}" diff --name-status "${PLAIN_BASE_REF}" -- os bootloader >"${TMP_FILE}"
	fail "plain kernel differs from ${PLAIN_BASE_REF}"
fi

plain_kernel_pattern='SYS_agent_|AGENT_CONTEXT|AGENT_TOOL_|AGENT_CAP_|agent_create|agent_run|context_snapshot|agent_file_|agent_wait|agent_heartbeat|\.agentmeta'
reject_text "os" "${plain_kernel_pattern}" "plain kernel contains AgentOS-specific symbols"
reject_text "user/include" "${plain_kernel_pattern}" "plain user ABI contains AgentOS-specific symbols"
reject_text "user/lib" "${plain_kernel_pattern}" "plain syscall wrappers contain AgentOS-specific symbols"

require_text "Makefile" "^plain-platform-run:" "plain platform run target is missing"
require_text "Makefile" "^agentos-platform-run:" "AgentOS platform run target is missing"
require_text "Makefile" "^dual-platform-run:" "dual platform run target is missing"
require_text "Makefile" "^target-readiness:" "target readiness target is missing"
require_text "Makefile" "^full-verify:" "full verification target is missing"
require_text "Makefile" "INIT_PROC=rp_orch CHAPTER=platform" "plain platform run target does not launch rp_orch"
require_text "Makefile" "INIT_PROC=rp_agentos_orch CHAPTER=platform_agentos" "AgentOS platform run target does not launch rp_agentos_orch"
require_text "Makefile" "scripts/run-dual-platforms.sh" "Makefile dual platform target does not call the dual runner"
require_text "Makefile" "scripts/check-target-readiness.sh" "Makefile target readiness target does not call the readiness checker"
require_text "Makefile" "scripts/run-full-verification.sh" "Makefile full verification target does not call the full runner"
require_text "Makefile" "^QEMU \\?= qemu-system-riscv64" "plain Makefile QEMU is not environment-overridable"
require_text "agentos_ucore/Makefile" "^QEMU \\?= qemu-system-riscv64" "AgentOS Makefile QEMU is not environment-overridable"

require_text "user/Makefile" "platform_plain" "plain platform chapter is not declared"
require_text "scripts/run-full-verification.sh" "verify-dual-target-structure" "full verification does not run the structure check"
require_text "scripts/run-full-verification.sh" "test_check_host_platform_alignment.py" "full verification does not run host platform alignment unit test"
require_text "scripts/run-full-verification.sh" "test_check_host_action_kind_alignment.py" "full verification does not run host action kind alignment unit test"
require_text "scripts/run-full-verification.sh" "test_check_seeded_action_state.py" "full verification does not run seeded action state unit test"
require_text "scripts/run-full-verification.sh" "test_check_host_surface_alignment.py" "full verification does not run host Web/API/action surface alignment unit test"
require_text "scripts/run-full-verification.sh" "test_check_host_test_alignment.py" "full verification does not run host test alignment unit test"
require_text "scripts/run-full-verification.sh" "test_plain_ucore_action_runner.py" "full verification does not run action runner unit test"
require_text "scripts/run-full-verification.sh" "test_plain_ucore_fs_extract.py" "full verification does not run fs extraction unit test"
require_text "scripts/run-full-verification.sh" "test_plain_ucore_llm_relay.py" "full verification does not run LLM relay unit test"
require_text "scripts/run-full-verification.sh" "check_host_platform_alignment.py" "full verification does not run host platform alignment check"
require_text "scripts/run-full-verification.sh" "check_host_action_kind_alignment.py" "full verification does not run host action kind alignment check"
require_text "scripts/run-full-verification.sh" "check_host_surface_alignment.py" "full verification does not run host Web/API/action surface alignment check"
require_text "scripts/run-full-verification.sh" "check_host_test_alignment.py" "full verification does not run host test alignment check"
require_text "scripts/run-full-verification.sh" "test_check_reader_output.py" "full verification does not run Reader output test"
require_text "scripts/run-full-verification.sh" "test_compare_dual_platform_reader.py" "full verification does not run Reader comparison test"
require_text "scripts/run-full-verification.sh" "test_compare_dual_platform_state.py" "full verification does not run state comparison test"
require_text "scripts/run-full-verification.sh" "test_summarize_dual_platform_results.py" "full verification does not run result summary test"
require_text "scripts/run-full-verification.sh" "test_plain_ucore_reader.py" "full verification does not run Host Reader unit test"
require_text "scripts/run-full-verification.sh" "test_plain_ucore_reader_e2e.py" "full verification does not run Host Reader e2e test"
require_text "scripts/run-full-verification.sh" "run-dual-platforms.sh" "full verification does not run dual platform QEMU"
require_text "scripts/run-full-verification.sh" "QEMU=.*run-dual-platforms.sh" "full verification does not pass QEMU to dual platform runner"
require_text "scripts/run-full-verification.sh" "run-agent-tests.sh" "full verification does not run AgentOS kernel tests"

require_text "scripts/check-target-readiness.sh" "verify-dual-target-structure" "target readiness checker does not run the structure check"
require_text "scripts/check-target-readiness.sh" "test_check_host_platform_alignment.py" "target readiness checker does not run host platform alignment unit test"
require_text "scripts/check-target-readiness.sh" "test_check_host_action_kind_alignment.py" "target readiness checker does not run host action kind unit test"
require_text "scripts/check-target-readiness.sh" "test_check_seeded_action_state.py" "target readiness checker does not run seeded action unit test"
require_text "scripts/check-target-readiness.sh" "test_check_host_surface_alignment.py" "target readiness checker does not run host surface unit test"
require_text "scripts/check-target-readiness.sh" "test_check_host_test_alignment.py" "target readiness checker does not run host test unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_action_runner.py" "target readiness checker does not run action runner unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_fs_extract.py" "target readiness checker does not run fs extraction unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_llm_relay.py" "target readiness checker does not run LLM relay unit test"
require_text "scripts/check-target-readiness.sh" "test_compare_dual_platform_state.py" "target readiness checker does not run state comparison unit test"
require_text "scripts/check-target-readiness.sh" "test_compare_dual_platform_reader.py" "target readiness checker does not run reader comparison unit test"
require_text "scripts/check-target-readiness.sh" "test_summarize_dual_platform_results.py" "target readiness checker does not run result summary unit test"
require_text "scripts/check-target-readiness.sh" "test_check_reader_output.py" "target readiness checker does not run reader output unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_reader.py" "target readiness checker does not run Host Reader unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_reader_e2e.py" "target readiness checker does not run Host Reader e2e unit test"

require_text "scripts/run-dual-platforms.sh" "verify-dual-target-structure" "dual platform runner does not run the structure check"
require_text "scripts/run-dual-platforms.sh" "export TOOLPREFIX QEMU PYTHON_BIN" "dual platform runner does not export tool variables"
require_text "scripts/run-dual-platforms.sh" "seeded dual-target research platform" "dual platform runner does not run the seeded dual-target platform path"
require_text "scripts/run-dual-platforms.sh" "compare_dual_platform_state.py" "dual platform runner does not compare extracted state files"
require_text "scripts/run-dual-platforms.sh" "rp_orch_timing" "dual platform runner does not require orchestrator timing state"
require_text "scripts/run-dual-platforms.sh" "check_host_platform_alignment.py" "dual platform runner does not check host platform capability runtime output"
require_text "scripts/run-dual-platforms.sh" "check_host_action_kind_alignment.py" "dual platform runner does not check host action kind handling"
require_text "scripts/run-dual-platforms.sh" "check_seeded_action_state.py" "dual platform runner does not check seeded action runtime state"
require_text "scripts/run-dual-platforms.sh" "check_host_surface_alignment.py" "dual platform runner does not check host Web/API/action surface runtime output"
require_text "scripts/run-dual-platforms.sh" "check_host_test_alignment.py" "dual platform runner does not check host platform test themes"
require_text "scripts/run-dual-platforms.sh" "plain-state" "dual platform runner does not pass plain extracted state to host alignment check"
require_text "scripts/run-dual-platforms.sh" "agentos-state" "dual platform runner does not pass AgentOS extracted state to host alignment check"
require_text "scripts/run-dual-platforms.sh" "plain_ucore_reader.py" "dual platform runner does not render Host Reader pages"
require_text "scripts/run-dual-platforms.sh" "check_reader_output.py" "dual platform runner does not validate Host Reader output"
require_text "scripts/run-dual-platforms.sh" "compare_dual_platform_reader.py" "dual platform runner does not compare Host Reader summaries"
require_text "scripts/run-dual-platforms.sh" "stage-timings.csv" "dual platform runner does not write stage timing diagnostics"
require_text "scripts/run-dual-platforms.sh" "summarize_dual_platform_results.py" "dual platform runner does not generate result charts and report"
require_text "scripts/run-dual-platforms.sh" "seeded-action-state.json" "dual platform runner does not pass seeded action state to Host Reader"
require_text "host_tools/plain_ucore_reader.py" "seeded-action-state" "Host Reader does not accept seeded action state input"
require_text "host_tools/plain_ucore_reader.py" "host_seeded_action" "Host Reader does not render seeded action state"
require_text "host_tools/plain_ucore_action_runner.py" 'os.environ.get\("TOOLPREFIX"' "action runner does not read TOOLPREFIX from environment"
require_text "host_tools/plain_ucore_action_runner.py" "run_observed_command" "action runner does not observe QEMU output directly"
require_text "host_tools/plain_ucore_action_runner.py" "qemu_elapsed_seconds" "action runner does not write QEMU timing evidence"
require_text "host_tools/compare_dual_platform_state.py" "verify_orch_timing" "dual platform comparison does not validate per-program timing evidence"
require_text "scripts/run-dual-platforms.sh" "cases=7 executable=7 userland_equivalent=ready" "plain backend marker is missing"
require_text "scripts/run-dual-platforms.sh" "cases=8 executable=8 agentos=mainflow_bound" "AgentOS backend marker is missing"

plain_platform_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" "PLATFORM_TESTS")"
agentos_platform_tests="$(make_var_words "${ROOT_DIR}/agentos_ucore/user/Makefile" "PLATFORM_TESTS")"
plain_seeded_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" "PLATFORM_SEEDED_TESTS")"
agentos_seeded_tests="$(make_var_words "${ROOT_DIR}/agentos_ucore/user/Makefile" "PLATFORM_SEEDED_TESTS")"
if [ "${plain_platform_tests}" != "${agentos_platform_tests}" ]; then
	fail "AgentOS platform build list no longer matches plain platform build list"
fi
if [ "${plain_seeded_tests}" != "${agentos_seeded_tests}" ]; then
	fail "AgentOS seeded platform build list no longer matches plain seeded build list"
fi

plain_platform_count=0
for app in ${plain_platform_tests}; do
	case "${app}" in
	rp_*)
		plain_platform_count=$((plain_platform_count + 1))
		if [ ! -f "${ROOT_DIR}/agentos_ucore/user/src/${app}.c" ]; then
			fail "AgentOS platform is missing source for plain app: ${app}"
		fi
		case " ${agentos_platform_tests} " in
		*" ${app} "*) ;;
		*) fail "AgentOS platform build list does not include plain app: ${app}" ;;
		esac
		;;
	esac
done
if [ "${plain_platform_count}" -lt 60 ]; then
	fail "plain platform program count is unexpectedly small: ${plain_platform_count}"
fi

plain_source_count=0
plain_source_identical_count=0
agentos_adapted_count=0
while IFS= read -r source_path; do
	app="$(basename "${source_path}" .c)"
	source_name="${app}.c"
	agentos_source="${ROOT_DIR}/agentos_ucore/user/src/${source_name}"

	plain_source_count=$((plain_source_count + 1))
	if [ ! -f "${agentos_source}" ]; then
		fail "AgentOS platform is missing mirrored root source: ${source_name}"
	fi
	if same_source_content "${source_path}" "${agentos_source}"; then
		plain_source_identical_count=$((plain_source_identical_count + 1))
	elif is_agentos_adapted_source "${source_name}"; then
		agentos_adapted_count=$((agentos_adapted_count + 1))
	else
		fail "AgentOS platform source differs without being declared adapted: ${source_name}"
	fi
done <<EOF
$(find "${ROOT_DIR}/user/src" -maxdepth 1 -type f -name 'rp_*.c' | sort)
EOF
if [ "${plain_source_count}" -lt "${plain_platform_count}" ]; then
	fail "plain rp source count is smaller than platform build list count: ${plain_source_count} < ${plain_platform_count}"
fi

for source_name in ${agentos_adapted_sources}; do
	if [ ! -f "${ROOT_DIR}/user/src/${source_name}" ]; then
		fail "declared AgentOS adapted source is missing in plain platform: ${source_name}"
	fi
	if [ ! -f "${ROOT_DIR}/agentos_ucore/user/src/${source_name}" ]; then
		fail "declared AgentOS adapted source is missing in AgentOS platform: ${source_name}"
	fi
	if same_source_content "${ROOT_DIR}/user/src/${source_name}" "${ROOT_DIR}/agentos_ucore/user/src/${source_name}"; then
		fail "declared AgentOS adapted source no longer differs from plain source: ${source_name}"
	fi
done

plain_backend_src="${ROOT_DIR}/user/src/rp_backend.c"
agentos_backend_src="${ROOT_DIR}/agentos_ucore/user/src/rp_backend.c"
plain_backend_cases="$(first_number_after_key "${plain_backend_src}" "cases")"
agentos_backend_cases="$(first_number_after_key "${agentos_backend_src}" "cases")"
plain_detail_rows="$(first_number_after_key "${plain_backend_src}" "runner_detail_rows")"
agentos_detail_rows="$(first_number_after_key "${agentos_backend_src}" "runner_detail_rows")"
plain_report_rows="$(first_number_after_key "${plain_backend_src}" "runner_report_rows")"
agentos_report_rows="$(first_number_after_key "${agentos_backend_src}" "runner_report_rows")"

if [ "${agentos_backend_cases}" -lt "${plain_backend_cases}" ]; then
	fail "AgentOS backend has fewer executable cases than plain backend: ${agentos_backend_cases} < ${plain_backend_cases}"
fi
if [ "${agentos_detail_rows}" -lt "${plain_detail_rows}" ]; then
	fail "AgentOS backend has fewer detail rows than plain backend: ${agentos_detail_rows} < ${plain_detail_rows}"
fi
if [ "${agentos_report_rows}" -lt "${plain_report_rows}" ]; then
	fail "AgentOS backend has fewer report rows than plain backend: ${agentos_report_rows} < ${plain_report_rows}"
fi

plain_cost_count=0
while IFS= read -r cost; do
	[ -n "${cost}" ] || continue
	plain_cost_count=$((plain_cost_count + 1))
	if ! grep -q "plain_cost=${cost}" "${agentos_backend_src}"; then
		fail "AgentOS backend does not preserve plain platform cost item: ${cost}"
	fi
done <<EOF
$(grep -o 'plain_cost=[^;"]*' "${plain_backend_src}" | sed 's/plain_cost=//' | sort -u)
EOF
if [ "${plain_cost_count}" -lt 7 ]; then
	fail "plain backend cost item count is unexpectedly small: ${plain_cost_count}"
fi

for marker in \
	"context_trusted=kernel_shadow" \
	"metadata_query=used_index" \
	"agent_event_notify=kernel_queue" \
	"failure_recovery=generic_action" \
	"provenance_audit=kernel_ledger" \
	"permission_control=sentinel_action_denied" \
	"timeline_observe=kernel_snapshot" \
	"edit_lease=kernel_exclusive"
do
	if ! grep -q "${marker}" "${agentos_backend_src}"; then
		fail "AgentOS backend mainflow marker is missing: ${marker}"
	fi
done

require_text "agentos_ucore/os" "SYS_agent_create" "AgentOS syscall table is missing agent_create"
require_text "agentos_ucore/os" "AGENT_CONTEXT" "AgentOS context definitions are missing"
require_text "agentos_ucore/os" "AGENT_TOOL_LLM_REQUEST" "AgentOS LLM request tool is missing"
require_text "agentos_ucore/os" "agent_file_edit_begin" "AgentOS edit lease syscall is missing"
require_text "agentos_ucore/user/Makefile" "agentllm_ucore" "AgentOS LLM test is not in the user build list"
require_text "agentos_ucore/scripts/run-agent-tests.sh" "agentllm_ucore" "AgentOS LLM test is not in the test script"

if grep -R -E -n 'AGENT_TOOL_(RERUN_STAGE|WRITE_REPORT)' \
	"${ROOT_DIR}/agentos_ucore/user/src" 2>/dev/null |
	grep -v 'agentsecurity_ucore.c' >"${TMP_FILE}"; then
	fail "AgentOS platform code uses legacy demo tool ids outside security tests"
fi
: >"${TMP_FILE}"

demo_kernel_pattern='lab-gene-x|RUN-042|nightly-regression|/lab/projects|INC-RUN|PLAN-RUN|MSG-RUN|minimal_rerun|memory_limit|recovery report|rerun completed|stage=(prepare|align|analyze|report|archive)|label=(prepare|align|analyze|report|archive)|source_stage=(prepare|align|analyze|report|archive)|next_stage=(prepare|align|analyze|report|archive)'
reject_text "agentos_ucore/os" "${demo_kernel_pattern}" "AgentOS kernel contains research demo constants"

bad_matrix="矩""阵"
bad_loop="闭""环"
bad_scope="边""界"
bad_regression="回""归"
bad_record_time="记录""时间"
bad_push_cn="推""送"
bad_commit_record="提交""记录"
bad_git_commit="git ""commit"
bad_git_push="git ""push"
bad_glpat="gl""pat-"
bad_oauth2="oauth""2:"
bad_auth="Authorization:"" Basic"
bad_tokens="tokens""\\.txt"
doc_pattern="${bad_matrix}|${bad_loop}|${bad_scope}|${bad_regression}|${bad_record_time}|${bad_glpat}|${bad_oauth2}|${bad_auth}|${bad_tokens}|${bad_git_commit}|${bad_git_push}|${bad_push_cn}|${bad_commit_record}"
reject_text "README.md" "${doc_pattern}" "root README contains forbidden or sensitive wording"
reject_text "docs" "${doc_pattern}" "root docs contain forbidden or sensitive wording"
reject_text "agentos_ucore/README.md" "${doc_pattern}" "AgentOS README contains forbidden or sensitive wording"
reject_text "agentos_ucore/docs" "${doc_pattern}" "AgentOS docs contain forbidden or sensitive wording"

echo "[dual-target-check] plain kernel: clean"
echo "[dual-target-check] plain kernel base: ${PLAIN_BASE_REF}"
echo "[dual-target-check] AgentOS kernel: present"
echo "[dual-target-check] platform source coverage: ${plain_source_count} root rp sources mirrored"
echo "[dual-target-check] platform app coverage: ${plain_platform_count} build-list apps mirrored"
echo "[dual-target-check] platform source sync: identical=${plain_source_identical_count} adapted=${agentos_adapted_count}"
echo "[dual-target-check] backend evidence coverage: plain=${plain_backend_cases} agentos=${agentos_backend_cases} preserved_costs=${plain_cost_count}"
echo "[dual-target-check] platform runners: present"
echo "[dual-target-check] AgentOS kernel demo constants: absent"
echo "[dual-target-check] AgentOS platform legacy tools: security tests only"
echo "[dual-target-check] docs: wording scan passed"
