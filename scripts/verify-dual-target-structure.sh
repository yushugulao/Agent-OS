#!/usr/bin/env bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_FILE="${TMPDIR:-/tmp}/agentos-dual-target-check.$$"
PYTHON_BIN="${PYTHON_BIN:-python3}"

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

	if ! grep -R -E -n -- "${pattern}" "${ROOT_DIR}/${path}" >"${TMP_FILE}" 2>/dev/null; then
		fail "${message}: ${path}"
	fi
	: >"${TMP_FILE}"
}

reject_text() {
	local path="$1"
	local pattern="$2"
	local message="$3"

	if grep -R -E -n -- "${pattern}" "${ROOT_DIR}/${path}" >"${TMP_FILE}" 2>/dev/null; then
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
rp_seed_orch.c
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

require_path "baseline_ucore/os" "baseline kernel directory is missing"
require_path ".gitlab-ci.yml" "GitLab CI budget pipeline is missing"
require_path "os/kernel_work.c" "AgentOS kernel work budget module is missing"
require_path "os/kernel_work.h" "AgentOS kernel work budget API is missing"
require_path "baseline_ucore/os/kernel_work.c" "baseline kernel work budget module is missing"
require_path "baseline_ucore/os/kernel_work.h" "baseline kernel work budget API is missing"
require_path "user/src/syscallfair_ucore.c" "AgentOS syscall fairness guest is missing"
require_path "baseline_ucore/user/src/syscallfair_ucore.c" "baseline syscall fairness guest is missing"
require_path "user/src/fileresource_ucore.c" "AgentOS file resource guest is missing"
require_path "baseline_ucore/user/src/fileresource_ucore.c" "baseline file resource guest is missing"
require_path "user/src/fspquota_ucore.c" "AgentOS persistent quota guest is missing"
require_path "user/src/workflow_teardown_race_ucore.c" "workflow teardown race guest is missing"
require_path "baseline_ucore/user/src/fspquota_ucore.c" "baseline persistent quota guest is missing"
require_path "baseline_ucore/user/src/rp_orch.c" "baseline platform orchestrator is missing"
require_path "baseline_ucore/user/src/rp_backend.c" "baseline platform backend is missing"
require_path "os/agent.c" "AgentOS kernel module is missing"
require_path "user/src/rp_agentos_orch.c" "AgentOS platform orchestrator is missing"
require_path "user/src/agentconflict_ucore.c" "AgentOS edit lease test is missing"
require_path "user/src/agentllm_ucore.c" "AgentOS LLM relay test is missing"
require_path "user/src/agenttoolabi_ucore.c" "AgentOS tool ABI test is missing"
require_path "host_tools/check_host_platform_alignment.py" "host platform alignment checker is missing"
require_path "host_tools/check_host_action_kind_alignment.py" "host action kind alignment checker is missing"
require_path "host_tools/check_seeded_action_state.py" "seeded action state checker is missing"
require_path "host_tools/check_host_surface_alignment.py" "host Web/API/action surface alignment checker is missing"
require_path "host_tools/check_host_test_alignment.py" "host test alignment checker is missing"
require_path "host_tools/summarize_dual_platform_results.py" "dual platform result summarizer is missing"
require_path "host_tools/test_summarize_dual_platform_results.py" "dual platform result summarizer test is missing"
require_path "host_tools/benchmark_source_contract.py" "measured benchmark source contract is missing"
require_path "host_tools/test_measured_experiments.py" "measured benchmark mutation tests are missing"
require_path "host_tools/test_dual_measurement_source_contract.py" "runner-owned measurement mutation tests are missing"
require_path "host_tools/result_bundle_contract.py" "served result bundle contract is missing"
require_path "host_tools/test_result_bundle_contract.py" "served result bundle mutation tests are missing"
require_path "host_tools/backend_evidence_contract.py" "shared backend evidence contract is missing"
require_path "host_tools/test_backend_evidence_contract.py" "backend evidence contract regressions are missing"
require_path "host_tools/reference_catalog_contract.py" "reference catalog source contract is missing"
require_path "host_tools/test_reference_catalog_contract.py" "reference catalog mutation tests are missing"
require_path "host_tools/gitlab_ci_contract.py" "GitLab CI effective-job resolver is missing"
require_path "host_tools/test_gitlab_ci_contract.py" "GitLab CI resolver mutation tests are missing"
require_path "host_tools/remote_ci_evidence.py" "GitLab CI execution attester is missing"
require_path "host_tools/remote_ci_archive.py" "GitLab CI archive verifier is missing"
require_path "host_tools/remote_ci_job_semantics.py" "GitLab CI semantic adapter is missing"
require_path "host_tools/remote_ci_bundle.py" "GitLab CI bundle bridge is missing"
require_path "host_tools/test_remote_ci_evidence.py" "GitLab CI attestation mutations are missing"
require_path "ci/research-state-manifest.json" "shared research state manifest is missing"
require_path "host_tools/__init__.py" "host tools package marker is missing"
require_path "host_tools/research_state_manifest.py" "research state manifest resolver is missing"
require_path "host_tools/test_research_state_manifest.py" "research state manifest mutations are missing"
require_path "host_tools/test_llm_relay_mode_contract.py" "LLM relay mode contract test is missing"
require_path "host_tools/test_chart_svg_layout_contract.py" "chart SVG layout contract test is missing"
require_path "scripts/check-target-readiness.sh" "target readiness checker is missing"
require_path "scripts/check-dependencies.sh" "Linux dependency checker is missing"
require_path "scripts/check-windows-prereqs.ps1" "Windows dependency checker is missing"
require_path "scripts/install-ubuntu-deps.sh" "Ubuntu dependency installer is missing"
require_path "scripts/run-dual-platforms.sh" "dual target runner is missing"
require_path "scripts/run-full-verification.sh" "full verification runner is missing"
require_path "scripts/run-ci-mechanism.sh" "CI mechanism evidence wrapper is missing"
require_path "scripts/run-physical-resource-tests.sh" "physical resource runner is missing"
require_path "scripts/run-metadata-recovery-tests.sh" "metadata recovery runner is missing"
require_path "scripts/run-observe-recovery-tests.sh" "observation recovery runner is missing"
require_path "scripts/run-virtio-disk-tests.sh" "VirtIO disk runner is missing"
require_path "scripts/check-wait-queue-contract.py" "wait queue API contract is missing"
require_path "scripts/test-wait-atomic-wiring.py" "atomic wait mutation contract is missing"
require_text "scripts/validate-kernel-test-log.py" "WAIT_ATOMIC_MARKERS" "atomic wait log profile is missing"
require_text "Makefile" "scripts/test-wait-atomic-wiring.py" "ordinary CI omits atomic wait mutations"
require_path "scripts/check-agent-module-boundaries.sh" "AgentOS module boundary checker is missing"
require_path "scripts/check-kernel-budgets.py" "kernel budget checker is missing"
require_path "scripts/test-check-kernel-budgets.py" "kernel budget checker tests are missing"
require_path "scripts/agent_test_runner.py" "Agent test output runner is missing"
require_path "scripts/test-agent-test-runner.py" "Agent test output runner tests are missing"
require_path "scripts/validate-kernel-test-log.py" "specialized kernel log validator is missing"
require_path "scripts/test-validate-kernel-test-log.py" "specialized kernel log validator tests are missing"
require_path "scripts/validate-metadata-crash-log.py" "metadata crash log validator is missing"
require_path "scripts/test-validate-metadata-crash-log.py" "metadata crash log validator tests are missing"
require_path "scripts/probes/struct-proc-size.c" "struct proc budget probe is missing"
require_path "scripts/probes/agent-metadata-disk-layout.c" "metadata disk ABI probe is missing"
require_path "ci/kernel-budgets.json" "machine-readable kernel budgets are missing"
require_path "ci/agent-metadata-disk-format.json" "metadata disk ABI contract is missing"
require_path "agent_metadata_disk_abi.h" "shared metadata disk ABI is missing"
require_path "os/agent_context.c" "Agent context subsystem is missing"
require_path "os/agent_metadata_objects.c" "Agent metadata object subsystem is missing"
require_path "os/agent_metadata_directory.c" "Agent metadata directory subsystem is missing"
require_path "os/agent_metadata_directory.h" "Agent metadata directory contract is missing"
require_path "os/agent_metadata_disk.h" "Agent metadata disk contract is missing"
require_path "os/agent_metadata_store.c" "Agent metadata store subsystem is missing"
require_path "host_tools/agent_metadata_disk_format.py" "metadata raw-bank validator is missing"
require_path "scripts/check-agent-metadata-disk-format.py" "metadata disk ABI checker is missing"
require_path "scripts/test-agent-metadata-disk-format.py" "metadata raw-bank parser tests are missing"
require_path "scripts/run-syscall-fairness-tests.sh" "syscall fairness runner is missing"
require_path "scripts/run-file-resource-tests.sh" "file resource runner is missing"
require_path "scripts/run-fs-enospc-tests.sh" "filesystem ENOSPC runner is missing"
require_path "scripts/run-proc-reap-tests.sh" "process reaper runner is missing"
require_path "scripts/run-thread-resource-tests.sh" "thread resource runner is missing"
require_path "scripts/run-workflow-teardown-race-tests.sh" "workflow teardown race runner is missing"
require_path "scripts/evidence-wiring.sh" "final evidence runner wiring is missing"
require_path "scripts/capture-final-evidence.py" "final evidence collector is missing"
require_path "host_tools/evidence_toolchain_attestation.py" "final evidence tool attestation module is missing"
require_path "host_tools/test_capture_final_evidence.py" "final evidence selftest is missing"
require_path "host_tools/evidence_delivery_contract.py" "final evidence delivery contract is missing"
require_path "host_tools/test_evidence_delivery_contract.py" "final evidence delivery mutations are missing"
require_path "evidence/README.md" "final evidence documentation is missing"
require_path "scripts/serve-reader.sh" "reader server script is missing"
require_path "docs/windows-quickstart.md" "Windows quickstart document is missing"

bash "${ROOT_DIR}/scripts/check-agent-module-boundaries.sh"

plain_kernel_pattern='SYS_agent_|AGENT_CONTEXT|AGENT_TOOL_|AGENT_CAP_|agent_create|agent_run|context_snapshot|agent_file_|agent_wait|agent_heartbeat|\.agentmeta'
reject_text "baseline_ucore/os" "${plain_kernel_pattern}" "baseline kernel contains AgentOS-specific symbols"
reject_text "baseline_ucore/user/include" "${plain_kernel_pattern}" "baseline user ABI contains AgentOS-specific symbols"
reject_text "baseline_ucore/user/lib" "${plain_kernel_pattern}" "baseline syscall wrappers contain AgentOS-specific symbols"

require_text "Makefile" "^plain-platform-run:" "plain platform run target is missing"
require_text "Makefile" "^agentos-platform-run:" "AgentOS platform run target is missing"
require_text "Makefile" "^dual-platform-run:" "dual platform run target is missing"
require_text "Makefile" "^reader:" "reader target is missing"
require_text "Makefile" "^target-readiness:" "target readiness target is missing"
require_text "Makefile" "^full-verify:" "full verification target is missing"
require_text "Makefile" "^workflow-teardown-race-test:" "workflow teardown race target is missing"
require_text "Makefile" "^evidence-capture-selftest:" "evidence collector selftest target is missing"
require_text "Makefile" "^ci-check:.*evidence-capture-selftest" "ci-check omits the evidence collector selftest"
require_text "Makefile" "^doctor:" "dependency doctor target is missing"
require_text "Makefile" "INIT_PROC=rp_orch CHAPTER=platform" "plain platform run target does not launch rp_orch"
require_text "Makefile" "INIT_PROC=rp_agentos_orch CHAPTER=platform_agentos" "AgentOS platform run target does not launch rp_agentos_orch"
require_text "Makefile" "scripts/run-dual-platforms.sh" "Makefile dual platform target does not call the dual runner"
require_text "Makefile" "scripts/serve-reader.sh" "Makefile reader target does not call the reader server"
require_text "Makefile" "scripts/check-target-readiness.sh" "Makefile target readiness target does not call the readiness checker"
require_text "Makefile" "scripts/run-full-verification.sh" "Makefile full verification target does not call the full runner"
require_text "Makefile" "kernel-budget-check" "Makefile kernel budget target is missing"
require_text "Makefile" "agent-module-check" "Makefile Agent module budget target is missing"
require_text "Makefile" "check agent-modules" "Makefile does not enforce Agent module budgets"
require_text "Makefile" "scripts/test-check-kernel-budgets.py" "Makefile kernel budget self-test is missing"
require_text "Makefile" "scripts/test-agent-test-runner.py" "Makefile Agent output runner self-test is missing"
require_text "Makefile" "scripts/test-validate-kernel-test-log.py" "Makefile specialized log validator self-test is missing"
require_text "Makefile" "scripts/test-validate-metadata-crash-log.py" "Makefile metadata crash validator self-test is missing"
require_text "scripts/run-metadata-recovery-tests.sh" "scripts/validate-metadata-crash-log.py" \
	"metadata recovery runner omits the explicit crash-target contract"
require_text "scripts/run-metadata-recovery-tests.sh" "require_crash_hook_absent" \
	"metadata recovery runner does not prove test-hook isolation"
require_text "scripts/run-metadata-recovery-tests.sh" \
	'--image[[:space:]]+"\$\{image\}"[[:space:]]+--stage[[:space:]]+genesis' \
	"metadata recovery runner does not validate the mkfs genesis image"
require_text "Makefile" "scripts/test-agent-metadata-disk-format.py" \
	"ordinary CI omits metadata genesis mutation tests"
require_text "ci/kernel-budgets.json" '"agent_metadata_disk_abi[.]h"' \
	"metadata aggregate budget omits the shared disk ABI"
if ! "${PYTHON_BIN}" "${ROOT_DIR}/host_tools/gitlab_ci_contract.py" verify \
	--path "${ROOT_DIR}/.gitlab-ci.yml" \
	--budget-config "${ROOT_DIR}/ci/kernel-budgets.json" >"${TMP_FILE}" 2>&1; then
	fail "GitLab CI effective-job contract failed"
fi
: >"${TMP_FILE}"
if ! "${PYTHON_BIN}" "${ROOT_DIR}/host_tools/research_state_manifest.py" \
	>"${TMP_FILE}" 2>&1; then
	fail "research state manifest contract failed"
fi
: >"${TMP_FILE}"
require_text "ci/kernel-budgets.json" '"agent_modules"' "Agent module budgets are missing"
require_text "ci/kernel-budgets.json" '"agent_context_sidecar"' "Agent sidecar budgets are missing"
require_text "ci/kernel-budgets.json" '"boot_stack_start_symbol"' "boot stack budget is missing"
require_text "ci/kernel-budgets.json" '"calibration_status"' "Agent duration calibration state is missing"
require_text "scripts/check-kernel-budgets.py" '"provisional_requires_full_suite"' "checker does not recognize the fail-closed calibration state"
require_text "scripts/check-kernel-budgets.py" '"calibrated_full_suite"' "checker does not recognize the reviewed calibration state"
require_text "host_tools/test_capture_final_evidence.py" \
	'test_agent_duration_policy_fails_before_build_with_bounded_exceptions' \
	"Agent duration mode behavior lacks a regression contract"
require_text "Makefile" \
	'^[[:space:]]*@\$\(PYTHON_CMD\)[[:space:]]+host_tools/test_capture_final_evidence\.py$' \
	"Agent duration mode regression is not executed by its self-test target"
require_text "Makefile" '^ci-check:.*evidence-capture-selftest' \
	"Agent duration mode regression is not wired into ci-check"
require_text "scripts/run-agent-tests.sh" '--check[[:space:]]+agent-test-policy' \
	"full Agent suite does not reject provisional duration policy before QEMU"
require_text "scripts/run-full-verification.sh" '--check[[:space:]]+agent-test-policy' \
	"full verification does not reject provisional duration policy before executing its profile"
require_text "scripts/check-kernel-budgets.py" "invalid_global_object_exports" "Agent writable export gate is missing"
require_text "Makefile" "scripts/run-syscall-fairness-tests.sh" "Makefile syscall fairness target does not call its runner"
require_text "Makefile" "scripts/run-file-resource-tests.sh" "Makefile file resource target does not call its runner"
require_text "Makefile" "scripts/run-workflow-teardown-race-tests.sh" "Makefile workflow teardown target does not call its runner"
require_text "scripts/run-full-verification.sh" "run-fs-enospc-tests.sh" "full verification omits filesystem ENOSPC regression"
require_text "Makefile" "scripts/check-dependencies.sh" "Makefile doctor target does not call dependency checker"

for specialized_runner in \
	scripts/run-proc-reap-tests.sh \
	scripts/run-thread-resource-tests.sh \
	scripts/run-file-resource-tests.sh \
	scripts/run-workflow-teardown-race-tests.sh \
	scripts/run-syscall-fairness-tests.sh \
	scripts/run-fs-enospc-tests.sh
do
	require_text "${specialized_runner}" "scripts/agent_test_runner.py" \
		"${specialized_runner} bypasses the shared fail-closed QEMU runner"
	require_text "${specialized_runner}" "scripts/validate-kernel-test-log.py" \
		"${specialized_runner} bypasses the full-log profile validator"
	require_text "${specialized_runner}" "evidence_append_guest_log" \
		"${specialized_runner} does not preserve validated Guest evidence"
done

require_text "scripts/run-agent-tests.sh" \
	'^[[:space:]]*if[[:space:]]+"\$\{PYTHON_BIN\}"[[:space:]]+-I[[:space:]]+-S[[:space:]]+-B[[:space:]]+scripts/agent_test_runner\.py[[:space:]]+\\$' \
	"Agent regression runner bypasses the shared fail-closed QEMU runner"
require_text "scripts/run-agent-tests.sh" "AGENT_TEST_GUEST_LOG_FILE" \
	"Agent regression runner does not preserve per-case Guest evidence"
for natural_runner in \
	scripts/run-agent-tests.sh \
	scripts/run-proc-reap-tests.sh \
	scripts/run-thread-resource-tests.sh \
	scripts/run-file-resource-tests.sh \
	scripts/run-workflow-teardown-race-tests.sh \
	scripts/run-syscall-fairness-tests.sh
do
	reject_text "${natural_runner}" "completion-mode" \
		"${natural_runner} must require natural process exit"
done
require_text "scripts/run-fs-enospc-tests.sh" \
	'^[[:space:]]*orphan-crash[[:space:]]+\|[[:space:]]+persistent-seed\)$' \
	"filesystem checkpoint profiles are not explicitly bounded"
require_text "scripts/run-fs-enospc-tests.sh" \
	'^[[:space:]]*completion_args\+=\(--completion-mode checkpoint\)$' \
	"filesystem checkpoint profiles do not select checkpoint completion"
require_text "scripts/run-fs-enospc-tests.sh" \
	'^[[:space:]]*"\$\{completion_args\[@\]\}";[[:space:]]*then$' \
	"filesystem completion policy is not passed to the shared runner"
fs_completion_mode_count="$(
	grep -c -- '--completion-mode' \
		"${ROOT_DIR}/scripts/run-fs-enospc-tests.sh"
)"
if [ "${fs_completion_mode_count}" -ne 1 ]; then
	fail "filesystem runner must contain exactly one bounded checkpoint mapping"
fi

require_text "Makefile" "^QEMU \\?= qemu-system-riscv64" "plain Makefile QEMU is not environment-overridable"
require_text "Makefile" "^QEMU \\?= qemu-system-riscv64" "AgentOS Makefile QEMU is not environment-overridable"
require_text "Makefile" '^run: build/kernel \$\(F\)/fs-copy\.img$' "AgentOS fresh run does not rebuild the writable image"
require_text "Makefile" '^\$\(F\)/fs\.img: user \.FORCE$' "AgentOS fresh image does not rebuild userspace"
require_text "Makefile" '^run-persist: build/kernel$' "AgentOS persistent reboot depends on the fresh image"
require_text "Makefile" 'if \[ ! -f "\$\(F\)/fs-copy\.img" \]' "AgentOS persistent reboot cannot initialize a missing disk"
require_text "baseline_ucore/Makefile" '^run: build/kernel \$\(F\)/fs-copy\.img$' "baseline fresh run does not rebuild the writable image"
require_text "baseline_ucore/Makefile" '^\$\(F\)/fs\.img: user \.FORCE$' "baseline fresh image does not rebuild userspace"
require_text "baseline_ucore/Makefile" '^run-persist: build/kernel$' "baseline persistent reboot depends on the fresh image"
require_text "baseline_ucore/Makefile" 'if \[ ! -f "\$\(F\)/fs-copy\.img" \]' "baseline persistent reboot cannot initialize a missing disk"

require_text "baseline_ucore/user/Makefile" "platform_plain" "baseline platform chapter is not declared"
require_text "user/Makefile" "FILE_RESOURCE_TESTS.*fileresource_ucore" "AgentOS file resource chapter omits its guest"
require_text "user/Makefile" "WORKFLOW_TEARDOWN_TESTS.*workflow_teardown_race_ucore" "AgentOS workflow teardown chapter omits its guest"
require_text "baseline_ucore/user/Makefile" "FILE_RESOURCE_TESTS.*fileresource_ucore" "baseline file resource chapter omits its guest"
require_text "user/Makefile" "FS_ENOSPC_TESTS.*fspquota_ucore" "AgentOS fs test chapter omits persistent quota guest"
require_text "baseline_ucore/user/Makefile" "FS_ENOSPC_TESTS.*fspquota_ucore" "baseline fs test chapter omits persistent quota guest"
if ! grep -A1 -F 'X("fspquota_ucore"' \
	"${ROOT_DIR}/user/include/exec_policy_manifest.h" | \
	grep -q "EXEC_MANIFEST_F_SEALED"; then
	fail "persistent quota guest is not sealed"
fi
if ! grep -A1 -F 'X("fspquota_ucore"' \
	"${ROOT_DIR}/user/include/exec_policy_manifest.h" | \
	grep -q "EXEC_MANIFEST_VFS_PROFILE_NONE"; then
	fail "persistent quota guest must install as stable PUBLIC"
fi
require_text "scripts/run-fs-enospc-tests.sh" "principal-agent-seed" "fs runner omits AgentOS persistent quota seed boot"
require_text "scripts/run-fs-enospc-tests.sh" "principal-agent-verify" "fs runner omits AgentOS persistent quota verify boot"
require_text "scripts/run-fs-enospc-tests.sh" "principal-baseline-seed" "fs runner omits baseline persistent quota seed boot"
require_text "scripts/run-fs-enospc-tests.sh" "principal-baseline-verify" "fs runner omits baseline persistent quota verify boot"
require_text "scripts/run-fs-enospc-tests.sh" "principal-agent-orphan" "fs runner omits AgentOS crash-orphan boot"
require_text "scripts/run-fs-enospc-tests.sh" "principal-baseline-orphan" "fs runner omits baseline crash-orphan boot"
require_text "scripts/run-fs-enospc-tests.sh" "crash_orphan_ready=1" "fs runner omits crash-orphan checkpoint"
require_text "scripts/run-fs-enospc-tests.sh" "FS_PERSIST_BLOCK_LIMIT=18" "persistent quota block contract is not fixed at eighteen"
require_text "scripts/run-fs-enospc-tests.sh" "FS_PERSIST_INODE_LIMIT=8" "persistent quota inode contract is not fixed at eight"
require_text "scripts/validate-kernel-test-log.py" "sponsored_object_charged=1 blocks=" "fs runner omits sponsored PUBLIC ownership transfer contract"
require_text "scripts/run-fs-enospc-tests.sh" "durable_fixture=1 blocks=18 inodes=8 owner_exited=1" "fs runner omits durable quota seed contract"
require_text "scripts/validate-kernel-test-log.py" "reboot_charge_persisted=1" "fs runner omits reboot quota accounting contract"
require_text "scripts/validate-kernel-test-log.py" "deletion_reuse=1" "fs runner omits persistent quota release contract"
require_text "scripts/validate-kernel-test-log.py" "relaunch_charge_persisted=1 launches=2" "fs runner omits repeated relaunch quota contract"
require_text "scripts/validate-kernel-test-log.py" "cleanup_reuse=1" "fs runner omits persistent quota cleanup contract"
require_text "scripts/run-full-verification.sh" "verify-dual-target-structure" "full verification does not run the structure check"
require_text "scripts/run-full-verification.sh" "make ci-check" "full verification does not enforce kernel budgets"
require_text "scripts/run-agent-tests.sh" "check_suite_budget" "Agent test suite has no total duration budget"
require_text "Makefile" '^ci-check: host-contract-selftest ' "ci-check bypasses the shared Host contract suite"
require_text "Makefile" '^override HOST_CONTRACT_TESTS :=' "Host contract inventory can be overridden"
require_text "Makefile" '^host-contract-selftest: \$\(HOST_CONTRACT_TESTS\)' "Host contract target is not inventory-bound"
require_text "Makefile" 'for test in \$\(HOST_CONTRACT_TESTS\)' "Host contract target does not execute its inventory"
require_text "Makefile" '^override KERNEL_BUDGET_TOOLPREFIX = \$\(TOOLPREFIX\)$' \
	"kernel budgets do not use the selected compiler toolchain"
require_text "Makefile" '^override KERNEL_BUDGET_PYTHON = \$\(PYTHON_BIN\)$' \
	"kernel budgets do not use the selected Python interpreter"
require_text "Makefile" '^override PY = \$\(PYTHON_BIN\)$' \
	"kernel build helpers do not use the selected Python interpreter"
for host_contract_test in \
	test_check_host_platform_alignment test_check_host_action_kind_alignment \
	test_check_seeded_action_state test_check_host_surface_alignment \
	test_check_host_test_alignment test_gitlab_ci_contract test_remote_ci_evidence \
	test_plain_ucore_action_runner test_research_state_manifest \
	test_plain_ucore_fs_extract test_plain_ucore_llm_relay \
	test_llm_relay_mode_contract test_check_reader_output \
	test_compare_dual_platform_reader test_compare_dual_platform_state \
	test_backend_evidence_contract test_reference_catalog_contract \
	test_measured_experiments test_dual_measurement_source_contract \
	test_summarize_dual_platform_results test_result_bundle_contract \
	test_chart_type_data_contract test_chart_svg_layout_contract \
	test_plain_ucore_reader; do
	require_text "Makefile" "host_tools/${host_contract_test}[.]py" \
		"shared Host contract suite omits ${host_contract_test}"
done
require_text "scripts/run-full-verification.sh" "test_plain_ucore_reader_e2e.py" "full verification does not run 本地结果阅读器 e2e test"
require_text "scripts/run-full-verification.sh" "run-dual-platforms.sh" "full verification does not run dual platform QEMU"
require_text "scripts/run-full-verification.sh" 'QEMU="\$\{QEMU\}"' "full verification does not pass QEMU to child runners"
require_text "scripts/run-full-verification.sh" "run-agent-tests.sh" "full verification does not run AgentOS kernel tests"
require_text "scripts/run-full-verification.sh" "run-syscall-fairness-tests.sh" "full verification does not run syscall fairness tests"
require_text "scripts/run-full-verification.sh" "run-file-resource-tests.sh" "full verification does not run file resource tests"
require_text "scripts/run-full-verification.sh" "run-physical-resource-tests.sh" "full verification does not run physical resource tests"
require_text "scripts/run-full-verification.sh" "run-metadata-recovery-tests.sh" "full verification does not run metadata recovery tests"
require_text "scripts/run-full-verification.sh" "run-observe-recovery-tests.sh" "full verification does not run observation recovery tests"
require_text "scripts/run-full-verification.sh" "run-virtio-disk-tests.sh" "full verification does not run VirtIO disk tests"
require_text "scripts/run-full-verification.sh" "run-workflow-teardown-race-tests.sh" "full verification does not run workflow teardown race tests"
require_text "scripts/run-full-verification.sh" "evidence_initialize" "full verification does not initialize collector-owned evidence"
require_text "scripts/run-full-verification.sh" "evidence_step_end" \
	"full verification does not derive summary steps from actual orchestration"
require_text "scripts/run-full-verification.sh" "write-summary" \
	"full verification does not atomically publish its public summary"
require_text "scripts/evidence-wiring.sh" 'pipeline_status=.*PIPESTATUS' \
	"evidence tee status is not captured fail-closed"
require_text "scripts/capture-final-evidence.py" '^SCHEMA_VERSION = 6$' \
	"evidence summary schema version is not stable"
require_text "scripts/capture-final-evidence.py" '^FULL_VERIFY_PROFILE_VERSION = 5$' \
	"evidence full-verify profile version is not stable"
require_text "scripts/check-kernel-budgets.py" 'agent-modules checks begin' \
	"Agent module budget output lacks a strict begin boundary"
require_text "scripts/capture-final-evidence.py" '^REMOTE_CI_SCHEMA_VERSION = 1$' \
	"evidence remote CI provenance schema version is not stable"
require_text "scripts/capture-final-evidence.py" 'SUMMARY_NAME = "verification-summary.json"' \
	"evidence collector does not expose verification-summary.json"
require_text "scripts/capture-final-evidence.py" 'commands.add_parser\("write-summary"\)' \
	"evidence collector lacks the summary writer interface"
require_text ".gitignore" '^!evidence/releases/\*\*$' \
	"final evidence releases cannot be tracked without git add -f"
require_text "scripts/run-full-verification.sh" 'MECHANISM_MARKER_GRACE_SECONDS=.*5s' \
	"full verification does not reserve a 5s mechanism fault window"
for mechanism_runner in \
	scripts/run-proc-reap-tests.sh \
	scripts/run-syscall-fairness-tests.sh \
	scripts/run-file-resource-tests.sh \
	scripts/run-thread-resource-tests.sh \
	scripts/run-physical-resource-tests.sh \
	scripts/run-metadata-recovery-tests.sh \
	scripts/run-observe-recovery-tests.sh \
	scripts/run-virtio-disk-tests.sh \
	scripts/run-workflow-teardown-race-tests.sh \
	scripts/run-fs-enospc-tests.sh \
	scripts/run-fs-allocator-fault-tests.sh
do
	require_text "${mechanism_runner}" 'MARKER_GRACE_SECONDS=.*:-5s' \
		"${mechanism_runner} does not default to a 5s fault window"
done
for dynamic_runner in \
	scripts/run-physical-resource-tests.sh \
	scripts/run-metadata-recovery-tests.sh \
	scripts/run-observe-recovery-tests.sh \
	scripts/run-virtio-disk-tests.sh \
	scripts/run-fs-allocator-fault-tests.sh
do
	require_text "${dynamic_runner}" "scripts/agent_test_runner.py" \
		"mechanism runner does not execute a Guest marker contract: ${dynamic_runner}"
	require_text "${dynamic_runner}" "evidence_append_guest_log" \
		"mechanism runner does not retain real Guest output: ${dynamic_runner}"
	require_text "${dynamic_runner}" "runner_status" \
		"mechanism runner can exit before preserving a failing Guest log: ${dynamic_runner}"
	require_text "${dynamic_runner}" "append_status" \
		"mechanism runner does not fail closed when Guest-log preservation fails: ${dynamic_runner}"
done
require_text "scripts/run-full-verification.sh" "fs-allocator-evidence.tar" \
	"full verification does not publish allocator raw-image evidence"
reject_text "scripts/run-fs-allocator-fault-tests.sh" 'evidence_publish_file|FINAL_EVIDENCE_STAGE' \
	"allocator runner must not publish into the final evidence stage"
require_text "scripts/capture-final-evidence.py" "validate_fs_allocator_archive" \
	"final evidence collector does not semantically verify allocator evidence"
require_text ".gitlab-ci.yml" 'FS_ALLOCATOR_EVIDENCE_ARCHIVE=\$\{CI_PROJECT_DIR\}/ci-artifacts/fs-allocator-evidence.tar' \
	"allocator CI job does not retain the canonical evidence archive"
require_text ".gitlab-ci.yml" 'fs-allocator-evidence.py verify-archive --archive "\$\{CI_PROJECT_DIR\}/ci-artifacts/fs-allocator-evidence.tar"' \
	"allocator CI job does not verify the exact archived artifact"
require_text "scripts/test-fs-allocator-evidence.py" "test_archive_rejects_noncanonical_bytes" \
	"allocator archive contract lacks canonical-byte mutation coverage"
require_text "scripts/run-ci-mechanism.sh" "runner-stdout" \
	"CI mechanism wrapper omits runner stdout"
require_text "scripts/run-ci-mechanism.sh" "runner-guest-logs" \
	"CI mechanism wrapper omits Guest logs"

for header in os/kernel_work.h baseline_ucore/os/kernel_work.h; do
	for contract in \
		'^#define KERNEL_WORK_STREAM_GRANULE 64U$' \
		'^#define KERNEL_WORK_BUDGET_UNITS 1024U$' \
		'^#define KERNEL_WORK_OPERATION_UNITS 256U$' \
		'^#define KERNEL_WORK_PAGE_UNITS 64U$' \
		'^void kernel_work_reset\(struct thread \*\);$' \
		'^void kernel_work_on_dispatch\(struct thread \*\);$' \
		'^void kernel_work_begin\(void\);$' \
		'^void kernel_work_end\(void\);$' \
		'^void kernel_work_request_resched\(void\);$' \
		'^int kernel_work_checkpoint\(uint work_units\);$' \
		'^int kernel_work_checkpoint_cleanup\(uint work_units\);$'
	do
		require_text "${header}" "${contract}" \
			"kernel work public budget contract drifted"
	done
done
for source in os/kernel_work.c baseline_ucore/os/kernel_work.c; do
	for contract in \
		'^#define KERNEL_WORK_QUANTUM_CYCLES \(CPU_FREQ / TICKS_PER_SEC\)$' \
		'kernel_slice_deadline = get_cycle\(\) \+ KERNEL_WORK_QUANTUM_CYCLES;' \
		'kernel_work_resumed = t->kernel_work_depth != 0;' \
		'work_units >= KERNEL_WORK_BUDGET_UNITS - t->kernel_work_units' \
		'proc_thread_exit_requested\(\)' \
		'kernel_work_checkpoint_mode\(work_units, 1\)' \
		'yield\(\);'
	do
		require_text "${source}" "${contract}" \
			"kernel work fairness protocol drifted"
	done
done
if ! cmp -s "${ROOT_DIR}/user/src/syscallfair_ucore.c" \
	"${ROOT_DIR}/baseline_ucore/user/src/syscallfair_ucore.c"; then
	fail "dual targets do not share the same syscall fairness guest"
fi
if ! cmp -s "${ROOT_DIR}/user/src/fileresource_ucore.c" \
	"${ROOT_DIR}/baseline_ucore/user/src/fileresource_ucore.c"; then
	fail "dual targets do not share the same file resource guest"
fi
if ! same_source_content "${ROOT_DIR}/user/src/fspquota_ucore.c" \
	"${ROOT_DIR}/baseline_ucore/user/src/fspquota_ucore.c"; then
	fail "dual targets do not share the same persistent quota guest"
fi

require_text "scripts/check-target-readiness.sh" "verify-dual-target-structure" "target readiness checker does not run the structure check"
require_text "scripts/check-target-readiness.sh" "test_check_host_platform_alignment.py" "target readiness checker does not run host platform alignment unit test"
require_text "scripts/check-target-readiness.sh" "test_check_host_action_kind_alignment.py" "target readiness checker does not run host action kind unit test"
require_text "scripts/check-target-readiness.sh" "test_check_seeded_action_state.py" "target readiness checker does not run seeded action unit test"
require_text "scripts/check-target-readiness.sh" "test_check_host_surface_alignment.py" "target readiness checker does not run host surface unit test"
require_text "scripts/check-target-readiness.sh" "test_check_host_test_alignment.py" "target readiness checker does not run host test unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_action_runner.py" "target readiness checker does not run action runner unit test"
require_text "scripts/check-target-readiness.sh" "test_research_state_manifest.py" "target readiness checker does not run research state manifest test"
require_text "scripts/check-target-readiness.sh" "unittest discover" "target readiness checker does not exercise package import discovery"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_fs_extract.py" "target readiness checker does not run fs extraction unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_llm_relay.py" "target readiness checker does not run LLM relay unit test"
require_text "scripts/check-target-readiness.sh" "test_llm_relay_mode_contract.py" "target readiness checker does not run LLM relay mode contract test"
require_text "scripts/check-target-readiness.sh" "test_compare_dual_platform_state.py" "target readiness checker does not run state comparison unit test"
require_text "scripts/check-target-readiness.sh" "test_compare_dual_platform_reader.py" "target readiness checker does not run reader comparison unit test"
require_text "scripts/check-target-readiness.sh" "test_measured_experiments.py" "target readiness checker does not run measured experiment unit test"
require_text "scripts/check-target-readiness.sh" "test_backend_evidence_contract.py" "target readiness checker does not run backend evidence contract test"
require_text "scripts/check-target-readiness.sh" "test_reference_catalog_contract.py" "target readiness checker does not run reference catalog mutation tests"
require_text "scripts/check-target-readiness.sh" "test_gitlab_ci_contract.py" "target readiness checker does not run GitLab CI resolver tests"
require_text "scripts/check-target-readiness.sh" "test_summarize_dual_platform_results.py" "target readiness checker does not run result summary unit test"
require_text "scripts/check-target-readiness.sh" "test_dual_measurement_source_contract.py" "target readiness checker does not test runner-owned measurement evidence"
require_text "scripts/check-target-readiness.sh" "test_result_bundle_contract.py" "target readiness checker does not test served result provenance"
require_text "scripts/check-target-readiness.sh" "test_check_reader_output.py" "target readiness checker does not run reader output unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_reader.py" "target readiness checker does not run 本地结果阅读器 unit test"
require_text "scripts/check-target-readiness.sh" "test_plain_ucore_reader_e2e.py" "target readiness checker does not run 本地结果阅读器 e2e unit test"

require_text "scripts/run-dual-platforms.sh" "verify-dual-target-structure" "dual platform runner does not run the structure check"
require_text "scripts/run-dual-platforms.sh" "export TOOLPREFIX QEMU PYTHON_BIN" "dual platform runner does not export tool variables"
require_text "scripts/run-dual-platforms.sh" "seeded dual-target research platform" "dual platform runner does not run the seeded dual-target platform path"
require_text "scripts/run-dual-platforms.sh" "compare_dual_platform_state.py" "dual platform runner does not compare extracted state files"
require_text "scripts/run-dual-platforms.sh" "rp_orch_timing" "dual platform runner does not require orchestrator timing state"
require_text "scripts/run-dual-platforms.sh" "require_plain_program_inventory" "dual platform runner does not enforce plain program evidence role"
require_text "scripts/run-dual-platforms.sh" "require_agentos_program_inventory" "dual platform runner does not enforce AgentOS program evidence role"
require_text "scripts/run-dual-platforms.sh" "program_source_hash" "dual platform runner does not require source-bound program evidence"
require_text "baseline_ucore/user/src/rp_orch.c" "evidence_role=demo_reference" "plain orchestrator does not emit demo-reference inventory evidence"
require_text "baseline_ucore/user/src/rp_seed_orch.c" "RP_PLATFORM_PROGRAMS" "seeded plain orchestrator duplicates the trusted program manifest"
require_text "baseline_ucore/user/src/rp_seed_orch.c" "rp_evidence_measure_program_ledger" "seeded plain orchestrator does not measure its program ledger"
require_text "baseline_ucore/user/src/rp_seed_orch.c" '"rp_seed_orch", PROGRAMS' "seeded plain evidence is not bound to its actual orchestrator"
require_text "user/src/rp_orch.c" "evidence_role=runtime_verified" "AgentOS orchestrator does not emit runtime-verified inventory evidence"
require_text "user/include/rp_program_manifest.h" "RP_PLATFORM_PROGRAMS" "AgentOS program evidence lacks an ordered trusted manifest"
require_text "baseline_ucore/user/include/rp_program_manifest.h" "RP_PLATFORM_PROGRAMS" "plain program evidence lacks an ordered trusted manifest"
require_text "user/include/rp_program_manifest.h" "RP_AGENTOS_ROLE_PROGRAMS" "AgentOS program manifest lacks role-launch identities"
require_text "baseline_ucore/user/include/rp_program_manifest.h" "RP_AGENTOS_ROLE_PROGRAMS" "plain copy lacks the shared AgentOS role-launch contract"
require_text "user/lib/main.c" "rp_report_launch_identity" "AgentOS children do not report their identity after exec"
require_text "user/src/rp_orch.c" "child_after_exec" "AgentOS launcher ledger is not bound to post-exec child identity"
require_text "user/include/rp_evidence.h" "~0ULL - digit" "Guest program ledger parser accepts overflowing integers"
require_text "user/src/rp_orch.c" "agent_worker_create" "AgentOS launcher does not distinguish delegated workers"
require_text "user/src/rp_orch.c" "launcher=mixed_attested" "AgentOS launcher header still claims one launch mechanism"
require_text "host_tools/check_host_platform_alignment.py" "mismatched attested identity" "Host program verifier ignores post-exec identity"
require_text "host_tools/test_check_host_platform_alignment.py" "rejects_launcher_and_post_exec_identity_mutations" "program ledger lacks launcher/identity mutation coverage"
reject_text "host_tools/compare_dual_platform_state.py" "agentos_fork_launches" "AgentOS delegated workers are still reported as fork launches"
require_text "host_tools/check_host_platform_alignment.py" "read_expected_programs" "Host program verifier is not bound to the trusted manifest"
require_text "host_tools/test_check_host_platform_alignment.py" "rejects_fixed_program_count" "program inventory test does not reject fixed counts"
require_text "host_tools/test_check_host_platform_alignment.py" "rejects_self_bound_program_substitution" "program inventory test does not reject same-count substitutions"
require_text "host_tools/test_check_host_platform_alignment.py" "impersonating_agentos_verification" "program inventory test does not reject plain evidence impersonation"
reject_text "host_tools/check_host_platform_alignment.py" "validate_mainflow_source_contract" "mainflow still relies on a bypassable source-text ownership proof"
require_text "host_tools/check_host_platform_alignment.py" '"verification_origin": "host_inventory"' "mainflow verification is not Host-derived"
require_text "host_tools/test_check_host_platform_alignment.py" "host_rejects_any_guest_verified_receipt" "Guest mainflow receipt forgery lacks regression coverage"
require_text "host_tools/test_check_host_platform_alignment.py" "host_rejects_conflicting_claim_and_status" "Host-derived mainflow predicates lack conflict coverage"
require_text "host_tools/check_host_test_alignment.py" "EXPECTED_RUNTIME_ASSERTIONS" "runtime evidence verifier lacks exact source predicates"
require_text "host_tools/check_host_test_alignment.py" "validate_comparator_runtime_evidence" "runtime comparator claims are not independently verified"
require_text "host_tools/test_check_host_test_alignment.py" "requires_its_full_assertion_count" "runtime manifest test does not reject reduced assertion counts"
require_text "host_tools/test_check_host_test_alignment.py" "rejects_matching_substring" "runtime manifest test does not reject substring-forged predicates"
require_text "scripts/run-dual-platforms.sh" "check_host_platform_alignment.py" "dual platform runner does not check host platform capability runtime output"
require_text "scripts/run-dual-platforms.sh" "--plain-profile seeded" "dual platform verifier does not require the seeded launcher profile"
require_text "scripts/run-dual-platforms.sh" "--plain-log" "dual platform verifier does not bind extracted inventory to QEMU logs"
require_text "scripts/run-dual-platforms.sh" "check_host_action_kind_alignment.py" "dual platform runner does not check host action kind handling"
require_text "scripts/run-dual-platforms.sh" "check_seeded_action_state.py" "dual platform runner does not check seeded action runtime state"
require_text "scripts/run-dual-platforms.sh" "check_host_surface_alignment.py" "dual platform runner does not check host Web/API/action surface runtime output"
require_text "scripts/run-dual-platforms.sh" "check_host_test_alignment.py" "dual platform runner does not check host platform test themes"
require_text "scripts/run-dual-platforms.sh" "plain-state" "dual platform runner does not pass plain extracted state to host alignment check"
require_text "scripts/run-dual-platforms.sh" "agentos-state" "dual platform runner does not pass AgentOS extracted state to host alignment check"
require_text "scripts/run-dual-platforms.sh" "plain_ucore_reader.py" "dual platform runner does not render 本地结果阅读器 pages"
require_text "scripts/run-dual-platforms.sh" "check_reader_output.py" "dual platform runner does not validate 本地结果阅读器 output"
require_text "scripts/run-dual-platforms.sh" "compare_dual_platform_reader.py" "dual platform runner does not compare 本地结果阅读器 summaries"
require_text "scripts/run-dual-platforms.sh" "stage-timings.csv" "dual platform runner does not write stage timing diagnostics"
require_text "scripts/run-dual-platforms.sh" "summarize_dual_platform_results.py" "dual platform runner does not generate result charts and report"
require_text "scripts/run-dual-platforms.sh" "extract_measured_experiments.py" "dual platform runner does not extract real Guest measurements"
require_text "scripts/run-dual-platforms.sh" "external measured Agent log injection is forbidden" "dual platform runner does not reject externally supplied measurement evidence"
require_text "scripts/run-dual-platforms.sh" "targeted-agentbench-guest.log" "dual platform runner does not own its targeted Agent Guest log"
require_text "scripts/run-dual-platforms.sh" "AGENT_TEST_CASE=agentbench_ucore" "dual platform runner does not execute the targeted measurement case"
require_text "scripts/run-dual-platforms.sh" "--require-measured-experiments" "dual platform runner does not fail closed without measured experiments"
require_text "scripts/run-dual-platforms.sh" "result monitor" "dual platform runner does not print monitor page path"
require_text "scripts/run-dual-platforms.sh" "seeded-action-state.json" "dual platform runner does not pass seeded action state to 本地结果阅读器"
require_text "host_tools/summarize_dual_platform_results.py" "runtime-observation.svg" "result summarizer does not generate the runtime observation chart"
require_text "host_tools/summarize_dual_platform_results.py" "cost-replacement.svg" "result summarizer does not generate the cost replacement chart"
require_text "host_tools/summarize_dual_platform_results.py" "RUNNER_TICK_STATUS_UNAVAILABLE" "result summarizer does not preserve runner unavailability"
require_text "host_tools/summarize_dual_platform_results.py" "runner-sweep.csv" "result summarizer does not write runner sweep csv"
require_text "host_tools/result_bundle_contract.py" "removed runner measurement fields are present" "served result contract does not reject the removed runner measurement ABI"
require_text "host_tools/test_result_bundle_contract.py" "removed-runner-fields" "served result contract lacks removed runner ABI mutation coverage"
reject_text "host_tools/compare_dual_platform_state.py" "RUNNER_TICK_STATUS_MEASURED|RUNNER_TICK_REASON_SOURCE_BOUND|collect_runner_tick_comparison|runner_tick_comparison" "state comparison still exposes the removed runner measurement ABI"
reject_text "host_tools/evidence_semantic_profiles.py" "source_bound_complete|runner_tick_comparison|runner_tick_expected_pairs|runner_tick_pairs" "evidence semantics still accept the removed runner measurement ABI"
reject_text "host_tools/summarize_dual_platform_results.py" "RUNNER_TICK_STATUS_MEASURED|RUNNER_TICK_REASON_SOURCE_BOUND|RUNNER_TICK_IDENTITIES" "result summarizer still exposes the removed runner measurement ABI"
reject_text "host_tools/result_bundle_contract.py" "source_bound_complete|RUNNER_IDENTITIES" "served result contract still accepts the removed runner measurement ABI"
require_text "host_tools/summarize_dual_platform_results.py" "file-query-benchmark.csv" "result summarizer does not write measured file query csv"
require_text "host_tools/summarize_dual_platform_results.py" "measured-experiments.json" "result summarizer does not verify a measurement manifest"
require_text "host_tools/summarize_dual_platform_results.py" "require_measured_experiments" "result summarizer lacks fail-closed measured mode"
require_text "host_tools/summarize_dual_platform_results.py" '"status": "measured" if rows else "unavailable"' "result summarizer does not disclose missing measurements"
require_text "host_tools/summarize_dual_platform_results.py" "experiment-stats.csv" "result summarizer does not write experiment stats csv"
require_text "host_tools/summarize_dual_platform_results.py" "mechanism-notes.csv" "result summarizer does not write mechanism notes csv"
require_text "host_tools/summarize_dual_platform_results.py" "experiment-file-query-bar.svg" "result summarizer does not generate file query experiment chart"
require_text "host_tools/summarize_dual_platform_results.py" "monitor.html" "result summarizer does not generate the monitor page"
require_text "host_tools/summarize_dual_platform_results.py" "reader-guide.html" "result summarizer does not generate the reader guide page"
require_text "host_tools/summarize_dual_platform_results.py" "evidence-manifest.csv" "result summarizer does not write evidence manifest csv"
require_text "host_tools/summarize_dual_platform_results.py" "evidence-map.html" "result summarizer does not generate evidence map page"
require_text "host_tools/summarize_dual_platform_results.py" "reader-checklist.csv" "result summarizer does not write reader checklist csv"
require_text "host_tools/summarize_dual_platform_results.py" "reader-checklist.html" "result summarizer does not generate reader checklist page"
require_text "host_tools/summarize_dual_platform_results.py" "delivery-readiness.csv" "result summarizer does not write delivery readiness csv"
require_text "host_tools/summarize_dual_platform_results.py" "delivery-readiness.html" "result summarizer does not generate delivery readiness page"
require_text "host_tools/summarize_dual_platform_results.py" "test-suite.csv" "result summarizer does not write test suite csv"
require_text "host_tools/summarize_dual_platform_results.py" "test-suite.html" "result summarizer does not generate test suite page"
require_text "host_tools/summarize_dual_platform_results.py" "experiment-design.csv" "result summarizer does not write experiment design csv"
require_text "host_tools/summarize_dual_platform_results.py" "experiment-design.html" "result summarizer does not generate experiment design page"
require_text "host_tools/test_summarize_dual_platform_results.py" "runtime-observation.svg" "result summary test does not check the runtime observation chart"
require_text "host_tools/test_summarize_dual_platform_results.py" "cost-replacement.svg" "result summary test does not check the cost replacement chart"
require_text "host_tools/test_summarize_dual_platform_results.py" "runner-sweep.csv" "result summary test does not check runner sweep csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "plain_runtime_cases_zero" "result summary test does not preserve unavailable runner reason"
require_text "host_tools/test_summarize_dual_platform_results.py" "file-query-benchmark.csv" "result summary test does not check measured file query csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "source_log_sha256" "result summary test does not check raw-data provenance"
require_text "host_tools/test_summarize_dual_platform_results.py" "unavailable" "result summary test does not check missing measurement disclosure"
require_text "host_tools/test_summarize_dual_platform_results.py" "experiment-stats.csv" "result summary test does not check experiment stats csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "mechanism-notes.csv" "result summary test does not check mechanism notes csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "experiment-file-query-bar.svg" "result summary test does not check file query experiment chart"
require_text "host_tools/test_summarize_dual_platform_results.py" "monitor.html" "result summary test does not check the monitor page"
require_text "host_tools/test_summarize_dual_platform_results.py" "reader-guide.html" "result summary test does not check the reader guide page"
require_text "host_tools/test_summarize_dual_platform_results.py" "evidence-manifest.csv" "result summary test does not check evidence manifest csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "evidence-map.html" "result summary test does not check evidence map page"
require_text "host_tools/test_summarize_dual_platform_results.py" "reader-checklist.csv" "result summary test does not check reader checklist csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "reader-checklist.html" "result summary test does not check reader checklist page"
require_text "host_tools/test_summarize_dual_platform_results.py" "delivery-readiness.csv" "result summary test does not check delivery readiness csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "delivery-readiness.html" "result summary test does not check delivery readiness page"
require_text "host_tools/test_summarize_dual_platform_results.py" "test-suite.csv" "result summary test does not check test suite csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "test-suite.html" "result summary test does not check test suite page"
require_text "host_tools/test_summarize_dual_platform_results.py" "experiment-design.csv" "result summary test does not check experiment design csv"
require_text "host_tools/test_summarize_dual_platform_results.py" "experiment-design.html" "result summary test does not check experiment design page"
require_text "host_tools/test_chart_type_data_contract.py" "runner-sweep.csv" "chart data contract test does not check runner sweep csv"
require_text "host_tools/test_chart_type_data_contract.py" "file-query-benchmark.csv" "chart data contract test does not check measured file query csv"
require_text "host_tools/test_chart_type_data_contract.py" "source_log_sha256" "chart data contract test does not check measurement provenance"
require_text "host_tools/test_chart_type_data_contract.py" "experiment-file-query-bar.svg" "chart data contract test does not check file query experiment chart"
require_text "host_tools/test_chart_type_data_contract.py" "evidence-manifest.csv" "chart data contract test does not check evidence manifest csv"
require_text "host_tools/measured_experiments.py" "source_marker_sha256" "measurement extractor does not bind marker hashes"
require_text "host_tools/measured_experiments.py" "agentbench_ucore: parent passed" "measurement extractor does not require complete Guest success"
require_text "host_tools/benchmark_source_contract.py" "FIELD_BINDINGS" "benchmark fields are not bound to source expressions"
require_text "host_tools/benchmark_source_contract.py" "_validate_timed_loop" "benchmark operation counts are not bound to measured loops"
require_text "host_tools/test_measured_experiments.py" "agentbench-hardcoded-" "benchmark hard-coded field mutations are not rejected"
require_text "host_tools/test_measured_experiments.py" "agentbench-short-loop.c" "benchmark loop-count mutations are not rejected"
require_text "host_tools/test_measured_experiments.py" "not followed by a pass marker" "measurement test does not reject uncompleted Guest runs"
require_text "host_tools/test_measured_experiments.py" "child-only.log" "measurement test does not reject child-only success"
require_text "host_tools/test_backend_evidence_contract.py" "substring_matched" "backend log contract lacks strict-line regressions"
require_text "host_tools/test_backend_evidence_contract.py" "printf_binding_mutations" "backend source wiring mutations are not rejected"
require_text "host_tools/test_gitlab_ci_contract.py" "recursive_extends_and_child_override" "GitLab CI resolver lacks inheritance override regression"
require_text "host_tools/test_gitlab_ci_contract.py" "unknown_parent_cycle_and_duplicate_field" "GitLab CI resolver lacks fail-closed graph regressions"
require_text "host_tools/test_gitlab_ci_contract.py" "child_duplication_are_rejected" "GitLab CI contract permits duplicated inherited policy"
require_text "host_tools/test_gitlab_ci_contract.py" "skip_capable_root_and_job_policies" "GitLab CI contract lacks skip-policy mutations"
require_text "host_tools/test_gitlab_ci_contract.py" "wrapper_extra_command_and_environment_hijacks" "GitLab CI contract lacks command and environment mutations"
require_text "host_tools/test_remote_ci_evidence.py" "real_zip_attestation_round_trip" "remote CI evidence lacks a real ZIP positive control"
require_text "host_tools/test_remote_ci_evidence.py" "unsafe_zip_mutations" "remote CI evidence lacks unsafe ZIP mutations"
require_text "host_tools/test_remote_ci_evidence.py" "stay_within_maintenance_budgets" "remote CI modules have no line-count budgets"
require_text "host_tools/remote_ci_evidence.py" "host_tools/remote_ci_archive.py" "remote CI attestation does not bind its archive verifier"
require_text "host_tools/remote_ci_evidence.py" "host_tools/evidence_semantic_profiles.py" "remote CI attestation does not bind semantic implementations"
require_text "host_tools/remote_ci_evidence.py" "host_tools/dual_state_evidence_contract.py" "remote CI attestation does not bind the dual-state contract"
require_text "scripts/capture-final-evidence.py" "host-platform-alignment.json" "final evidence omits Host-derived alignment"
require_text "scripts/run-full-verification.sh" "MAIN_FLOW_SOURCE_ARTIFACTS" "full verification does not publish Mainflow source evidence"
require_text "host_tools/evidence_semantic_dual.py" "mainflow_host_telemetry_hash" "offline evidence does not replay Mainflow telemetry"
require_text "host_tools/test_capture_final_evidence.py" "Host-derived Mainflow receipt differs" "final evidence lacks Mainflow receipt mutations"
require_text "scripts/run-full-verification.sh" "PROGRAM_LEDGER_ARTIFACTS" "full verification does not publish program ledgers"
require_text "host_tools/evidence_semantic_dual.py" "validate_program_ledgers" "offline evidence does not replay program ledgers"
require_text "host_tools/test_capture_final_evidence.py" "rp_forgery" "final evidence lacks self-consistent program identity forgery coverage"
require_text "host_tools/test_capture_final_evidence.py" "test_semantic_program_manifest_matches_both_targets" "offline trusted program inventory can drift from Guest manifests"
require_text "host_tools/dual_state_archive.py" "min_common_files=240" "final evidence does not replay complete dual state"
require_text "scripts/run-dual-platforms.sh" "--plain-run-result" "dual comparison does not receive an independent plain Host receipt"
require_text "scripts/run-dual-platforms.sh" "--agentos-run-result" "dual comparison does not receive an independent AgentOS Host receipt"
require_text "scripts/run-dual-platforms.sh" "--seeded-summary" "dual comparison does not bind the seeded action summary"
reject_text "scripts/run-dual-platforms.sh" 'plain-state/rp_host_run_result|agentos-state/rp_host_run_result' "Host run receipt is copied into Guest state"
reject_text "scripts/run-dual-platforms.sh" 'target_dir[^[:space:]]*rp_host_run_result|rp_host_run_result[^[:space:]]*target_dir' "Host run receipt is copied through a generic Guest state target"
require_text "host_tools/plain_ucore_action_runner.py" "sidecar must be outside Guest state" "Host receipt publisher does not enforce namespace isolation"
require_text "host_tools/plain_ucore_action_runner.py" "create_private_directory" "action runner does not claim a private run directory"
require_text "host_tools/test_plain_ucore_action_runner.py" "preplanted run-directory symlink was accepted" "action runner lacks preplanted run-directory link coverage"
require_text "host_tools/plain_ucore_action_runner.py" "guest_state_sha256" "Host receipt does not bind Guest state contents"
require_text "host_tools/plain_ucore_reader.py" "does not bind the Guest state contents" "Reader does not verify Guest state receipt contents"
require_text "host_tools/test_plain_ucore_reader.py" "same-count Guest content mutation kept a valid receipt" "Reader lacks same-count Guest mutation coverage"
require_text "host_tools/plain_ucore_reader.py" "HOST_LLM_OVERLAY_RELATIVE_PATH" "Host LLM relay output is not isolated from Guest state"
require_text "host_tools/plain_ucore_reader.py" "ensure_private_directory" "Host LLM relay does not use the shared private-directory mechanism"
require_text "host_tools/test_plain_ucore_reader.py" "relay accepted a linked host-state directory" "Reader lacks Host overlay junction coverage"
require_text "host_tools/test_plain_ucore_reader.py" "relay accepted a linked output ancestor" "Reader lacks Host overlay ancestor-link coverage"
require_text "host_tools/dual_state_archive.py" "HOST_RUN_RESULT_STATE_NAME" "complete-state archive does not reject Host receipt members"
require_text "host_tools/test_compare_dual_platform_state.py" "Host run result must not appear in Guest state inventory" "state comparison lacks Host receipt inventory isolation coverage"
require_text "host_tools/test_compare_dual_platform_state.py" "contradictory success fields" "state comparison lacks contradictory Host receipt coverage"
require_text "host_tools/test_capture_final_evidence.py" "traversal_files" "complete-state archive lacks path traversal mutation coverage"
require_text "host_tools/evidence_semantic_dual.py" "CAPABILITY_GROUPS" "Host alignment groups are not bound to the shared capability contract"
require_text "scripts/capture-final-evidence.py" "verify_job_execution" "final evidence does not verify downloaded CI execution attestations"
require_text "scripts/check-target-readiness.sh" "test_remote_ci_evidence.py" "target readiness omits remote CI attestation mutations"
reject_text "host_tools/summarize_dual_platform_results.py" "stable_jitter" "result summarizer still contains formula jitter generation"
if find "${ROOT_DIR}/docs/assets/verification-charts" -maxdepth 1 -type f \
	-name 'experiment-*.svg' -print -quit | grep -q .; then
	fail "committed documentation still contains obsolete formula experiment SVGs"
fi
reject_text "README.md" "file-metadata\.csv|context-timeline\.csv|event-loop\.csv|agent-concurrency\.csv|llm-relay\.csv|recovery-flow\.csv" "README still claims obsolete formula experiments"
reject_text "docs" "file-metadata\.csv|context-timeline\.csv|event-loop\.csv|agent-concurrency\.csv|llm-relay\.csv|recovery-flow\.csv" "documentation still claims obsolete formula experiments"
require_text "scripts/check-target-readiness.sh" "test_chart_type_data_contract.py" "target readiness does not run chart data contract test"
require_text "scripts/check-target-readiness.sh" "test_chart_svg_layout_contract.py" "target readiness does not run chart layout contract test"
require_text "host_tools/test_chart_svg_layout_contract.py" "validate_chart" "chart layout test does not validate SVG charts"
require_text "host_tools/test_chart_svg_layout_contract.py" "intersects" "chart layout test does not check text overlap"
require_text "host_tools/test_chart_svg_layout_contract.py" "verification-charts" "chart layout test does not check committed documentation charts"
require_text "host_tools/test_llm_relay_mode_contract.py" "deepseek-v4-pro" "LLM relay mode contract does not check the DeepSeek model field"
require_text "host_tools/test_llm_relay_mode_contract.py" "AGENT_PLATFORM_LLM_API_KEY_FILE" "LLM relay mode contract does not check external key file handling"
require_text "host_tools/test_llm_relay_mode_contract.py" "key_present=0" "LLM relay mode contract does not check no-key default mode"
require_text "host_tools/test_llm_relay_mode_contract.py" "key_present=1" "LLM relay mode contract does not check external-key mode"
require_text "host_tools/test_llm_relay_mode_contract.py" "secret_material=not_written" "LLM relay mode contract does not check secret write protection"
require_text "host_tools/compare_dual_platform_state.py" "SCENARIO_EVIDENCE_SPECS" "state comparison does not define scenario evidence specs"
require_text "host_tools/compare_dual_platform_state.py" "collect_cost_replacements" "state comparison does not collect backend cost replacement evidence"
require_text "host_tools/compare_dual_platform_state.py" "collect_runner_tick_evidence" "state comparison does not disclose runner availability"
require_text "host_tools/test_compare_dual_platform_state.py" "scenario_evidence" "state comparison test does not check scenario evidence"
require_text "host_tools/test_compare_dual_platform_state.py" "cost_replacement_count" "state comparison test does not check cost replacement evidence"
require_text "host_tools/test_compare_dual_platform_state.py" "plain_runtime_cases_zero" "state comparison test does not check runner unavailability"
require_text "host_tools/plain_ucore_reader.py" "seeded-action-state" "本地结果阅读器 does not accept seeded action state input"
require_text "host_tools/plain_ucore_reader.py" "host_seeded_action" "本地结果阅读器 does not render seeded action state"
require_text "host_tools/plain_ucore_reader.py" "--host-run-result" "本地结果阅读器 does not accept a Host receipt sidecar"
require_text "host_tools/plain_ucore_action_runner.py" 'os.environ.get\("TOOLPREFIX"' "action runner does not read TOOLPREFIX from environment"
require_text "host_tools/plain_ucore_action_runner.py" "run_observed_command" "action runner does not observe QEMU output directly"
require_text "host_tools/plain_ucore_action_runner.py" "qemu_elapsed_seconds" "action runner does not write QEMU timing evidence"
require_text "host_tools/compare_dual_platform_state.py" "verify_orch_timing" "dual platform comparison does not validate per-program timing evidence"
require_text "scripts/run-dual-platforms.sh" "backend_evidence_contract.py" "dual runner does not use the shared backend contract"
require_text "scripts/run-dual-platforms.sh" "plain_backend_summary" "dual runner does not render parsed plain backend evidence"
require_text "scripts/run-dual-platforms.sh" "agentos_backend_summary" "dual runner does not render parsed AgentOS backend evidence"
reject_text "scripts/run-dual-platforms.sh" "rp_backend: evidence_(role|generation)=" "dual runner duplicates backend marker literals outside the shared contract"
if ! "${PYTHON_BIN}" "${ROOT_DIR}/host_tools/backend_evidence_contract.py" \
	verify-source --target plain \
	--source "${ROOT_DIR}/baseline_ucore/user/src/rp_backend.c" >"${TMP_FILE}" 2>&1; then
	fail "plain backend source differs from the shared evidence contract"
fi
if ! "${PYTHON_BIN}" "${ROOT_DIR}/host_tools/backend_evidence_contract.py" \
	verify-source --target agentos \
	--source "${ROOT_DIR}/user/src/rp_backend.c" >"${TMP_FILE}" 2>&1; then
	fail "AgentOS backend source differs from the shared evidence contract"
fi
: >"${TMP_FILE}"
require_text "scripts/serve-reader.sh" "rp_agentos_mainflow" "reader script does not check AgentOS mainflow state"
require_text "scripts/serve-reader.sh" "serve" "reader script does not start the local 本地结果服务"
require_text "scripts/serve-reader.sh" "dual-results/monitor.html" "reader script does not expose result monitor under the 本地结果服务"
require_text "scripts/serve-reader.sh" "dual-results/reader-guide.html" "reader script does not expose result guide under the 本地结果服务"
require_text "scripts/serve-reader.sh" "reader-url-list.txt" "reader script does not generate the URL list"
require_text "scripts/serve-reader.sh" "dual-results/test-suite.html" "reader script does not expose test suite page"
require_text "scripts/serve-reader.sh" "dual-results/experiment-design.html" "reader script does not expose experiment design page"
require_text "scripts/serve-reader.sh" "dual-results/evidence-map.html" "reader script does not expose evidence map page"
require_text "scripts/serve-reader.sh" "RESULT_DIR" "reader script does not accept a result directory"
require_text "scripts/serve-reader.sh" "result_bundle_contract.py" "reader script does not validate an existing result bundle"
require_text "scripts/serve-reader.sh" "拒绝提供过期或伪造证据" "reader script does not fail closed on stale evidence"
require_text "host_tools/plain_ucore_reader.py" "relative_to\\(out_dir.resolve\\(\\)\\)" "本地结果阅读器 does not guard nested static file paths"
require_text "host_tools/test_plain_ucore_reader.py" "dual-results/monitor.html" "本地结果阅读器 test does not cover nested result monitor serving"
require_text "host_tools/test_plain_ucore_reader.py" "dual-results/reader-guide.html" "本地结果阅读器 test does not cover nested reader guide serving"
require_text "host_tools/test_plain_ucore_reader.py" "reader-url-list.txt" "本地结果阅读器 test does not cover URL list serving"
require_text "host_tools/test_plain_ucore_reader.py" "dual-results/test-suite.html" "本地结果阅读器 test does not cover nested test suite serving"
require_text "host_tools/test_plain_ucore_reader.py" "dual-results/experiment-design.html" "本地结果阅读器 test does not cover nested experiment design serving"
require_text "host_tools/test_plain_ucore_reader.py" "dual-results/evidence-map.html" "本地结果阅读器 test does not cover nested evidence map serving"

require_text "agent_tool_abi.h" "struct agent_request_v2" "shared UAPI lacks the versioned tool request"
require_text "agent_tool_abi.h" "AGENT_STATUS_UNKNOWN_PARAM" "versioned tool ABI lacks strict unknown-parameter status"
require_text "os/syscall_ids.h" "SYS_tool_call 547" "kernel syscall table lacks literal sys_tool_call ABI"
require_text "os/syscall_ids.h" "SYS_tool_list 548" "kernel syscall table lacks literal sys_tool_list ABI"
require_text "user/lib/syscall.c" "int sys_tool_call" "user ABI lacks literal sys_tool_call wrapper"
require_text "user/lib/syscall.c" "int sys_tool_list" "user ABI lacks literal sys_tool_list wrapper"
require_text "user/src/agenttoolabi_ucore.c" "agenttoolabi_ucore: tool_list_contract=1" "tool ABI Guest test lacks list contract marker"
require_text "user/src/agenttoolabi_ucore.c" "agenttoolabi_ucore: optional_schema=1 heartbeat_zero_stop=1" "tool ABI Guest test lacks descriptor semantics marker"
require_text "user/src/agenttoolabi_ucore.c" "agenttoolabi_ucore: schema_generated=1 validated=%d" "tool ABI Guest test lacks full generated-schema marker"
require_text "user/src/agenttoolabi_ucore.c" "agenttoolabi_ucore: key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1" "tool ABI Guest test lacks key capacity and response sentinel evidence"
require_text "user/src/agenttoolabi_ucore.c" "agenttoolabi_ucore: strict_negative_matrix=1" "tool ABI Guest test lacks strict negative marker"
require_text "scripts/run-agent-tests.sh" "agenttoolabi_ucore: tool_list_contract=1" "Agent runner does not require tool list contract evidence"
require_text "scripts/run-agent-tests.sh" "agenttoolabi_ucore: optional_schema=1 heartbeat_zero_stop=1" "Agent runner does not require descriptor semantics evidence"
require_text "scripts/run-agent-tests.sh" "agenttoolabi_ucore: schema_generated=1 validated=25" "Agent runner does not require full generated-schema evidence"
require_text "scripts/run-agent-tests.sh" "agenttoolabi_ucore: key_capacity=1 llm_response_v1_v2=1 buffer_sentinel=1" "Agent runner does not require key capacity and response sentinel evidence"
require_text "scripts/run-agent-tests.sh" "agenttoolabi_ucore: strict_negative_matrix=1" "Agent runner does not require strict tool ABI evidence"

require_text "os/syscall_ids.h" "SYS_agent_heartbeat_set 552" "kernel syscall table lacks independent heartbeat set ABI"
require_text "os/syscall_ids.h" "SYS_agent_heartbeat_stop 553" "kernel syscall table lacks independent heartbeat stop ABI"
require_text "os/syscall_ids.h" "SYS_agent_heartbeat 512" "kernel syscall table lost the legacy heartbeat ABI"
require_text "user/lib/syscall_ids.h" "SYS_agent_heartbeat 512" "user syscall table lost the legacy heartbeat ABI"
require_text "user/lib/syscall_ids.h" "SYS_agent_heartbeat_set 552" "user syscall table lacks heartbeat set ABI"
require_text "user/lib/syscall_ids.h" "SYS_agent_heartbeat_stop 553" "user syscall table lacks heartbeat stop ABI"
require_text "user/lib/arch/riscv/syscall_ids.h.in" "__NR_agent_heartbeat 512" "arch syscall template lost the legacy heartbeat ABI"
require_text "user/lib/arch/riscv/syscall_ids.h.in" "__NR_agent_heartbeat_set 552" "arch syscall template lacks heartbeat set ABI"
require_text "user/lib/arch/riscv/syscall_ids.h.in" "__NR_agent_heartbeat_stop 553" "arch syscall template lacks heartbeat stop ABI"
require_text "agent_tool_abi.h" "AGENT_HEARTBEAT_MAX_TICKS 0x7fffffffULL" "shared ABI lacks the heartbeat interval bound"
require_text "user/lib/syscall.c" "int sys_agent_heartbeat_set" "user ABI lacks literal heartbeat set wrapper"
require_text "user/lib/syscall.c" "int sys_agent_heartbeat_stop" "user ABI lacks literal heartbeat stop wrapper"
require_text "user/src/agentloop_ucore.c" "heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1" "heartbeat Guest test lacks strict mechanism evidence"
require_text "scripts/run-agent-tests.sh" "heartbeat_intrinsic=1 dynamic=1 coalesced=1 stop=1 bounds=1 legacy=1" "Agent runner does not require strict heartbeat evidence"

require_text "os/agent.h" "AGENT_CONTEXT_VERSION[[:space:]]+8" "kernel Context ABI is not version 8"
require_text "user/include/agent.h" "AGENT_CONTEXT_VERSION[[:space:]]+8" "user Context ABI is not version 8"
require_text "user/src/agentfinal_ucore.c" "context_rollback_branch=1 sequence_reuse=0 provenance_bound=1" "Context rollback Guest test lacks immutable-branch evidence"
require_text "user/src/agentfinal_ucore.c" "context_active_path=1 archive_retained=1 direct_query=1 fifo_suffix=1" "Context rollback Guest test lacks active-path evidence"
require_text "os/agent.h" "path_parent_sequence" "kernel Context ABI lacks local active-path parent"
require_text "user/include/agent.h" "path_parent_sequence" "user Context ABI lacks local active-path parent"
require_text "os/agent_context.c" "record.path_parent_sequence = p->context_path_visible_head" "Context append does not bind the active-path predecessor"
require_text "os/agent_context.c" "agent_context_active_record" "Context query does not project the active path"
require_text "os/agent_context_path.c" "kernel_work_checkpoint\(1\)" "Context active-path query lacks bounded fairness checkpoints"
require_text "os/agent_context_path.c" "record->path_parent_sequence" "Context record hash omits the active-path predecessor"
require_text "user/lib/syscall.c" "context_mirror_active_query" "user ABI lacks direct active-path mirror validation"
require_text "user/src/agentfinal_ucore.c" "context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1" "Context Guest test lacks failed-sync atomicity evidence"
require_text "user/src/agentfinal_ucore.c" "context_rollback_negative nonexistent=1 evicted=1" "Context rollback Guest test lacks negative evidence"
require_text "user/src/agentfinal_ucore.c" "fifo oldest=.*policy=1" "Context Guest test does not publish FIFO policy evidence"
require_text "user/src/agentfinal_ucore.c" "context_query_cache=1 user_managed=1 kernel_cache_hit=0" "Context Guest test lacks user-managed structured query cache evidence"
require_text "scripts/run-agent-tests.sh" "context_rollback_branch=1 sequence_reuse=0 provenance_bound=1" "Agent runner does not require rollback branch evidence"
require_text "scripts/run-agent-tests.sh" "context_sync_atomic=1 append=1 rollback=1 clear=1 recovery=1" "Agent runner does not require failed-sync atomicity evidence"
require_text "scripts/run-agent-tests.sh" "wait_publication_atomic=1 event_wake_none=1 event_no_sleep=1 sibling_wake_none=1 teardown_completed=1" "Agent runner does not require atomic wait evidence"
require_text "scripts/run-agent-tests.sh" "thread_wait_deadlines finite_infinite=1 distinct_deadlines=1 keyed_timer=1 loop_aggregate=1 slot_reuse=1" "Agent runner does not require per-thread deadline evidence"
require_text "scripts/run-agent-tests.sh" "--profile wait-atomic" "Agent runner bypasses the atomic wait log validator"
require_text "Makefile" 'AGENT_CONTEXT_SYNC_TEST_PROFILE=\$\(AGENT_CONTEXT_SYNC_TEST_PROFILE\)' "kernel build fingerprint omits the Context sync profile"
require_text "Makefile" 'WAIT_ATOMIC_TEST_PROFILE=\$\(WAIT_ATOMIC_TEST_PROFILE\)' "kernel build fingerprint omits the atomic wait test profile"
require_text "Makefile" 'AGENT_SIZE_OPTIMIZED_MODULES=\$\(AGENT_SIZE_OPTIMIZED_MODULES\)' "kernel build fingerprint omits target-specific size optimization membership"
require_text "user/Makefile" "USER_BUILD_CONFIG" "user objects do not track their effective build configuration"
require_text "user/Makefile" 'CFLAGS=\$\(CFLAGS\)' "user build fingerprint omits effective CFLAGS"
require_text "scripts/run-agent-tests.sh" "agent-mechanism:context-sync-atomicity" "Context sync fault profile is not isolated from suite evidence"
require_text "scripts/run-agent-tests.sh" "context_rollback_negative nonexistent=1 evicted=1" "Agent runner does not require rollback negative evidence"
require_text "scripts/run-agent-tests.sh" "context_query_cache=1 user_managed=1 kernel_cache_hit=0" "Agent runner does not require user-managed query cache evidence"
reject_text "os/agent_metadata_query.c" "agent_file_query_cache|AGENT_FILE_QUERY_REASON_CACHE_HIT" "kernel file query path still contains a global result cache"

agent_suite_step_line="$(grep -n 'AgentOS kernel tests' "${ROOT_DIR}/scripts/run-full-verification.sh" | head -1 | cut -d: -f1)"
dual_platform_step_line="$(grep -n '\[full-verify\] dual platforms' "${ROOT_DIR}/scripts/run-full-verification.sh" | head -1 | cut -d: -f1)"
if [ -z "${agent_suite_step_line}" ] || [ -z "${dual_platform_step_line}" ] ||
   [ "${agent_suite_step_line}" -ge "${dual_platform_step_line}" ]; then
	fail "full verification must capture Agent Guest measurements before rendering dual-platform results"
fi

plain_platform_tests="$(make_var_words "${ROOT_DIR}/baseline_ucore/user/Makefile" "PLATFORM_TESTS")"
agentos_platform_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" "PLATFORM_TESTS")"
plain_seeded_tests="$(make_var_words "${ROOT_DIR}/baseline_ucore/user/Makefile" "PLATFORM_SEEDED_TESTS")"
agentos_seeded_tests="$(make_var_words "${ROOT_DIR}/user/Makefile" "PLATFORM_SEEDED_TESTS")"
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
		if [ ! -f "${ROOT_DIR}/user/src/${app}.c" ]; then
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
	agentos_source="${ROOT_DIR}/user/src/${source_name}"

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
$(find "${ROOT_DIR}/baseline_ucore/user/src" -maxdepth 1 -type f -name 'rp_*.c' | sort)
EOF
if [ "${plain_source_count}" -lt "${plain_platform_count}" ]; then
	fail "plain rp source count is smaller than platform build list count: ${plain_source_count} < ${plain_platform_count}"
fi

for source_name in ${agentos_adapted_sources}; do
	if [ ! -f "${ROOT_DIR}/baseline_ucore/user/src/${source_name}" ]; then
		fail "declared AgentOS adapted source is missing in plain platform: ${source_name}"
	fi
	if [ ! -f "${ROOT_DIR}/user/src/${source_name}" ]; then
		fail "declared AgentOS adapted source is missing in AgentOS platform: ${source_name}"
	fi
	if same_source_content "${ROOT_DIR}/baseline_ucore/user/src/${source_name}" "${ROOT_DIR}/user/src/${source_name}"; then
		fail "declared AgentOS adapted source no longer differs from plain source: ${source_name}"
	fi
done

plain_backend_src="${ROOT_DIR}/baseline_ucore/user/src/rp_backend.c"
agentos_backend_src="${ROOT_DIR}/user/src/rp_backend.c"
plain_backend_cases="$(first_number_after_key "${plain_backend_src}" "reference_cases")"
agentos_backend_cases="$(first_number_after_key "${agentos_backend_src}" "cases")"
plain_detail_rows="$(first_number_after_key "${plain_backend_src}" "reference_case_rows")"
agentos_detail_rows="$(first_number_after_key "${agentos_backend_src}" "runner_detail_rows")"
plain_report_rows="$(first_number_after_key "${plain_backend_src}" "reference_report_rows")"
agentos_report_rows="$(first_number_after_key "${agentos_backend_src}" "runner_report_rows")"

if [ "${agentos_backend_cases}" -lt "${plain_backend_cases}" ]; then
	fail "AgentOS runtime catalog has fewer cases than the plain reference catalog: ${agentos_backend_cases} < ${plain_backend_cases}"
fi
if [ "${agentos_detail_rows}" -lt "${plain_detail_rows}" ]; then
	fail "AgentOS runtime catalog has fewer detail rows than the plain reference catalog: ${agentos_detail_rows} < ${plain_detail_rows}"
fi
if [ "${agentos_report_rows}" -lt "${plain_report_rows}" ]; then
	fail "AgentOS runtime catalog has fewer report rows than the plain reference catalog: ${agentos_report_rows} < ${plain_report_rows}"
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

require_text "os" "SYS_agent_create" "AgentOS syscall table is missing agent_create"
require_text "os" "AGENT_CONTEXT" "AgentOS context definitions are missing"
require_text "os" "AGENT_TOOL_LLM_REQUEST" "AgentOS LLM request tool is missing"
require_text "os" "agent_file_edit_begin" "AgentOS edit lease syscall is missing"
require_text "user/Makefile" "agentllm_ucore" "AgentOS LLM test is not in the user build list"
require_text "scripts/run-agent-tests.sh" "agentllm_ucore" "AgentOS LLM test is not in the test script"

if grep -E -n 'AGENT_TOOL_(RERUN_STAGE|WRITE_REPORT)' \
	"${ROOT_DIR}"/user/src/rp_*.c >"${TMP_FILE}" 2>/dev/null; then
	fail "AgentOS platform code uses legacy sample tool ids"
fi
: >"${TMP_FILE}"

sample_run_marker='RUN-''042'
sample_kernel_pattern="lab-gene-x|${sample_run_marker}|nightly-regression|/lab/projects|INC-RUN|PLAN-RUN|MSG-RUN|minimal_rerun|memory_limit|recovery report|rerun completed|stage=(prepare|align|analyze|report|archive)|label=(prepare|align|analyze|report|archive)|source_stage=(prepare|align|analyze|report|archive)|next_stage=(prepare|align|analyze|report|archive)"
reject_text "os" "${sample_kernel_pattern}" "AgentOS kernel contains research sample constants"

bad_git_commit="git ""commit"
bad_git_push="git ""push"
bad_glpat="gl""pat-"
bad_oauth2="oauth""2:"
bad_auth="Authorization:"" Basic"
bad_tokens="tokens""\\.txt"
doc_pattern="${bad_glpat}|${bad_oauth2}|${bad_auth}|${bad_tokens}|${bad_git_commit}|${bad_git_push}"
reject_text "README.md" "${doc_pattern}" "root README contains forbidden or sensitive wording"
reject_text "docs" "${doc_pattern}" "root docs contain forbidden or sensitive wording"

echo "[dual-target-check] baseline AgentOS surface: absent"
echo "[dual-target-check] AgentOS kernel: present"
echo "[dual-target-check] platform source coverage: ${plain_source_count} baseline rp sources mirrored"
echo "[dual-target-check] platform app coverage: ${plain_platform_count} build-list apps mirrored"
echo "[dual-target-check] platform source sync: identical=${plain_source_identical_count} adapted=${agentos_adapted_count}"
echo "[dual-target-check] backend evidence coverage: plain=${plain_backend_cases} agentos=${agentos_backend_cases} preserved_costs=${plain_cost_count}"
echo "[dual-target-check] platform runners: present"
echo "[dual-target-check] AgentOS kernel sample constants: absent"
echo "[dual-target-check] AgentOS platform legacy tools: security tests only"
echo "[dual-target-check] docs: wording scan passed"
